"""The Gateway's MCP server: what an Agent connects to.

It lists the downstream servers' tools as its own, asking them afresh on every listing, and
forwards every Tool call, unchanged, to the server that owns the tool. There is no Decision
yet: every call is allowed.

Run it with `make gateway`, or `uv run mcp-guard-gateway` (GATEWAY_HOST and GATEWAY_PORT
pick the address; the default is http://127.0.0.1:8000/mcp). GATEWAY_DEFENCES is `on` (the
default) or `off`, so the Red-team eval can measure the Gateway with and without its
defences; there are no defences yet, so for now it changes nothing.

Every MCP request must carry the person's MCP token (see tokens.py): the MCP SDK's bearer auth
refuses one without it, so the Gateway knows the person and role behind every Tool call. The
login check runs with the defences on or off: it is identity, not a defence. The Gateway's
HTTP endpoints (login.py) are served beside /mcp.

The MCP SDK traces every Tool call: a span for the call the Gateway receives, joined to the
caller's trace through the request's `_meta`, and inside it a span for the call to the
downstream server. The Gateway's span records who made the call (`user.id`, `user.roles`).
Spans are sent only if tracing is configured (see tracing.py).
"""

import os
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

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
from mcp_guard.gateway.downstream import Downstream, connect
from mcp_guard.gateway.tokens import (
    Identity,
    McpTokenVerifier,
    Tokens,
    identity_of,
    signing_key,
)
from mcp_guard.gateway.tracing import start_tracing, stop_tracing


async def list_tools(
    ctx: ServerRequestContext[Downstream], params: PaginatedRequestParams | None
) -> ListToolsResult:
    return ListToolsResult(tools=await ctx.lifespan_context.list_tools())


async def call_tool(
    ctx: ServerRequestContext[Downstream], params: CallToolRequestParams
) -> CallToolResult:
    caller = _caller(ctx)
    trace.get_current_span().set_attributes({"user.id": caller.person, "user.roles": [caller.role]})
    downstream = ctx.lifespan_context
    if not downstream.owns(params.name):
        return CallToolResult(
            content=[TextContent(text=f"Unknown tool: {params.name}")], is_error=True
        )
    return await downstream.call_tool(params.name, params.arguments)


def _caller(ctx: ServerRequestContext[Downstream]) -> Identity:
    """Who makes the request, from the MCP token the SDK's bearer auth accepted.

    The Gateway trusts the signed claims and doesn't look the role up.
    """
    request = ctx.request
    user = request.scope.get("user") if isinstance(request, Request) else None
    if not isinstance(user, AuthenticatedUser):
        raise RuntimeError("A request reached the Gateway without an accepted MCP token")
    return identity_of(user.access_token)


@asynccontextmanager
async def lifespan(_: Server[Downstream]) -> AsyncGenerator[Downstream]:
    try:
        async with connect() as downstream:
            yield downstream
    finally:
        # uvicorn ends the process with the signal that stopped it, so exit hooks never run.
        stop_tracing()


def app(tokens: Tokens, host: str = "127.0.0.1") -> Starlette:
    """The Gateway as an ASGI app, serving MCP over streamable HTTP at /mcp."""
    server = Server[Downstream](
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
    except ValueError as e:
        sys.exit(str(e))
    start_tracing("mcp-guard-gateway")
    uvicorn.run(app(Tokens(key, f"http://{host}:{port}"), host), host=host, port=port)


if __name__ == "__main__":
    main()
