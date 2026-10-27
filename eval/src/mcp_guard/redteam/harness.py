"""Running tasks through the Agent and the Gateway, and scoring them by exact checks.

Each task runs alone: the bank is reset to its seeded state, the task's setup is applied, a
fresh Gateway is started over it (with its defences off or on) and the Agent talks to it for
the task's Customer, Staff member or Supervisor. When the conversation ends, the task's check
is read from the bank.

A model call that fails ends the task as an error, which counts towards no rate. The Agent
has no step limit of its own, so the eval stops a conversation after MAX_MODEL_CALLS model
calls and scores what happened until then. Each result is saved as soon as its task finishes;
running again skips tasks already done (unless their file changed) and retries those that
ended in an error.
"""

import time
import uuid
from collections.abc import Callable, Collection, Sequence
from datetime import UTC, datetime
from pathlib import Path

from langchain_core.messages import AIMessage, BaseMessage
from mcp.types import Tool

from mcp_guard.agent.agent import start_agent
from mcp_guard.agent.model import Model, ModelCallFailed
from mcp_guard.fake_servers.reset import reset
from mcp_guard.fake_servers.scenarios import plant_ticket, set_fx_mode
from mcp_guard.gateway.process import start_gateway
from mcp_guard.redteam.fake import FAKE
from mcp_guard.redteam.prices import Price
from mcp_guard.redteam.results import (
    OUTCOMES,
    Defences,
    ItemResult,
    RecordedToolCall,
    Summary,
    current_result,
    read_results,
    summarise,
    write_result,
)
from mcp_guard.redteam.scoring import is_met
from mcp_guard.redteam.tasks import Task

# Far more than any task needs; it keeps a runaway loop from spending the whole budget.
MAX_MODEL_CALLS = 50


async def run_eval(
    tasks: Sequence[Task],
    model_for: Callable[[Task], Model],
    run_dir: Path,
    *,
    model_name: str,
    defences: Defences,
    price: Price,
    database_url: str,
    only: Collection[str] | None = None,
    limit: int | None = None,
    on_result: Callable[[ItemResult], None] | None = None,
) -> Summary:
    """Run the tasks not done yet in `run_dir` and return the summary of all of them.

    `only` picks Attack classes (and "benign") to run; `limit` caps how many tasks run now.
    `on_result` is told each task's result as it is saved. The bank in `database_url` is
    reset for every task.
    """
    results = read_results(run_dir)
    to_run = [
        task
        for task in tasks
        if (only is None or task.group in only)
        and ((done := current_result(task, results)) is None or done.outcome == "error")
    ][:limit]
    for task in to_run:
        result = await _run_task(task, model_for(task), model_name, defences, database_url)
        write_result(run_dir, result)
        if on_result is not None:
            on_result(result)
        summarise(tasks, run_dir, model=model_name, defences=defences, price=price)
    return summarise(tasks, run_dir, model=model_name, defences=defences, price=price)


async def _run_task(
    task: Task, model: Model, model_name: str, defences: Defences, database_url: str
) -> ItemResult:
    reset(database_url)
    for ticket in task.spec.setup.tickets:
        plant_ticket(database_url, ticket.customer, ticket.subject, ticket.body)
    set_fx_mode(database_url, task.spec.setup.fx_mode, task.spec.setup.fx_instructions)

    recording = _RecordingModel(model)
    replies: list[str] = []
    error: str | None = None
    stopped = False
    with start_gateway(database_url, {"GATEWAY_DEFENCES": defences}) as gateway:
        async with start_agent(
            recording,
            gateway.url,
            database_url,
            person=task.spec.person,
            model_name=None if model_name == FAKE else model_name,
        ) as agent:
            conversation_id = f"eval-{uuid.uuid4()}"
            try:
                for text in task.spec.messages:
                    replies.append(await agent.ask(conversation_id, text))
            except ModelCallFailed as e:
                error = str(e)
            except _CallLimitReached:
                stopped = True

    outcome = (
        "error"
        if error is not None
        else OUTCOMES[task.kind, is_met(task.spec.success, database_url, replies)]
    )
    return ItemResult(
        task=task.id,
        fingerprint=task.fingerprint,
        outcome=outcome,
        error=error,
        stopped_at_call_limit=stopped,
        model_calls=recording.calls,
        input_tokens=recording.input_tokens,
        output_tokens=recording.output_tokens,
        latency_seconds=recording.latency_seconds,
        tool_calls=recording.tool_calls,
        replies=replies,
        finished_at=datetime.now(UTC),
    )


class _RecordingModel:
    """Passes every call on to the model, adding up its tokens, model time and Tool calls."""

    def __init__(self, model: Model) -> None:
        self._model = model
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.latency_seconds = 0.0
        self.tool_calls: list[RecordedToolCall] = []

    async def respond(self, messages: Sequence[BaseMessage], tools: Sequence[Tool]) -> AIMessage:
        if self.calls == MAX_MODEL_CALLS:
            raise _CallLimitReached
        started = time.perf_counter()
        reply = await self._model.respond(messages, tools)
        elapsed = time.perf_counter() - started
        self.calls += 1
        # An adapter reports its model time when the call costs more than the model's own
        # work (a CLI starting up); otherwise the whole call is model time.
        self.latency_seconds += reply.response_metadata.get("latency_seconds", elapsed)
        if usage := reply.usage_metadata:
            self.input_tokens += usage["input_tokens"]
            self.output_tokens += usage["output_tokens"]
        self.tool_calls += [
            RecordedToolCall(name=c["name"], args=c["args"]) for c in reply.tool_calls
        ]
        return reply


class _CallLimitReached(Exception):
    """The conversation used MAX_MODEL_CALLS model calls; the eval stops it there."""
