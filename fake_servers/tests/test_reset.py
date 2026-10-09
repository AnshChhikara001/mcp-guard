import os
import subprocess
from typing import Any

import psycopg
import pytest
from psycopg import sql

from mcp_guard.fake_servers.bank import generate
from mcp_guard.fake_servers.canaries import CANARIES, Canary
from mcp_guard.fake_servers.reset import reset

type Snapshot = dict[str, list[tuple[Any, ...]]]


def snapshot(database_url: str) -> Snapshot:
    """Every row of every table in the bank schema, in a stable order."""
    with psycopg.connect(database_url) as conn:
        tables = [
            name
            for (name,) in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'bank' ORDER BY table_name"
            )
        ]
        return {
            table: conn.execute(
                sql.SQL("SELECT * FROM {} ORDER BY 1").format(sql.Identifier("bank", table))
            ).fetchall()
            for table in tables
        }


def test_reset_loads_every_seeded_row(database_url: str) -> None:
    reset(database_url)

    counts = {table: len(rows) for table, rows in snapshot(database_url).items()}
    assert counts == {table: len(rows) for table, rows in generate().tables().items()}


@pytest.mark.parametrize("canary", CANARIES, ids=lambda c: f"{c.row_id}.{c.column}")
def test_reset_plants_canary_at_its_documented_place(database_url: str, canary: Canary) -> None:
    reset(database_url)

    with psycopg.connect(database_url) as conn:
        row = conn.execute(
            sql.SQL("SELECT {} FROM {} WHERE id = %s").format(
                sql.Identifier(canary.column), sql.Identifier("bank", canary.table)
            ),
            (canary.row_id,),
        ).fetchone()
    assert row == (canary.value,)


def test_reset_restores_seeded_state_from_a_modified_state(database_url: str) -> None:
    reset(database_url)
    seeded = snapshot(database_url)
    with psycopg.connect(database_url) as conn:
        conn.execute("UPDATE bank.accounts SET balance_paise = 0 WHERE id = 'A001'")
        conn.execute("DELETE FROM bank.tickets WHERE id = 'TK002'")
        conn.execute("UPDATE bank.customers SET phone = '+91 9000000000' WHERE id = 'C001'")
        conn.execute("DROP TABLE bank.transactions")
        conn.execute("CREATE TABLE bank.planted (note text)")

    reset(database_url)

    assert snapshot(database_url) == seeded


def test_reset_command_seeds_the_database_named_in_database_url(database_url: str) -> None:
    subprocess.run(["bank-reset"], env={**os.environ, "DATABASE_URL": database_url}, check=True)

    assert snapshot(database_url)["customers"]
