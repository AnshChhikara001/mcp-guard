"""Chat with the Agent in the terminal, as one Customer.

Run it with `make chat`, or `uv run mcp-guard-chat --customer C001`. The Gateway must be
running (`make gateway`). GATEWAY_URL and DATABASE_URL pick where the Gateway and the
conversation store are. MODEL picks the model, as `provider:model` (for example
`gemini:gemini-2.5-flash`); without it, the scripted fake model answers.
MODEL_ADAPTERS_FILE names a Python file outside the repository with further providers.
OTEL_EXPORTER_OTLP_ENDPOINT turns tracing on (see tracing.py).
Pass --conversation with an earlier ID to carry that conversation on, even after the chat
was closed. End the chat with Ctrl-D.
"""

import argparse
import os
import uuid

import anyio
import anyio.to_thread

from mcp_guard.agent.adapters import load_model
from mcp_guard.agent.agent import start_agent
from mcp_guard.agent.fake_model import ScriptedModel, balance_script
from mcp_guard.agent.model import Model, ModelCallFailed
from mcp_guard.agent.tracing import start_tracing

LOCAL_GATEWAY_URL = "http://127.0.0.1:8000/mcp"
LOCAL_DATABASE_URL = "postgresql://mcpguard:mcpguard@localhost:5432/mcpguard"


async def chat(customer_id: str, conversation_id: str) -> None:
    gateway_url = os.environ.get("GATEWAY_URL", LOCAL_GATEWAY_URL)
    database_url = os.environ.get("DATABASE_URL", LOCAL_DATABASE_URL)
    model_name, model = _configured_model(customer_id)
    async with start_agent(
        model, gateway_url, database_url, customer_id=customer_id, model_name=model_name
    ) as agent:
        print(f"Model {model_name or 'scripted fake (MODEL is not set)'}.")
        print(f"Conversation {conversation_id}. Carry it on later with --conversation.")
        while True:
            try:
                # input() blocks, so it runs on a thread while the Gateway connection stays live.
                text = await anyio.to_thread.run_sync(input, "you> ")
            except EOFError:
                print()
                return
            if text.strip():
                try:
                    print(f"agent> {await agent.ask(conversation_id, text)}")
                except ModelCallFailed as e:
                    print(f"error> {e}")


def _configured_model(customer_id: str) -> tuple[str | None, Model]:
    """The model MODEL names, with that name; None names the scripted fake."""
    if name := os.environ.get("MODEL"):
        return name, load_model(name, os.environ.get("MODEL_ADAPTERS_FILE"))
    return None, ScriptedModel(balance_script(customer_id))


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat with the Agent as one Customer.")
    parser.add_argument("--customer", required=True, help="the Customer to serve, e.g. C001")
    parser.add_argument(
        "--conversation",
        default=str(uuid.uuid4()),
        help="the conversation to carry on (default: a new one)",
    )
    args = parser.parse_args()
    start_tracing("mcp-guard-agent")
    try:
        anyio.run(chat, args.customer, args.conversation)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
