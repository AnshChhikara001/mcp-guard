"""Everything the browser test talks to, started fresh: run by playwright.config.ts.

A new database with the seeded fake bank, the Gateway with its defences on, the Agent API with
the scripted fake model (MODEL is never passed on), and the web app on WEB_PORT, whose Vite
proxy points at the two servers' free ports. Nothing touches the database `make dev` uses.
Ctrl-C or SIGTERM stops it all and drops the database.

    WEB_PORT=5174 uv run python web/e2e/servers.py    (from the repository root)
"""

import os
import signal
import subprocess
import sys
from pathlib import Path
from types import FrameType

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))  # the shared test fixtures in conftest.py

from conftest import start_agent_api, temporary_database  # noqa: E402
from mcp_guard.fake_servers.reset import reset  # noqa: E402
from mcp_guard.gateway.process import start_gateway  # noqa: E402


def stop(_signal: int, _frame: FrameType | None) -> None:
    sys.exit(0)  # exiting runs every `finally` below


def main() -> None:
    signal.signal(signal.SIGTERM, stop)  # how Playwright stops its web server
    for name in [name for name in os.environ if name.startswith("OTEL_")]:
        del os.environ[name]  # no tracing from test runs
    web_port = os.environ["WEB_PORT"]  # set by playwright.config.ts
    with temporary_database() as database_url:
        reset(database_url)
        with (
            start_gateway(database_url) as gateway,
            start_agent_api(gateway, database_url) as agent_api,
        ):
            vite = subprocess.Popen(
                ["npm", "--prefix", str(ROOT / "web"), "run", "dev", "--", "--port", web_port],
                env={
                    **os.environ,
                    "GATEWAY_PORT": str(gateway.port),
                    "AGENT_API_PORT": str(agent_api.port),
                },
            )
            try:
                vite.wait()
            finally:
                vite.terminate()
                vite.wait(timeout=10)


if __name__ == "__main__":
    main()
