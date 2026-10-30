import hashlib
import os
import re
import subprocess
import sys
from dataclasses import asdict

import pytest

from mcp_guard.fake_servers.bank import SEED, generate
from mcp_guard.fake_servers.canaries import CANARIES, Canary


def test_same_seed_gives_identical_bank() -> None:
    assert generate(SEED) == generate(SEED)


def test_two_separate_runs_give_identical_bank() -> None:
    # Separate processes with different hash seeds, so nothing can rely on per-process state.
    def run(hash_seed: str) -> str:
        return subprocess.run(
            [
                sys.executable,
                "-c",
                "from mcp_guard.fake_servers.bank import generate; print(generate())",
            ],
            env={**os.environ, "PYTHONHASHSEED": hash_seed},
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    first, second = run("1"), run("2")

    assert first == second
    assert "C001" in first


def test_seeded_bank_matches_its_recorded_fingerprint() -> None:
    # Eval runs are only comparable if the seeded bank never changes. If this fails after a
    # dependency upgrade (Faker especially) or a generator change, the data moved: make sure
    # that's intended, then record the new fingerprint.
    fingerprint = hashlib.sha256(repr(generate()).encode()).hexdigest()

    assert fingerprint == "b7dd0e98fb0c3a29066957755c2b71316a934cb3ca5e3c2d331a3bd0607fdbe5"


def test_different_seed_gives_different_bank() -> None:
    assert generate(1) != generate(2)


def test_seeded_bank_has_customers_staff_a_supervisor_and_tickets() -> None:
    bank = generate()

    assert len(bank.customers) >= 2
    assert len([s for s in bank.staff if not s.is_supervisor]) >= 2
    assert len([s for s in bank.staff if s.is_supervisor]) >= 1
    assert len(bank.tickets) >= 1


def test_there_are_canaries_and_each_value_is_unique() -> None:
    assert len(CANARIES) >= 2
    assert len({c.value for c in CANARIES}) == len(CANARIES)


@pytest.mark.parametrize("canary", CANARIES, ids=lambda c: f"{c.row_id}.{c.column}")
def test_canary_is_at_its_documented_place_and_nowhere_else(canary: Canary) -> None:
    # Also catch the same digits written differently, e.g. a phone without "+91 " or spaces.
    digits = re.sub(r"\D", "", canary.value)
    places = [
        (table, row.get("id"), column, str(value))
        for table, rows in generate().tables().items()
        for row in map(asdict, rows)
        for column, value in row.items()
    ]

    found = [
        (table, row_id, column)
        for table, row_id, column, text in places
        if canary.value.lower() in text.lower()
        or (len(digits) >= 8 and digits[-10:] in re.sub(r"\D", "", text))
    ]

    assert found == [(canary.table, canary.row_id, canary.column)]


def test_each_staff_member_serves_some_customers_but_not_all() -> None:
    bank = generate()
    customers = {c.id for c in bank.customers}
    served = {
        s.id: {pair.customer_id for pair in bank.serving if pair.staff_id == s.id}
        for s in bank.staff
        if not s.is_supervisor
    }

    assert served["S001"] >= {"C001", "C005"}
    assert served["S002"] >= {"C001", "C002", "C004", "C008"}
    assert served["S003"] >= {"C003"}
    assert all(set() < customers_served < customers for customers_served in served.values())
    assert set().union(*served.values()) == customers


def test_the_supervisor_has_no_serving_rows_because_they_serve_everyone() -> None:
    bank = generate()
    supervisors = {s.id for s in bank.staff if s.is_supervisor}

    assert not [pair for pair in bank.serving if pair.staff_id in supervisors]
