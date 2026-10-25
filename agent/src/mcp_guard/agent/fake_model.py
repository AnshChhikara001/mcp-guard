"""A scripted fake model, for tests and for chatting without an API key.

It implements the same Model interface as the real providers, but its next message comes
from a script: a plain function of the conversation so far. Nothing is sent anywhere.
"""

import json
import uuid
from collections.abc import Callable, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolCall, ToolMessage
from langchain_core.messages.tool import tool_call
from mcp.types import Tool

type Script = Callable[[Sequence[BaseMessage]], AIMessage]


class ScriptedModel:
    def __init__(self, script: Script) -> None:
        self._script = script

    async def respond(self, messages: Sequence[BaseMessage], tools: Sequence[Tool]) -> AIMessage:
        message = self._script(messages)
        offered = {tool.name for tool in tools}
        for call in message.tool_calls:
            # A real model can only call the tools it was offered.
            if call["name"] not in offered:
                raise ValueError(f"The script called {call['name']!r}, which was not offered")
        # Nothing is sent to a provider, so no tokens are used and no model time is spent.
        if message.usage_metadata is None:
            message.usage_metadata = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        message.response_metadata.setdefault("latency_seconds", 0.0)
        return message


def balance_script(customer_id: str) -> Script:
    """Answers a Customer's balance questions the way a real model would.

    "What's my balance?" lists the Customer's accounts, fetches each balance, then says it.
    "What was that in paise?" is answered only from a balance fetched earlier in the
    conversation, so it gets a right answer only if the Agent remembers that earlier turn.
    """

    def script(messages: Sequence[BaseMessage]) -> AIMessage:
        last = messages[-1]
        if isinstance(last, HumanMessage):
            question = last.text.lower()
            if "paise" in question:
                return _paise_from_earlier_balance(messages)
            if "balance" in question:
                return _call_tools(_call("list_accounts", customer_id=customer_id))
            return AIMessage("I can tell you the balance of your accounts.")

        results = _latest_tool_results(messages)
        failed = [r for r in results if r.status == "error"]
        if failed:
            return AIMessage(f"Sorry, I couldn't get that: {failed[0].text}")
        if results[0].name == "list_accounts":
            accounts = json.loads(results[0].text)["accounts"]
            calls = [_call("get_balance", account_id=a["account_id"]) for a in accounts]
            return _call_tools(*calls)
        balances = [json.loads(r.text) for r in results]
        return AIMessage(
            "Your balance: " + ", ".join(f"{b['account_id']}: {b['balance']}" for b in balances)
        )

    return script


def _paise_from_earlier_balance(messages: Sequence[BaseMessage]) -> AIMessage:
    paise: dict[str, int] = {}  # the latest balance of each account, by account ID
    for message in messages:
        if (
            isinstance(message, ToolMessage)
            and message.name == "get_balance"
            and message.status == "success"
        ):
            balance = json.loads(message.text)
            paise[balance["account_id"]] = balance["balance_paise"]
    if not paise:
        return AIMessage("I don't know which balance you mean. Shall I look up your balance?")
    return AIMessage(", ".join(f"{account} holds {p} paise" for account, p in paise.items()) + ".")


def _latest_tool_results(messages: Sequence[BaseMessage]) -> list[ToolMessage]:
    results: list[ToolMessage] = []
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            break
        results.insert(0, message)
    return results


def _call(name: str, **args: object) -> ToolCall:
    return tool_call(name=name, args=args, id=f"call_{uuid.uuid4().hex}")


def _call_tools(*calls: ToolCall) -> AIMessage:
    return AIMessage("", tool_calls=list(calls))
