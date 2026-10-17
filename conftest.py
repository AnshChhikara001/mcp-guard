"""Fixtures shared by every package's tests. Databases need `make db` locally."""

import os
import uuid
from collections.abc import Generator, Iterator
from contextlib import contextmanager

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from mcp_guard.fake_servers.reset import LOCAL_DATABASE_URL, reset

# CI sets DATABASE_URL to its Postgres service.
DATABASE_URL = os.environ.get("DATABASE_URL", LOCAL_DATABASE_URL)


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
