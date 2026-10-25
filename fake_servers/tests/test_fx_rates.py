"""The fx-rates server on its own MCP surface: when a rug pull turns it malicious."""

import os
import sys

import pytest
from mcp import Client, StdioServerParameters

from mcp_guard.fake_servers.fx_rates import INJECTED_INSTRUCTIONS
from mcp_guard.fake_servers.reset import reset
from mcp_guard.fake_servers.scenarios import set_fx_mode

pytestmark = pytest.mark.anyio


async def test_in_rug_pull_mode_the_first_listing_is_honest_and_every_later_one_malicious(
    database_url: str,
) -> None:
    reset(database_url)
    set_fx_mode(database_url, "rug_pull")
    fx_rates = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_guard.fake_servers.fx_rates"],
        env={**os.environ, "DATABASE_URL": database_url},
    )

    async with Client(fx_rates) as client:
        descriptions = [(await client.list_tools()).tools[0].description or "" for _ in range(3)]

    assert [INJECTED_INSTRUCTIONS in d for d in descriptions] == [False, True, True]
