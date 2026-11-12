"""Pseudonyms: PII in Tool call results replaced by per-conversation Pseudonyms like <PHONE_1>.

With the defences on, the Agent, its model and its memory never see a Customer's PII (ADR 0002).
Before a result reaches the Agent, the Gateway replaces each piece of PII in it with a Pseudonym:
the type and a counter per type, `<PHONE_1>`, `<PHONE_2>`. The same exact text always gets the
same Pseudonym within one conversation; in another conversation `<PHONE_1>` means something else,
or nothing.

PII is found two ways. The fields in PII_FIELDS (a server's `phone`, `name`, account `number` and
so on) are always replaced whole. Every other string, such as a ticket's subject and body, is
scanned by Presidio (pii.py), with the field's name as context.

When the Agent passes a Pseudonym into a Tool call, restore() puts the real value back, in every
string argument at any depth, also inside a longer text, so the call does what the Agent meant.
A Pseudonym the conversation doesn't know is refused (UnknownPseudonym). Restoring doesn't stop
exfiltration: an allowed call carries the real value wherever it goes. Taint does that.

The Pseudonyms of each conversation live in Postgres, in `gateway.pseudonyms` beside the Audit
log, so they survive a Gateway restart like the Agent's conversations do. They belong to the
Person and the conversation together: the client names the conversation, so another Person who
names the same one gets none of them. A Tool call that names no conversation (an MCP client
without sessions) gets Pseudonyms that are never stored, so no later call can use them.

Known limit: text shaped like a Pseudonym in a result (a ticket that says "<AADHAAR_1>") reaches
the Agent as it is, and is restored if the Agent passes it on and the conversation knows it.
"""

import json
import os
import re
from collections import Counter
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Any, cast

import anyio
import psycopg
from mcp.types import CallToolResult, TextContent

from mcp_guard.gateway.decisions import Deny
from mcp_guard.gateway.pii import PiiDetector

LOCAL_DATABASE_URL = "postgresql://mcpguard:mcpguard@localhost:5432/mcpguard"
CONNECT_TIMEOUT_SECONDS = 5
LAYER = "redact"
LAYER_NAME = "Redact"

# Fields always replaced whole, by the server that returns them: field name -> Pseudonym type.
PII_FIELDS: Mapping[str, Mapping[str, str]] = {
    "accounts": {
        "name": "NAME",
        "date_of_birth": "DATE_OF_BIRTH",
        "email": "EMAIL",
        "phone": "PHONE",
        "address": "ADDRESS",
        "aadhaar": "AADHAAR",
        "pan": "PAN",
        "upi_id": "UPI_ID",
        "number": "ACCOUNT_NUMBER",
    },
}

# What Presidio finds, as Pseudonym types. An IFSC isn't here: it is a public branch code, not
# a Customer's own, so it stays readable.
TYPE_OF_ENTITY: Mapping[str, str] = {
    "PERSON": "NAME",
    "EMAIL_ADDRESS": "EMAIL",
    "IN_PHONE": "PHONE",
    "IN_AADHAAR": "AADHAAR",
    "IN_PAN": "PAN",
    "IN_UPI_ID": "UPI_ID",
    "IN_BANK_ACCOUNT": "ACCOUNT_NUMBER",
    "IN_GSTIN": "GSTIN",
    "IN_PASSPORT": "PASSPORT",
    "IN_VOTER": "VOTER_ID",
    "IN_VEHICLE_REGISTRATION": "VEHICLE_NUMBER",
}

_TYPES = sorted({*TYPE_OF_ENTITY.values(), *(t for f in PII_FIELDS.values() for t in f.values())})
# Only the types above count, so text like "<B_1>" in an argument is left alone.
PSEUDONYM = re.compile(rf"<(?:{'|'.join(_TYPES)})_[1-9][0-9]*>")

UNKNOWN_PSEUDONYM = Deny(
    LAYER,
    "unknown_pseudonym",
    "The call names a Pseudonym this conversation never got, so the Gateway can't tell what it"
    " stands for.",
)
REDACT_FAILED = Deny(
    LAYER,
    "redact_failed",
    "The Gateway couldn't look up or keep Pseudonyms, so it didn't make the call or return its"
    " result.",
)
EXPLANATIONS: Mapping[str, str] = {
    d.reason: d.explanation for d in (UNKNOWN_PSEUDONYM, REDACT_FAILED)
}

SETUP = """
SELECT pg_advisory_xact_lock(hashtext('gateway.pseudonyms setup'));

CREATE SCHEMA IF NOT EXISTS gateway;

CREATE TABLE IF NOT EXISTS gateway.pseudonyms (
    person       text NOT NULL,
    conversation text NOT NULL,
    pseudonym    text NOT NULL,
    value        text NOT NULL,
    PRIMARY KEY (person, conversation, pseudonym),
    UNIQUE (person, conversation, value)
);
"""


class UnknownPseudonym(Exception):
    """A Tool call names a Pseudonym its conversation doesn't know."""


@dataclass(frozen=True)
class Redacted:
    """A result as the Agent gets it, and how many pieces of PII of each type it held."""

    result: CallToolResult
    counts: Mapping[str, int]  # empty when the result held no PII


@dataclass(frozen=True)
class Conversation:
    """Whose Pseudonyms a Tool call uses: its Person's, in the conversation it names."""

    person: str
    id: str | None  # None when the call names no conversation


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    type: str


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", LOCAL_DATABASE_URL)


def setup() -> None:
    """Create the table that keeps each conversation's Pseudonyms, if missing."""
    with psycopg.connect(_database_url()) as conn:
        conn.execute(SETUP)


class Pseudonyms:
    """Replaces PII in results, and restores Pseudonyms in arguments, per conversation."""

    def __init__(self, detector: PiiDetector) -> None:
        self._detector = detector
        # spaCy isn't promised to be safe across threads, so one scan runs at a time.
        self._scanning = anyio.CapacityLimiter(1)

    async def restore(
        self, conversation: Conversation, arguments: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """The arguments with each Pseudonym replaced by its real value.

        Raises UnknownPseudonym if the conversation doesn't know one of them.
        """
        named = {p for _, text in _strings(arguments) for p in PSEUDONYM.findall(text)}
        if not named:
            return arguments
        values = await _values_of(conversation, named) if conversation.id else {}
        if named - values.keys():
            raise UnknownPseudonym
        return cast(
            dict[str, Any],
            _map_strings(arguments, lambda _, text: PSEUDONYM.sub(lambda m: values[m[0]], text)),
        )

    async def redact(
        self, conversation: Conversation, server: str | None, result: CallToolResult
    ) -> Redacted:
        """The result with each piece of PII replaced by its Pseudonym in this conversation."""
        fields = PII_FIELDS.get(server or "", {})
        # A text holding JSON is scanned field by field, so field names can serve as context.
        parsed = [_json(c.text) if isinstance(c, TextContent) else None for c in result.content]
        found: dict[tuple[str | None, str], list[_Span]] = {}

        def scan(key: str | None, text: str) -> str:
            if (key, text) not in found:
                found[key, text] = self._find(fields, key, text)
            return text

        def scan_all() -> None:
            _map_strings(result.structured_content, scan)
            for c, json_value in zip(result.content, parsed, strict=True):
                if json_value is not None:
                    _map_strings(json_value, scan)
                elif isinstance(c, TextContent):
                    scan(None, c.text)

        async with self._scanning:
            await anyio.to_thread.run_sync(scan_all)

        pieces: dict[str, str] = {}  # each value found, and its type; the first type found wins
        for (_, text), spans in found.items():
            for span in spans:
                pieces.setdefault(text[span.start : span.end], span.type)
        if not pieces:
            return Redacted(result, {})
        pseudonyms = (
            await _pseudonyms_for(conversation, pieces)
            if conversation.id
            else _number(pieces, {})  # not stored: no later call can restore them
        )

        def replace(key: str | None, text: str) -> str:
            for span in sorted(found[key, text], key=lambda s: -s.start):
                value = text[span.start : span.end]
                text = text[: span.start] + pseudonyms[value] + text[span.end :]
            return text

        content = [
            _replace_text(c, json_value, replace)
            for c, json_value in zip(result.content, parsed, strict=True)
        ]
        redacted = result.model_copy(
            update={
                "content": content,
                "structured_content": _map_strings(result.structured_content, replace),
            }
        )
        return Redacted(redacted, dict(Counter(pieces.values())))

    def _find(self, fields: Mapping[str, str], key: str | None, text: str) -> list[_Span]:
        """Where the PII in one string is: all of it, for a PII field, or what Presidio finds."""
        if key is not None and (whole := fields.get(key)):
            return [_Span(0, len(text), whole)] if text else []
        context = key.lower().split("_") if key else None
        return [
            _Span(m.start, m.end, TYPE_OF_ENTITY[m.entity_type])
            for m in self._detector.find(text, context)
            if m.entity_type in TYPE_OF_ENTITY
        ]


def _replace_text(
    content: Any, json_value: Any | None, replace: Callable[[str | None, str], str]
) -> Any:
    """One content item of a result with `replace` applied: field by field if it holds JSON."""
    if not isinstance(content, TextContent):
        return content
    if json_value is None:
        return content.model_copy(update={"text": replace(None, content.text)})
    text = json.dumps(_map_strings(json_value, replace), ensure_ascii=False, indent=2)
    return content.model_copy(update={"text": text})


async def reveal(conversation: Conversation, text: str, database_url: str | None = None) -> str:
    """The text with each Pseudonym the conversation knows replaced by its real value."""
    named = set(PSEUDONYM.findall(text))
    if not named or not conversation.id:
        return text
    values = await _values_of(conversation, named, database_url)
    return PSEUDONYM.sub(lambda m: values.get(m[0], m[0]), text)


async def _connect(database_url: str | None = None) -> psycopg.AsyncConnection[Any]:
    return await psycopg.AsyncConnection.connect(
        database_url or _database_url(), connect_timeout=CONNECT_TIMEOUT_SECONDS
    )


async def _values_of(
    conversation: Conversation, pseudonyms: Collection[str], database_url: str | None = None
) -> dict[str, str]:
    """The real value of each of `pseudonyms` the conversation knows."""
    async with await _connect(database_url) as conn:
        cursor = await conn.execute(
            "SELECT pseudonym, value FROM gateway.pseudonyms"
            " WHERE person = %s AND conversation = %s AND pseudonym = ANY(%s)",
            (conversation.person, conversation.id, list(pseudonyms)),
        )
        return dict(await cursor.fetchall())


async def _pseudonyms_for(conversation: Conversation, pieces: Mapping[str, str]) -> dict[str, str]:
    """The conversation's Pseudonym for each value in `pieces` (value -> type), storing a new
    one for each value it hasn't seen."""
    async with await _connect() as conn:
        # Calls of one conversation that run at once number their Pseudonyms one after another.
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"{conversation.person}/{conversation.id}",),
        )
        cursor = await conn.execute(
            "SELECT value, pseudonym FROM gateway.pseudonyms"
            " WHERE person = %s AND conversation = %s",
            (conversation.person, conversation.id),
        )
        known: dict[str, str] = dict(await cursor.fetchall())
        pseudonyms = _number(pieces, known)
        new = [
            (conversation.person, conversation.id, pseudonym, value)
            for value, pseudonym in pseudonyms.items()
            if value not in known
        ]
        if new:
            async with conn.cursor() as insert:
                await insert.executemany(
                    "INSERT INTO gateway.pseudonyms (person, conversation, pseudonym, value)"
                    " VALUES (%s, %s, %s, %s)",
                    new,
                )
    return pseudonyms


def _number(pieces: Mapping[str, str], known: Mapping[str, str]) -> dict[str, str]:
    """`known` (value -> Pseudonym) plus a Pseudonym for each new value in `pieces` (value ->
    type), numbered after the last one of its type."""
    numbered = dict(known)
    used = Counter(p[1 : p.rindex("_")] for p in known.values())
    for value, kind in pieces.items():
        if value not in numbered:
            used[kind] += 1
            numbered[value] = f"<{kind}_{used[kind]}>"
    return numbered


def _json(text: str) -> Any | None:
    """The JSON object or array the text holds, or None if it holds something else."""
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return cast(Any, parsed) if isinstance(parsed, dict | list) else None


def _strings(value: Any, key: str | None = None) -> list[tuple[str | None, str]]:
    """Every string inside a JSON value, with the name of the field that holds it."""
    found: list[tuple[str | None, str]] = []

    def keep(k: str | None, text: str) -> str:
        found.append((k, text))
        return text

    _map_strings(value, keep, key)
    return found


def _map_strings(
    value: Any, change: Callable[[str | None, str], str], key: str | None = None
) -> Any:
    """A copy of a JSON value with `change(field name, text)` applied to every string inside it.

    A string in a list has the name of the field that holds the list.
    """
    if isinstance(value, str):
        return change(key, value)
    if isinstance(value, dict):
        return {k: _map_strings(v, change, k) for k, v in cast(dict[str, Any], value).items()}
    if isinstance(value, list):
        return [_map_strings(v, change, key) for v in cast(list[Any], value)]
    return value
