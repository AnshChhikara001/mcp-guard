"""The real model adapters, and choosing one by configuration.

A model is named `provider:model`, for example `gemini:gemini-3.5-flash` or
`openai:gpt-5-mini`. Gemini and OpenAI are built in; each reads its API key and base URL
the way its own SDK does (GOOGLE_API_KEY, else GEMINI_API_KEY; OPENAI_API_KEY). Further
providers come from a Python file outside the repository that defines

    ADAPTERS = {"local": make_local_model}

where `make_local_model("some-model")` returns any object with the Model interface. The file
may also list its models' prices, in US dollars per million input and output tokens:

    PRICES = {"local:some-model": (2, 10)}

`configured_model` picks the model the MODEL environment variable names (and
MODEL_ADAPTERS_FILE), or else the scripted fake model, for the chats and the Agent API;
`configured_price` its price: MODEL_PRICE if set, else the listed one (see `price_of`).
"""

import importlib.util
import os
import time
from collections.abc import Callable, Sequence
from functools import cache
from types import ModuleType
from typing import Any, cast

import httpx
from langchain_core.exceptions import (
    ModelError,
    ModelNotFoundError,
    ModelRateLimitError,
    ModelTimeoutError,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from mcp.types import Tool

from mcp_guard.agent.cost import FREE, LISTED_PRICES, Price
from mcp_guard.agent.fake_model import ScriptedModel, demo_script
from mcp_guard.agent.model import Model, ModelCallFailed

type ModelFactory = Callable[[str], Model]

# One retry rides out a brief hiccup; more would keep the person waiting on a quota that is
# used up, which a free tier often is.
RETRIES = 1

DEFAULT_TIMEOUT_SECONDS = 60

# Providers' error texts can run long; the start says what went wrong.
ERROR_DETAIL_LENGTH = 200


class ChatModelAdapter:
    """Puts any LangChain chat model behind the Model interface."""

    def __init__(self, name: str, chat_model: BaseChatModel, timeout_seconds: float) -> None:
        self._name = name
        self._chat_model = chat_model
        self._timeout_seconds = timeout_seconds

    async def respond(self, messages: Sequence[BaseMessage], tools: Sequence[Tool]) -> AIMessage:
        bound = self._chat_model.bind_tools([_as_provider_tool(tool) for tool in tools])
        started = time.perf_counter()
        try:
            reply = await bound.ainvoke(list(messages))
        except ModelRateLimitError as e:
            raise ModelCallFailed(
                f"The model {self._name} is rate-limited (its quota is used up). "
                "Wait a minute and try again."
            ) from e
        # Google's SDK lets its HTTP client's timeout through unwrapped.
        except (ModelTimeoutError, httpx.TimeoutException) as e:
            raise ModelCallFailed(
                f"The model {self._name} timed out: it gave no answer within "
                f"{self._timeout_seconds:g} seconds per attempt. Try again."
            ) from e
        except ModelNotFoundError as e:
            raise ModelCallFailed(
                f"There is no model {self._name} for this API key (providers retire old "
                "models). Pick one the provider lists, in MODEL."
            ) from e
        # Anything else the provider refused or failed at, such as an overloaded server.
        except ModelError as e:
            raise ModelCallFailed(
                f"The model {self._name} failed: {str(e)[:ERROR_DETAIL_LENGTH]}. Try again, "
                "or pick another model in MODEL."
            ) from e
        assert isinstance(reply, AIMessage)
        reply.response_metadata["latency_seconds"] = time.perf_counter() - started
        return reply


def openai_model(model: str) -> Model:
    timeout = _timeout_seconds()
    chat_model = ChatOpenAI(
        model=model, use_responses_api=False, timeout=timeout, max_retries=RETRIES
    )
    return ChatModelAdapter(f"openai:{model}", chat_model, timeout)


def gemini_model(model: str) -> Model:
    timeout = _timeout_seconds()
    # Google's SDK counts the first attempt as a retry.
    chat_model = ChatGoogleGenerativeAI(model=model, timeout=timeout, max_retries=RETRIES + 1)
    return ChatModelAdapter(f"gemini:{model}", chat_model, timeout)


BUILT_IN: dict[str, ModelFactory] = {"openai": openai_model, "gemini": gemini_model}


def load_model(name: str, adapters_file: str | None = None) -> Model:
    """The model named `provider:model`, from the built-in providers or the adapters file."""
    providers = BUILT_IN | (_load_adapters(adapters_file) if adapters_file else {})
    provider, _, model = name.partition(":")
    if not model:
        raise ValueError(
            f"Name the model as provider:model (e.g. gemini:gemini-3.5-flash), not {name!r}"
        )
    if provider not in providers:
        raise ValueError(
            f"There is no model provider {provider!r}. The providers are: "
            + ", ".join(sorted(providers))
        )
    return providers[provider](model)


@cache
def _adapters_module(path: str) -> ModuleType:
    """The Python file outside the repository, run once however often it is asked for."""
    path = os.path.expanduser(path)
    spec = importlib.util.spec_from_file_location("mcp_guard_external_adapters", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"The adapters file {path} is not a Python file")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_adapters(path: str) -> dict[str, ModelFactory]:
    """The ADAPTERS a Python file outside the repository defines."""
    adapters = getattr(_adapters_module(path), "ADAPTERS", None)
    if not isinstance(adapters, dict):
        raise ValueError(f"The adapters file {path} must define ADAPTERS as a dict")
    if clashes := BUILT_IN.keys() & adapters.keys():
        raise ValueError(f"The adapters file {path} redefines built-in providers: {clashes}")
    return cast(dict[str, ModelFactory], adapters)


def _as_provider_tool(tool: Tool) -> dict[str, Any]:
    """An MCP tool in the format both providers' LangChain models accept."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.input_schema,
        },
    }


def _timeout_seconds() -> float:
    """How long one request to the model may take: MODEL_TIMEOUT_SECONDS, or a minute."""
    return float(os.environ.get("MODEL_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))


def configured_model(person_id: str) -> tuple[str | None, Model]:
    """The model MODEL names, with that name; without MODEL, the scripted fake model working
    for `person_id`, named None."""
    if name := os.environ.get("MODEL"):
        return name, load_model(name, os.environ.get("MODEL_ADAPTERS_FILE"))
    return None, ScriptedModel(demo_script(person_id))


def price_of(name: str, adapters_file: str | None = None) -> Price | None:
    """The price of the model named `provider:model`: listed in the adapters file's PRICES, or
    else in the built-in list; None if neither lists it."""
    if adapters_file:
        listed = getattr(_adapters_module(adapters_file), "PRICES", {})
        if not isinstance(listed, dict):
            raise ValueError(f"The adapters file {adapters_file} must define PRICES as a dict")
        prices = cast(dict[str, tuple[float, float]], listed)
        if name in prices:
            input_price, output_price = prices[name]
            return Price(input_per_million=input_price, output_per_million=output_price)
    return LISTED_PRICES.get(name)


def configured_price() -> Price | None:
    """The price of the model `configured_model` picks: MODEL_PRICE (like `2,10`) if set, else
    its listed price; the scripted fake model is free. None if no price is known.

    Raises ValueError if MODEL_PRICE or the adapters file's PRICES is malformed.
    """
    if setting := os.environ.get("MODEL_PRICE"):
        return Price.parse(setting)
    if name := os.environ.get("MODEL"):
        return price_of(name, os.environ.get("MODEL_ADAPTERS_FILE"))
    return FREE
