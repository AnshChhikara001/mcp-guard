"""Seam 1 (chat UI -> Gateway): a viewer with a UI token reveals a Pseudonym from their chat.

    POST /reveal {"conversation": "<id>", "pseudonym": "<PHONE_1>"}  (UI token)

Cedar decides: a Customer sees their own data, Staff the data of the Customers they serve, a
Supervisor everyone's. The MCP token the Agent holds never reveals (ADR-0005). Every reveal,
allowed or refused, is an Audit entry that names the Pseudonym and never its value.
"""

import json
from collections.abc import Iterator

import httpx
import pytest
from mcp import Client
from mcp.types import CallToolResult

from conftest import RunningGateway, WritableBank, gateway_client, temporary_database
from mcp_guard.fake_servers.reset import reset
from mcp_guard.fake_servers.scenarios import plant_ticket
from mcp_guard.gateway.process import start_gateway
from mcp_guard.gateway.server import CONVERSATION_META_KEY

pytestmark = pytest.mark.anyio

C001_PHONE = "+91 9845012376"
C002_PHONE = "+91 7019283746"


@pytest.fixture(scope="module")
def _guarded_bank() -> Iterator[WritableBank]:
    """A seeded database and a Gateway over it with its defences on, shared by this module."""
    with temporary_database() as url:
        reset(url)
        with start_gateway(url) as running:
            yield WritableBank(url, running)


@pytest.fixture
def guarded_bank(_guarded_bank: WritableBank) -> WritableBank:
    """The guarded Gateway over a bank reset to its seeded state before each test."""
    reset(_guarded_bank.database_url)
    return _guarded_bank


def tokens_of(gateway: RunningGateway, person: str) -> dict[str, str]:
    reply = httpx.post(f"{gateway.base_url}/demo/login", json={"person": person})
    reply.raise_for_status()
    return reply.json()


def reveal(
    gateway: RunningGateway, token: str, conversation: str, pseudonym: str
) -> httpx.Response:
    return httpx.post(
        f"{gateway.base_url}/reveal",
        json={"conversation": conversation, "pseudonym": pseudonym},
        headers={"Authorization": f"Bearer {token}"},
    )


async def call(client: Client, conversation: str, tool: str, **arguments: object) -> CallToolResult:
    """A Tool call that names its conversation, as the Agent makes it."""
    return await client.call_tool(tool, arguments, meta={CONVERSATION_META_KEY: conversation})


def profile_phone(result: CallToolResult) -> str:
    assert result.structured_content is not None
    return result.structured_content["phone"]


async def test_a_customer_reveals_their_own_phone_number(
    guarded_gateway: RunningGateway,
) -> None:
    async with gateway_client(guarded_gateway, "C001") as client:
        phone = profile_phone(await call(client, "r1", "get_customer_profile", customer_id="C001"))

    reply = reveal(guarded_gateway, tokens_of(guarded_gateway, "C001")["ui_token"], "r1", phone)

    assert reply.status_code == 200
    assert reply.json() == {"pseudonym": "<PHONE_1>", "value": C001_PHONE}


async def test_a_customer_cant_reveal_another_customers_phone_number_from_their_chat(
    guarded_bank: WritableBank,
) -> None:
    gateway = guarded_bank.gateway
    ticket_id = plant_ticket(
        guarded_bank.database_url, "C001", "Call my friend", f"Please ring {C002_PHONE} today."
    )
    async with gateway_client(gateway, "C001") as client:
        ticket = await call(client, "r2", "get_ticket", ticket_id=ticket_id)
    assert ticket.structured_content is not None
    body = json.dumps(ticket.structured_content)
    assert C002_PHONE not in body
    assert "<PHONE_1>" in body

    reply = reveal(gateway, tokens_of(gateway, "C001")["ui_token"], "r2", "<PHONE_1>")

    assert reply.status_code == 403
    assert reply.json()["reason"] == "not_yours_to_see"
    assert C002_PHONE not in reply.text


@pytest.mark.parametrize(
    ("staff", "status"),
    [("S002", 200), ("S003", 403), ("S004", 200)],
    ids=["serves-C002", "doesnt-serve-C002", "supervisor"],
)
async def test_staff_reveal_a_phone_number_only_for_a_customer_they_serve_and_a_supervisor_for_any(
    guarded_bank: WritableBank, staff: str, status: int
) -> None:
    gateway = guarded_bank.gateway
    ticket_id = plant_ticket(
        guarded_bank.database_url, "C002", "New number", f"Please note {C002_PHONE} is mine."
    )
    async with gateway_client(gateway, staff) as client:
        ticket = await call(client, "r3", "get_ticket", ticket_id=ticket_id)
    assert "<PHONE_1>" in json.dumps(ticket.structured_content)

    reply = reveal(gateway, tokens_of(gateway, staff)["ui_token"], "r3", "<PHONE_1>")

    assert reply.status_code == status
    assert (reply.json().get("value") == C002_PHONE) == (status == 200)


async def test_the_agents_mcp_token_cant_reveal(guarded_gateway: RunningGateway) -> None:
    async with gateway_client(guarded_gateway, "C001") as client:
        phone = profile_phone(await call(client, "r4", "get_customer_profile", customer_id="C001"))

    reply = reveal(guarded_gateway, tokens_of(guarded_gateway, "C001")["mcp_token"], "r4", phone)

    assert reply.status_code == 401
    assert C001_PHONE not in reply.text


def reveal_entries(gateway: RunningGateway, conversation: str) -> list[dict[str, object]]:
    """The conversation's reveal Audit entries, oldest first, read as a Supervisor reads them."""
    supervisor = tokens_of(gateway, "S004")["ui_token"]
    reply = httpx.get(
        f"{gateway.base_url}/audit", headers={"Authorization": f"Bearer {supervisor}"}
    )
    reply.raise_for_status()
    entries: list[dict[str, object]] = reply.json()["entries"]
    return [
        {k: e[k] for k in ("person", "tool", "arguments", "decision", "reason")}
        for e in reversed(entries)
        if e["conversation"] == conversation and e["tool"] == "reveal"
    ]


async def test_every_reveal_allowed_or_refused_is_audited_without_its_value(
    guarded_bank: WritableBank,
) -> None:
    gateway = guarded_bank.gateway
    ticket_id = plant_ticket(
        guarded_bank.database_url, "C001", "Call my friend", f"Please ring {C002_PHONE} today."
    )
    async with gateway_client(gateway, "C001") as client:
        await call(client, "r5", "get_customer_profile", customer_id="C001")  # <PHONE_1>
        await call(client, "r5", "get_ticket", ticket_id=ticket_id)  # C002's is <PHONE_2>
    tokens = tokens_of(gateway, "C001")

    reveal(gateway, tokens["ui_token"], "r5", "<PHONE_1>")
    reveal(gateway, tokens["ui_token"], "r5", "<PHONE_2>")
    reveal(gateway, tokens["ui_token"], "r5", "<PHONE_9>")
    reveal(gateway, tokens["mcp_token"], "r5", "<PHONE_1>")

    entries = reveal_entries(gateway, "r5")
    assert entries == [
        {
            "person": "C001",
            "tool": "reveal",
            "arguments": {"pseudonym": "<PHONE_1>"},
            "decision": "allow",
            "reason": None,
        },
        {
            "person": "C001",
            "tool": "reveal",
            "arguments": {"pseudonym": "<PHONE_2>"},
            "decision": "deny",
            "reason": "not_yours_to_see",
        },
        {
            "person": "C001",
            "tool": "reveal",
            "arguments": {"pseudonym": "<PHONE_9>"},
            "decision": "deny",
            "reason": "unknown_pseudonym",
        },
        {
            "person": "C001",
            "tool": "reveal",
            "arguments": {"pseudonym": "<PHONE_1>"},
            "decision": "deny",
            "reason": "agent_token",
        },
    ]
    assert C001_PHONE not in json.dumps(entries)
    assert C002_PHONE not in json.dumps(entries)
