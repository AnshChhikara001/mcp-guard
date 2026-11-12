"""The Gateway's MCP server: what an Agent connects to.

It lists the downstream servers' tools as its own, asking them afresh on every listing, and
makes a Decision on every Tool call: Cedar policies (permissions.py) allow it, and it goes to
the server that owns the tool, or deny it. A denied call never reaches the server: the Agent
gets an error result with a reason code and a reason it can explain.

With the defences on, the Agent never sees PII (pseudonyms.py). The Gateway first restores the
real values of the Pseudonyms the call names, so Cedar decides on the call as it will be made,
and denies a call naming a Pseudonym its conversation doesn't know. A result that held PII
reaches the Agent with Pseudonyms in its place, and makes a second Audit entry, Decision
`redact`; the result's Decision is then that one. The Audit log keeps the arguments as sent,
with their Pseudonyms.

Run it with `make gateway`, or `uv run mcp-guard-gateway` (GATEWAY_HOST and GATEWAY_PORT
pick the address; the default is http://127.0.0.1:8000/mcp). GATEWAY_DEFENCES is `on` (the
default) or `off`, so the Red-team eval can measure the Gateway with and without its
defences; with them off, no policy is asked and every call is allowed.

Every MCP request must carry the person's MCP token (see tokens.py): the MCP SDK's bearer auth
refuses one without it, so the Gateway knows the person and role behind every Tool call. The
login check runs with the defences on or off: it is identity, not a defence. The Gateway's
HTTP endpoints (login.py, audit_api.py) are served beside /mcp.

Every Decision is written to the Audit log (audit.py) before an allowed call is forwarded,
with the conversation the client names in the call's `_meta` (`mcp-guard/conversation`) or
else its MCP session. A call the log can't record is denied, and a result whose redact the log
can't record is withheld. Each new entry is also sent live
to whoever watches `/events` (live.py), with the deny's explanation rebuilt from its reason.

Every Tool call result, allowed or denied, carries the Decision in its `_meta`
(`mcp-guard/decision`): decision, reason, explanation, layer, and `audit_id`, the number of
its Audit entry (null if the log couldn't record it), so a step in the chat links to its entry.

The MCP SDK traces every Tool call: a span for the call the Gateway receives, joined to the
caller's trace through the request's `_meta`, and inside it a span for the call to the
downstream server. The Gateway's span records who made the call (`user.id`, `user.roles`) and
its Decision (`gateway.decision`, `gateway.decision.reason` and `gateway.decision.layer`, the
layer that decided), and the number of its Audit entry (`gateway.audit.id`). Spans are sent
only if tracing is configured (see tracing.py).
"""

import os
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import psycopg
import uvicorn
from mcp.server import Server, ServerRequestContext
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.settings import AuthSettings
from mcp.server.streamable_http import MCP_SESSION_ID_HEADER
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
)
from opentelemetry import trace
from starlette.applications import Starlette
from starlette.requests import Request

from mcp_guard.gateway import audit, audit_api, live, login, pseudonyms
from mcp_guard.gateway.audit import Entry
from mcp_guard.gateway.decisions import (
    REDACT_EXPLANATION,
    REDACT_REASON,
    Allow,
    Decision,
    Deny,
    Redact,
    fields_of,
)
from mcp_guard.gateway.downstream import Downstream, connect
from mcp_guard.gateway.permissions import (
    EXPLANATIONS,
    LAYER,
    LAYER_NAME,
    Permissions,
    PolicyError,
    configured_policies_dir,
)
from mcp_guard.gateway.pii import PiiDetector
from mcp_guard.gateway.pseudonyms import (
    REDACT_FAILED,
    UNKNOWN_PSEUDONYM,
    Conversation,
    Pseudonyms,
    UnknownPseudonym,
)
from mcp_guard.gateway.tokens import McpTokenVerifier, Person, Tokens, person_of, signing_key
from mcp_guard.gateway.tracing import start_tracing, stop_tracing

ALLOWED_WITH_DEFENCES_OFF = Allow("defences_off")
AUDIT_FAILED = Deny(
    "gateway", "audit_failed", "The Gateway couldn't record this call, so it wasn't made."
)
RESULT_AUDIT_FAILED = Deny(
    "gateway",
    "audit_failed",
    "The Gateway couldn't record what it hid in this call's result, so it withheld the result.",
)

# Where a client names the conversation a Tool call belongs to, in the call's `_meta`.
CONVERSATION_META_KEY = "mcp-guard/conversation"
# Where the Gateway puts its Decision on a Tool call, in the result's `_meta`.
DECISION_META_KEY = "mcp-guard/decision"


@dataclass(frozen=True)
class Gateway:
    """What every request handler needs: the downstream servers, the policies and Pseudonyms."""

    downstream: Downstream
    permissions: Permissions | None  # None with the defences off
    redactor: Pseudonyms | None  # None with the defences off
    feed: live.Feed


async def list_tools(
    ctx: ServerRequestContext[Gateway], params: PaginatedRequestParams | None
) -> ListToolsResult:
    return ListToolsResult(tools=await ctx.lifespan_context.downstream.list_tools())


async def call_tool(
    ctx: ServerRequestContext[Gateway], params: CallToolRequestParams
) -> CallToolResult:
    person = _person(ctx)
    span = trace.get_current_span()
    span.set_attributes({"user.id": person.id, "user.roles": [person.role]})
    gateway = ctx.lifespan_context
    conversation = _conversation(ctx, params)
    arguments = params.arguments  # as the call will be made, with Pseudonyms restored
    decision: Decision
    if not gateway.downstream.owns(params.name):
        decision = _unknown_tool(params.name)
    elif gateway.permissions is None or gateway.redactor is None:
        decision = ALLOWED_WITH_DEFENCES_OFF
    else:
        try:
            arguments = await gateway.redactor.restore(
                Conversation(person.id, conversation), arguments
            )
            decision = await gateway.permissions.decide(person, params.name, arguments)
        except UnknownPseudonym:
            decision = UNKNOWN_PSEUDONYM
        except (psycopg.Error, OSError):
            decision = REDACT_FAILED
    entry_id: int | None = None
    try:
        # The log keeps the arguments as sent, so it never holds what the Pseudonyms hide.
        entry = await gateway.feed.record(
            person, conversation, params.name, params.arguments, decision
        )
        entry_id = entry.id
    except (psycopg.Error, OSError):
        decision = AUDIT_FAILED  # fail closed: a call the Audit log can't record isn't made
    if isinstance(decision, Allow):
        result = await gateway.downstream.call_tool(params.name, arguments)
        if gateway.redactor is not None:
            decision, entry_id, result = await _redact(
                gateway,
                gateway.redactor,
                person,
                conversation,
                params,
                result,
                decision,
                entry_id,
            )
    else:
        result = _refused(decision)
    if entry_id is not None:
        span.set_attribute("gateway.audit.id", entry_id)
    span.set_attributes(_decision_attributes(decision))
    meta = {**(result.meta or {}), DECISION_META_KEY: _decision_meta(decision, entry_id)}
    return result.model_copy(update={"meta": meta})


async def _redact(
    gateway: Gateway,
    redactor: Pseudonyms,
    person: Person,
    conversation: str | None,
    params: CallToolRequestParams,
    result: CallToolResult,
    allowed: Allow,
    entry_id: int | None,
) -> tuple[Decision, int | None, CallToolResult]:
    """The result as the Agent gets it, with Pseudonyms for its PII, and the Decision and Audit
    entry it carries: a new `redact` entry if it held PII. Fails closed: a result whose PII the
    Gateway can't replace, or whose redact it can't record, is withheld."""
    try:
        redacted = await redactor.redact(
            Conversation(person.id, conversation), gateway.downstream.server_of(params.name), result
        )
    except (psycopg.Error, OSError):
        return REDACT_FAILED, entry_id, _refused(REDACT_FAILED)
    if not redacted.counts:
        return allowed, entry_id, redacted.result
    decision = Redact(pseudonyms.LAYER, redacted.counts)
    try:
        entry = await gateway.feed.record(
            person, conversation, params.name, params.arguments, decision
        )
    except (psycopg.Error, OSError):
        return RESULT_AUDIT_FAILED, entry_id, _refused(RESULT_AUDIT_FAILED)
    return decision, entry.id, redacted.result


def _refused(decision: Decision) -> CallToolResult:
    """The error result the Agent gets instead of a call's result."""
    fields = fields_of(decision)
    return CallToolResult(
        content=[
            TextContent(text=f"Denied by the Gateway ({fields['reason']}): {fields['explanation']}")
        ],
        is_error=True,
    )


def _unknown_tool(tool: str) -> Deny:
    return Deny("gateway", "unknown_tool", f"No server has a tool called {tool}.")


def _explain(entry: Entry) -> str | None:
    """A logged deny's explanation, rebuilt from its reason code (the log doesn't keep it)."""
    if entry.reason == "unknown_tool":
        return _unknown_tool(entry.tool).explanation
    explanations = {**EXPLANATIONS, **pseudonyms.EXPLANATIONS, REDACT_REASON: REDACT_EXPLANATION}
    return explanations.get(entry.reason) if entry.reason else None


def _decision_meta(decision: Decision, entry_id: int | None) -> dict[str, object]:
    """The Decision as the result's `_meta` carries it, with its Audit entry's number."""
    return {**fields_of(decision), "audit_id": entry_id}


def _decision_attributes(decision: Decision) -> dict[str, str]:
    decided = fields_of(decision)
    attributes = {"gateway.decision.reason": decided["reason"]} if decided["reason"] else {}
    return attributes | {
        "gateway.decision": str(decided["decision"]),
        "gateway.decision.layer": str(decided["layer"]),
    }


def _conversation(ctx: ServerRequestContext[Gateway], params: CallToolRequestParams) -> str | None:
    """The conversation the call belongs to: the one the client names in the call's `_meta`,
    or else its MCP session."""
    named = (params.meta or {}).get(CONVERSATION_META_KEY)
    if isinstance(named, str) and named:
        return named
    request = ctx.request
    return request.headers.get(MCP_SESSION_ID_HEADER) if isinstance(request, Request) else None


def _person(ctx: ServerRequestContext[Gateway]) -> Person:
    """Who makes the request, from the MCP token the SDK's bearer auth accepted.

    The Gateway trusts the signed claims and doesn't look the role up.
    """
    request = ctx.request
    user = request.scope.get("user") if isinstance(request, Request) else None
    if not isinstance(user, AuthenticatedUser):
        raise RuntimeError("A request reached the Gateway without an accepted MCP token")
    return person_of(user.access_token)


def app(
    tokens: Tokens,
    permissions: Permissions | None,
    redactor: Pseudonyms | None,
    host: str = "127.0.0.1",
) -> Starlette:
    """The Gateway as an ASGI app, serving MCP over streamable HTTP at /mcp.

    With `permissions` and `redactor` None, the defences are off.
    """
    # In the order a Tool call meets them: Pseudonyms are restored before Cedar decides.
    layers = (
        []
        if permissions is None
        else [live.Layer(pseudonyms.LAYER, pseudonyms.LAYER_NAME), live.Layer(LAYER, LAYER_NAME)]
    )
    feed = live.Feed(_explain)

    @asynccontextmanager
    async def lifespan(_: Server[Gateway]) -> AsyncGenerator[Gateway]:
        try:
            async with connect() as downstream:
                feed.learn_servers(downstream.server_of)
                yield Gateway(downstream, permissions, redactor, feed)
        finally:
            # uvicorn ends the process with the signal that stopped it, so exit hooks never run.
            stop_tracing()

    server = Server[Gateway](
        "mcp-guard",
        lifespan=lifespan,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
    return server.streamable_http_app(
        host=host,
        # The SDK's bearer auth refuses MCP requests without a valid MCP token, with a 401
        # pointing to the protected resource metadata. The verifier checks the audience itself.
        # Given as strings, the URLs keep their exact form (no slash added after the port).
        auth=AuthSettings.model_validate(
            {
                "issuer_url": tokens.issuer,
                "resource_server_url": tokens.mcp_url,
                "validate_token_resource": False,
            }
        ),
        token_verifier=McpTokenVerifier(tokens),
        custom_starlette_routes=[
            *login.routes(tokens),
            *audit_api.routes(tokens),
            *live.routes(tokens, layers, feed),
        ],
    )


DEFENCES_SETTINGS = ("on", "off")
SHUTDOWN_SECONDS = 3


def main() -> None:
    defences = os.environ.get("GATEWAY_DEFENCES", "on")
    if defences not in DEFENCES_SETTINGS:
        sys.exit(f"GATEWAY_DEFENCES must be on or off, not {defences!r}")
    host = os.environ.get("GATEWAY_HOST", "127.0.0.1")
    port = int(os.environ.get("GATEWAY_PORT", "8000"))
    try:
        key = signing_key(os.environ.get("GATEWAY_SIGNING_KEY"))
        permissions = Permissions(configured_policies_dir()) if defences == "on" else None
    except (ValueError, PolicyError) as e:
        sys.exit(str(e))
    try:
        audit.setup()
        pseudonyms.setup()
    except psycopg.Error as e:
        sys.exit(f"Couldn't set up the Audit log and Pseudonyms in DATABASE_URL: {e}")
    # Loading the spaCy model takes a few seconds, so it happens once, before serving.
    redactor = Pseudonyms(PiiDetector()) if defences == "on" else None
    start_tracing("mcp-guard-gateway")
    gateway = app(Tokens(key, f"http://{host}:{port}"), permissions, redactor, host)
    # Open /events streams never end by themselves, so shutdown doesn't wait for them long.
    uvicorn.run(gateway, host=host, port=port, timeout_graceful_shutdown=SHUTDOWN_SECONDS)


if __name__ == "__main__":
    main()
