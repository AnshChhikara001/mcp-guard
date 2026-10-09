import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from mcp_guard.fake_servers.reset import LOCAL_DATABASE_URL

# CI sets DATABASE_URL to its Postgres service.
DATABASE_URL = os.environ.get("DATABASE_URL", LOCAL_DATABASE_URL)


@pytest.fixture
def database_url() -> Iterator[str]:
    """A fresh, empty database for one test, dropped afterwards. Needs `make db` locally."""
    name = f"test_{uuid.uuid4().hex}"
    with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield make_conninfo(DATABASE_URL, dbname=name)
    finally:
        with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
