"""Seam 2: the smoke eval, every task scripting the fake model, run as CI does, with no key.

With the Gateway's defences off, every scripted Attack must succeed and every Benign task must
get done. That proves each script really carries out its Attack, so when defences are on, an
Attack that fails was stopped by the Gateway rather than by a broken script.
"""

from pathlib import Path

import pytest

from mcp_guard.redteam.fake import fake_model
from mcp_guard.redteam.harness import run_eval
from mcp_guard.redteam.prices import PRICES
from mcp_guard.redteam.tasks import ATTACK_CLASSES, load_tasks

pytestmark = pytest.mark.anyio

TASKS_DIR = Path(__file__).parents[1] / "tasks"


async def test_with_defences_off_every_scripted_attack_succeeds_and_no_benign_task_is_blocked(
    tmp_path: Path, database_url: str
) -> None:
    tasks = [t for t in load_tasks(TASKS_DIR) if t.spec.fake_model]

    summary = await run_eval(
        tasks,
        fake_model,
        tmp_path,
        model_name="fake",
        defences="off",
        price=PRICES["fake"],
        database_url=database_url,
    )

    assert sum(t.kind == "attack" for t in tasks) >= 10
    assert sum(t.kind == "benign" for t in tasks) >= 10
    assert {t.group for t in tasks} == {*ATTACK_CLASSES, "benign"}
    assert summary.errors == []
    assert summary.attack_success_rate == 1.0
    assert summary.false_block_rate == 0.0
