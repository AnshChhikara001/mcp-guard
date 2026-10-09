from mcp_guard import gateway


def test_package_is_installed() -> None:
    assert gateway.__doc__
