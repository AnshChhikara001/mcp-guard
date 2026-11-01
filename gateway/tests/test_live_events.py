"""What the web app shows live: each Tool call's Decision, the checks in order, and new entries.

Seam 1 (MCP client -> Gateway): every Tool call result carries its Decision in `_meta`.
Seam 2 (the Gateway's HTTP API): `/layers` names the checks switched on; `/events` streams
each new Audit entry to whoever may see it.

The Gateways here are shared with other tests, so each test only looks at what it caused.
"""

import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
import httpx
import pytest

from conftest import RunningGateway, gateway_client
from mcp_guard.gateway.server import CONVERSATION_META_KEY, DECISION_META_KEY

pytestmark = pytest.mark.anyio


async def test_an_allowed_result_carries_its_decision_and_audit_entry(
    guarded_gateway: RunningGateway,
) -> None:
    async with gateway_client(guarded_gateway, "C001") as client:
        result = await client.call_tool("get_balance", {"account_id": "A001"})

    decision: dict[str, Any] = (result.meta or {})[DECISION_META_KEY]
    assert decision["decision"] == "allow"
    assert decision["layer"] == "cedar"
    assert decision["reason"] is None
    assert decision["explanation"] is None
    assert isinstance(decision["audit_id"], int)
    assert not result.is_error


async def test_a_denied_result_carries_its_reason_and_explanation(
    guarded_gateway: RunningGateway,
) -> None:
    async with gateway_client(guarded_gateway, "C001") as client:
        result = await client.call_tool("get_balance", {"account_id": "A002"})

    decision: dict[str, Any] = (result.meta or {})[DECISION_META_KEY]
    assert decision["decision"] == "deny"
    assert decision["layer"] == "cedar"
    assert decision["reason"] == "not_found_or_not_yours"
    assert decision["explanation"].startswith("Not found, or not yours")
    assert isinstance(decision["audit_id"], int)


def test_layers_names_the_checks_switched_on_in_order(guarded_gateway: RunningGateway) -> None:
    reply = httpx.get(f"{guarded_gateway.base_url}/layers")

    assert reply.status_code == 200
    assert reply.json() == [{"id": "cedar", "name": "Permissions"}]


def test_with_the_defences_off_no_check_is_switched_on(gateway: RunningGateway) -> None:
    reply = httpx.get(f"{gateway.base_url}/layers")

    assert reply.json() == []


def login(gateway: RunningGateway, person: str) -> dict[str, str]:
    return httpx.post(f"{gateway.base_url}/demo/login", json={"person": person}).json()


class EventStream:
    """The events `/events` sends one viewer, read as they arrive."""

    def __init__(self, lines: AsyncIterator[str]) -> None:
        self._lines = lines

    async def next(self) -> dict[str, Any]:
        """The next event's id (as a number) and data; fails if none comes within 5 seconds."""
        fields: dict[str, str] = {}
        with anyio.fail_after(5):
            async for line in self._lines:
                if line == "" and "data" in fields:
                    assert fields["id"] == str(json.loads(fields["data"])["id"])
                    return json.loads(fields["data"])
                name, _, value = line.partition(":")
                if name:
                    fields[name] = value.removeprefix(" ")
        raise AssertionError("The event stream ended")


@asynccontextmanager
async def watching(
    gateway: RunningGateway, person: str, last_event_id: int | None = None
) -> AsyncGenerator[EventStream]:
    """`person`'s view of `/events`, from the moment it returns."""
    headers = {"Authorization": f"Bearer {login(gateway, person)['ui_token']}"}
    if last_event_id is not None:
        headers["Last-Event-ID"] = str(last_event_id)
    async with (
        httpx.AsyncClient(timeout=10) as http,
        http.stream("GET", f"{gateway.base_url}/events", headers=headers) as reply,
    ):
        assert reply.status_code == 200
        assert reply.headers["content-type"].startswith("text/event-stream")
        lines = reply.aiter_lines()
        # The Gateway opens with a comment once the viewer is subscribed.
        with anyio.fail_after(5):
            assert (await anext(lines)).startswith(":")
        yield EventStream(lines)


def test_events_need_a_ui_token(guarded_gateway: RunningGateway) -> None:
    url = f"{guarded_gateway.base_url}/events"
    mcp_token = login(guarded_gateway, "S004")["mcp_token"]

    assert httpx.get(url).status_code == 401
    assert httpx.get(url, headers={"Authorization": f"Bearer {mcp_token}"}).status_code == 401


async def test_each_new_entry_is_sent_with_its_server_and_explanation(
    guarded_gateway: RunningGateway,
) -> None:
    async with watching(guarded_gateway, "C001") as events:
        async with gateway_client(guarded_gateway, "C001") as client:
            result = await client.call_tool(
                "get_balance", {"account_id": "A002"}, meta={CONVERSATION_META_KEY: "conv-live"}
            )

        event = await events.next()

    assert event["id"] == (result.meta or {})[DECISION_META_KEY]["audit_id"]
    assert (event["person"], event["role"], event["conversation"]) == (
        "C001",
        "customer",
        "conv-live",
    )
    assert (event["tool"], event["arguments"], event["server"]) == (
        "get_balance",
        {"account_id": "A002"},
        "accounts",
    )
    assert (event["decision"], event["reason"], event["layer"]) == (
        "deny",
        "not_found_or_not_yours",
        "cedar",
    )
    assert event["explanation"].startswith("Not found, or not yours")
    assert len(event["hash"]) == 64
    assert event["time"].endswith("+00:00")


async def test_a_customer_sees_only_their_own_entries(guarded_gateway: RunningGateway) -> None:
    async with watching(guarded_gateway, "C001") as events:
        async with gateway_client(guarded_gateway, "C003") as other:
            await other.call_tool("get_balance", {"account_id": "A001"})
        async with gateway_client(guarded_gateway, "C001") as own:
            await own.call_tool("list_accounts", {"customer_id": "C001"})

        event = await events.next()

    assert (event["person"], event["tool"]) == ("C001", "list_accounts")


async def test_staff_see_only_their_own_entries(guarded_gateway: RunningGateway) -> None:
    async with watching(guarded_gateway, "S001") as events:
        async with gateway_client(guarded_gateway, "C001") as customer:
            await customer.call_tool("get_balance", {"account_id": "A001"})
        async with gateway_client(guarded_gateway, "S001") as own:
            await own.call_tool("list_tickets", {})

        event = await events.next()

    assert (event["person"], event["tool"]) == ("S001", "list_tickets")


async def test_a_supervisor_sees_everyones_entries(guarded_gateway: RunningGateway) -> None:
    async with watching(guarded_gateway, "S004") as events:
        async with gateway_client(guarded_gateway, "C003") as customer:
            await customer.call_tool("get_balance", {"account_id": "A002"})

        event = await events.next()

    assert (event["person"], event["tool"], event["decision"]) == ("C003", "get_balance", "deny")


async def test_after_a_reconnect_last_event_id_resends_the_missed_entries(
    guarded_gateway: RunningGateway,
) -> None:
    async with watching(guarded_gateway, "C001") as events:
        async with gateway_client(guarded_gateway, "C001") as client:
            await client.call_tool("get_balance", {"account_id": "A001"})
        seen = (await events.next())["id"]

    async with gateway_client(guarded_gateway, "C001") as client:
        await client.call_tool("list_accounts", {"customer_id": "C001"})
    async with gateway_client(guarded_gateway, "C003") as other:
        await other.call_tool("get_balance", {"account_id": "A003"})
    async with gateway_client(guarded_gateway, "C001") as client:
        await client.call_tool("get_balance", {"account_id": "A002"})

    async with watching(guarded_gateway, "C001", last_event_id=seen) as events:
        missed = [await events.next(), await events.next()]
        async with gateway_client(guarded_gateway, "C001") as client:
            await client.call_tool("list_transactions", {"account_id": "A001"})
        live = await events.next()

    assert [(e["tool"], e["decision"]) for e in missed] == [
        ("list_accounts", "allow"),
        ("get_balance", "deny"),
    ]
    assert missed[0]["server"] == "accounts"
    assert missed[1]["explanation"].startswith("Not found, or not yours")
    assert live["tool"] == "list_transactions"
    assert missed[0]["id"] < missed[1]["id"] < live["id"]


@pytest.mark.parametrize("last_event_id", [b"abc", "²".encode("latin-1"), b"-1", b"9" * 30])
def test_a_last_event_id_that_names_no_entry_is_refused(
    guarded_gateway: RunningGateway, last_event_id: bytes
) -> None:
    token = login(guarded_gateway, "C001")["ui_token"]
    reply = httpx.get(
        f"{guarded_gateway.base_url}/events",
        headers={b"Authorization": f"Bearer {token}".encode(), b"Last-Event-ID": last_event_id},
    )

    assert reply.status_code == 400
