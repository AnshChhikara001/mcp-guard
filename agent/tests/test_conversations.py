"""Seam 2: whole conversations, driven by the scripted fake model, through the real Gateway."""

from collections.abc import Sequence

import pytest
from langchain_core.messages import AIMessage, BaseMessage
from mcp.types import Tool

from conftest import RunningGateway, WritableBank
from mcp_guard.agent.agent import start_agent
from mcp_guard.agent.fake_model import ScriptedModel, balance_script
from mcp_guard.fake_servers.fx_rates import INJECTED_INSTRUCTIONS
from mcp_guard.fake_servers.scenarios import set_fx_mode

pytestmark = pytest.mark.anyio


async def test_customer_gets_their_balance_through_the_gateway(
    gateway: RunningGateway, database_url: str
) -> None:
    model = ScriptedModel(balance_script("C001"))
    async with start_agent(model, gateway.url, database_url, customer_id="C001") as agent:
        reply = await agent.ask("conversation-1", "What's my balance?")

    assert "₹1,94,844.00" in reply


async def test_follow_up_is_answered_from_an_earlier_turn(
    gateway: RunningGateway, database_url: str
) -> None:
    model = ScriptedModel(balance_script("C001"))
    async with start_agent(model, gateway.url, database_url, customer_id="C001") as agent:
        await agent.ask("conversation-1", "What's my balance?")
        reply = await agent.ask("conversation-1", "What was that in paise?")

    assert "19484400 paise" in reply


async def test_conversations_do_not_share_memory(
    gateway: RunningGateway, database_url: str
) -> None:
    model = ScriptedModel(balance_script("C001"))
    async with start_agent(model, gateway.url, database_url, customer_id="C001") as agent:
        await agent.ask("conversation-1", "What's my balance?")
        reply = await agent.ask("conversation-2", "What was that in paise?")

    assert "paise" not in reply
    assert "don't know" in reply


async def test_conversation_memory_survives_restarting_the_agent(
    gateway: RunningGateway, database_url: str
) -> None:
    async with start_agent(
        ScriptedModel(balance_script("C001")), gateway.url, database_url, customer_id="C001"
    ) as agent:
        await agent.ask("conversation-1", "What's my balance?")

    async with start_agent(
        ScriptedModel(balance_script("C001")), gateway.url, database_url, customer_id="C001"
    ) as restarted:
        reply = await restarted.ask("conversation-1", "What was that in paise?")

    assert "19484400 paise" in reply


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

    async with start_agent(
        DescriptionReader(), writable_bank.gateway.url, database_url, customer_id="C001"
    ) as agent:
        await agent.ask("conversation-1", "How much is 100 dollars in rupees?")
        set_fx_mode(writable_bank.database_url, "poisoned")
        await agent.ask("conversation-1", "And 200?")

    assert [INJECTED_INSTRUCTIONS in description for description in seen] == [False, True]
