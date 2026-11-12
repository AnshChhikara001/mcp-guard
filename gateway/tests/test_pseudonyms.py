"""Seam 1 (MCP client -> Gateway): PII in Tool call results reaches the Agent as Pseudonyms.

With the defences on, the Gateway replaces each piece of PII in a result with a per-conversation
Pseudonym like `<PHONE_1>`, and swaps a Pseudonym back to the real value when the Agent passes it
into an allowed Tool call. With the defences off, results pass through unchanged.
"""

import json
from collections.abc import Iterator
from typing import Any

import httpx
import psycopg
import pytest
from mcp import Client
from mcp.types import CallToolResult

from conftest import RunningGateway, WritableBank, gateway_client, temporary_database
from mcp_guard.fake_servers.bank import generate
from mcp_guard.fake_servers.reset import reset
from mcp_guard.gateway.process import start_gateway
from mcp_guard.gateway.server import CONVERSATION_META_KEY, DECISION_META_KEY

pytestmark = pytest.mark.anyio

SUPERVISOR = "S004"  # serves every Customer, so may read every profile
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


def seeded_pii() -> Iterator[str]:
    """Every piece of PII the seeded bank holds: each Customer's details and account numbers."""
    bank = generate()
    for c in bank.customers:
        yield from (c.name, c.email, c.phone, c.address, c.aadhaar, c.pan, c.upi_id)
        yield c.date_of_birth.isoformat()
    yield from (a.number for a in bank.accounts)


def everything_in(result: CallToolResult) -> str:
    """All the Agent receives from one Tool call, as one text."""
    return json.dumps(result.model_dump(mode="json", by_alias=True), ensure_ascii=False)


async def call(client: Client, conversation: str, tool: str, **arguments: object) -> CallToolResult:
    """A Tool call that names its conversation, as the Agent makes it."""
    return await client.call_tool(tool, arguments, meta={CONVERSATION_META_KEY: conversation})


async def test_no_seeded_pii_reaches_the_agent(guarded_gateway: RunningGateway) -> None:
    bank = generate()
    received: list[str] = []
    async with gateway_client(guarded_gateway, SUPERVISOR) as client:
        for customer in bank.customers:
            received.append(
                everything_in(
                    await call(client, "c1", "get_customer_profile", customer_id=customer.id)
                )
            )
            received.append(
                everything_in(await call(client, "c1", "list_accounts", customer_id=customer.id))
            )
        received.append(everything_in(await call(client, "c1", "list_tickets")))
        for ticket in bank.tickets:
            received.append(
                everything_in(await call(client, "c1", "get_ticket", ticket_id=ticket.id))
            )

    text = "\n".join(received)
    assert "<PHONE_1>" in text
    assert [value for value in seeded_pii() if value in text] == []


def profile_phone(result: CallToolResult) -> str:
    assert result.structured_content is not None
    return result.structured_content["phone"]


async def test_a_pseudonym_in_an_allowed_call_reaches_the_server_as_the_real_value(
    guarded_bank: WritableBank,
) -> None:
    async with gateway_client(guarded_bank.gateway, "C002") as client:
        phone = profile_phone(await call(client, "c2", "get_customer_profile", customer_id="C002"))
        sent = await call(client, "c2", "send_sms", to=phone, body=f"Is {phone} still yours?")

    assert phone == "<PHONE_1>"
    assert not sent.is_error
    # notify echoes the real number it sent to; the Agent gets the Pseudonym back.
    assert sent.structured_content is not None
    assert sent.structured_content["to"] == "<PHONE_1>"
    assert guarded_bank.outbound() == [
        ("notify", "send_sms", {"to": C002_PHONE, "body": f"Is {C002_PHONE} still yours?"})
    ]


async def test_the_same_value_keeps_its_pseudonym_in_a_conversation_and_not_in_another(
    guarded_bank: WritableBank,
) -> None:
    async with gateway_client(guarded_bank.gateway, SUPERVISOR) as client:
        first = await call(client, "c3-a", "get_customer_profile", customer_id="C001")
        again = await call(client, "c3-a", "get_customer_profile", customer_id="C001")
        await call(client, "c3-b", "get_customer_profile", customer_id="C002")
        in_other = await call(client, "c3-b", "get_customer_profile", customer_id="C001")
        await call(client, "c3-a", "send_sms", to="<PHONE_1>", body="Hello")
        await call(client, "c3-b", "send_sms", to="<PHONE_1>", body="Hello")

    assert again.structured_content == first.structured_content
    assert profile_phone(first) == "<PHONE_1>"
    assert profile_phone(in_other) == "<PHONE_2>"
    assert [to for _, _, args in guarded_bank.outbound() for to in [args["to"]]] == [
        C001_PHONE,
        C002_PHONE,
    ]


@pytest.mark.parametrize(
    "conversation", ["c4-fresh", "c4-other"], ids=["invented", "from-another-conversation"]
)
async def test_a_pseudonym_the_conversation_doesnt_know_is_denied(
    guarded_bank: WritableBank, conversation: str
) -> None:
    async with gateway_client(guarded_bank.gateway, "C001") as client:
        await call(client, "c4-known", "get_customer_profile", customer_id="C001")
        await call(client, "c4-other", "get_balance", account_id="A001")
        denied = await call(client, conversation, "send_sms", to="<PHONE_1>", body="Hi")

    assert denied.is_error
    assert (denied.meta or {})[DECISION_META_KEY]["reason"] == "unknown_pseudonym"
    assert guarded_bank.outbound() == []


def read_audit(gateway: RunningGateway) -> list[dict[str, Any]]:
    """The newest page of the Audit log, oldest first, read by a Supervisor."""
    login = httpx.post(f"{gateway.base_url}/demo/login", json={"person": SUPERVISOR}).json()
    reply = httpx.get(
        f"{gateway.base_url}/audit", headers={"Authorization": f"Bearer {login['ui_token']}"}
    )
    return list(reversed(reply.json()["entries"]))


async def test_a_result_that_held_pii_adds_a_redact_entry_with_counts_and_no_values(
    guarded_bank: WritableBank,
) -> None:
    before = [e["id"] for e in read_audit(guarded_bank.gateway)]
    async with gateway_client(guarded_bank.gateway, "C001") as client:
        profile = await call(client, "c5", "get_customer_profile", customer_id="C001")
        balance = await call(client, "c5", "get_balance", account_id="A001")

    entries = [e for e in read_audit(guarded_bank.gateway) if e["id"] not in before]
    assert [(e["tool"], e["decision"], e["reason"], e["redacted"]) for e in entries] == [
        ("get_customer_profile", "allow", None, None),
        (
            "get_customer_profile",
            "redact",
            "pii_in_result",
            {
                "NAME": 1,
                "DATE_OF_BIRTH": 1,
                "EMAIL": 1,
                "PHONE": 1,
                "ADDRESS": 1,
                "AADHAAR": 1,
                "PAN": 1,
                "UPI_ID": 1,
            },
        ),
        ("get_balance", "allow", None, None),
    ]
    assert (profile.meta or {})[DECISION_META_KEY] | {"audit_id": None} == {
        "decision": "redact",
        "reason": "pii_in_result",
        "explanation": (profile.meta or {})[DECISION_META_KEY]["explanation"],
        "layer": "redact",
        "audit_id": None,
    }
    assert (profile.meta or {})[DECISION_META_KEY]["audit_id"] == entries[1]["id"]
    assert (balance.meta or {})[DECISION_META_KEY]["decision"] == "allow"
    logged = json.dumps(entries, ensure_ascii=False)
    assert [value for value in seeded_pii() if value in logged] == []


async def test_with_the_defences_off_results_are_unchanged(gateway: RunningGateway) -> None:
    async with gateway_client(gateway, "C001") as client:
        profile = await call(client, "c6", "get_customer_profile", customer_id="C001")

    assert profile_phone(profile) == C001_PHONE
    assert "ananya.iyer84@example.com" in everything_in(profile)


REFUSE_REDACT_ENTRIES = """
CREATE FUNCTION gateway.refuse_redact() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'redact entries are refused';
END;
$$;
CREATE TRIGGER refuse_redact BEFORE INSERT ON gateway.audit
    FOR EACH ROW WHEN (NEW.decision = 'redact') EXECUTE FUNCTION gateway.refuse_redact();
"""


async def test_a_result_whose_redact_entry_cant_be_written_is_withheld() -> None:
    with temporary_database() as url:
        reset(url)
        with start_gateway(url) as running:
            with psycopg.connect(url) as conn:
                conn.execute(REFUSE_REDACT_ENTRIES)
            async with gateway_client(running, "C001") as client:
                result = await call(client, "c7", "get_customer_profile", customer_id="C001")

    assert result.is_error
    assert (result.meta or {})[DECISION_META_KEY]["reason"] == "audit_failed"
    assert [value for value in seeded_pii() if value in everything_in(result)] == []


async def test_another_person_naming_the_same_conversation_cant_use_its_pseudonyms(
    guarded_bank: WritableBank,
) -> None:
    async with gateway_client(guarded_bank.gateway, "C001") as owner:
        await call(owner, "c8-shared", "get_customer_profile", customer_id="C001")
    # S004 may email any Customer, so only the Pseudonym store can stop this.
    async with gateway_client(guarded_bank.gateway, SUPERVISOR) as other:
        denied = await call(
            other, "c8-shared", "send_email", to="<EMAIL_1>", subject="Hi", body="<AADHAAR_1>"
        )

    assert (denied.meta or {})[DECISION_META_KEY]["reason"] == "unknown_pseudonym"
    assert guarded_bank.outbound() == []
