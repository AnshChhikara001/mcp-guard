"""Fixtures shared by every package's tests. Databases need `make db` locally."""

import json
import os
import threading
import time
import uuid
from collections.abc import AsyncGenerator, Generator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import psycopg
import pytest
from mcp import Client
from mcp.types import CallToolResult, TextContent
from psycopg import sql
from psycopg.conninfo import make_conninfo

from mcp_guard.agent.gateway import connect, login
from mcp_guard.fake_servers.outbound import read_outbound
from mcp_guard.fake_servers.reset import LOCAL_DATABASE_URL, reset
from mcp_guard.gateway.process import RunningGateway, start_gateway

# CI sets DATABASE_URL to its Postgres service.
DATABASE_URL = os.environ.get("DATABASE_URL", LOCAL_DATABASE_URL)


@contextmanager
def temporary_database() -> Generator[str]:
    """A fresh, empty database, dropped on exit."""
    name = f"test_{uuid.uuid4().hex}"
    with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield make_conninfo(DATABASE_URL, dbname=name)
    finally:
        with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture(scope="session", autouse=True)
def no_tracing() -> Iterator[None]:
    """Tests send no spans anywhere, even from a shell where tracing is configured."""
    with pytest.MonkeyPatch.context() as patch:
        for name in [name for name in os.environ if name.startswith("OTEL_")]:
            patch.delenv(name)
        yield


@pytest.fixture
def database_url() -> Iterator[str]:
    """A fresh, empty database for one test."""
    with temporary_database() as url:
        yield url


@pytest.fixture(scope="session")
def seeded_database_url() -> Iterator[str]:
    """A database holding the seeded fake bank, shared by tests that only read it."""
    with temporary_database() as url:
        reset(url)
        yield url


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


# Most tests check what the servers behind the Gateway do, so their Gateway has its defences
# off and passes every Tool call through; the defences are tested with a guarded Gateway.
DEFENCES_OFF = {"GATEWAY_DEFENCES": "off"}


@pytest.fixture(scope="session")
def gateway(seeded_database_url: str) -> Iterator[RunningGateway]:
    """The Gateway with its defences off, started the way it runs for real, on a free port."""
    with start_gateway(seeded_database_url, DEFENCES_OFF) as running:
        yield running


@pytest.fixture(scope="session")
def guarded_gateway(seeded_database_url: str) -> Iterator[RunningGateway]:
    """The Gateway with its defences on, as it runs for real, over the seeded bank."""
    with start_gateway(seeded_database_url) as running:
        yield running


@asynccontextmanager
async def gateway_client(gateway: RunningGateway, person: str = "S001") -> AsyncGenerator[Client]:
    """An MCP client of the Gateway, logged in as `person` (by default Staff member S001)."""
    logged_in = await login(gateway.url, person)
    async with connect(gateway.url, logged_in.mcp_token) as client:
        yield client


@dataclass(frozen=True)
class WritableBank:
    database_url: str
    gateway: RunningGateway

    def outbound(self) -> list[tuple[str, str, dict[str, Any]]]:
        """(server, tool, arguments) of every call recorded in the outbound record, in order."""
        return [(o.server, o.tool, o.arguments) for o in read_outbound(self.database_url)]


@pytest.fixture(scope="module")
def _writable_bank() -> Iterator[WritableBank]:
    """A seeded database and a Gateway over it, with its defences off, shared by one module's
    writing tests."""
    with temporary_database() as url:
        reset(url)
        with start_gateway(url, DEFENCES_OFF) as running:
            yield WritableBank(url, running)


@pytest.fixture
def writable_bank(_writable_bank: WritableBank) -> WritableBank:
    """A bank reset to its seeded state before each test, and a Gateway over it.

    The servers open a fresh database connection for every Tool call, so resetting the bank
    under a running Gateway is safe.
    """
    reset(_writable_bank.database_url)
    return _writable_bank


@pytest.fixture
def writable_gateway(writable_bank: WritableBank) -> RunningGateway:
    """A Gateway over a bank reset to its seeded state before each test, for tests that write."""
    return writable_bank.gateway


@dataclass
class Reply:
    body: Mapping[str, object]
    status: int = 200
    delay_seconds: float = 0


@dataclass
class FakeProvider:
    """A local HTTP server standing in for a model provider's API (Gemini or OpenAI).

    It records each request's JSON body and serves the queued replies in order; the last
    reply repeats, so a client's retries see it again.
    """

    url: str
    replies: list[Reply] = field(default_factory=list[Reply])
    requests: list[dict[str, object]] = field(default_factory=list[dict[str, object]])


@pytest.fixture
def fake_provider() -> Iterator[FakeProvider]:
    """A fake model provider on a free local port; point an adapter's base URL at it."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    provider = FakeProvider(url=f"http://127.0.0.1:{server.server_port}")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            provider.requests.append(json.loads(self.rfile.read(length)))
            reply = provider.replies.pop(0) if len(provider.replies) > 1 else provider.replies[0]
            time.sleep(reply.delay_seconds)
            body = json.dumps(reply.body).encode()
            try:
                self.send_response(reply.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                pass  # the client stopped waiting

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    server.RequestHandlerClass = Handler
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield provider
    finally:
        server.shutdown()
        server.server_close()


def error_message(result: CallToolResult) -> str:
    """The text of a Tool call result that must be an error."""
    assert result.is_error
    [message] = [c.text for c in result.content if isinstance(c, TextContent)]
    return message
