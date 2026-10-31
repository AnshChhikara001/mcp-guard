"""The Audit log: one hash-chained Audit entry for the Decision on every Tool call.

Entries live in Postgres, in the `gateway.audit` table of the Gateway's own `gateway` schema,
so resetting the bank leaves them alone. The Gateway creates the schema, table and trigger at
startup if they are missing (setup()). It only ever inserts: a trigger rejects any update,
delete or truncate, so changing an entry takes a database admin who turns the trigger off.

Each entry's hash is SHA-256 over canonical JSON (sorted keys, no spaces) of its fields and the
previous entry's hash; the first entry's previous hash is 64 zeros. verify() walks the chain
and names the first entry whose hash or link doesn't match. The hash has no key, so someone
who edits an entry and then rewrites every hash after it goes undetected, and deleting the
newest entry leaves no break. Entries are written one at a time, under a table lock, and
numbered in order.
"""

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, LiteralString

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from mcp_guard.gateway.decisions import Allow, Decision, Deny
from mcp_guard.gateway.tokens import Person

LOCAL_DATABASE_URL = "postgresql://mcpguard:mcpguard@localhost:5432/mcpguard"
FIRST_PREVIOUS_HASH = "0" * 64
PAGE_SIZE = 50
# A database that doesn't answer denies the call soon, rather than holding it up.
CONNECT_TIMEOUT_SECONDS = 5

SETUP = """
SELECT pg_advisory_xact_lock(hashtext('gateway.audit setup'));

CREATE SCHEMA IF NOT EXISTS gateway;

CREATE TABLE IF NOT EXISTS gateway.audit (
    id            bigint PRIMARY KEY,
    time          timestamptz NOT NULL,
    person        text NOT NULL,
    role          text NOT NULL,
    conversation  text,
    tool          text NOT NULL,
    arguments     json NOT NULL,  -- json, not jsonb: it keeps the text, so numbers rehash the same
    decision      text NOT NULL,
    reason        text,
    layer         text NOT NULL,
    previous_hash text NOT NULL,
    hash          text NOT NULL
);

CREATE OR REPLACE FUNCTION gateway.audit_is_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'The Audit log is append-only: % is not allowed', TG_OP;
END;
$$;

CREATE OR REPLACE TRIGGER audit_is_append_only
    BEFORE UPDATE OR DELETE ON gateway.audit
    FOR EACH ROW EXECUTE FUNCTION gateway.audit_is_append_only();

CREATE OR REPLACE TRIGGER audit_is_never_truncated
    BEFORE TRUNCATE ON gateway.audit
    FOR EACH STATEMENT EXECUTE FUNCTION gateway.audit_is_append_only();
"""

COLUMNS: LiteralString = (
    "id, time, person, role, conversation, tool, arguments, decision, reason, layer,"
    " previous_hash, hash"
)
PLACEHOLDERS: LiteralString = "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s"


@dataclass(frozen=True)
class Entry:
    """One Audit entry: who made which Tool call, in which conversation, and the Decision."""

    id: int
    time: datetime
    person: str
    role: str
    conversation: str | None
    tool: str
    arguments: dict[str, Any]
    decision: str  # allow or deny
    reason: str | None  # the reason code of a deny
    layer: str  # the layer that decided
    previous_hash: str
    hash: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self) | {"time": self.time.astimezone(UTC).isoformat()}


@dataclass(frozen=True)
class Verified:
    """What verify() found: the number of entries, and the first broken one if any."""

    entries: int
    first_broken: int | None

    def to_json(self) -> dict[str, Any]:
        if self.first_broken is None:
            return {"intact": True, "entries": self.entries}
        return {"intact": False, "first_broken": self.first_broken}


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", LOCAL_DATABASE_URL)


def setup() -> None:
    """Create the `gateway` schema, the Audit log's table and its trigger, if missing."""
    with psycopg.connect(_database_url()) as conn:
        conn.execute(SETUP)


def entry_hash(fields: Mapping[str, Any]) -> str:
    """SHA-256 of canonical JSON of an entry's fields (with its previous hash, not its own)."""
    canonical = json.dumps(
        {name: value for name, value in fields.items() if name != "hash"},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_isoformat,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _isoformat(value: object) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat(timespec="microseconds")
    raise TypeError(f"Can't hash a {type(value).__name__}")


async def record(
    person: Person,
    conversation: str | None,
    tool: str,
    arguments: dict[str, Any] | None,
    decision: Decision,
) -> int:
    """Append the Decision on one Tool call to the Audit log; the new entry's id.

    `arguments` are stored as sent; a call sent with none is stored with `{}`.
    """
    match decision:
        case Allow(layer=layer):
            outcome, reason = "allow", None
        case Deny(layer=layer, reason=reason):
            outcome = "deny"
    async with await psycopg.AsyncConnection.connect(
        _database_url(), connect_timeout=CONNECT_TIMEOUT_SECONDS
    ) as conn:
        # One writer at a time, so each entry links to the one before it. Readers aren't held up.
        await conn.execute("LOCK TABLE gateway.audit IN EXCLUSIVE MODE")
        cursor = await conn.execute("SELECT id, hash FROM gateway.audit ORDER BY id DESC LIMIT 1")
        last = await cursor.fetchone()
        previous_id, previous_hash = last if last else (0, FIRST_PREVIOUS_HASH)
        fields: dict[str, Any] = {
            "id": previous_id + 1,
            "time": datetime.now(UTC),
            "person": person.id,
            "role": person.role,
            "conversation": conversation,
            "tool": tool,
            "arguments": arguments or {},
            "decision": outcome,
            "reason": reason,
            "layer": layer,
            "previous_hash": previous_hash,
        }
        fields["hash"] = entry_hash(fields)
        await conn.execute(
            f"INSERT INTO gateway.audit ({COLUMNS}) VALUES ({PLACEHOLDERS})",
            [Json(v) if k == "arguments" else v for k, v in fields.items()],
        )
    return fields["id"]


async def read(before: int | None = None, limit: int = PAGE_SIZE) -> list[Entry]:
    """Up to `limit` entries, newest first, all numbered below `before` if it is given."""
    async with await psycopg.AsyncConnection.connect(
        _database_url(), connect_timeout=CONNECT_TIMEOUT_SECONDS
    ) as conn:
        cursor = conn.cursor(row_factory=dict_row)
        await cursor.execute(
            f"SELECT {COLUMNS} FROM gateway.audit WHERE id < %s ORDER BY id DESC LIMIT %s",
            (before if before is not None else 2**63 - 1, limit),
        )
        return [Entry(**row) for row in await cursor.fetchall()]


async def verify() -> Verified:
    """Walk the chain from the first entry; the first whose hash or link is wrong is broken."""
    async with await psycopg.AsyncConnection.connect(
        _database_url(), connect_timeout=CONNECT_TIMEOUT_SECONDS
    ) as conn:
        cursor = conn.cursor(row_factory=dict_row)
        await cursor.execute(f"SELECT {COLUMNS} FROM gateway.audit ORDER BY id")
        expected_previous = FIRST_PREVIOUS_HASH
        count = 0
        async for row in cursor:
            count += 1
            if row["previous_hash"] != expected_previous or entry_hash(row) != row["hash"]:
                return Verified(count, row["id"])
            expected_previous = row["hash"]
    return Verified(count, None)
