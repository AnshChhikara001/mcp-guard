"""The Gateway's reads of the bank: who a person is, who owns what, and whom Staff serve.

The Gateway reads the same `bank` schema as the servers behind it (DATABASE_URL), but only to
decide: it never writes, and never returns what it reads to the Agent. Every read opens its own
connection, like the servers do.
"""

import os
from typing import LiteralString, cast

import psycopg

from mcp_guard.gateway.tokens import Role

LOCAL_DATABASE_URL = "postgresql://mcpguard:mcpguard@localhost:5432/mcpguard"

FIND_ROLE = """
SELECT 'customer' FROM bank.customers WHERE id = %s
UNION ALL
SELECT CASE WHEN is_supervisor THEN 'supervisor' ELSE 'staff' END FROM bank.staff WHERE id = %s
"""


async def role_of(person_id: str) -> Role | None:
    """The person's role, or None if the bank doesn't know them."""
    return cast(Role | None, await _one(FIND_ROLE, person_id, person_id))


async def customers_served_by(staff_id: str) -> frozenset[str]:
    """The Customers a Staff member serves (none for a Supervisor: they serve everyone)."""
    rows = await _all("SELECT customer_id FROM bank.serving WHERE staff_id = %s", staff_id)
    return frozenset(rows)


async def customer(customer_id: str) -> str | None:
    """The Customer with this ID, if there is one."""
    return await _one("SELECT id FROM bank.customers WHERE id = %s", customer_id)


async def owner_of_account(account_id: str) -> str | None:
    return await _one("SELECT customer_id FROM bank.accounts WHERE id = %s", account_id)


async def owner_of_transaction(transaction_id: str) -> str | None:
    return await _one(
        "SELECT a.customer_id FROM bank.transactions t JOIN bank.accounts a ON a.id = t.account_id"
        " WHERE t.id = %s",
        transaction_id,
    )


async def owner_of_ticket(ticket_id: str) -> str | None:
    return await _one("SELECT customer_id FROM bank.tickets WHERE id = %s", ticket_id)


async def customer_with_email(email: str) -> str | None:
    """The Customer whose registered email this is, ignoring case and spaces."""
    return await _one(
        "SELECT id FROM bank.customers WHERE lower(regexp_replace(email, '\\s', '', 'g')) = %s",
        _squeeze(email),
    )


async def customer_with_phone(phone: str) -> str | None:
    """The Customer whose registered phone this is, ignoring spaces."""
    return await _one(
        "SELECT id FROM bank.customers WHERE lower(regexp_replace(phone, '\\s', '', 'g')) = %s",
        _squeeze(phone),
    )


def _squeeze(text: str) -> str:
    return "".join(text.split()).lower()


async def _one(query: LiteralString, *params: object) -> str | None:
    rows = await _all(query, *params)
    return rows[0] if rows else None


async def _all(query: LiteralString, *params: object) -> list[str]:
    """The first column of every row the query returns."""
    database_url = os.environ.get("DATABASE_URL", LOCAL_DATABASE_URL)
    async with await psycopg.AsyncConnection.connect(database_url) as conn:
        rows = await (await conn.execute(query, params)).fetchall()
    return [row[0] for row in rows]
