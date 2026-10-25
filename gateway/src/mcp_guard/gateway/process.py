"""Running the Gateway as a child process, the way it runs for real: for tests and the eval."""

import os
import socket
import subprocess
import time
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

STARTUP_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class RunningGateway:
    process: subprocess.Popen[bytes]
    port: int

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"


@contextmanager
def start_gateway(
    database_url: str, config: Mapping[str, str] | None = None
) -> Generator[RunningGateway]:
    """A Gateway process on a free local port, with extra environment `config`; stopped on exit."""
    port = _free_port()
    process = subprocess.Popen(
        ["mcp-guard-gateway"],
        env={
            **os.environ,
            "DATABASE_URL": database_url,
            "GATEWAY_PORT": str(port),
            **(config or {}),
        },
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
