"""The one small interface every model sits behind.

Provider adapters (Gemini, OpenAI, and ones loaded from outside the repository) and the
scripted fake model all implement it, so the Agent never knows which one it is talking to.
"""

from collections.abc import Sequence
from typing import Protocol

from langchain_core.messages import AIMessage, BaseMessage
from mcp.types import Tool


class Model(Protocol):
    async def respond(self, messages: Sequence[BaseMessage], tools: Sequence[Tool]) -> AIMessage:
        """The model's next message: a reply to the person, or Tool calls to make first.

        `messages` is the whole conversation so far, starting with the system message.
        `tools` are the tools the Gateway offers, as MCP defines them.
        """
        ...
