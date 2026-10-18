# mcp-guard

[![CI](https://github.com/AnshChhikara001/mcp-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/AnshChhikara001/mcp-guard/actions/workflows/ci.yml)

A security gateway for AI agents that use MCP tools, with a red-team eval that measures how many attacks it stops.

Work in progress. Design decisions are in [`docs/adr/`](docs/adr/) and the project vocabulary in [`GLOSSARY.md`](GLOSSARY.md).

## Layout

| Folder | Package | What it is |
| --- | --- | --- |
| `gateway/` | `mcp_guard.gateway` | The Gateway, an MCP server that decides every Tool call |
| `agent/` | `mcp_guard.agent` | The demo Agent |
| `fake_servers/` | `mcp_guard.fake_servers` | The six fake bank MCP servers |
| `eval/` | `mcp_guard.redteam` | The Red-team eval |

## Development

Needs [uv](https://docs.astral.sh/uv/) and Docker.

```sh
cp .env.example .env   # optional; the defaults work locally
make install           # Python 3.13, all packages and dev tools, from uv.lock
make db                # local Postgres on localhost:5432
make reset-bank        # load the seeded fake bank into Postgres (also restores it later)
make gateway           # the Gateway at http://127.0.0.1:8000/mcp, with the fake bank servers behind it
make chat              # in a second terminal: chat with the Agent as Customer C001 (CUSTOMER=, CONVERSATION=)
make check             # ruff, pyright (strict) and pytest, as CI runs them; tests need make db
```

## Seeded fake bank

The fake bank (Customers, Staff, accounts, transactions and support tickets) is generated with Faker's Indian locale from a fixed seed, so every run produces the same data. It lives in the `bank` schema in Postgres; `make reset-bank` drops that schema and reloads it, restoring the seeded state from any change. All names, numbers and addresses are fake; emails use reserved `example` domains.

Canaries, the unique values whose appearance in an outgoing Tool call proves an exfiltration Attack worked, are the PII of the two test Customers C001 and C002 and their accounts A001 and A002. Their exact places are listed in [`canaries.py`](fake_servers/src/mcp_guard/fake_servers/canaries.py); each value appears nowhere else in the seeded data.

## Gateway

The Gateway is an MCP server (streamable HTTP at `/mcp`) that any MCP client can connect to. Behind it, the fake bank servers run as its child processes and speak MCP over their stdin/stdout, so none of them opens a network port: the only way to reach them is through the Gateway. It lists their tools as its own and, for now, forwards every Tool call unchanged.

| Server | Tools |
| --- | --- |
| accounts | `get_customer_profile`, `list_accounts`, `get_balance` |

The Seam 1 tests in `gateway/tests/` start the real Gateway against a freshly seeded database and call it as an MCP client.

## Agent

The Agent is a LangGraph loop: the model sees the conversation and the Gateway's tools, asks for Tool calls, the Agent makes them through the Gateway (its only way to reach a tool), and the model replies once it has what it needs. Conversations are stored in Postgres by the LangGraph checkpointer, keyed by conversation ID, so `make chat CONVERSATION=<id>` carries one on after the chat was closed.

The model sits behind one small interface ([`model.py`](agent/src/mcp_guard/agent/model.py)): given the conversation and the tools, it returns its next message, with the tokens it used. Which model answers is configuration only, set with `MODEL` as `provider:model`:

| `MODEL` | Model | Needs |
| --- | --- | --- |
| _(unset)_ | a scripted fake ([`fake_model.py`](agent/src/mcp_guard/agent/fake_model.py)) that answers balance questions the way a real model would | nothing |
| `gemini:<model>`, e.g. `gemini:gemini-2.5-flash` | Gemini | `GEMINI_API_KEY` |
| `openai:<model>`, e.g. `openai:gpt-5-mini` | OpenAI | `OPENAI_API_KEY` |
| `<provider>:<model>` | a provider from the Python file named by `MODEL_ADAPTERS_FILE` | whatever that file needs |

For example, `make chat MODEL=gemini:gemini-2.5-flash`, or put `MODEL` and the key in `.env`. Try "What's my balance?", then "What was that in paise?". The Gemini and OpenAI adapters ([`adapters.py`](agent/src/mcp_guard/agent/adapters.py)) give up after `MODEL_TIMEOUT_SECONDS` (default 60) and one retry, and say so plainly when the model timed out or its quota is used up.

`MODEL_ADAPTERS_FILE` lets you plug in another model without touching the repository: the file defines `ADAPTERS`, a dict from a provider name to a function that takes the model name and returns an object with the model interface.

```python
# ~/my_adapters.py, used with MODEL=local:my-model MODEL_ADAPTERS_FILE=~/my_adapters.py
from langchain_core.messages import AIMessage


class LocalModel:
    def __init__(self, model: str): ...
    async def respond(self, messages, tools) -> AIMessage: ...


ADAPTERS = {"local": LocalModel}
```

The Seam 2 tests in `agent/tests/` drive whole conversations with the fake model through the real Gateway, including the terminal chat as its own process, and check the Gemini and OpenAI adapters against a fake provider API on a local port (no API key, no cost).

## License

[MIT](LICENSE)
