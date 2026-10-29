"""Seam 1: the notify server through the Gateway: messages are recorded, never sent."""

import pytest

from conftest import WritableBank, error_message, gateway_client

pytestmark = pytest.mark.anyio


async def test_every_message_given_to_notify_is_recorded(writable_bank: WritableBank) -> None:
    email = {
        "to": "ananya.iyer84@example.com",
        "subject": "Your refund",
        "body": "We refunded ₹1,499 to account 50100483920176.",
    }
    sms = {"to": "+91 9845012376", "body": "Your refund of ₹1,499 is on its way."}
    async with gateway_client(writable_bank.gateway) as client:
        sent_email = await client.call_tool("send_email", email)
        sent_sms = await client.call_tool("send_sms", sms)

    assert not sent_email.is_error
    assert not sent_sms.is_error
    assert writable_bank.outbound() == [
        ("notify", "send_email", email),
        ("notify", "send_sms", sms),
    ]


async def test_repeating_a_message_with_the_same_key_returns_the_first_and_records_once(
    writable_bank: WritableBank,
) -> None:
    sms = {"to": "+91 9845012376", "body": "Your refund is on its way.", "idempotency_key": "h-1"}
    async with gateway_client(writable_bank.gateway) as client:
        first = await client.call_tool("send_sms", sms)
        repeated = await client.call_tool("send_sms", sms)

    assert not repeated.is_error
    assert repeated.structured_content == first.structured_content
    assert writable_bank.outbound() == [("notify", "send_sms", sms)]


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("send_sms", {"to": "+91 9845012376", "body": "Something else."}),
        ("send_email", {"to": "+91 9845012376", "subject": "Hi", "body": "Your refund."}),
    ],
    ids=["other-body", "other-tool"],
)
async def test_reusing_a_key_for_a_different_message_is_an_error_and_records_nothing(
    writable_bank: WritableBank, tool: str, arguments: dict[str, str]
) -> None:
    sms = {"to": "+91 9845012376", "body": "Your refund.", "idempotency_key": "h-2"}
    async with gateway_client(writable_bank.gateway) as client:
        await client.call_tool("send_sms", sms)
        reused = await client.call_tool(tool, arguments | {"idempotency_key": "h-2"})

    assert "Idempotency key h-2 was already used for a different message" in error_message(reused)
    assert writable_bank.outbound() == [("notify", "send_sms", sms)]
