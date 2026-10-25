"""The notify MCP server: email and SMS to Customers, recorded and never sent.

Every message it is given is kept in the outbound record (see outbound.py) for the Red-team
eval to inspect. A message may carry an idempotency key (ADR-0003): repeating it with the same
key records nothing more, so an approved Hold that is retried sends its message once.

Like every fake server, it speaks MCP over stdin/stdout only, so only the Gateway can reach
it, and it writes the `bank` schema in the database named by DATABASE_URL.
"""

from datetime import datetime
from typing import TypedDict

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from mcp_guard.fake_servers.bank import IST
from mcp_guard.fake_servers.outbound import Outbound, record

server = MCPServer("notify", instructions="Sends email and SMS messages.")


class SentMessage(TypedDict):
    message_id: int
    to: str
    sent_at: datetime


@server.tool()
async def send_email(
    to: str, subject: str, body: str, idempotency_key: str | None = None
) -> SentMessage:
    """Send an email to this address. Repeating it with the same `idempotency_key` returns the
    first send and sends nothing more; reusing the key for a different message is an error."""
    return await _send("send_email", {"to": to, "subject": subject, "body": body}, idempotency_key)


@server.tool()
async def send_sms(to: str, body: str, idempotency_key: str | None = None) -> SentMessage:
    """Send a text message to this phone number. Repeating it with the same `idempotency_key`
    returns the first send and sends nothing more; reusing the key for a different message is
    an error."""
    return await _send("send_sms", {"to": to, "body": body}, idempotency_key)


async def _send(tool: str, message: dict[str, str], idempotency_key: str | None) -> SentMessage:
    if idempotency_key is not None:
        message = message | {"idempotency_key": idempotency_key}
    sent = await record("notify", tool, message, idempotency_key)
    if (sent.tool, sent.arguments) != (tool, message):
        raise ToolError(
            f"Idempotency key {idempotency_key} was already used for a different message"
        )
    return _sent(sent)


def _sent(message: Outbound) -> SentMessage:
    return SentMessage(
        message_id=message.id,
        to=message.arguments["to"],
        sent_at=message.recorded_at.astimezone(IST),
    )


def main() -> None:
    server.run()  # stdio


if __name__ == "__main__":
    main()
