"""The accounts MCP server: Customer profiles, accounts and balances from the seeded bank.

It speaks MCP over stdin/stdout only and opens no network port, so the only way to reach it
is to be the process that started it: the Gateway. It reads the `bank` schema in the
database named by DATABASE_URL (load it with `make reset-bank`).
"""

import os
from datetime import date
from typing import Any, LiteralString, TypedDict

import psycopg
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from psycopg.rows import dict_row

from mcp_guard.fake_servers.reset import LOCAL_DATABASE_URL

server = MCPServer(
    "accounts",
    instructions="Customer profiles, bank accounts and balances. Amounts are in Indian rupees.",
)


class CustomerProfile(TypedDict):
    customer_id: str
    name: str
    date_of_birth: date
    email: str
    phone: str
    address: str
    aadhaar: str
    pan: str
    upi_id: str


class AccountSummary(TypedDict):
    account_id: str
    number: str
    ifsc: str
    kind: str
    opened_on: date


class Balance(TypedDict):
    account_id: str
    balance_paise: int
    balance: str


class CustomerAccounts(TypedDict):
    customer_id: str
    accounts: list[AccountSummary]


@server.tool()
async def get_customer_profile(customer_id: str) -> CustomerProfile:
    """A Customer's personal details: name, date of birth, contact details and KYC numbers."""
    row = await _fetch_one(
        "SELECT id AS customer_id, name, date_of_birth, email, phone, address, aadhaar, pan,"
        " upi_id FROM bank.customers WHERE id = %s",
        customer_id,
    )
    if row is None:
        raise ToolError(f"No Customer with ID {customer_id}")
    return CustomerProfile(**row)


@server.tool()
async def list_accounts(customer_id: str) -> CustomerAccounts:
    """The bank accounts a Customer owns, without balances."""
    if await _fetch_one("SELECT 1 FROM bank.customers WHERE id = %s", customer_id) is None:
        raise ToolError(f"No Customer with ID {customer_id}")
    rows = await _fetch_all(
        "SELECT id AS account_id, number, ifsc, kind, opened_on FROM bank.accounts"
        " WHERE customer_id = %s ORDER BY id",
        customer_id,
    )
    accounts = [AccountSummary(**row) for row in rows]
    return CustomerAccounts(customer_id=customer_id, accounts=accounts)


@server.tool()
async def get_balance(account_id: str) -> Balance:
    """An account's current balance, in paise and written out in rupees."""
    row = await _fetch_one("SELECT balance_paise FROM bank.accounts WHERE id = %s", account_id)
    if row is None:
        raise ToolError(f"No account with ID {account_id}")
    paise: int = row["balance_paise"]
    return Balance(account_id=account_id, balance_paise=paise, balance=format_rupees(paise))


def format_rupees(paise: int) -> str:
    """12345678 -> "₹1,23,456.78", grouped the Indian way (lakhs and crores)."""
    rupees, rest = divmod(paise, 100)
    digits = str(rupees)
    head, tail = digits[:-3], digits[-3:]
    groups = [head[max(0, i - 2) : i] for i in range(len(head), 0, -2)][::-1]
    return "₹" + ",".join([*groups, tail]) + f".{rest:02d}"


async def _fetch_one(query: LiteralString, *params: object) -> dict[str, Any] | None:
    rows = await _fetch_all(query, *params)
    return rows[0] if rows else None


async def _fetch_all(query: LiteralString, *params: object) -> list[dict[str, Any]]:
    database_url = os.environ.get("DATABASE_URL", LOCAL_DATABASE_URL)
    async with (
        await psycopg.AsyncConnection.connect(database_url) as conn,
        conn.cursor(row_factory=dict_row) as cursor,
    ):
        await cursor.execute(query, params)
        return await cursor.fetchall()


def main() -> None:
    server.run()  # stdio


if __name__ == "__main__":
    main()
