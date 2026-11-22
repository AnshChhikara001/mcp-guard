"""Reveal: the chat UI shows a Person the real value behind a Pseudonym in their own chat.

    POST /reveal {"conversation": "<id>", "pseudonym": "<PHONE_1>"}  (UI token)
      ->  200 {"pseudonym": "<PHONE_1>", "value": "+91 9845012376"}
      or  403 {"error": "forbidden", "reason": "<code>", "explanation": "..."}

The Pseudonym must be one the Person's own conversation got (pseudonyms.py). Cedar then
decides whether they may see the value (permissions.py): a Customer sees their own
details, Staff those of the Customers they serve, a Supervisor everyone's.

Only a UI token reveals (ADR-0005). The MCP token, the only one the Agent holds, gets a 401,
so a prompt-injected Agent can't unmask PII. Such an attempt is logged too, under the Person
the token names; a request with no valid token names nobody, so it isn't.

Every reveal, allowed or refused, is an Audit entry with tool `reveal` and the Pseudonym as its
argument, never the value. Fails closed: a reveal the Gateway can't decide is refused, and one
the Audit log can't record shows nothing (503). A request whose body names no Pseudonym is not
a reveal (400).

The route exists only with the defences on: with them off, the Agent gets no Pseudonyms.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

import psycopg
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcp_guard.gateway import live, pseudonyms
from mcp_guard.gateway.decisions import Allow, Decision, Deny, fields_of
from mcp_guard.gateway.login import mcp_person, ui_person, ui_token_required
from mcp_guard.gateway.permissions import Permissions
from mcp_guard.gateway.pseudonyms import UNKNOWN_PSEUDONYM, Conversation
from mcp_guard.gateway.tokens import Person, Tokens

TOOL = "reveal"
AGENT_TOKEN = Deny(
    "gateway",
    "agent_token",
    "Only the chat UI's token can reveal a Pseudonym; the Agent's MCP token never can.",
)
REVEAL_FAILED = Deny(
    "gateway",
    "reveal_failed",
    "The Gateway couldn't look up the Pseudonym or who may see it, so it kept it hidden.",
)
EXPLANATIONS: Mapping[str, str] = {d.reason: d.explanation for d in (AGENT_TOKEN, REVEAL_FAILED)}


def routes(tokens: Tokens, permissions: Permissions, feed: live.Feed) -> list[Route]:
    async def reveal(request: Request) -> JSONResponse:
        person = ui_person(request, tokens)
        asked = await _asked(request)
        if person is None:
            agent = mcp_person(request, tokens)
            # A request with no valid token names nobody, so there is no one to log it under.
            if agent is not None and not await _recorded(feed, agent, asked, AGENT_TOKEN):
                return _audit_failed()
            return ui_token_required()
        if asked is None:
            return JSONResponse(
                {"error": 'Send {"conversation": "<id>", "pseudonym": "<TYPE_N>"}'},
                status_code=400,
            )
        decision, value = await _decide(permissions, person, asked)
        if not await _recorded(feed, person, asked, decision):
            return _audit_failed()
        if isinstance(decision, Allow) and value is not None:
            return JSONResponse({"pseudonym": asked.pseudonym, "value": value})
        fields = fields_of(decision)
        return JSONResponse(
            {
                "error": "forbidden",
                "reason": fields["reason"],
                "explanation": fields["explanation"],
            },
            status_code=403,
        )

    return [Route("/reveal", reveal, methods=["POST"])]


def _audit_failed() -> JSONResponse:
    """The 503 for a reveal the Audit log couldn't record: fail closed, show nothing."""
    return JSONResponse(
        {"error": "audit_failed", "error_description": "The reveal couldn't be recorded"},
        status_code=503,
    )


@dataclass(frozen=True)
class _Asked:
    """The Pseudonym a request asks to see, and the conversation it is in."""

    conversation: str
    pseudonym: str


async def _asked(request: Request) -> _Asked | None:
    """What the request asks to see, or None if its body doesn't say."""
    try:
        body: object = await request.json()
    except json.JSONDecodeError:
        return None
    if not isinstance(body, dict):
        return None
    fields = cast(dict[str, object], body)
    conversation, pseudonym = fields.get("conversation"), fields.get("pseudonym")
    if not isinstance(conversation, str) or not conversation or not isinstance(pseudonym, str):
        return None
    return _Asked(conversation, pseudonym)


async def _decide(
    permissions: Permissions, person: Person, asked: _Asked
) -> tuple[Decision, str | None]:
    """The Decision on one reveal, and the value if the conversation knows the Pseudonym."""
    try:
        value = await pseudonyms.value_of(
            Conversation(person.id, asked.conversation), asked.pseudonym
        )
        if value is None:
            return UNKNOWN_PSEUDONYM, None
        return await permissions.reveal(person, value), value
    except (psycopg.Error, OSError):
        return REVEAL_FAILED, None


async def _recorded(
    feed: live.Feed, person: Person, asked: _Asked | None, decision: Decision
) -> bool:
    """Whether the reveal's Audit entry was written: the Pseudonym, never the value."""
    conversation = asked.conversation if asked else None
    arguments = {"pseudonym": asked.pseudonym if asked else None}
    try:
        await feed.record(person, conversation, TOOL, arguments, decision)
    except (psycopg.Error, OSError):
        return False
    return True
