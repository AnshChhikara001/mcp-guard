"""Seam 1: the Gateway's defences are switched off or on by configuration, for the eval."""

import pytest

from mcp_guard.gateway.process import start_gateway


@pytest.mark.parametrize("defences", ["off", "on"])
def test_gateway_starts_with_its_defences_off_or_on(
    seeded_database_url: str, defences: str
) -> None:
    with start_gateway(seeded_database_url, {"GATEWAY_DEFENCES": defences}) as gateway:
        assert gateway.process.poll() is None


def test_gateway_refuses_to_start_with_an_unknown_defences_setting(
    seeded_database_url: str,
) -> None:
    with (
        pytest.raises(RuntimeError, match="exited during startup"),
        start_gateway(seeded_database_url, {"GATEWAY_DEFENCES": "maybe"}),
    ):
        pass
