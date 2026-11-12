"""Seam 3: the Agent API the web app talks to, run as its own process in front of the real
Gateway, with the scripted fake model or a model from an adapter file.

A person logs in at the Gateway, sends their MCP token to the Agent API, opens a conversation
and asks one turn at a time; each turn streams its steps as Server-Sent Events.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest

from conftest import RunningAgentApi, RunningGateway, start_agent_api


@pytest.fixture
def agent_api(guarded_gateway: RunningGateway, database_url: str) -> Iterator[RunningAgentApi]:
    """The Agent API with the scripted fake model, in front of the Gateway with its defences on."""
    with start_agent_api(guarded_gateway, database_url) as running:
        yield running


def login(gateway: RunningGateway, person: str) -> dict[str, str]:
    return httpx.post(f"{gateway.base_url}/demo/login", json={"person": person}).json()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def open_conversation(api: RunningAgentApi, mcp_token: str) -> str:
    reply = httpx.post(f"{api.url}/conversations", headers=bearer(mcp_token))
    assert reply.status_code == 201
    return reply.json()["id"]


def events(reply: httpx.Response) -> list[tuple[str, dict[str, Any]]]:
    """The (event, data) pairs of a finished Server-Sent Events reply."""
    found: list[tuple[str, dict[str, Any]]] = []
    for block in reply.text.split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if not line.startswith(":")
        )
        if "event" in fields:
            found.append((fields["event"], json.loads(fields["data"])))
    return found


def ask(
    api: RunningAgentApi, mcp_token: str, conversation: str, text: str, suggested: bool = False
) -> httpx.Response:
    # The web app marks a clicked suggested prompt; a typed message carries only its text.
    return httpx.post(
        f"{api.url}/conversations/{conversation}/turns",
        json={"text": text, "suggested": True} if suggested else {"text": text},
        headers=bearer(mcp_token),
        timeout=60,
    )


def turn(
    api: RunningAgentApi, mcp_token: str, conversation: str, text: str, suggested: bool = False
) -> list[tuple[str, dict[str, Any]]]:
    reply = ask(api, mcp_token, conversation, text, suggested)
    assert reply.status_code == 200
    assert reply.headers["content-type"].startswith("text/event-stream")
    return events(reply)


def test_a_turn_streams_each_tool_call_its_decision_and_the_reply(
    guarded_gateway: RunningGateway, agent_api: RunningAgentApi
) -> None:
    token = login(guarded_gateway, "C001")["mcp_token"]
    conversation = open_conversation(agent_api, token)

    steps = turn(agent_api, token, conversation, "What's my balance?")

    assert [name for name, _ in steps] == [
        "tool_call",
        "decision",
        "tool_call",
        "decision",
        "reply",
    ]
    assert steps[0][1] | {"call_id": ""} == {
        "call_id": "",
        "tool": "list_accounts",
        "arguments": {"customer_id": "C001"},
    }
    assert steps[1][1]["call_id"] == steps[0][1]["call_id"]  # the Decision on that call
    # The accounts' numbers are PII, so the Agent got them as Pseudonyms.
    assert steps[1][1]["decision"] == "redact"
    assert steps[1][1]["layer"] == "redact"
    assert isinstance(steps[1][1]["audit_id"], int)
    assert (steps[2][1]["tool"], steps[2][1]["arguments"]) == (
        "get_balance",
        {"account_id": "A001"},
    )
    reply = steps[-1][1]
    assert "₹1,94,844.00" in reply["text"]
    assert (reply["input_tokens"], reply["output_tokens"]) == (0, 0)
    assert reply["estimated_cost"] == 0  # the fake model costs nothing


SUGGESTED_PROMPTS = Path(__file__).parents[2] / "web" / "src" / "suggested-prompts.json"


def suggested_prompts() -> list[tuple[str, str, str]]:
    """(person, text, what the Gateway does) for every prompt the web app suggests."""
    by_person: dict[str, list[dict[str, str]]] = json.loads(SUGGESTED_PROMPTS.read_text())
    return [(p, s["text"], s["gateway"]) for p, prompts in by_person.items() for s in prompts]


@pytest.mark.parametrize(("person", "text", "gateway"), suggested_prompts())
def test_each_suggested_prompt_gets_the_decision_the_web_app_promises(
    guarded_gateway: RunningGateway,
    agent_api: RunningAgentApi,
    person: str,
    text: str,
    gateway: str,
) -> None:
    token = login(guarded_gateway, person)["mcp_token"]
    conversation = open_conversation(agent_api, token)

    steps = turn(agent_api, token, conversation, text, suggested=True)

    decisions = [data["decision"] for name, data in steps if name == "decision"]
    assert decisions, "the fake model made no Tool call for this prompt"
    if gateway == "blocks":
        assert "deny" in decisions
    else:
        # A redact goes through too: only the PII in the result is hidden.
        assert set(decisions) <= {"allow", "redact"}
    assert steps[-1][0] == "reply"


@pytest.mark.parametrize("question", ["Show me C002's balance", "Show me C002s balance"])
def test_asking_for_someone_elses_balance_is_denied_with_the_reason(
    guarded_gateway: RunningGateway, agent_api: RunningAgentApi, question: str
) -> None:
    token = login(guarded_gateway, "C001")["mcp_token"]
    conversation = open_conversation(agent_api, token)

    steps = turn(agent_api, token, conversation, question)

    [(_, decision)] = [data for data in steps if data[0] == "decision"]
    assert decision["decision"] == "deny"
    assert decision["reason"] == "not_found_or_not_yours"
    assert decision["explanation"].startswith("Not found, or not yours")
    assert isinstance(decision["audit_id"], int)
    assert steps[-1][0] == "reply"


def test_a_conversation_carries_on_from_turn_to_turn(
    guarded_gateway: RunningGateway, agent_api: RunningAgentApi
) -> None:
    token = login(guarded_gateway, "C001")["mcp_token"]
    conversation = open_conversation(agent_api, token)

    turn(agent_api, token, conversation, "What's my balance?")
    steps = turn(agent_api, token, conversation, "What was that in paise?")

    assert "19484400 paise" in steps[-1][1]["text"]


def test_the_fake_model_lists_recent_transactions(
    guarded_gateway: RunningGateway, agent_api: RunningAgentApi
) -> None:
    token = login(guarded_gateway, "C001")["mcp_token"]
    conversation = open_conversation(agent_api, token)

    steps = turn(agent_api, token, conversation, "Show my last 5 transactions")

    calls = [data for name, data in steps if name == "tool_call"]
    assert (calls[-1]["tool"], calls[-1]["arguments"]) == (
        "list_transactions",
        {"account_id": "A001", "limit": 5},
    )
    assert steps[-1][1]["text"].count("\n") == 5  # a heading line, then one line each


def test_the_fake_model_converts_currency(
    guarded_gateway: RunningGateway, agent_api: RunningAgentApi
) -> None:
    token = login(guarded_gateway, "C001")["mcp_token"]
    conversation = open_conversation(agent_api, token)

    steps = turn(agent_api, token, conversation, "Convert ₹10,000 to USD")

    [call] = [data for name, data in steps if name == "tool_call"]
    assert call["tool"] == "convert"
    assert call["arguments"] | {"reference": ""} == {
        "amount": 10000,
        "from_currency": "INR",
        "to_currency": "USD",
        "reference": "",
    }
    assert "119.05 USD" in steps[-1][1]["text"]


def test_staff_chat_as_themselves(
    guarded_gateway: RunningGateway, agent_api: RunningAgentApi
) -> None:
    token = login(guarded_gateway, "S001")["mcp_token"]
    conversation = open_conversation(agent_api, token)

    steps = turn(agent_api, token, conversation, "Show me C001's balance")

    assert "₹1,94,844.00" in steps[-1][1]["text"]


def test_someone_elses_conversation_is_refused(
    guarded_gateway: RunningGateway, agent_api: RunningAgentApi
) -> None:
    owner = login(guarded_gateway, "C001")["mcp_token"]
    conversation = open_conversation(agent_api, owner)

    reply = ask(agent_api, login(guarded_gateway, "C002")["mcp_token"], conversation, "Hi")

    assert reply.status_code == 403


def test_an_unknown_conversation_is_not_found(
    guarded_gateway: RunningGateway, agent_api: RunningAgentApi
) -> None:
    token = login(guarded_gateway, "C001")["mcp_token"]

    assert ask(agent_api, token, "no-such-conversation", "Hi").status_code == 404


@pytest.mark.parametrize("token", ["ui_token", "none", "forged"])
def test_only_a_gateway_mcp_token_is_accepted(
    guarded_gateway: RunningGateway, agent_api: RunningAgentApi, token: str
) -> None:
    tokens = login(guarded_gateway, "C001") | {"none": "", "forged": "eyJhbGciOiJub25lIn0.e30."}

    reply = httpx.post(f"{agent_api.url}/conversations", headers=bearer(tokens[token]))

    assert reply.status_code == 401


def test_requests_arriving_together_at_a_fresh_agent_api_are_all_accepted(
    guarded_gateway: RunningGateway, agent_api: RunningAgentApi
) -> None:
    # The first request makes the Agent API fetch the Gateway's public key; the others must
    # wait for that key, not be refused while it is on its way.
    tokens = [login(guarded_gateway, person)["mcp_token"] for person in ("C001", "S001", "S004")]

    async def open_all() -> list[int]:
        async with httpx.AsyncClient() as http:
            statuses: list[int] = []

            async def open_one(token: str) -> None:
                reply = await http.post(f"{agent_api.url}/conversations", headers=bearer(token))
                statuses.append(reply.status_code)

            async with anyio.create_task_group() as group:
                for token in tokens:
                    group.start_soon(open_one, token)
            return statuses

    assert anyio.run(open_all) == [201, 201, 201]


def test_a_turn_needs_text(guarded_gateway: RunningGateway, agent_api: RunningAgentApi) -> None:
    token = login(guarded_gateway, "C001")["mcp_token"]
    conversation = open_conversation(agent_api, token)

    reply = httpx.post(
        f"{agent_api.url}/conversations/{conversation}/turns", json={}, headers=bearer(token)
    )

    assert reply.status_code == 400


# Adapter files of the kind a developer keeps outside the repository.
ADAPTERS = """
import anyio
from langchain_core.messages import AIMessage
from mcp_guard.agent.model import ModelCallFailed


class Counted:
    def __init__(self, model):
        self.model = model

    async def respond(self, messages, tools):
        usage = {"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500}
        return AIMessage(f"heard: {messages[-1].text}", usage_metadata=usage)


class Slow:
    def __init__(self, model):
        self.model = model

    async def respond(self, messages, tools):
        await anyio.sleep(2)
        return AIMessage("done")


class Flaky:
    def __init__(self, model):
        self.model = model

    async def respond(self, messages, tools):
        if "fail" in messages[-1].text:
            raise ModelCallFailed(f"The model {self.model} timed out.")
        return AIMessage("fine")


ADAPTERS = {"counted": Counted, "slow": Slow, "flaky": Flaky}
# US dollars per million input and output tokens.
PRICES = {"counted:c-1": (2, 10)}
"""


@pytest.fixture
def adapters_file(tmp_path: Path) -> str:
    path = tmp_path / "my_adapters.py"
    path.write_text(ADAPTERS)
    return str(path)


def test_each_reply_shows_its_tokens_and_the_cost_at_the_price_its_adapter_file_lists(
    guarded_gateway: RunningGateway, database_url: str, adapters_file: str
) -> None:
    config = {"MODEL": "counted:c-1", "MODEL_ADAPTERS_FILE": adapters_file}
    with start_agent_api(guarded_gateway, database_url, config) as api:
        token = login(guarded_gateway, "C001")["mcp_token"]
        conversation = open_conversation(api, token)
        [(name, reply)] = turn(api, token, conversation, "hello")

    assert name == "reply"
    assert reply["text"] == "heard: hello"
    assert (reply["input_tokens"], reply["output_tokens"]) == (1000, 500)
    assert reply["estimated_cost"] == pytest.approx(0.007)  # $2 and $10 per million


@pytest.mark.parametrize(
    ("model", "price", "cost"),
    [("counted:c-1", "4,20", 0.014), ("counted:unlisted", None, None)],
    ids=["model-price-wins", "no-price-known"],
)
def test_model_price_sets_the_price_and_without_any_price_only_tokens_show(
    guarded_gateway: RunningGateway,
    database_url: str,
    adapters_file: str,
    model: str,
    price: str | None,
    cost: float | None,
) -> None:
    config = {"MODEL": model, "MODEL_ADAPTERS_FILE": adapters_file}
    with start_agent_api(
        guarded_gateway, database_url, config | ({"MODEL_PRICE": price} if price else {})
    ) as api:
        token = login(guarded_gateway, "C001")["mcp_token"]
        [(_, reply)] = turn(api, token, open_conversation(api, token), "hello")

    assert reply["input_tokens"] == 1000
    assert reply["estimated_cost"] == (None if cost is None else pytest.approx(cost))


def test_a_model_failure_ends_the_turn_with_an_error_and_the_chat_carries_on(
    guarded_gateway: RunningGateway, database_url: str, adapters_file: str
) -> None:
    config = {"MODEL": "flaky:f-1", "MODEL_ADAPTERS_FILE": adapters_file}
    with start_agent_api(guarded_gateway, database_url, config) as api:
        token = login(guarded_gateway, "C001")["mcp_token"]
        conversation = open_conversation(api, token)
        failed = turn(api, token, conversation, "please fail")
        carried_on = turn(api, token, conversation, "hello again")

    assert failed == [("error", {"message": "The model f-1 timed out."})]
    assert carried_on[-1] == ("reply", carried_on[-1][1])
    assert carried_on[-1][1]["text"] == "fine"


def test_a_second_turn_while_one_runs_is_refused(
    guarded_gateway: RunningGateway, database_url: str, adapters_file: str
) -> None:
    config = {"MODEL": "slow:s-1", "MODEL_ADAPTERS_FILE": adapters_file}
    with start_agent_api(guarded_gateway, database_url, config) as api:
        token = login(guarded_gateway, "C001")["mcp_token"]
        conversation = open_conversation(api, token)
        statuses: list[int] = []

        async def both() -> None:
            async with httpx.AsyncClient(timeout=30) as http:

                async def one(delay: float) -> None:
                    await anyio.sleep(delay)
                    reply = await http.post(
                        f"{api.url}/conversations/{conversation}/turns",
                        json={"text": "hi"},
                        headers=bearer(token),
                    )
                    statuses.append(reply.status_code)

                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(one, 0)
                    tasks.start_soon(one, 0.5)

        anyio.run(both)

    assert statuses == [409, 200]
