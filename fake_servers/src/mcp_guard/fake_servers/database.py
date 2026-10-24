"""Queries against the fake bank's `bank` schema, for the MCP servers.

Every call opens its own connection to the database named by DATABASE_URL (load it with
`make reset-bank`), so a server holds no connection between Tool calls.
"""

import os
from typing import Any, LiteralString

import psycopg
from psycopg.rows import DictRow, dict_row

from mcp_guard.fake_servers.reset import LOCAL_DATABASE_URL


async def connect() -> psycopg.AsyncConnection[DictRow]:
    """A new connection whose rows come back as dicts. Commits on a clean exit of `async with`."""
    database_url = os.environ.get("DATABASE_URL", LOCAL_DATABASE_URL)
    return await psycopg.AsyncConnection[DictRow].connect(database_url, row_factory=dict_row)


async def fetch_one(query: LiteralString, *params: object) -> dict[str, Any] | None:
    rows = await fetch_all(query, *params)
    return rows[0] if rows else None


async def fetch_all(query: LiteralString, *params: object) -> list[dict[str, Any]]:
    async with await connect() as conn:
        cursor = await conn.execute(query, params)
        return await cursor.fetchall()
