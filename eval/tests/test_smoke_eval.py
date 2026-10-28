"""Seam 2: the smoke eval, every task scripting the fake model, run as CI does, with no key.

With the Gateway's defences off, every scripted Attack must succeed and every Benign task must
get done. That proves each script really carries out its Attack, so when defences are on, an
Attack that fails was stopped by the Gateway rather than by a broken script. Every task has a
script, so every task's success condition is proven reachable.
"""

from collections import Counter
from pathlib import Path

import pytest

from mcp_guard.fake_servers.bank import generate
from mcp_guard.redteam.fake import fake_model
from mcp_guard.redteam.harness import run_eval
from mcp_guard.redteam.prices import PRICES
from mcp_guard.redteam.tasks import ATTACK_CLASSES, load_tasks

TASKS_DIR = Path(__file__).parents[1] / "tasks"


def test_every_attack_class_has_seeds_and_benign_work_covers_every_role() -> None:
    tasks = load_tasks(TASKS_DIR)
    attacks = Counter(t.group for t in tasks if t.kind == "attack")
    benign = Counter(t.spec.person.role for t in tasks if t.kind == "benign")

    assert all(attacks[attack_class] >= 6 for attack_class in ATTACK_CLASSES), attacks
    assert attacks.total() >= 40
    assert benign.total() >= 30
    assert set(benign) == {"customer", "staff", "supervisor"}
    assert [t.id for t in tasks if not t.spec.fake_model] == []


def test_every_task_works_for_someone_the_bank_has_in_that_role() -> None:
    bank = generate()
    people = {c.id: "customer" for c in bank.customers} | {
        s.id: "supervisor" if s.is_supervisor else "staff" for s in bank.staff
    }

    for task in load_tasks(TASKS_DIR):
        person = task.spec.person
        assert people.get(person.id) == person.role, task.id
        assert all(people.get(t.customer) == "customer" for t in task.spec.setup.tickets), task.id


@pytest.mark.anyio
async def test_with_defences_off_every_scripted_attack_succeeds_and_no_benign_task_is_blocked(
    tmp_path: Path, database_url: str
) -> None:
    tasks = load_tasks(TASKS_DIR)

    summary = await run_eval(
        tasks,
        fake_model,
        tmp_path,
        model_name="fake",
        defences="off",
        price=PRICES["fake"],
        database_url=database_url,
    )

    assert summary.errors == []
    assert summary.attack_success_rate == 1.0
    assert summary.false_block_rate == 0.0
