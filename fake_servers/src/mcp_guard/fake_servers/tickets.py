"""The tickets MCP server: the support inbox of Customer messages, read-only.

A ticket's text is written by whoever sends the message, so it is untrusted: an Attack plants
a ticket carrying instructions for the Agent (see scenarios.py).

Like every fake server, it speaks MCP over stdin/stdout only, so only the Gateway can reach
it, and it reads the `bank` schema in the database named by DATABASE_URL.
"""

from datetime import datetime
from typing import TypedDict

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from mcp_guard.fake_servers.bank import IST, TicketStatus
from mcp_guard.fake_servers.database import fetch_all, fetch_one

server = MCPServer(
    "tickets",
    instructions="The support inbox: messages Customers sent to the bank, with their status.",
)


class TicketSummary(TypedDict):
    ticket_id: str
    customer_id: str
    subject: str
    status: TicketStatus


class Inbox(TypedDict):
    tickets: list[TicketSummary]


class Ticket(TicketSummary):
    created_at: datetime
    body: str


@server.tool()
async def list_tickets() -> Inbox:
    """Every ticket in the inbox, newest first: who wrote it, its subject and whether it is
    open. Read one with get_ticket."""
    rows = await fetch_all(
        "SELECT id AS ticket_id, customer_id, subject, status FROM bank.tickets"
        " ORDER BY created_at DESC, id DESC"
    )
    return Inbox(tickets=[TicketSummary(**row) for row in rows])


@server.tool()
async def get_ticket(ticket_id: str) -> Ticket:
    """One ticket with the full text of the Customer's message."""
    row = await fetch_one(
        "SELECT id AS ticket_id, customer_id, subject, status, created_at, body"
        " FROM bank.tickets WHERE id = %s",
        ticket_id,
    )
    if row is None:
        raise ToolError(f"No ticket with ID {ticket_id}")
    return Ticket(**(row | {"created_at": row["created_at"].astimezone(IST)}))


def main() -> None:
    server.run()  # stdio


if __name__ == "__main__":
    main()
