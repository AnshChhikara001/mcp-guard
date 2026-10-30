"""Cedar permissions: whether the person the Agent works for may make a Tool call.

The policies and their schema are files in gateway/policies (GATEWAY_POLICIES_DIR picks
another folder): `schema.cedarschema` and every `*.cedar` file. They are validated against the
schema when the Gateway starts, and a policy that doesn't fit stops it from starting.

For each Tool call, the Gateway asks Cedar whether the person (principal) may use the tool
(action) on what the call touches (resource). Tools take account, transaction or ticket IDs,
not the Customer they belong to, so the Gateway first looks up in the bank who owns what the
call names. Cedar denies anything no policy permits, so a tool with no policy is denied, and
so is a tool the Gateway doesn't know how to look up. An ID the bank doesn't have is denied
with the same reason as someone else's, so the Agent can't probe which IDs exist.
"""

import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cedarpy

# cedarpy annotates its JSON arguments as bare `dict`, which strict pyright can't see into.
from cedarpy import is_authorized, validate_policies  # pyright: ignore[reportUnknownVariableType]

from mcp_guard.gateway import bank
from mcp_guard.gateway.decisions import Allow, Decision, Deny
from mcp_guard.gateway.tokens import Person

POLICIES_DIR = Path(__file__).parents[3] / "policies"
LAYER = "cedar"

NOT_YOURS = Deny(
    LAYER,
    "not_found_or_not_yours",
    "Not found, or not yours: the person you work for may only use their own accounts,"
    " transactions, profile and tickets, or, for Staff, those of the Customers they serve.",
)
RECIPIENT_NOT_ALLOWED = Deny(
    LAYER,
    "recipient_not_allowed",
    "Messages may only go to the registered email or phone of the person you work for, or,"
    " for Staff, of a Customer they serve.",
)
NOT_PERMITTED = Deny(
    LAYER, "not_permitted", "The person you work for may not use this tool in their role."
)
NO_RULE = Deny(LAYER, "no_rule", "No policy allows this tool, so the Gateway blocks it.")

type _Entity = dict[str, Any]  # an entity in Cedar's JSON form
type _Finder = Callable[[Mapping[str, object]], Awaitable[list[_Entity] | None]]


class PolicyError(Exception):
    """The policies or their schema can't be read, or a policy doesn't fit the schema."""


class Permissions:
    """The Cedar policies in `policies_dir`, read and validated once."""

    def __init__(self, policies_dir: Path) -> None:
        try:
            schema = cedarpy.Schema.from_str((policies_dir / "schema.cedarschema").read_text())
            policies = "\n".join(p.read_text() for p in sorted(policies_dir.glob("*.cedar")))
        except (OSError, ValueError) as e:
            raise PolicyError(f"Can't read the policies in {policies_dir}: {e}") from e
        validation = validate_policies(policies, schema)
        if not validation.validation_passed:
            errors = "; ".join(str(error) for error in validation.errors)
            raise PolicyError(f"The policies in {policies_dir} don't fit their schema: {errors}")
        self._schema = schema
        self._policies = cedarpy.PolicySet.from_str(policies)

    async def decide(
        self, person: Person, tool: str, arguments: Mapping[str, object] | None
    ) -> Decision:
        rule = _RULES.get(tool)
        if rule is None:
            return NO_RULE
        resource = await rule.find(arguments or {})
        if resource is None:
            return rule.denied
        principal = await _principal(person)
        request = {
            "principal": principal[0]["uid"],
            "action": {"type": "Action", "id": tool},
            "resource": resource[0]["uid"],
            "context": {},
        }
        entities = list({_key(e): e for e in principal + resource}.values())
        result = is_authorized(request, self._policies, entities, schema=self._schema)
        return Allow(LAYER) if result.allowed else rule.denied


def configured_policies_dir() -> Path:
    """The folder GATEWAY_POLICIES_DIR names, or gateway/policies."""
    return Path(os.environ.get("GATEWAY_POLICIES_DIR") or POLICIES_DIR)


@dataclass(frozen=True)
class _Rule:
    """How to find what a tool's call touches, and how it is denied."""

    find: _Finder
    denied: Deny


def _customer(argument: str, lookup: Callable[[str], Awaitable[str | None]]) -> _Finder:
    """The Customer that `lookup` finds from the call's `argument`."""

    async def find(arguments: Mapping[str, object]) -> list[_Entity] | None:
        value = arguments.get(argument)
        customer_id = await lookup(value) if isinstance(value, str) else None
        return None if customer_id is None else [_entity("Customer", customer_id)]

    return find


async def _ticket(arguments: Mapping[str, object]) -> list[_Entity] | None:
    ticket_id = arguments.get("ticket_id")
    if not isinstance(ticket_id, str) or (owner := await bank.owner_of_ticket(ticket_id)) is None:
        return None
    return [
        _entity("Ticket", ticket_id, owner=_reference("Customer", owner)),
        _entity("Customer", owner),
    ]


def _only(entity_type: str, entity_id: str) -> _Finder:
    """The one entity a tool always touches, whatever its arguments."""

    async def find(arguments: Mapping[str, object]) -> list[_Entity] | None:
        return [_entity(entity_type, entity_id)]

    return find


_RULES: Mapping[str, _Rule] = {
    "get_customer_profile": _Rule(_customer("customer_id", bank.customer), NOT_YOURS),
    "list_accounts": _Rule(_customer("customer_id", bank.customer), NOT_YOURS),
    "get_balance": _Rule(_customer("account_id", bank.owner_of_account), NOT_YOURS),
    "list_transactions": _Rule(_customer("account_id", bank.owner_of_account), NOT_YOURS),
    "raise_dispute": _Rule(_customer("transaction_id", bank.owner_of_transaction), NOT_YOURS),
    "refund": _Rule(_customer("transaction_id", bank.owner_of_transaction), NOT_YOURS),
    "send_email": _Rule(_customer("to", bank.customer_with_email), RECIPIENT_NOT_ALLOWED),
    "send_sms": _Rule(_customer("to", bank.customer_with_phone), RECIPIENT_NOT_ALLOWED),
    "get_ticket": _Rule(_ticket, NOT_YOURS),
    "list_tickets": _Rule(_only("Inbox", "inbox"), NOT_PERMITTED),
    "convert": _Rule(_only("Vendor", "fx-rates"), NOT_PERMITTED),
}


async def _principal(person: Person) -> list[_Entity]:
    match person.role:
        case "customer":
            return [_entity("Customer", person.id)]
        case "staff":
            served = sorted(await bank.customers_served_by(person.id))
            return [_entity("Staff", person.id, serves=[_reference("Customer", c) for c in served])]
        case "supervisor":
            return [_entity("Supervisor", person.id)]


def _entity(entity_type: str, entity_id: str, **attrs: object) -> _Entity:
    return {"uid": {"type": entity_type, "id": entity_id}, "attrs": attrs, "parents": []}


def _reference(entity_type: str, entity_id: str) -> dict[str, object]:
    return {"__entity": {"type": entity_type, "id": entity_id}}


def _key(entity: _Entity) -> tuple[str, str]:
    return entity["uid"]["type"], entity["uid"]["id"]
