"""How a person reaches the Gateway: logging in, and an MCP client that carries their token.

The caller logs in and gives the Agent only the person and their MCP token, which it sends as
`Authorization: Bearer` on every request to the Gateway; it never reads what is inside. The
login reply's UI token is dropped here: only the chat UI holds one (ADR-0005).
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from mcp_guard.agent.person import Person

# The MCP SDK's own defaults: a Gateway's reply stream may stay open a long time.
TIMEOUT = httpx2.Timeout(30, read=300)


@dataclass(frozen=True)
class Login:
    person: Person
    mcp_token: str


class LoginRefused(Exception):
    """The Gateway's demo issuer doesn't know the person."""


async def login(gateway_url: str, person_id: str) -> Login:
    """Log in to the Gateway whose MCP endpoint is `gateway_url`, as `person_id` (C001, S003)."""
    async with httpx.AsyncClient() as http:
        reply = await http.post(urljoin(gateway_url, "/demo/login"), json={"person": person_id})
    if reply.status_code == 401:
        raise LoginRefused(f"The Gateway refused to log in {person_id}: {reply.json()['error']}")
    reply.raise_for_status()
    body = reply.json()
    return Login(Person(body["person"], body["role"]), body["mcp_token"])


@asynccontextmanager
async def connect(gateway_url: str, mcp_token: str) -> AsyncGenerator[Client]:
    """An MCP client of the Gateway that sends `mcp_token` with every request."""
    headers = {"Authorization": f"Bearer {mcp_token}"}
    async with httpx2.AsyncClient(headers=headers, timeout=TIMEOUT) as http:
        transport = streamable_http_client(gateway_url, http_client=http)
        # No response cache: a tool's definition can change between two model calls.
        async with Client(transport, cache=None) as client:
            yield client
