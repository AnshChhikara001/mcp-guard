"""Seam 2, end to end: the terminal chat, run as its own process, with the scripted fake model."""

import os
import subprocess

from conftest import RunningGateway


def chat(gateway: RunningGateway, database_url: str, conversation_id: str, *lines: str) -> str:
    """Start the terminal chat, type these lines, end the input, and return what it printed."""
    finished = subprocess.run(
        ["mcp-guard-chat", "--customer", "C001", "--conversation", conversation_id],
        input="".join(f"{line}\n" for line in lines),
        env={**os.environ, "GATEWAY_URL": gateway.url, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return finished.stdout


def test_terminal_chat_answers_a_balance_question(
    gateway: RunningGateway, database_url: str
) -> None:
    output = chat(gateway, database_url, "conversation-1", "What's my balance?")

    assert "₹1,94,844.00" in output


def test_terminal_chat_carries_a_conversation_on_after_a_restart(
    gateway: RunningGateway, database_url: str
) -> None:
    chat(gateway, database_url, "conversation-1", "What's my balance?")
    output = chat(gateway, database_url, "conversation-1", "What was that in paise?")

    assert "19484400 paise" in output
