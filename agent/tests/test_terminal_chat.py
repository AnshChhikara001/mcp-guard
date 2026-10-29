"""Seam 2, end to end: the terminal chat, run as its own process, with the scripted fake model."""

import os
import subprocess
from pathlib import Path

import pytest

from conftest import RunningGateway


def chat(
    gateway: RunningGateway,
    database_url: str,
    conversation_id: str,
    *lines: str,
    config: dict[str, str] | None = None,
    person: str = "C001",
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Start the terminal chat as `person`, type these lines and end the input."""
    return subprocess.run(
        ["mcp-guard-chat", "--person", person, "--conversation", conversation_id],
        input="".join(f"{line}\n" for line in lines),
        env={
            **os.environ,
            "GATEWAY_URL": gateway.url,
            "DATABASE_URL": database_url,
            **(config or {}),
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=check,
    )


def test_terminal_chat_answers_a_balance_question(
    gateway: RunningGateway, database_url: str
) -> None:
    output = chat(gateway, database_url, "conversation-1", "What's my balance?").stdout

    assert "Model scripted fake" in output
    assert "₹1,94,844.00" in output


@pytest.mark.parametrize(
    ("person", "role"), [("C002", "customer"), ("S003", "staff"), ("S004", "supervisor")]
)
def test_terminal_chat_logs_in_and_learns_the_role(
    gateway: RunningGateway, database_url: str, person: str, role: str
) -> None:
    output = chat(gateway, database_url, "conversation-1", person=person).stdout

    assert f"Logged in as {person}, {role}." in output


def test_terminal_chat_refuses_someone_the_bank_doesnt_know(
    gateway: RunningGateway, database_url: str
) -> None:
    finished = chat(gateway, database_url, "conversation-1", person="S999", check=False)

    assert finished.returncode != 0
    assert "The Gateway refused to log in S999: No person with ID S999" in finished.stderr


def test_terminal_chat_carries_a_conversation_on_after_a_restart(
    gateway: RunningGateway, database_url: str
) -> None:
    chat(gateway, database_url, "conversation-1", "What's my balance?")
    output = chat(gateway, database_url, "conversation-1", "What was that in paise?").stdout

    assert "19484400 paise" in output


# An adapter file of the kind a developer keeps outside the repository.
ECHO_ADAPTERS = """
from langchain_core.messages import AIMessage


class Echo:
    def __init__(self, model):
        self.model = model

    async def respond(self, messages, tools):
        usage = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}
        return AIMessage(f"{self.model} heard: {messages[-1].text}", usage_metadata=usage)


ADAPTERS = {"echo": Echo}
"""


def test_terminal_chat_uses_a_model_from_an_adapter_file_outside_the_repository(
    gateway: RunningGateway, database_url: str, tmp_path: Path
) -> None:
    adapters_file = tmp_path / "my_adapters.py"
    adapters_file.write_text(ECHO_ADAPTERS)

    output = chat(
        gateway,
        database_url,
        "conversation-1",
        "hello",
        config={"MODEL": "echo:parrot-1", "MODEL_ADAPTERS_FILE": str(adapters_file)},
    ).stdout

    assert "parrot-1 heard: hello" in output


FAILING_ADAPTERS = """
from mcp_guard.agent.model import ModelCallFailed


class Unreachable:
    def __init__(self, model):
        self.model = model

    async def respond(self, messages, tools):
        raise ModelCallFailed(f"The model {self.model} timed out.")


ADAPTERS = {"unreachable": Unreachable}
"""


def test_terminal_chat_shows_why_the_model_failed_and_carries_on(
    gateway: RunningGateway, database_url: str, tmp_path: Path
) -> None:
    adapters_file = tmp_path / "my_adapters.py"
    adapters_file.write_text(FAILING_ADAPTERS)

    output = chat(
        gateway,
        database_url,
        "conversation-1",
        "hello",
        "are you there?",
        config={"MODEL": "unreachable:slow-1", "MODEL_ADAPTERS_FILE": str(adapters_file)},
    ).stdout

    assert output.count("The model slow-1 timed out.") == 2
