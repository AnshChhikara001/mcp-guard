"""Seam 1 harness: the real Gateway process, with the seeded bank behind it."""

import os
import socket
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

STARTUP_TIMEOUT_SECONDS = 30


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
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Gateway exited during startup with code {process.returncode}")
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"Gateway did not listen on port {port} within {STARTUP_TIMEOUT_SECONDS}s")
