"""The Agent: a LangGraph loop between a model and the Gateway, with memory in Postgres.

Each turn, the model sees the conversation so far and the Gateway's tools. If it asks for
Tool calls, the Agent makes them through the Gateway (its only way to reach any tool),
adds the results to the conversation and asks the model again, until the model replies.

Conversations are stored by the LangGraph Postgres checkpointer, keyed by conversation ID,
so a conversation carries on after the Agent restarts.

Each turn is one trace: a span for the turn, a span for every model call (with its tokens,
named by the OpenTelemetry GenAI conventions) and the MCP SDK's span for every Tool call,
which carries the trace on into the Gateway. Every Tool call names its conversation in its
`_meta` (under `mcp-guard/conversation`), so the Gateway's Audit log can tell them apart.

`Agent.turn` reports each step of a turn as it happens: every Tool call the model asks for,
then the Gateway's Decision on it (from the result's `_meta`, under `mcp-guard/decision`), and
returns the reply with the tokens every model call of the turn used.
"""

# LangGraph is a namespace package that pyright doesn't see as typed, and some of its
# signatures carry unparameterised generics. Our own code here is still checked strictly.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

import json
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict, cast

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from mcp import Client
from mcp.types import RequestParamsMeta, TextContent
from opentelemetry import trace
from opentelemetry.trace import Span

from mcp_guard.agent.gateway import connect
from mcp_guard.agent.model import Model
from mcp_guard.agent.person import Person

_tracer = trace.get_tracer("mcp_guard.agent")

# Where every Tool call names its conversation for the Gateway, in the call's `_meta`.
CONVERSATION_META_KEY = "mcp-guard/conversation"
# Where the Gateway puts its Decision on a Tool call, in the result's `_meta`.
DECISION_META_KEY = "mcp-guard/decision"


class Conversation(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


@dataclass(frozen=True)
class ToolCalled:
    """The model asked for a Tool call; the Agent makes it through the Gateway next."""

    call_id: str  # pairs it with its Decision
    tool: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Decided:
    """The Gateway's Decision on a Tool call, as its result's `_meta` carries it."""

    call_id: str
    decision: str  # allow, redact, hold or deny
    reason: str | None
    explanation: str | None
    layer: str
    audit_id: int | None  # the number of its Audit entry


type Step = ToolCalled | Decided


@dataclass(frozen=True)
class Reply:
    text: str
    input_tokens: int  # summed over every model call of the turn
    output_tokens: int


async def _ignore(step: Step) -> None:
    pass


class Agent:
    def __init__(self, graph: CompiledStateGraph[Conversation]) -> None:
        self._graph = graph

    async def ask(self, conversation_id: str, text: str) -> str:
        """Add the person's message to the conversation and return the Agent's reply."""
        return (await self.turn(conversation_id, text)).text

    async def turn(
        self,
        conversation_id: str,
        text: str,
        on_step: Callable[[Step], Awaitable[None]] = _ignore,
    ) -> Reply:
        """Add the person's message to the conversation, tell `on_step` each step as it
        happens, and return the Agent's reply.

        Raises ModelCallFailed when the model can't answer.
        """
        # Langfuse groups a conversation's turns by session.id.
        attributes = {"gen_ai.conversation.id": conversation_id, "session.id": conversation_id}
        input_tokens = output_tokens = 0
        reply_text = ""
        with _tracer.start_as_current_span("turn", attributes=attributes):
            updates = self._graph.astream(
                Conversation(messages=[HumanMessage(text)]),
                {"configurable": {"thread_id": conversation_id}},
                stream_mode="updates",
            )
            async for message in _new_messages(updates):
                if isinstance(message, AIMessage):  # the model's: a reply, or Tool calls
                    if usage := message.usage_metadata:
                        input_tokens += usage["input_tokens"]
                        output_tokens += usage["output_tokens"]
                    reply_text = message.text
                    for call in message.tool_calls:
                        await on_step(ToolCalled(call["id"] or "", call["name"], call["args"]))
                elif isinstance(message, ToolMessage) and (decided := _decided(message)):
                    await on_step(decided)
        return Reply(reply_text, input_tokens, output_tokens)


@asynccontextmanager
async def start_agent(
    model: Model,
    gateway_url: str,
    database_url: str,
    *,
    person: Person,
    mcp_token: str,
    model_name: str | None = None,
) -> AsyncGenerator[Agent]:
    """Connect to the Gateway and the conversation store, working for one person.

    `mcp_token` is the person's MCP token from logging in (see mcp_guard.agent.gateway); the
    Agent has no token of its own. `model_name` is the model's `provider:model` name, if it
    has one, for its traces.
    """
    provider, _, requested_model = (model_name or "").partition(":")
    model_span = f"chat {requested_model}" if requested_model else "chat"
    model_attributes = {"gen_ai.operation.name": "chat"}
    if requested_model:
        model_attributes |= {
            "gen_ai.provider.name": provider,
            "gen_ai.request.model": requested_model,
        }
    async with (
        connect(gateway_url, mcp_token) as gateway,
        AsyncPostgresSaver.from_conn_string(database_url) as checkpointer,
    ):
        await checkpointer.setup()
        # Kept out of the stored conversation: who is being served is decided per session.
        system = SystemMessage(
            f"You answer for the support desk of an Indian bank. {person.introduction()} "
            "Use the tools to answer; never guess account details."
        )

        async def call_model(state: Conversation) -> Conversation:
            # Listed afresh for every model call, so the model sees each tool as it is now.
            # The Gateway lists every tool on one page.
            tools = (await gateway.list_tools()).tools
            with _tracer.start_as_current_span(model_span, attributes=model_attributes) as span:
                reply = await model.respond([system, *state["messages"]], tools)
                _record_model_call(span, reply)
            return Conversation(messages=[reply])

        async def call_tools(state: Conversation, config: RunnableConfig) -> Conversation:
            last = state["messages"][-1]
            assert isinstance(last, AIMessage)
            conversation_id = str(config.get("configurable", {})["thread_id"])
            results: list[AnyMessage] = [
                await _call_through_gateway(gateway, call, conversation_id)
                for call in last.tool_calls
            ]
            return Conversation(messages=results)

        graph = (
            StateGraph(Conversation)
            .add_node("model", call_model)
            .add_node("tools", call_tools)
            .add_edge(START, "model")
            .add_conditional_edges("model", _after_model)
            .add_edge("tools", "model")
            .compile(checkpointer=checkpointer)
        )
        yield Agent(graph)


async def _new_messages(updates: AsyncIterator[Any]) -> AsyncIterator[AnyMessage]:
    """The messages each step of the graph adds, in order, from its "updates" stream."""
    async for update in updates:
        for state in cast(dict[str, Conversation], update).values():
            for message in state["messages"]:
                yield message


def _decided(result: ToolMessage) -> Decided | None:
    """The Gateway's Decision kept beside a Tool call's result, if the Gateway gave one.

    Only the fields known here are read, so a field the Gateway adds later is ignored.
    """
    meta: dict[str, Any] | None = result.response_metadata.get("decision")
    if not meta:
        return None
    return Decided(
        call_id=result.tool_call_id,
        decision=str(meta["decision"]),
        reason=meta.get("reason"),
        explanation=meta.get("explanation"),
        layer=str(meta.get("layer", "")),
        audit_id=meta.get("audit_id"),
    )


def _record_model_call(span: Span, reply: AIMessage) -> None:
    """Put the model that answered and the tokens it used on its span, where the reply says them."""
    if model_name := reply.response_metadata.get("model_name"):
        span.set_attribute("gen_ai.response.model", model_name)
    if usage := reply.usage_metadata:
        span.set_attribute("gen_ai.usage.input_tokens", usage["input_tokens"])
        span.set_attribute("gen_ai.usage.output_tokens", usage["output_tokens"])


def _after_model(state: Conversation) -> str:
    last = state["messages"][-1]
    return "tools" if isinstance(last, AIMessage) and last.tool_calls else END


async def _call_through_gateway(
    gateway: Client, call: ToolCall, conversation_id: str
) -> ToolMessage:
    """Make one Tool call through the Gateway; its result becomes a message for the model.

    The call names its conversation in `_meta`, so the Gateway can tell conversations apart.
    The Gateway's Decision, from the result's `_meta`, is kept in the message's metadata.
    """
    meta: RequestParamsMeta = {CONVERSATION_META_KEY: conversation_id}
    result = await gateway.call_tool(call["name"], call["args"], meta=meta)
    decision = (result.meta or {}).get(DECISION_META_KEY)
    if result.structured_content is not None:
        content = json.dumps(result.structured_content, ensure_ascii=False)
    else:
        content = _text(result.content)
    return ToolMessage(
        content,
        tool_call_id=call["id"],
        name=call["name"],
        status="error" if result.is_error else "success",
        # Kept beside the result, not shown to the model, so a turn can report it.
        response_metadata={"decision": decision} if decision else {},
    )


def _text(content: Sequence[object]) -> str:
    return "\n".join(c.text for c in content if isinstance(c, TextContent))
