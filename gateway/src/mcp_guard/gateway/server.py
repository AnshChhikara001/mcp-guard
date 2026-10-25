"""The Gateway's MCP server: what an Agent connects to.

It lists the downstream servers' tools as its own, asking them afresh on every listing, and
forwards every Tool call, unchanged, to the server that owns the tool. There is no Decision
yet: every call is allowed.

Run it with `make gateway`, or `uv run mcp-guard-gateway` (GATEWAY_HOST and GATEWAY_PORT
pick the address; the default is http://127.0.0.1:8000/mcp). GATEWAY_DEFENCES is `on` (the
default) or `off`, so the Red-team eval can measure the Gateway with and without its
defences; there are no defences yet, so for now it changes nothing.

The MCP SDK traces every Tool call: a span for the call the Gateway receives, joined to the
caller's trace through the request's `_meta`, and inside it a span for the call to the
downstream server. They are sent only if tracing is configured (see tracing.py).
"""

import os
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from mcp.server import Server, ServerRequestContext
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
)
from starlette.applications import Starlette

from mcp_guard.gateway.downstream import Downstream, connect
from mcp_guard.gateway.tracing import start_tracing, stop_tracing


async def list_tools(
    ctx: ServerRequestContext[Downstream], params: PaginatedRequestParams | None
) -> ListToolsResult:
    return ListToolsResult(tools=await ctx.lifespan_context.list_tools())


async def call_tool(
    ctx: ServerRequestContext[Downstream], params: CallToolRequestParams
) -> CallToolResult:
    downstream = ctx.lifespan_context
    if not downstream.owns(params.name):
        return CallToolResult(
            content=[TextContent(text=f"Unknown tool: {params.name}")], is_error=True
        )
    return await downstream.call_tool(params.name, params.arguments)


@asynccontextmanager
async def lifespan(_: Server[Downstream]) -> AsyncGenerator[Downstream]:
    try:
        async with connect() as downstream:
            yield downstream
    finally:
        # uvicorn ends the process with the signal that stopped it, so exit hooks never run.
        stop_tracing()


def app(host: str = "127.0.0.1") -> Starlette:
    """The Gateway as an ASGI app, serving MCP over streamable HTTP at /mcp."""
    server = Server[Downstream](
        "mcp-guard",
        lifespan=lifespan,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
    return server.streamable_http_app(host=host)


DEFENCES_SETTINGS = ("on", "off")


def main() -> None:
    defences = os.environ.get("GATEWAY_DEFENCES", "on")
    if defences not in DEFENCES_SETTINGS:
        sys.exit(f"GATEWAY_DEFENCES must be on or off, not {defences!r}")
    host = os.environ.get("GATEWAY_HOST", "127.0.0.1")
    port = int(os.environ.get("GATEWAY_PORT", "8000"))
    start_tracing("mcp-guard-gateway")
    uvicorn.run(app(host), host=host, port=port)


if __name__ == "__main__":
    main()
