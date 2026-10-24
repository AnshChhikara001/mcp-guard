"""The accounts MCP server: Customer profiles, accounts and balances from the seeded bank.

It speaks MCP over stdin/stdout only and opens no network port, so the only way to reach it
is to be the process that started it: the Gateway. It reads the `bank` schema in the
database named by DATABASE_URL (load it with `make reset-bank`).
"""

from datetime import date
from typing import TypedDict

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from mcp_guard.fake_servers.database import fetch_all, fetch_one
from mcp_guard.fake_servers.money import format_rupees

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
    row = await fetch_one(
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
    if await fetch_one("SELECT 1 FROM bank.customers WHERE id = %s", customer_id) is None:
        raise ToolError(f"No Customer with ID {customer_id}")
    rows = await fetch_all(
        "SELECT id AS account_id, number, ifsc, kind, opened_on FROM bank.accounts"
        " WHERE customer_id = %s ORDER BY id",
        customer_id,
    )
    accounts = [AccountSummary(**row) for row in rows]
    return CustomerAccounts(customer_id=customer_id, accounts=accounts)


@server.tool()
async def get_balance(account_id: str) -> Balance:
    """An account's current balance, in paise and written out in rupees."""
    row = await fetch_one("SELECT balance_paise FROM bank.accounts WHERE id = %s", account_id)
    if row is None:
        raise ToolError(f"No account with ID {account_id}")
    paise: int = row["balance_paise"]
    return Balance(account_id=account_id, balance_paise=paise, balance=format_rupees(paise))


def main() -> None:
    server.run()  # stdio


if __name__ == "__main__":
    main()
