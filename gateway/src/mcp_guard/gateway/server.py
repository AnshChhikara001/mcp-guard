"""The Gateway's MCP server: what an Agent connects to.

It lists the downstream servers' tools as its own, asking them afresh on every listing, and
makes a Decision on every Tool call: Cedar policies (permissions.py) allow it, and it goes,
unchanged, to the server that owns the tool, or deny it. A denied call never reaches the
server: the Agent gets an error result with a reason code and a reason it can explain.

Run it with `make gateway`, or `uv run mcp-guard-gateway` (GATEWAY_HOST and GATEWAY_PORT
pick the address; the default is http://127.0.0.1:8000/mcp). GATEWAY_DEFENCES is `on` (the
default) or `off`, so the Red-team eval can measure the Gateway with and without its
defences; with them off, no policy is asked and every call is allowed.

Every MCP request must carry the person's MCP token (see tokens.py): the MCP SDK's bearer auth
refuses one without it, so the Gateway knows the person and role behind every Tool call. The
login check runs with the defences on or off: it is identity, not a defence. The Gateway's
HTTP endpoints (login.py) are served beside /mcp.

The MCP SDK traces every Tool call: a span for the call the Gateway receives, joined to the
caller's trace through the request's `_meta`, and inside it a span for the call to the
downstream server. The Gateway's span records who made the call (`user.id`, `user.roles`) and
its Decision (`gateway.decision`, `gateway.decision.reason` and `gateway.decision.layer`, the
layer that decided). Spans are sent only if tracing is configured (see tracing.py).
"""

import os
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import uvicorn
from mcp.server import Server, ServerRequestContext
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.settings import AuthSettings
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

from mcp_guard.gateway import login
from mcp_guard.gateway.decisions import Allow, Decision, Deny
from mcp_guard.gateway.downstream import Downstream, connect
from mcp_guard.gateway.permissions import Permissions, PolicyError, configured_policies_dir
from mcp_guard.gateway.tokens import McpTokenVerifier, Person, Tokens, person_of, signing_key
from mcp_guard.gateway.tracing import start_tracing, stop_tracing

ALLOWED_WITH_DEFENCES_OFF = Allow("defences_off")


@dataclass(frozen=True)
class Gateway:
    """What every request handler needs: the downstream servers, and the policies."""

    downstream: Downstream
    permissions: Permissions | None  # None with the defences off


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
    decision: Decision
    if not gateway.downstream.owns(params.name):
        decision = Deny("gateway", "unknown_tool", f"No server has a tool called {params.name}.")
    elif gateway.permissions is None:
        decision = ALLOWED_WITH_DEFENCES_OFF
    else:
        decision = await gateway.permissions.decide(person, params.name, params.arguments)
    span.set_attributes(_decision_attributes(decision))
    match decision:
        case Allow():
            return await gateway.downstream.call_tool(params.name, params.arguments)
        case Deny(reason=reason, explanation=explanation):
            return CallToolResult(
                content=[TextContent(text=f"Denied by the Gateway ({reason}): {explanation}")],
                is_error=True,
            )


def _decision_attributes(decision: Decision) -> dict[str, str]:
    match decision:
        case Allow(layer=layer):
            return {"gateway.decision": "allow", "gateway.decision.layer": layer}
        case Deny(layer=layer, reason=reason):
            return {
                "gateway.decision": "deny",
                "gateway.decision.reason": reason,
                "gateway.decision.layer": layer,
            }


def _person(ctx: ServerRequestContext[Gateway]) -> Person:
    """Who makes the request, from the MCP token the SDK's bearer auth accepted.

    The Gateway trusts the signed claims and doesn't look the role up.
    """
    request = ctx.request
    user = request.scope.get("user") if isinstance(request, Request) else None
    if not isinstance(user, AuthenticatedUser):
        raise RuntimeError("A request reached the Gateway without an accepted MCP token")
    return person_of(user.access_token)


def app(tokens: Tokens, permissions: Permissions | None, host: str = "127.0.0.1") -> Starlette:
    """The Gateway as an ASGI app, serving MCP over streamable HTTP at /mcp.

    With `permissions` None, the defences are off.
    """

    @asynccontextmanager
    async def lifespan(_: Server[Gateway]) -> AsyncGenerator[Gateway]:
        try:
            async with connect() as downstream:
                yield Gateway(downstream, permissions)
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
        custom_starlette_routes=login.routes(tokens),
    )


DEFENCES_SETTINGS = ("on", "off")


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
    start_tracing("mcp-guard-gateway")
    gateway = app(Tokens(key, f"http://{host}:{port}"), permissions, host)
    uvicorn.run(gateway, host=host, port=port)


if __name__ == "__main__":
    main()
