"""Seam 2: the Gemini and OpenAI adapters behind the model interface, against a fake provider."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from mcp.types import Tool

from conftest import FakeProvider, Reply
from mcp_guard.agent.adapters import load_model
from mcp_guard.agent.model import ModelCallFailed

pytestmark = pytest.mark.anyio

LIST_ACCOUNTS = Tool(
    name="list_accounts",
    description="List a Customer's accounts.",
    input_schema={
        "type": "object",
        "properties": {"customer_id": {"type": "string"}},
        "required": ["customer_id"],
    },
)

CONVERSATION = [SystemMessage("You serve Customer C001."), HumanMessage("What's my balance?")]


@dataclass(frozen=True)
class Provider:
    """How one provider's API is configured, and what its requests and replies look like."""

    model: str  # the MODEL configuration value
    base_url_variable: str
    base_url_path: str
    key_variable: str
    calls_tool: Callable[[str, dict[str, object], int, int], dict[str, object]]
    offered_tools: Callable[[dict[str, Any]], list[str]]


def _openai_calls_tool(
    name: str, args: dict[str, object], input_tokens: int, output_tokens: int
) -> dict[str, object]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-test",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    }


def _gemini_calls_tool(
    name: str, args: dict[str, object], input_tokens: int, output_tokens: int
) -> dict[str, object]:
    return {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"functionCall": {"name": name, "args": args}}],
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": input_tokens,
            "candidatesTokenCount": output_tokens,
            "totalTokenCount": input_tokens + output_tokens,
        },
    }


OPENAI = Provider(
    model="openai:gpt-test",
    base_url_variable="OPENAI_BASE_URL",
    base_url_path="/v1",
    key_variable="OPENAI_API_KEY",
    calls_tool=_openai_calls_tool,
    offered_tools=lambda request: [tool["function"]["name"] for tool in request["tools"]],
)

GEMINI = Provider(
    model="gemini:gemini-test",
    base_url_variable="GOOGLE_GEMINI_BASE_URL",
    base_url_path="",
    key_variable="GOOGLE_API_KEY",
    calls_tool=_gemini_calls_tool,
    offered_tools=lambda request: [
        declaration["name"]
        for tool in request["tools"]
        for declaration in tool["functionDeclarations"]
    ],
)


def _calls_list_accounts(provider: Provider) -> dict[str, object]:
    """A reply in which the model calls list_accounts, using 12 input and 5 output tokens."""
    return provider.calls_tool("list_accounts", {"customer_id": "C001"}, 12, 5)


@pytest.fixture(params=[OPENAI, GEMINI], ids=["openai", "gemini"])
def provider(
    request: pytest.FixtureRequest, fake_provider: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> Provider:
    """One provider's adapter, configured to talk to the fake provider."""
    provider: Provider = request.param
    monkeypatch.setenv(provider.base_url_variable, fake_provider.url + provider.base_url_path)
    monkeypatch.setenv(provider.key_variable, "test-key")
    return provider


async def test_adapter_offers_the_tools_and_returns_the_models_tool_calls(
    provider: Provider, fake_provider: FakeProvider
) -> None:
    fake_provider.replies.append(Reply(_calls_list_accounts(provider)))
    model = load_model(provider.model)

    reply = await model.respond(CONVERSATION, [LIST_ACCOUNTS])

    assert provider.offered_tools(fake_provider.requests[0]) == ["list_accounts"]
    assert [(call["name"], call["args"]) for call in reply.tool_calls] == [
        ("list_accounts", {"customer_id": "C001"})
    ]


async def test_adapter_reports_the_tokens_the_model_used(
    provider: Provider, fake_provider: FakeProvider
) -> None:
    fake_provider.replies.append(Reply(_calls_list_accounts(provider)))
    model = load_model(provider.model)

    reply = await model.respond(CONVERSATION, [LIST_ACCOUNTS])

    assert reply.usage_metadata is not None
    assert (reply.usage_metadata["input_tokens"], reply.usage_metadata["output_tokens"]) == (12, 5)


async def test_adapter_reports_how_long_the_model_took(
    provider: Provider, fake_provider: FakeProvider
) -> None:
    fake_provider.replies.append(Reply(_calls_list_accounts(provider), delay_seconds=0.3))
    model = load_model(provider.model)

    reply = await model.respond(CONVERSATION, [LIST_ACCOUNTS])

    assert 0.3 <= reply.response_metadata["latency_seconds"] < 2


async def test_adapter_says_clearly_when_the_model_is_rate_limited(
    provider: Provider, fake_provider: FakeProvider
) -> None:
    quota_used_up = {
        "error": {"code": 429, "message": "Quota exceeded.", "status": "RESOURCE_EXHAUSTED"}
    }
    fake_provider.replies.append(Reply(quota_used_up, status=429))
    model = load_model(provider.model)

    with pytest.raises(ModelCallFailed) as failure:
        await model.respond(CONVERSATION, [LIST_ACCOUNTS])

    assert provider.model in str(failure.value)
    assert "rate-limited" in str(failure.value)


async def test_adapter_says_clearly_when_the_model_times_out(
    provider: Provider, fake_provider: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MODEL_TIMEOUT_SECONDS", "0.2")
    fake_provider.replies.append(Reply(_calls_list_accounts(provider), delay_seconds=2))
    model = load_model(provider.model)

    with pytest.raises(ModelCallFailed) as failure:
        await model.respond(CONVERSATION, [LIST_ACCOUNTS])

    assert provider.model in str(failure.value)
    assert "timed out" in str(failure.value)


async def test_adapter_says_clearly_when_the_model_does_not_exist(
    provider: Provider, fake_provider: FakeProvider
) -> None:
    retired = {"error": {"code": 404, "message": "Model not found.", "status": "NOT_FOUND"}}
    fake_provider.replies.append(Reply(retired, status=404))
    model = load_model(provider.model)

    with pytest.raises(ModelCallFailed) as failure:
        await model.respond(CONVERSATION, [LIST_ACCOUNTS])

    assert f"There is no model {provider.model}" in str(failure.value)


async def test_adapter_says_clearly_when_the_provider_fails(
    provider: Provider, fake_provider: FakeProvider
) -> None:
    overloaded = {"error": {"code": 503, "message": "High demand.", "status": "UNAVAILABLE"}}
    fake_provider.replies.append(Reply(overloaded, status=503))
    model = load_model(provider.model)

    with pytest.raises(ModelCallFailed) as failure:
        await model.respond(CONVERSATION, [LIST_ACCOUNTS])

    assert f"The model {provider.model} failed" in str(failure.value)
    assert "High demand." in str(failure.value)


def test_unknown_provider_names_the_providers_there_are() -> None:
    with pytest.raises(ValueError, match=r"'mistral'.*gemini.*openai"):
        load_model("mistral:large")
