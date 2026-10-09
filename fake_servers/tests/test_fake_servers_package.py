from mcp_guard import fake_servers


def test_package_is_installed() -> None:
    assert fake_servers.__doc__
