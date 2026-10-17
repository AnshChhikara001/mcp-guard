"""Seam 1: an MCP client talks to the Gateway, which passes Tool calls through unchanged."""

import sys

import pytest
from mcp import Client, StdioServerParameters
from mcp.types import TextContent

from .conftest import RunningGateway

pytestmark = pytest.mark.anyio


async def test_client_sees_the_accounts_tools_as_the_gateways_own(gateway: RunningGateway) -> None:
    async with Client(gateway.url) as client:
        listing = await client.list_tools()

    assert {"get_customer_profile", "list_accounts", "get_balance"} <= {
        tool.name for tool in listing.tools
    }


async def test_tool_definitions_reach_the_client_unchanged(gateway: RunningGateway) -> None:
    accounts_server = StdioServerParameters(
        command=sys.executable, args=["-m", "mcp_guard.fake_servers.accounts"]
    )
    async with Client(accounts_server) as direct, Client(gateway.url) as through_gateway:
        expected = (await direct.list_tools()).tools
        listed = (await through_gateway.list_tools()).tools

    assert [t for t in listed if t.name in {e.name for e in expected}] == expected


async def test_client_gets_a_seeded_customers_balance(gateway: RunningGateway) -> None:
    async with Client(gateway.url) as client:
        accounts = await client.call_tool("list_accounts", {"customer_id": "C001"})
        balance = await client.call_tool("get_balance", {"account_id": "A001"})

    assert not accounts.is_error
    assert accounts.structured_content == {
        "customer_id": "C001",
        "accounts": [
            {
                "account_id": "A001",
                "number": "50100483920176",
                "ifsc": "FAKE0000101",
                "kind": "current",
                "opened_on": "2019-03-11",
            }
        ],
    }
    assert not balance.is_error
    assert balance.structured_content == {
        "account_id": "A001",
        "balance_paise": 194_844_00,
        "balance": "₹1,94,844.00",
    }


async def test_client_gets_a_seeded_customers_profile(gateway: RunningGateway) -> None:
    async with Client(gateway.url) as client:
        result = await client.call_tool("get_customer_profile", {"customer_id": "C002"})

    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["customer_id"] == "C002"


async def test_downstream_errors_reach_the_client_unchanged(gateway: RunningGateway) -> None:
    async with Client(gateway.url) as client:
        result = await client.call_tool("get_balance", {"account_id": "A999"})

    assert result.is_error
    [message] = [c.text for c in result.content if isinstance(c, TextContent)]
    assert "No account with ID A999" in message


async def test_calling_a_tool_no_downstream_server_has_is_an_error(
    gateway: RunningGateway,
) -> None:
    async with Client(gateway.url) as client:
        result = await client.call_tool("transfer_everything", {})

    assert result.is_error
