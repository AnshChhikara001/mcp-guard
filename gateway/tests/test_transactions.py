"""Seam 1: the transactions server through the Gateway: listing transactions, raising disputes."""

import pytest
from mcp import Client

from conftest import RunningGateway, error_message

pytestmark = pytest.mark.anyio


async def test_client_lists_an_accounts_transactions_newest_first(gateway: RunningGateway) -> None:
    async with Client(gateway.url) as client:
        result = await client.call_tool("list_transactions", {"account_id": "A001", "limit": 3})

    assert not result.is_error
    assert result.structured_content == {
        "account_id": "A001",
        "transactions": [
            {
                "transaction_id": "T00014",
                "posted_at": "2026-09-24T21:16:00+05:30",
                "direction": "debit",
                "amount_paise": 1_499_00,
                "amount": "₹1,499.00",
                "channel": "CARD",
                "counterparty": "StreamBox Subscription",
            },
            {
                "transaction_id": "T00013",
                "posted_at": "2026-09-24T21:14:00+05:30",
                "direction": "debit",
                "amount_paise": 1_499_00,
                "amount": "₹1,499.00",
                "channel": "CARD",
                "counterparty": "StreamBox Subscription",
            },
            {
                "transaction_id": "T00012",
                "posted_at": "2026-09-23T15:03:21+05:30",
                "direction": "debit",
                "amount_paise": 4_100_00,
                "amount": "₹4,100.00",
                "channel": "ATM",
                "counterparty": "ATM withdrawal",
            },
        ],
    }


async def test_client_raises_a_dispute_on_a_transaction(writable_gateway: RunningGateway) -> None:
    async with Client(writable_gateway.url) as client:
        result = await client.call_tool(
            "raise_dispute",
            {"transaction_id": "T00028", "reason": "I never shopped at Skyline Electronics."},
        )

    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content.pop("raised_at")
    assert result.structured_content == {
        "dispute_id": "D0001",
        "transaction_id": "T00028",
        "reason": "I never shopped at Skyline Electronics.",
        "status": "open",
    }


async def test_raising_a_second_dispute_on_a_transaction_returns_the_open_one(
    writable_gateway: RunningGateway,
) -> None:
    async with Client(writable_gateway.url) as client:
        first = await client.call_tool(
            "raise_dispute", {"transaction_id": "T00014", "reason": "Charged twice."}
        )
        second = await client.call_tool(
            "raise_dispute", {"transaction_id": "T00014", "reason": "Still charged twice!"}
        )
        other = await client.call_tool(
            "raise_dispute", {"transaction_id": "T00013", "reason": "Charged twice."}
        )

    assert not second.is_error
    assert second.structured_content == first.structured_content
    assert other.structured_content is not None
    assert other.structured_content["dispute_id"] != second.structured_content["dispute_id"]


async def test_listing_an_unknown_accounts_transactions_is_an_error(
    gateway: RunningGateway,
) -> None:
    async with Client(gateway.url) as client:
        result = await client.call_tool("list_transactions", {"account_id": "A999"})

    assert "No account with ID A999" in error_message(result)


async def test_disputing_an_unknown_transaction_is_an_error(
    writable_gateway: RunningGateway,
) -> None:
    async with Client(writable_gateway.url) as client:
        result = await client.call_tool(
            "raise_dispute", {"transaction_id": "T99999", "reason": "Not mine."}
        )

    assert "No transaction with ID T99999" in error_message(result)
