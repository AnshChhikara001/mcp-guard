"""Seam 2: whole conversations, driven by the scripted fake model, through the real Gateway."""

import pytest

from conftest import RunningGateway
from mcp_guard.agent.agent import start_agent
from mcp_guard.agent.fake_model import ScriptedModel, balance_script

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
