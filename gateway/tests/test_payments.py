"""Seam 1: the payments server through the Gateway: refunds, paid once per idempotency key."""

import anyio
import pytest
from mcp import Client
from mcp.types import CallToolResult

from conftest import RunningGateway, error_message

pytestmark = pytest.mark.anyio

SEEDED_A001_BALANCE_PAISE = 194_844_00  # T00013 and T00014 on A001 are debits of ₹1,499


async def test_a_refund_pays_the_amount_back_into_the_transactions_account(
    writable_gateway: RunningGateway,
) -> None:
    async with Client(writable_gateway.url) as client:
        result = await client.call_tool(
            "refund",
            {"transaction_id": "T00014", "amount_paise": 1_499_00, "idempotency_key": "k-1"},
        )
        balance = await client.call_tool("get_balance", {"account_id": "A001"})

    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content.pop("refunded_at")
    assert result.structured_content == {
        "refund_id": "R0001",
        "transaction_id": "T00014",
        "account_id": "A001",
        "amount_paise": 1_499_00,
        "amount": "₹1,499.00",
        "idempotency_key": "k-1",
    }
    assert balance.structured_content is not None
    assert balance.structured_content["balance_paise"] == SEEDED_A001_BALANCE_PAISE + 1_499_00


async def test_repeating_a_refund_with_the_same_key_returns_the_first_result_and_pays_once(
    writable_gateway: RunningGateway,
) -> None:
    arguments = {"transaction_id": "T00013", "amount_paise": 1_000_00, "idempotency_key": "k-2"}
    async with Client(writable_gateway.url) as client:
        first = await client.call_tool("refund", arguments)
        repeated = await client.call_tool("refund", arguments)
        balance = await client.call_tool("get_balance", {"account_id": "A001"})

    assert not repeated.is_error
    assert repeated.structured_content == first.structured_content
    assert balance.structured_content is not None
    assert balance.structured_content["balance_paise"] == SEEDED_A001_BALANCE_PAISE + 1_000_00


@pytest.mark.parametrize(
    "changed",
    [{"amount_paise": 500_00}, {"transaction_id": "T00014"}],
    ids=["amount", "transaction"],
)
async def test_reusing_a_key_with_different_arguments_is_an_error_and_pays_nothing(
    writable_gateway: RunningGateway, changed: dict[str, object]
) -> None:
    arguments = {"transaction_id": "T00013", "amount_paise": 1_000_00, "idempotency_key": "k-3"}
    async with Client(writable_gateway.url) as client:
        await client.call_tool("refund", arguments)
        reused = await client.call_tool("refund", {**arguments, **changed})
        balance = await client.call_tool("get_balance", {"account_id": "A001"})

    assert "Idempotency key k-3 was already used for a different refund" in error_message(reused)
    assert balance.structured_content is not None
    assert balance.structured_content["balance_paise"] == SEEDED_A001_BALANCE_PAISE + 1_000_00


async def test_refunds_of_a_transaction_add_up_to_at_most_its_debit(
    writable_gateway: RunningGateway,
) -> None:
    async with Client(writable_gateway.url) as client:
        await client.call_tool(
            "refund",
            {"transaction_id": "T00013", "amount_paise": 1_000_00, "idempotency_key": "k-4"},
        )
        too_much = await client.call_tool(
            "refund",
            {"transaction_id": "T00013", "amount_paise": 500_00, "idempotency_key": "k-5"},
        )
        the_rest = await client.call_tool(
            "refund",
            {"transaction_id": "T00013", "amount_paise": 499_00, "idempotency_key": "k-6"},
        )
        balance = await client.call_tool("get_balance", {"account_id": "A001"})

    assert "At most ₹499.00 of transaction T00013 can still be refunded" in error_message(too_much)
    assert not the_rest.is_error
    assert balance.structured_content is not None
    assert balance.structured_content["balance_paise"] == SEEDED_A001_BALANCE_PAISE + 1_499_00


@pytest.mark.parametrize(
    ("transaction_id", "amount_paise", "message"),
    [
        ("T00002", 100_00, "Transaction T00002 is a credit; only debits can be refunded"),
        ("T99999", 100_00, "No transaction with ID T99999"),
        ("T00013", 0, "The amount to refund must be more than zero"),
        ("T00013", -100_00, "The amount to refund must be more than zero"),
    ],
    ids=["credit", "unknown transaction", "zero", "negative"],
)
async def test_a_refund_that_cant_be_made_is_an_error_and_pays_nothing(
    writable_gateway: RunningGateway, transaction_id: str, amount_paise: int, message: str
) -> None:
    async with Client(writable_gateway.url) as client:
        result = await client.call_tool(
            "refund",
            {
                "transaction_id": transaction_id,
                "amount_paise": amount_paise,
                "idempotency_key": "k-7",
            },
        )
        balance = await client.call_tool("get_balance", {"account_id": "A001"})

    assert message in error_message(result)
    assert balance.structured_content is not None
    assert balance.structured_content["balance_paise"] == SEEDED_A001_BALANCE_PAISE


async def test_refunds_made_at_the_same_time_still_add_up_to_at_most_the_debit(
    writable_gateway: RunningGateway,
) -> None:
    results: list[CallToolResult] = []

    async def refund_all_of_it(key: str) -> None:
        async with Client(writable_gateway.url) as client:
            arguments = {"transaction_id": "T00013", "amount_paise": 1_499_00}
            results.append(await client.call_tool("refund", {**arguments, "idempotency_key": key}))

    async with anyio.create_task_group() as group:
        for n in range(5):
            group.start_soon(refund_all_of_it, f"k-{n}")
    async with Client(writable_gateway.url) as client:
        balance = await client.call_tool("get_balance", {"account_id": "A001"})

    assert [r.is_error for r in results].count(False) == 1
    assert balance.structured_content is not None
    assert balance.structured_content["balance_paise"] == SEEDED_A001_BALANCE_PAISE + 1_499_00


async def test_one_key_used_for_two_refunds_at_the_same_time_pays_only_one(
    writable_gateway: RunningGateway,
) -> None:
    results: list[CallToolResult] = []

    async def refund_all_of(transaction_id: str) -> None:
        async with Client(writable_gateway.url) as client:
            arguments = {"transaction_id": transaction_id, "amount_paise": 1_499_00}
            results.append(await client.call_tool("refund", {**arguments, "idempotency_key": "k"}))

    async with anyio.create_task_group() as group:
        group.start_soon(refund_all_of, "T00013")
        group.start_soon(refund_all_of, "T00014")
    async with Client(writable_gateway.url) as client:
        balance = await client.call_tool("get_balance", {"account_id": "A001"})

    [failed] = [r for r in results if r.is_error]
    assert "Idempotency key k was already used for a different refund" in error_message(failed)
    assert balance.structured_content is not None
    assert balance.structured_content["balance_paise"] == SEEDED_A001_BALANCE_PAISE + 1_499_00
