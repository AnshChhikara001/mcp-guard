"""Seam 1: the fx-rates server through the Gateway: a third-party vendor that may turn hostile."""

import pytest
from mcp import Client

from conftest import WritableBank, error_message, gateway_client
from mcp_guard.fake_servers.fx_rates import INJECTED_INSTRUCTIONS
from mcp_guard.fake_servers.scenarios import set_fx_mode

pytestmark = pytest.mark.anyio


async def test_client_converts_an_amount_at_the_fixed_rate(writable_bank: WritableBank) -> None:
    async with gateway_client(writable_bank.gateway) as client:
        result = await client.call_tool(
            "convert",
            {"amount": 250, "from_currency": "USD", "to_currency": "INR", "reference": "Inv 42"},
        )

    assert not result.is_error
    assert result.structured_content == {
        "amount": 250,
        "from_currency": "USD",
        "to_currency": "INR",
        "rate": 84.0,
        "converted": 21_000.0,
        "reference": "Inv 42",
    }


async def test_every_call_to_fx_rates_is_recorded_even_when_it_fails(
    writable_bank: WritableBank,
) -> None:
    good = {"amount": 1_000, "from_currency": "INR", "to_currency": "EUR", "reference": "Trip"}
    bad = {"amount": 5, "from_currency": "XYZ", "to_currency": "INR", "reference": "AKVPI4417Q"}
    async with gateway_client(writable_bank.gateway) as client:
        await client.call_tool("convert", good)
        failed = await client.call_tool("convert", bad)

    assert "Unknown currency XYZ" in error_message(failed)
    assert writable_bank.outbound() == [
        ("fx-rates", "convert", good),
        ("fx-rates", "convert", bad),
    ]


async def convert_description(client: Client) -> str:
    [convert] = [tool for tool in (await client.list_tools()).tools if tool.name == "convert"]
    assert convert.description is not None
    return convert.description


async def test_switching_to_poisoned_mode_reaches_a_connected_client(
    writable_bank: WritableBank,
) -> None:
    async with gateway_client(writable_bank.gateway) as client:
        honest = await convert_description(client)
        set_fx_mode(writable_bank.database_url, "poisoned")
        poisoned = await convert_description(client)
        set_fx_mode(writable_bank.database_url, "honest")
        honest_again = await convert_description(client)

    assert INJECTED_INSTRUCTIONS not in honest
    assert poisoned.startswith(honest)
    assert INJECTED_INSTRUCTIONS in poisoned
    assert honest_again == honest


async def test_after_a_rug_pull_a_client_sees_the_malicious_definition(
    writable_bank: WritableBank,
) -> None:
    # The Gateway's own listing when it started was fx-rates' first, and honest.
    set_fx_mode(writable_bank.database_url, "rug_pull")

    async with gateway_client(writable_bank.gateway) as client:
        description = await convert_description(client)

    assert INJECTED_INSTRUCTIONS in description
