"""Fixtures shared by every package's tests. Databases need `make db` locally."""

import os
import socket
import subprocess
import time
import uuid
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from mcp_guard.fake_servers.reset import LOCAL_DATABASE_URL, reset

# CI sets DATABASE_URL to its Postgres service.
DATABASE_URL = os.environ.get("DATABASE_URL", LOCAL_DATABASE_URL)

GATEWAY_STARTUP_TIMEOUT_SECONDS = 30


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


@dataclass(frozen=True)
class RunningGateway:
    process: subprocess.Popen[bytes]
    port: int

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def gateway(seeded_database_url: str) -> Iterator[RunningGateway]:
    """The Gateway, started the way it runs for real, listening on a free local port."""
    port = _free_port()
    process = subprocess.Popen(
        ["mcp-guard-gateway"],
        env={**os.environ, "DATABASE_URL": seeded_database_url, "GATEWAY_PORT": str(port)},
    )
    try:
        _wait_until_listening(process, port)
        yield RunningGateway(process, port)
    finally:
        process.terminate()
        process.wait(timeout=10)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_listening(process: subprocess.Popen[bytes], port: int) -> None:
    deadline = time.monotonic() + GATEWAY_STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Gateway exited during startup with code {process.returncode}")
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(
        f"Gateway did not listen on port {port} within {GATEWAY_STARTUP_TIMEOUT_SECONDS}s"
    )
