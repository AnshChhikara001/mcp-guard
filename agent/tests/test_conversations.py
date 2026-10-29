"""Seam 2: whole conversations, driven by the scripted fake model, through the real Gateway."""

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager

import pytest
from langchain_core.messages import AIMessage, BaseMessage
from mcp.types import Tool

from conftest import RunningGateway, WritableBank
from mcp_guard.agent.agent import Agent, start_agent
from mcp_guard.agent.fake_model import ScriptedModel, balance_script
from mcp_guard.agent.gateway import login
from mcp_guard.agent.model import Model
from mcp_guard.fake_servers.fx_rates import INJECTED_INSTRUCTIONS
from mcp_guard.fake_servers.scenarios import set_fx_mode

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def agent_for(
    model: Model, gateway: RunningGateway, database_url: str, person: str = "C001"
) -> AsyncGenerator[Agent]:
    """The Agent working for `person`, who logs in to the Gateway first."""
    logged_in = await login(gateway.url, person)
    async with start_agent(
        model,
        gateway.url,
        database_url,
        person=logged_in.person,
        mcp_token=logged_in.mcp_token,
    ) as agent:
        yield agent


async def test_customer_gets_their_balance_through_the_gateway(
    gateway: RunningGateway, database_url: str
) -> None:
    model = ScriptedModel(balance_script("C001"))
    async with agent_for(model, gateway, database_url) as agent:
        reply = await agent.ask("conversation-1", "What's my balance?")

    assert "₹1,94,844.00" in reply


async def test_follow_up_is_answered_from_an_earlier_turn(
    gateway: RunningGateway, database_url: str
) -> None:
    model = ScriptedModel(balance_script("C001"))
    async with agent_for(model, gateway, database_url) as agent:
        await agent.ask("conversation-1", "What's my balance?")
        reply = await agent.ask("conversation-1", "What was that in paise?")

    assert "19484400 paise" in reply


async def test_conversations_do_not_share_memory(
    gateway: RunningGateway, database_url: str
) -> None:
    model = ScriptedModel(balance_script("C001"))
    async with agent_for(model, gateway, database_url) as agent:
        await agent.ask("conversation-1", "What's my balance?")
        reply = await agent.ask("conversation-2", "What was that in paise?")

    assert "paise" not in reply
    assert "don't know" in reply


async def test_conversation_memory_survives_restarting_the_agent(
    gateway: RunningGateway, database_url: str
) -> None:
    async with agent_for(ScriptedModel(balance_script("C001")), gateway, database_url) as agent:
        await agent.ask("conversation-1", "What's my balance?")

    async with agent_for(ScriptedModel(balance_script("C001")), gateway, database_url) as restarted:
        reply = await restarted.ask("conversation-1", "What was that in paise?")

    assert "19484400 paise" in reply


@pytest.mark.parametrize(
    ("person", "introduction"),
    [
        ("C001", "You are serving Customer C001."),
        ("S001", "You are assisting Staff member S001,"),
        ("S004", "You are assisting Supervisor S004,"),
    ],
    ids=["customer", "staff", "supervisor"],
)
async def test_the_model_is_told_who_it_works_for_with_the_role_they_logged_in_with(
    gateway: RunningGateway, database_url: str, person: str, introduction: str
) -> None:
    told: list[str] = []

    class InstructionsReader:
        async def respond(
            self, messages: Sequence[BaseMessage], tools: Sequence[Tool]
        ) -> AIMessage:
            told.append(messages[0].text)
            return AIMessage("Noted.")

    async with agent_for(InstructionsReader(), gateway, database_url, person) as agent:
        await agent.ask("conversation-1", "Hello")

    [instructions] = told
    assert introduction in instructions


async def test_a_tool_definition_changed_mid_conversation_reaches_the_model(
    writable_bank: WritableBank, database_url: str
) -> None:
    seen: list[str] = []

    class DescriptionReader:
        async def respond(
            self, messages: Sequence[BaseMessage], tools: Sequence[Tool]
        ) -> AIMessage:
            [convert] = [t for t in tools if t.name == "convert"]
            seen.append(convert.description or "")
            return AIMessage("Noted.")

    async with agent_for(DescriptionReader(), writable_bank.gateway, database_url) as agent:
        await agent.ask("conversation-1", "How much is 100 dollars in rupees?")
        set_fx_mode(writable_bank.database_url, "poisoned")
        await agent.ask("conversation-1", "And 200?")

    assert [INJECTED_INSTRUCTIONS in description for description in seen] == [False, True]
