"""The record of everything that leaves the bank: messages given to notify, calls to fx-rates.

Nothing is really sent. Each call those servers receive is kept as a row in `bank.outbound`
with its full arguments, so the Red-team eval can search it for Canaries. There is no tool to
read it back (that would be one more way to leak data); trusted code reads it here. A reset
empties it.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import class_row
from psycopg.types.json import Jsonb

from mcp_guard.fake_servers.database import connect


@dataclass(frozen=True)
class Outbound:
    id: int
    server: str
    tool: str
    arguments: dict[str, Any]
    recorded_at: datetime


async def record(
    server: str, tool: str, arguments: dict[str, Any], idempotency_key: str | None = None
) -> Outbound:
    """Keep one call a server received, as it arrived.

    If this server already recorded a call with the same `idempotency_key`, nothing is
    recorded and that earlier call is returned; the caller checks it is the same call.
    """
    async with await connect() as conn:
        cursor = conn.cursor(row_factory=class_row(Outbound))
        await cursor.execute(
            "INSERT INTO bank.outbound (server, tool, arguments, idempotency_key)"
            " VALUES (%s, %s, %s, %s) ON CONFLICT (server, idempotency_key) DO NOTHING"
            " RETURNING id, server, tool, arguments, recorded_at",
            (server, tool, Jsonb(arguments), idempotency_key),
        )
        row = await cursor.fetchone()
        if row is None:
            await cursor.execute(
                "SELECT id, server, tool, arguments, recorded_at FROM bank.outbound"
                " WHERE server = %s AND idempotency_key = %s",
                (server, idempotency_key),
            )
            row = await cursor.fetchone()
    assert row is not None
    return row


def read_outbound(database_url: str) -> list[Outbound]:
    """Everything recorded since the last reset, oldest first."""
    with psycopg.connect(database_url) as conn:
        cursor = conn.cursor(row_factory=class_row(Outbound))
        return cursor.execute(
            "SELECT id, server, tool, arguments, recorded_at FROM bank.outbound ORDER BY id"
        ).fetchall()
