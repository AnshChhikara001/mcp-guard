"""The servers behind the Gateway can't be reached from outside it (ADR-0001)."""

import psutil

from conftest import RunningGateway


def test_only_the_gateway_listens_and_the_accounts_server_has_no_port(
    gateway: RunningGateway,
) -> None:
    gateway_process = psutil.Process(gateway.process.pid)
    children = gateway_process.children(recursive=True)

    assert any("mcp_guard.fake_servers.accounts" in " ".join(c.cmdline()) for c in children)
    assert [c.laddr.port for c in gateway_process.net_connections() if c.status == "LISTEN"] == [
        gateway.port
    ]
    for child in children:
        listening = [c for c in child.net_connections() if c.status == "LISTEN"]
        assert listening == [], child.cmdline()
