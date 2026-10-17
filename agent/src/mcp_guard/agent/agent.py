"""The Agent: a LangGraph loop between a model and the Gateway, with memory in Postgres.

Each turn, the model sees the conversation so far and the Gateway's tools. If it asks for
Tool calls, the Agent makes them through the Gateway (its only way to reach any tool),
adds the results to the conversation and asks the model again, until the model replies.

Conversations are stored by the LangGraph Postgres checkpointer, keyed by conversation ID,
so a conversation carries on after the Agent restarts.
"""

# LangGraph is a namespace package that pyright doesn't see as typed, and some of its
# signatures carry unparameterised generics. Our own code here is still checked strictly.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

import json
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from typing import Annotated, TypedDict

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
)
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from mcp import Client
from mcp.types import TextContent

from mcp_guard.agent.model import Model


class Conversation(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


class Agent:
    def __init__(self, graph: CompiledStateGraph[Conversation]) -> None:
        self._graph = graph

    async def ask(self, conversation_id: str, text: str) -> str:
        """Add the person's message to the conversation and return the Agent's reply."""
        conversation = await self._graph.ainvoke(
            Conversation(messages=[HumanMessage(text)]),
            {"configurable": {"thread_id": conversation_id}},
        )
        return conversation["messages"][-1].text


@asynccontextmanager
async def start_agent(
    model: Model, gateway_url: str, database_url: str, *, customer_id: str
) -> AsyncGenerator[Agent]:
    """Connect to the Gateway and the conversation store, serving one Customer."""
    async with (
        Client(gateway_url) as gateway,
        AsyncPostgresSaver.from_conn_string(database_url) as checkpointer,
    ):
        await checkpointer.setup()
        # The Gateway lists every tool on one page.
        tools = (await gateway.list_tools()).tools
        # Kept out of the stored conversation: who is being served is decided per session.
        system = SystemMessage(
            "You answer for the support desk of an Indian bank. You are serving Customer "
            f"{customer_id}. Use the tools to answer; never guess account details."
        )

        async def call_model(state: Conversation) -> Conversation:
            reply = await model.respond([system, *state["messages"]], tools)
            return Conversation(messages=[reply])

        async def call_tools(state: Conversation) -> Conversation:
            last = state["messages"][-1]
            assert isinstance(last, AIMessage)
            results: list[AnyMessage] = [
                await _call_through_gateway(gateway, call) for call in last.tool_calls
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


def _after_model(state: Conversation) -> str:
    last = state["messages"][-1]
    return "tools" if isinstance(last, AIMessage) and last.tool_calls else END


async def _call_through_gateway(gateway: Client, call: ToolCall) -> ToolMessage:
    """Make one Tool call through the Gateway; its result becomes a message for the model."""
    result = await gateway.call_tool(call["name"], call["args"])
    if result.structured_content is not None:
        content = json.dumps(result.structured_content, ensure_ascii=False)
    else:
        content = _text(result.content)
    return ToolMessage(
        content,
        tool_call_id=call["id"],
        name=call["name"],
        status="error" if result.is_error else "success",
    )


def _text(content: Sequence[object]) -> str:
    return "\n".join(c.text for c in content if isinstance(c, TextContent))
