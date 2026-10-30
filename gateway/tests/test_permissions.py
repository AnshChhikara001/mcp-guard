"""Seam 1: Cedar policies decide every Tool call by person, role, tool and arguments.

A Customer acts on their own data only, Staff for the Customers they serve, and a Supervisor
for every Customer. A denied call is an error result carrying a reason code and a reason the
Agent can explain; an unknown ID is denied with the same reason as someone else's.
"""

import shutil
from pathlib import Path

import pytest

from conftest import RunningGateway, error_message, gateway_client
from mcp_guard.gateway.permissions import POLICIES_DIR
from mcp_guard.gateway.process import start_gateway

pytestmark = pytest.mark.anyio

NOT_YOURS = "not_found_or_not_yours"
RECIPIENT = "recipient_not_allowed"
NOT_PERMITTED = "not_permitted"


async def test_a_customer_reads_their_own_balance(guarded_gateway: RunningGateway) -> None:
    async with gateway_client(guarded_gateway, "C001") as client:
        result = await client.call_tool("get_balance", {"account_id": "A001"})

    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["balance_paise"] == 194_844_00


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("get_balance", {"account_id": "A002"}),
        ("list_transactions", {"account_id": "A001", "limit": 5}),
        ("get_customer_profile", {"customer_id": "C001"}),
        ("list_accounts", {"customer_id": "C001"}),
        ("raise_dispute", {"transaction_id": "T00028", "reason": "Not me"}),
    ],
)
async def test_a_customer_reading_or_acting_on_another_customers_data_is_denied_with_a_reason(
    guarded_gateway: RunningGateway, tool: str, arguments: dict[str, object]
) -> None:
    async with gateway_client(guarded_gateway, "C003") as client:
        result = await client.call_tool(tool, arguments)

    message = error_message(result)
    assert NOT_YOURS in message
    assert "not found" in message.lower()


async def test_an_unknown_id_is_denied_like_someone_elses(guarded_gateway: RunningGateway) -> None:
    async with gateway_client(guarded_gateway, "C003") as client:
        unknown = await client.call_tool("get_balance", {"account_id": "A999"})
        someone_elses = await client.call_tool("get_balance", {"account_id": "A002"})

    assert error_message(unknown) == error_message(someone_elses)


async def test_a_denied_refund_moves_no_money(guarded_gateway: RunningGateway) -> None:
    refund = {"transaction_id": "T00013", "amount_paise": 1_499_00, "idempotency_key": "x-1"}
    async with gateway_client(guarded_gateway, "C003") as client:
        denied = await client.call_tool("refund", refund)
    async with gateway_client(guarded_gateway, "C001") as client:
        balance = await client.call_tool("get_balance", {"account_id": "A001"})

    assert NOT_YOURS in error_message(denied)
    assert balance.structured_content is not None
    assert balance.structured_content["balance_paise"] == 194_844_00


async def test_a_customer_reads_their_own_ticket_but_not_others_nor_the_inbox(
    guarded_gateway: RunningGateway,
) -> None:
    async with gateway_client(guarded_gateway, "C001") as client:
        own = await client.call_tool("get_ticket", {"ticket_id": "TK001"})
        others = await client.call_tool("get_ticket", {"ticket_id": "TK002"})
        inbox = await client.call_tool("list_tickets", {})

    assert not own.is_error
    assert NOT_YOURS in error_message(others)
    assert NOT_PERMITTED in error_message(inbox)


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        # C002's registered email and phone, and addresses that belong to no Customer.
        ("send_email", {"to": "rohan.mehta57@example.com", "subject": "Hi", "body": "Hi"}),
        ("send_sms", {"to": "+91 7019283746", "body": "Hi"}),
        ("send_email", {"to": "me@elsewhere.example", "subject": "Hi", "body": "Hi"}),
        ("send_sms", {"to": "+91 9000000000", "body": "Hi"}),
    ],
)
async def test_a_customer_cant_message_anyone_but_themselves(
    guarded_gateway: RunningGateway, tool: str, arguments: dict[str, object]
) -> None:
    async with gateway_client(guarded_gateway, "C003") as client:
        result = await client.call_tool(tool, arguments)

    assert RECIPIENT in error_message(result)


async def test_anyone_can_convert_currency(guarded_gateway: RunningGateway) -> None:
    arguments = {"amount": 1, "from_currency": "USD", "to_currency": "INR", "reference": "x"}
    async with gateway_client(guarded_gateway, "C003") as client:
        result = await client.call_tool("convert", arguments)

    assert not result.is_error


async def test_staff_read_customers_they_serve_and_are_denied_for_others(
    guarded_gateway: RunningGateway,
) -> None:
    async with gateway_client(guarded_gateway, "S001") as client:
        served = await client.call_tool("get_balance", {"account_id": "A005"})
        profile = await client.call_tool("get_customer_profile", {"customer_id": "C001"})
        not_served = await client.call_tool("get_balance", {"account_id": "A002"})
        not_served_profile = await client.call_tool("get_customer_profile", {"customer_id": "C002"})

    assert not served.is_error
    assert not profile.is_error
    assert NOT_YOURS in error_message(not_served)
    assert NOT_YOURS in error_message(not_served_profile)


async def test_staff_read_the_whole_inbox(guarded_gateway: RunningGateway) -> None:
    async with gateway_client(guarded_gateway, "S003") as client:
        inbox = await client.call_tool("list_tickets", {})
        # TK002 is Rohan Mehta's (C002), whom S003 doesn't serve.
        ticket = await client.call_tool("get_ticket", {"ticket_id": "TK002"})

    assert not inbox.is_error
    assert not ticket.is_error


async def test_staff_cant_message_a_customer_they_dont_serve(
    guarded_gateway: RunningGateway,
) -> None:
    arguments = {"to": "rohan.mehta57@example.com", "subject": "Hi", "body": "Hi"}
    async with gateway_client(guarded_gateway, "S001") as client:
        result = await client.call_tool("send_email", arguments)

    assert RECIPIENT in error_message(result)


async def test_a_supervisor_reads_every_customer(guarded_gateway: RunningGateway) -> None:
    async with gateway_client(guarded_gateway, "S004") as client:
        results = [
            await client.call_tool("get_balance", {"account_id": f"A{n:03d}"}) for n in range(1, 17)
        ]

    assert not [r for r in results if r.is_error]


async def test_with_defences_off_a_customer_reads_another_customers_account(
    gateway: RunningGateway,
) -> None:
    async with gateway_client(gateway, "C003") as client:
        result = await client.call_tool("get_balance", {"account_id": "A002"})

    assert not result.is_error


def test_the_gateway_refuses_to_start_with_a_policy_the_schema_rejects(
    seeded_database_url: str, tmp_path: Path
) -> None:
    policies = tmp_path / "policies"
    shutil.copytree(POLICIES_DIR, policies)
    (policies / "broken.cedar").write_text(
        "permit (principal is Customer, action, resource) when { principal.salary > 0 };"
    )

    with (
        pytest.raises(RuntimeError, match="exited during startup"),
        start_gateway(seeded_database_url, {"GATEWAY_POLICIES_DIR": str(policies)}),
    ):
        pass
