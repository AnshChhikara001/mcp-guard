"""A scripted fake model, for tests and for chatting without an API key.

It implements the same Model interface as the real providers, but its next message comes
from a script: a plain function of the conversation so far. Nothing is sent anywhere.
"""

import json
import re
import uuid
from collections.abc import Callable, Sequence
from typing import Any

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


def demo_script(person_id: str) -> Script:
    """Answers the web app's suggested questions the way a real model would, for `person_id`.

    - "What's my balance?" lists the person's accounts, fetches each balance, then says it.
      Naming a Customer ("C002's balance") or an account ("A002") asks about theirs instead.
    - "My last 5 transactions" lists the accounts, then each one's latest transactions.
    - "Convert ₹10,000 to USD" converts with the currencies in the order they are named.
    - "What was that in paise?" is answered only from a balance fetched earlier in the
      conversation, so it gets a right answer only if the Agent remembers that earlier turn.

    A Tool call the Gateway denies (or that fails) is reported back with its reason.
    """

    def script(messages: Sequence[BaseMessage]) -> AIMessage:
        last = messages[-1]
        if isinstance(last, HumanMessage):
            return _first_step(last.text, person_id, messages)

        results = _latest_tool_results(messages)
        failed = [r for r in results if r.status == "error"]
        if failed:
            return AIMessage(f"Sorry, I couldn't get that: {failed[0].text}")
        match results[0].name:
            case "list_accounts":
                accounts = json.loads(results[0].text)["accounts"]
                question = _latest_question(messages)
                if "transaction" in question.lower():
                    limit = _how_many(question)
                    return _call_tools(
                        *(
                            _call("list_transactions", account_id=a["account_id"], limit=limit)
                            for a in accounts
                        )
                    )
                calls = [_call("get_balance", account_id=a["account_id"]) for a in accounts]
                return _call_tools(*calls)
            case "list_transactions":
                return AIMessage("\n\n".join(_transactions(json.loads(r.text)) for r in results))
            case "convert":
                c = json.loads(results[0].text)
                return AIMessage(
                    f"{c['amount']:,.2f} {c['from_currency']} is {c['converted']:,.2f}"
                    f" {c['to_currency']}, at {c['rate']} {c['to_currency']} per"
                    f" {c['from_currency']}."
                )
            case _:
                balances = [json.loads(r.text) for r in results]
                return AIMessage(
                    "Balance: " + ", ".join(f"{b['account_id']}: {b['balance']}" for b in balances)
                )

    return script


CUSTOMER_ID = re.compile(r"\bC\d{3}\b", re.IGNORECASE)
ACCOUNT_ID = re.compile(r"\bA\d{3}\b", re.IGNORECASE)
AMOUNT = re.compile(r"\d[\d,]*(?:\.\d+)?")
# How each currency may be named, by its code.
CURRENCY_NAMES = {
    "INR": ("₹", "inr", "rupee", "rs"),
    "USD": ("$", "usd", "dollar"),
    "EUR": ("€", "eur", "euro"),
    "GBP": ("£", "gbp", "pound"),
    "AED": ("aed", "dirham"),
    "SGD": ("sgd",),
}
DEFAULT_TRANSACTIONS = 5


def _first_step(text: str, person_id: str, messages: Sequence[BaseMessage]) -> AIMessage:
    question = text.lower()
    if "paise" in question:
        return _paise_from_earlier_balance(messages)
    if "convert" in question or " in rupees" in question:
        return _convert(text)
    if "balance" in question and (account := ACCOUNT_ID.search(text)):
        return _call_tools(_call("get_balance", account_id=account.group().upper()))
    if "balance" in question or "transaction" in question:
        customer = CUSTOMER_ID.search(text)
        customer_id = customer.group().upper() if customer else person_id
        return _call_tools(_call("list_accounts", customer_id=customer_id))
    return AIMessage(
        "I can tell you a balance, list recent transactions, or convert between currencies."
    )


def _convert(text: str) -> AIMessage:
    amount = AMOUNT.search(text)
    lowered = text.lower()
    named: list[tuple[int, str]] = []  # (where it is first named, currency code)
    for code, names in CURRENCY_NAMES.items():
        found = [i for name in names if (i := _find_word(lowered, name)) >= 0]
        if found:
            named.append((min(found), code))
    currencies = [code for _, code in sorted(named)]
    if amount is None or not currencies:
        return AIMessage("Tell me an amount and the currencies, like: convert ₹10,000 to USD.")
    if len(currencies) == 1:
        currencies.append("USD" if currencies[0] == "INR" else "INR")
    value = float(amount.group().replace(",", ""))
    return _call_tools(
        _call(
            "convert",
            amount=int(value) if value.is_integer() else value,
            from_currency=currencies[0],
            to_currency=currencies[1],
            reference="",
        )
    )


def _find_word(text: str, name: str) -> int:
    """Where `name` starts in `text` as a word (a symbol anywhere), or -1."""
    if not name.isalpha():
        return text.find(name)
    match = re.search(rf"\b{name}", text)
    return match.start() if match else -1


def _how_many(question: str) -> int:
    match = re.search(r"\b(\d{1,3})\b", question)
    return min(int(match.group()), 100) if match else DEFAULT_TRANSACTIONS


def _transactions(listed: dict[str, Any]) -> str:
    lines = [
        f"{t['posted_at'][:10]}  {t['direction']}  {t['amount']}  {t['counterparty']}"
        for t in listed["transactions"]
    ]
    return "\n".join([f"The last {len(lines)} transactions on {listed['account_id']}:", *lines])


def _latest_question(messages: Sequence[BaseMessage]) -> str:
    return next(m.text for m in reversed(messages) if isinstance(m, HumanMessage))


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
