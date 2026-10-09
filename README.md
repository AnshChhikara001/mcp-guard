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
make check             # ruff, pyright (strict) and pytest, as CI runs them; tests need make db
```

## Seeded fake bank

The fake bank (Customers, Staff, accounts, transactions and support tickets) is generated with Faker's Indian locale from a fixed seed, so every run produces the same data. It lives in the `bank` schema in Postgres; `make reset-bank` drops that schema and reloads it, restoring the seeded state from any change. All names, numbers and addresses are fake; emails use reserved `example` domains.

Canaries, the unique values whose appearance in an outgoing Tool call proves an exfiltration Attack worked, are the PII of the two test Customers C001 and C002 and their accounts A001 and A002. Their exact places are listed in [`canaries.py`](fake_servers/src/mcp_guard/fake_servers/canaries.py); each value appears nowhere else in the seeded data.
