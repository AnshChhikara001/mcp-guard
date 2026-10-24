"""The payments MCP server: refunds of debit transactions.

Like every fake server, it speaks MCP over stdin/stdout only, so only the Gateway can reach
it, and it reads and writes the `bank` schema in the database named by DATABASE_URL.
"""

from datetime import datetime
from typing import TypedDict

import psycopg
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from psycopg.errors import UniqueViolation
from psycopg.rows import DictRow

from mcp_guard.fake_servers.bank import IST
from mcp_guard.fake_servers.database import connect
from mcp_guard.fake_servers.money import format_rupees

server = MCPServer(
    "payments",
    instructions="Refunds of debit transactions back into the account they came from. Amounts"
    " are in paise (1 rupee = 100 paise).",
)


class Refund(TypedDict):
    refund_id: str
    transaction_id: str
    account_id: str
    amount_paise: int
    amount: str
    idempotency_key: str
    refunded_at: datetime


@server.tool()
async def refund(transaction_id: str, amount_paise: int, idempotency_key: str) -> Refund:
    """Pay `amount_paise` of a debit transaction back into its account. All refunds of one
    transaction add up to at most its amount.

    Repeating a refund with the same `idempotency_key` returns the first refund and pays
    nothing more; reusing the key with different arguments is an error.
    """
    if amount_paise <= 0:
        raise ToolError("The amount to refund must be more than zero")
    async with await connect() as conn:
        # Locking the transaction makes refunds of it run one at a time. Everything read after
        # the lock sees the refunds committed while this call waited for it.
        cursor = await conn.execute(
            "SELECT account_id, direction, amount_paise FROM bank.transactions"
            " WHERE id = %s FOR UPDATE",
            (transaction_id,),
        )
        transaction = await cursor.fetchone()
        if transaction is None:
            raise ToolError(f"No transaction with ID {transaction_id}")
        if transaction["direction"] != "debit":
            raise ToolError(
                f"Transaction {transaction_id} is a credit; only debits can be refunded"
            )
        earlier = await _refund_with_key(conn, idempotency_key)
        if earlier is not None:
            if (earlier["transaction_id"], earlier["amount_paise"]) != (
                transaction_id,
                amount_paise,
            ):
                raise _key_reused(idempotency_key)
            return earlier
        cursor = await conn.execute(
            "SELECT coalesce(sum(amount_paise), 0)::bigint AS refunded_paise FROM bank.refunds"
            " WHERE transaction_id = %s",
            (transaction_id,),
        )
        refunded = await cursor.fetchone()
        assert refunded is not None
        refundable: int = transaction["amount_paise"] - refunded["refunded_paise"]
        if amount_paise > refundable:
            raise ToolError(
                f"At most {format_rupees(refundable)} of transaction {transaction_id}"
                " can still be refunded"
            )
        try:
            await conn.execute(
                "INSERT INTO bank.refunds (transaction_id, amount_paise, idempotency_key)"
                " VALUES (%s, %s, %s)",
                (transaction_id, amount_paise, idempotency_key),
            )
        except UniqueViolation:
            # The same key, at the same moment, in a refund of another transaction.
            raise _key_reused(idempotency_key) from None
        await conn.execute(
            "UPDATE bank.accounts SET balance_paise = balance_paise + %s WHERE id = %s",
            (amount_paise, transaction["account_id"]),
        )
        new_refund = await _refund_with_key(conn, idempotency_key)
    assert new_refund is not None
    return new_refund


def _key_reused(idempotency_key: str) -> ToolError:
    return ToolError(f"Idempotency key {idempotency_key} was already used for a different refund")


async def _refund_with_key(
    conn: psycopg.AsyncConnection[DictRow], idempotency_key: str
) -> Refund | None:
    cursor = await conn.execute(
        "SELECT r.id, r.transaction_id, t.account_id, r.amount_paise, r.refunded_at"
        " FROM bank.refunds r JOIN bank.transactions t ON t.id = r.transaction_id"
        " WHERE r.idempotency_key = %s",
        (idempotency_key,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return Refund(
        refund_id=row["id"],
        transaction_id=row["transaction_id"],
        account_id=row["account_id"],
        amount_paise=row["amount_paise"],
        amount=format_rupees(row["amount_paise"]),
        idempotency_key=idempotency_key,
        refunded_at=row["refunded_at"].astimezone(IST),
    )


def main() -> None:
    server.run()  # stdio


if __name__ == "__main__":
    main()
