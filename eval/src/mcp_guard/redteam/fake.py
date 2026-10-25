"""The scripted fake model for a task: it plays the task's `fake_model` steps, in order.

It is the eval without an API key: CI runs every task that has steps this way. It always does
what its script says, so it falls for every Attack it is scripted to; whether the Attack then
succeeds is up to the Gateway.
"""

from collections.abc import Sequence
from typing import Any, cast

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.messages.tool import tool_call

from mcp_guard.agent.fake_model import ScriptedModel
from mcp_guard.agent.model import Model
from mcp_guard.redteam.tasks import Task

FAKE = "fake"  # its model name
LAST_RESULT = "{last_result}"


def fake_model(task: Task) -> Model:
    steps = [step for step in task.spec.fake_model for _ in range(step.repeat)]
    if not steps:
        raise ValueError(f"Task {task.id} has no fake_model steps")

    def script(messages: Sequence[BaseMessage]) -> AIMessage:
        # Each earlier model call left one AI message, so their count says which step is next.
        index = sum(isinstance(m, AIMessage) for m in messages)
        if index >= len(steps):
            raise ValueError(f"Task {task.id}'s fake_model has no step {index + 1}")
        step = steps[index]
        last_result = _latest_tool_results(messages)
        if step.reply is not None:
            return AIMessage(_fill(step.reply, last_result))
        return AIMessage(
            "",
            tool_calls=[
                tool_call(
                    name=call.name,
                    args=_fill(call.args, last_result),
                    id=f"call_{index}_{n}",
                )
                for n, call in enumerate(step.tool_calls)
            ],
        )

    return ScriptedModel(script)


def _latest_tool_results(messages: Sequence[BaseMessage]) -> str:
    results: list[str] = []
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            break
        results.insert(0, message.text)
    return "\n".join(results)


def _fill(value: Any, last_result: str) -> Any:
    """`value` with {last_result} replaced in every string inside it."""
    if isinstance(value, str):
        return value.replace(LAST_RESULT, last_result)
    if isinstance(value, dict):
        return {k: _fill(v, last_result) for k, v in cast(dict[str, Any], value).items()}
    if isinstance(value, list):
        return [_fill(v, last_result) for v in cast(list[Any], value)]
    return value
