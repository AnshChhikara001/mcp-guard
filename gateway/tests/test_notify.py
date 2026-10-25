"""Seam 1: the notify server through the Gateway: messages are recorded, never sent."""

import pytest
from mcp import Client

from conftest import WritableBank

pytestmark = pytest.mark.anyio


async def test_every_message_given_to_notify_is_recorded(writable_bank: WritableBank) -> None:
    email = {
        "to": "ananya.iyer84@example.com",
        "subject": "Your refund",
        "body": "We refunded ₹1,499 to account 50100483920176.",
    }
    sms = {"to": "+91 9845012376", "body": "Your refund of ₹1,499 is on its way."}
    async with Client(writable_bank.gateway.url) as client:
        sent_email = await client.call_tool("send_email", email)
        sent_sms = await client.call_tool("send_sms", sms)

    assert not sent_email.is_error
    assert not sent_sms.is_error
    assert writable_bank.outbound() == [
        ("notify", "send_email", email),
        ("notify", "send_sms", sms),
    ]
