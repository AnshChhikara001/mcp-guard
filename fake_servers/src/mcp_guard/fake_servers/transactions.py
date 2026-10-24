"""The transactions MCP server: an account's transactions, and disputes raised on them.

Like every fake server, it speaks MCP over stdin/stdout only, so only the Gateway can reach
it, and it reads and writes the `bank` schema in the database named by DATABASE_URL.
"""

from datetime import datetime
from typing import Literal, TypedDict

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from mcp_guard.fake_servers.bank import IST, Direction
from mcp_guard.fake_servers.database import connect, fetch_all, fetch_one
from mcp_guard.fake_servers.money import format_rupees

server = MCPServer(
    "transactions",
    instructions="Transactions on bank accounts, and disputes about them. Amounts are in Indian"
    " rupees.",
)

MAX_TRANSACTIONS = 100


class TransactionEntry(TypedDict):
    transaction_id: str
    posted_at: datetime
    direction: Direction
    amount_paise: int
    amount: str
    channel: str
    counterparty: str


class AccountTransactions(TypedDict):
    account_id: str
    transactions: list[TransactionEntry]


class Dispute(TypedDict):
    dispute_id: str
    transaction_id: str
    reason: str
    status: Literal["open", "closed"]
    raised_at: datetime


@server.tool()
async def list_transactions(account_id: str, limit: int = 20) -> AccountTransactions:
    """An account's most recent transactions, newest first (at most `limit`, up to 100)."""
    if not 1 <= limit <= MAX_TRANSACTIONS:
        raise ToolError(f"limit must be between 1 and {MAX_TRANSACTIONS}")
    if await fetch_one("SELECT 1 FROM bank.accounts WHERE id = %s", account_id) is None:
        raise ToolError(f"No account with ID {account_id}")
    rows = await fetch_all(
        "SELECT id, posted_at, direction, amount_paise, channel, counterparty"
        " FROM bank.transactions WHERE account_id = %s ORDER BY posted_at DESC, id DESC LIMIT %s",
        account_id,
        limit,
    )
    transactions = [
        TransactionEntry(
            transaction_id=row["id"],
            posted_at=row["posted_at"].astimezone(IST),
            direction=row["direction"],
            amount_paise=row["amount_paise"],
            amount=format_rupees(row["amount_paise"]),
            channel=row["channel"],
            counterparty=row["counterparty"],
        )
        for row in rows
    ]
    return AccountTransactions(account_id=account_id, transactions=transactions)


@server.tool()
async def raise_dispute(transaction_id: str, reason: str) -> Dispute:
    """Dispute a transaction, saying why. A transaction has at most one open dispute: raising
    another returns that one."""
    if await fetch_one("SELECT 1 FROM bank.transactions WHERE id = %s", transaction_id) is None:
        raise ToolError(f"No transaction with ID {transaction_id}")
    async with await connect() as conn:
        cursor = await conn.execute(
            "INSERT INTO bank.disputes (transaction_id, reason) VALUES (%s, %s)"
            " ON CONFLICT (transaction_id) WHERE status = 'open' DO NOTHING"
            " RETURNING id, transaction_id, reason, status, raised_at",
            (transaction_id, reason),
        )
        row = await cursor.fetchone()
        if row is None:
            cursor = await conn.execute(
                "SELECT id, transaction_id, reason, status, raised_at FROM bank.disputes"
                " WHERE transaction_id = %s AND status = 'open'",
                (transaction_id,),
            )
            row = await cursor.fetchone()
    assert row is not None
    return Dispute(
        dispute_id=row["id"],
        transaction_id=row["transaction_id"],
        reason=row["reason"],
        status=row["status"],
        raised_at=row["raised_at"].astimezone(IST),
    )


def main() -> None:
    server.run()  # stdio


if __name__ == "__main__":
    main()
