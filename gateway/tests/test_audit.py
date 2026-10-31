"""Every Tool call's Decision is written to a hash-chained Audit log.

Seam 1 (MCP client -> Gateway): one Tool call makes exactly one Audit entry.
Seam 2 (the Gateway's HTTP API): only a Supervisor reads the Audit log and verifies its chain.

The Gateways here are shared with other tests, so each test only looks at the entries
written after it starts.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, LiteralString

import anyio
import httpx
import psycopg
import pytest

from conftest import RunningGateway, error_message, gateway_client, temporary_database
from mcp_guard.fake_servers.reset import reset
from mcp_guard.gateway.audit import PAGE_SIZE
from mcp_guard.gateway.process import start_gateway
from mcp_guard.gateway.server import CONVERSATION_META_KEY

pytestmark = pytest.mark.anyio

SUPERVISOR = "S004"


def ui_token(gateway: RunningGateway, person: str) -> str:
    reply = httpx.post(f"{gateway.base_url}/demo/login", json={"person": person})
    return reply.json()["ui_token"]


def read_audit(gateway: RunningGateway, **params: Any) -> list[dict[str, Any]]:
    """The newest page of the Audit log, read by a Supervisor."""
    token = ui_token(gateway, SUPERVISOR)
    reply = httpx.get(
        f"{gateway.base_url}/audit",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert reply.status_code == 200, reply.text
    return reply.json()["entries"]


def newest_id(gateway: RunningGateway) -> int:
    entries = read_audit(gateway, limit=1)
    return entries[0]["id"] if entries else 0


def entries_since(gateway: RunningGateway, start: int) -> list[dict[str, Any]]:
    """The entries written after the entry numbered `start`, oldest first."""
    return [e for e in reversed(read_audit(gateway)) if e["id"] > start]


async def test_an_allowed_call_makes_one_entry_saying_who_did_what(
    guarded_gateway: RunningGateway,
) -> None:
    start = newest_id(guarded_gateway)
    arguments = {"account_id": "A001"}
    async with gateway_client(guarded_gateway, "C001") as client:
        await client.call_tool("get_balance", arguments, meta={CONVERSATION_META_KEY: "conv-allow"})

    [entry] = entries_since(guarded_gateway, start)
    assert entry["person"] == "C001"
    assert entry["role"] == "customer"
    assert entry["conversation"] == "conv-allow"
    assert entry["tool"] == "get_balance"
    assert entry["arguments"] == arguments
    assert entry["decision"] == "allow"
    assert entry["reason"] is None
    assert entry["layer"] == "cedar"
    assert entry["time"].endswith("+00:00")


async def test_a_denied_call_makes_one_entry_with_the_reason(
    guarded_gateway: RunningGateway,
) -> None:
    start = newest_id(guarded_gateway)
    async with gateway_client(guarded_gateway, "C003") as client:
        await client.call_tool("get_balance", {"account_id": "A002"})

    [entry] = entries_since(guarded_gateway, start)
    assert (entry["person"], entry["tool"], entry["decision"]) == ("C003", "get_balance", "deny")
    assert entry["reason"] == "not_found_or_not_yours"
    assert entry["layer"] == "cedar"


async def test_a_call_to_an_unknown_tool_makes_one_entry(
    guarded_gateway: RunningGateway,
) -> None:
    start = newest_id(guarded_gateway)
    async with gateway_client(guarded_gateway, "S001") as client:
        await client.call_tool("wire_everything", {"to": "elsewhere"})

    [entry] = entries_since(guarded_gateway, start)
    assert (entry["tool"], entry["decision"]) == ("wire_everything", "deny")
    assert entry["reason"] == "unknown_tool"
    assert entry["arguments"] == {"to": "elsewhere"}


async def test_with_the_defences_off_a_call_still_makes_one_entry(gateway: RunningGateway) -> None:
    start = newest_id(gateway)
    async with gateway_client(gateway, "C003") as client:
        await client.call_tool("get_balance", {"account_id": "A002"})

    [entry] = entries_since(gateway, start)
    assert (entry["decision"], entry["layer"]) == ("allow", "defences_off")


async def test_listing_tools_makes_no_entry(guarded_gateway: RunningGateway) -> None:
    start = newest_id(guarded_gateway)
    async with gateway_client(guarded_gateway, "C001") as client:
        await client.list_tools()

    assert entries_since(guarded_gateway, start) == []


def legacy_session_calls(gateway: RunningGateway, calls: int) -> None:
    """One MCP session of an older client (protocol 2025-06-18, which has sessions) making
    `calls` Tool calls without naming a conversation."""
    token = httpx.post(f"{gateway.base_url}/demo/login", json={"person": "C001"}).json()[
        "mcp_token"
    ]
    headers = {"Accept": "application/json, text/event-stream", "Authorization": f"Bearer {token}"}
    with httpx.Client(headers=headers) as http:

        def send(message: dict[str, Any], session: str | None = None) -> httpx.Response:
            extra = (
                {"Mcp-Session-Id": session, "Mcp-Protocol-Version": "2025-06-18"} if session else {}
            )
            reply = http.post(gateway.url, json=message, headers=extra)
            assert reply.status_code in (200, 202), reply.text
            return reply

        opened = send(
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "older-client", "version": "1"},
                },
            }
        )
        session = opened.headers["Mcp-Session-Id"]
        send({"jsonrpc": "2.0", "method": "notifications/initialized"}, session)
        for n in range(1, calls + 1):
            call = {"name": "get_balance", "arguments": {"account_id": "A001"}}
            send({"jsonrpc": "2.0", "id": n, "method": "tools/call", "params": call}, session)


def test_an_older_client_that_names_no_conversation_is_logged_by_its_mcp_session(
    guarded_gateway: RunningGateway,
) -> None:
    start = newest_id(guarded_gateway)
    legacy_session_calls(guarded_gateway, 2)
    legacy_session_calls(guarded_gateway, 1)

    first, second, other_session = [
        e["conversation"] for e in entries_since(guarded_gateway, start)
    ]
    assert first
    assert first == second
    assert other_session != first


async def test_a_client_without_sessions_that_names_no_conversation_is_logged_without_one(
    guarded_gateway: RunningGateway,
) -> None:
    # The MCP SDK's own client speaks protocol 2026-07-28, which has no sessions.
    start = newest_id(guarded_gateway)
    async with gateway_client(guarded_gateway, "C001") as client:
        await client.call_tool("get_balance", {"account_id": "A001"})

    [entry] = entries_since(guarded_gateway, start)
    assert entry["conversation"] is None


def test_reading_the_audit_log_needs_a_ui_token(gateway: RunningGateway) -> None:
    mcp_token = httpx.post(f"{gateway.base_url}/demo/login", json={"person": SUPERVISOR}).json()[
        "mcp_token"
    ]
    for path in ("/audit", "/audit/verify"):
        without = httpx.get(f"{gateway.base_url}{path}")
        with_mcp_token = httpx.get(
            f"{gateway.base_url}{path}", headers={"Authorization": f"Bearer {mcp_token}"}
        )

        assert without.status_code == 401
        assert with_mcp_token.status_code == 401


@pytest.mark.parametrize("person", ["C001", "S001"])
def test_customers_and_staff_cant_read_the_audit_log(gateway: RunningGateway, person: str) -> None:
    headers = {"Authorization": f"Bearer {ui_token(gateway, person)}"}
    for path in ("/audit", "/audit/verify"):
        reply = httpx.get(f"{gateway.base_url}{path}", headers=headers)

        assert reply.status_code == 403
        assert "entries" not in reply.json()


async def test_a_supervisor_pages_through_the_audit_log_newest_first(
    gateway: RunningGateway,
) -> None:
    async with gateway_client(gateway, "C001") as client:
        for _ in range(3):
            await client.call_tool("get_balance", {"account_id": "A001"})

    newest_two = read_audit(gateway, limit=2)
    next_page = read_audit(gateway, before=newest_two[-1]["id"], limit=2)

    ids = [e["id"] for e in newest_two + next_page]
    assert ids == sorted(ids, reverse=True)
    assert ids == list(range(ids[0], ids[0] - 4, -1))
    assert len(read_audit(gateway, limit=500)) <= PAGE_SIZE


@dataclass(frozen=True)
class OwnLog:
    """A Gateway whose Audit log no other test writes to, and its database."""

    gateway: RunningGateway
    database_url: str

    def as_database_admin(self, statement: LiteralString) -> None:
        """Change the Audit log the way only a database admin can: with its trigger off."""
        with psycopg.connect(self.database_url) as conn:
            conn.execute("ALTER TABLE gateway.audit DISABLE TRIGGER USER")
            conn.execute(statement)
            conn.execute("ALTER TABLE IF EXISTS gateway.audit ENABLE TRIGGER USER")


@pytest.fixture
def own_log() -> Iterator[OwnLog]:
    with temporary_database() as url:
        reset(url)
        with start_gateway(url) as running:
            yield OwnLog(running, url)


def verify(gateway: RunningGateway) -> dict[str, Any]:
    token = ui_token(gateway, SUPERVISOR)
    reply = httpx.get(
        f"{gateway.base_url}/audit/verify", headers={"Authorization": f"Bearer {token}"}
    )
    assert reply.status_code == 200, reply.text
    return reply.json()


async def make_calls(gateway: RunningGateway, calls: int) -> None:
    async with gateway_client(gateway, "C001") as client:
        for _ in range(calls):
            await client.call_tool("get_balance", {"account_id": "A001"})


async def test_verify_passes_on_an_untouched_log(own_log: OwnLog) -> None:
    await make_calls(own_log.gateway, 3)

    assert verify(own_log.gateway) == {"intact": True, "entries": 3}


async def test_verify_names_the_entry_a_database_admin_edited(own_log: OwnLog) -> None:
    await make_calls(own_log.gateway, 4)

    own_log.as_database_admin(
        """UPDATE gateway.audit SET arguments = '{"account_id": "A002"}' WHERE id = 2"""
    )

    assert verify(own_log.gateway) == {"intact": False, "first_broken": 2}


async def test_a_deleted_entry_shows_as_a_break_at_the_next_one(own_log: OwnLog) -> None:
    await make_calls(own_log.gateway, 4)

    own_log.as_database_admin("DELETE FROM gateway.audit WHERE id = 2")

    assert verify(own_log.gateway) == {"intact": False, "first_broken": 3}


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE gateway.audit SET decision = 'deny'",
        "DELETE FROM gateway.audit",
        "TRUNCATE gateway.audit",
    ],
)
async def test_the_audit_log_refuses_changes_while_its_trigger_is_on(
    own_log: OwnLog, statement: LiteralString
) -> None:
    await make_calls(own_log.gateway, 1)

    with (
        psycopg.connect(own_log.database_url) as conn,
        pytest.raises(psycopg.errors.RaiseException),
    ):
        conn.execute(statement)

    assert verify(own_log.gateway) == {"intact": True, "entries": 1}


async def test_calls_made_at_once_are_numbered_in_order_and_chained(own_log: OwnLog) -> None:
    async with anyio.create_task_group() as calls:
        for _ in range(8):
            calls.start_soon(make_calls, own_log.gateway, 2)

    assert [e["id"] for e in read_audit(own_log.gateway)] == list(range(16, 0, -1))
    assert verify(own_log.gateway) == {"intact": True, "entries": 16}


async def test_a_call_the_audit_log_cant_record_is_denied(own_log: OwnLog) -> None:
    own_log.as_database_admin("ALTER TABLE gateway.audit RENAME TO audit_elsewhere")
    async with gateway_client(own_log.gateway, "C001") as client:
        result = await client.call_tool("get_balance", {"account_id": "A001"})

    assert "audit_failed" in error_message(result)


async def test_verify_passes_whatever_numbers_the_arguments_hold(own_log: OwnLog) -> None:
    arguments = {"amount": 1e16, "rate": 0.1, "tiny": 1e-7, "big": 2**70, "nested": [1.0, -0.0]}
    async with gateway_client(own_log.gateway, "C001") as client:
        await client.call_tool("convert", arguments)
        await client.call_tool("no_such_tool", arguments)

    assert verify(own_log.gateway) == {"intact": True, "entries": 2}
