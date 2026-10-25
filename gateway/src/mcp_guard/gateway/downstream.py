"""The MCP servers behind the Gateway, and the connections it holds to them.

Each server is a child process of the Gateway speaking MCP over its stdin/stdout. None of
them opens a network port, so nothing but the Gateway can reach them, wherever it runs.
"""

import os
import sys
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from mcp import Client, StdioServerParameters
from mcp.types import CallToolResult, Tool

# Environment variables a downstream server is given. Everything else the Gateway has
# (keys, tokens) stays with the Gateway.
PASSED_ENVIRONMENT = ("DATABASE_URL",)

SERVERS: Mapping[str, list[str]] = {
    "accounts": ["-m", "mcp_guard.fake_servers.accounts"],
    "transactions": ["-m", "mcp_guard.fake_servers.transactions"],
    "payments": ["-m", "mcp_guard.fake_servers.payments"],
    "tickets": ["-m", "mcp_guard.fake_servers.tickets"],
    "notify": ["-m", "mcp_guard.fake_servers.notify"],
    "fx-rates": ["-m", "mcp_guard.fake_servers.fx_rates"],
}


@dataclass(frozen=True)
class Downstream:
    """Open connections to the downstream servers, and which server owns each tool."""

    _servers: Sequence[Client]
    _owners: Mapping[str, Client]

    async def list_tools(self) -> list[Tool]:
        """Every server's tools as defined right now: a server may change a definition at
        any time, and the Agent must see the change."""
        return [tool for client in self._servers for tool in await _all_tools(client)]

    def owns(self, tool_name: str) -> bool:
        return tool_name in self._owners

    async def call_tool(self, name: str, arguments: dict[str, object] | None) -> CallToolResult:
        return await self._owners[name].call_tool(name, arguments)


@asynccontextmanager
async def connect() -> AsyncGenerator[Downstream]:
    """Start every downstream server, learn which tools it owns, and stop them all on exit.

    Which server owns a tool is settled here, from each server's first tool listing.
    """
    environment = {name: os.environ[name] for name in PASSED_ENVIRONMENT if name in os.environ}
    servers: list[Client] = []
    owners: dict[str, Client] = {}
    async with AsyncExitStack() as stack:
        for name, args in SERVERS.items():
            params = StdioServerParameters(command=sys.executable, args=args, env=environment)
            # No response cache: the Gateway must always see a server's current definitions.
            client = await stack.enter_async_context(Client(params, cache=None))
            servers.append(client)
            for tool in await _all_tools(client):
                if tool.name in owners:
                    raise ValueError(f"Tool {tool.name!r} from {name!r} is already defined")
                owners[tool.name] = client
        yield Downstream(servers, owners)


async def _all_tools(client: Client) -> list[Tool]:
    tools: list[Tool] = []
    cursor: str | None = None
    while True:
        page = await client.list_tools(cursor=cursor)
        tools += page.tools
        cursor = page.next_cursor
        if cursor is None:
            return tools
