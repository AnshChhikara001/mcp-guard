# CONTEXT: mcp-guard

A cold-session map. Read it before the code. Terms: `GLOSSARY.md`. Decisions: `docs/adr/0001`–`0006`.

## Purpose
A security Gateway between an AI Agent and MCP servers. The Agent reaches its tools only through the Gateway. The Gateway decides every Tool call (Cedar permissions) and records it in a hash-chained Audit log. A fake Indian bank (6 MCP servers) is the target. The Red-team eval measures attacks with defences off and on.

## Run it
- `make install` (uv + npm), then `make db` (Postgres in Docker). Settings: copy `.env.example` to `.env`.
- `make dev`: Gateway :8000/mcp + Agent API :8001 + web app :5173. Without `MODEL`, a scripted fake model runs.
- `make chat PERSON=C001|S003|S004`: chat in the terminal, as a Customer, Staff member or Supervisor.
- `make eval DEFENCES=off|on [MODEL=…] [ONLY=…] [LIMIT=n]`: resumes where it stopped.
- `make check` = lint + typecheck + test + e2e. CI runs the same. Run it before a ticket is done.

## Layout (uv workspace, package `mcp_guard.*`)
| Folder | Role | Key files |
|---|---|---|
| `gateway/` | MCP server towards Agents, HTTP API for the web app | `server.py` (call_tool), `permissions.py` (Cedar), `audit.py`, `tokens.py` (Ed25519 JWTs), `downstream.py`, `live.py`, `pii.py` (Presidio), `pseudonyms.py` (Redact) |
| `gateway/policies/` | Cedar rules per role | `customer/staff/supervisor/everyone.cedar`, `schema.cedarschema` |
| `agent/` | LangGraph Agent + Agent API (SSE turns) | `agent.py`, `api.py`, `gateway.py` (login + MCP client), `adapters.py`, `fake_model.py` |
| `fake_servers/` | 6 seeded bank MCP servers | `bank.py` (fixed seed), `canaries.py`, `scenarios.py` (plant Attacks), `outbound.py` |
| `eval/` | Red-team eval | `harness.py`, `scoring.py` (exact checks on the bank), `tasks/attacks` (42), `tasks/benign` (30), `results/` (committed) |
| `web/` | Vite + React chat, role switcher, flow strip | `App.tsx`, `FlowStrip.tsx`, `sse.ts`, `flow.ts`; e2e in `web/e2e/` |

## One Tool call, start to end
1. The web app sends a turn to the Agent API: `agent/api.py:272` `take_turn`. It streams steps over SSE.
2. The Agent runs its LangGraph loop: `agent/agent.py:107` `turn`. It calls tools only through `_call_through_gateway` (`agent.py:247`), with the person's MCP token.
3. The Gateway checks the token: `gateway/tokens.py:103` (the audience separates Agent from UI, ADR 0005).
4. `gateway/server.py:128` `call_tool`: unknown tool → Deny. Defences off → Allow. Otherwise `pseudonyms.py` `restore` puts real values back for the Agent's Pseudonyms (unknown → Deny), then `permissions.py` `decide` (Cedar) judges the restored call.
5. `audit.py:156` `record` writes the hash-chained entry. If the write fails, the call is denied (fail closed, `server.py:158`).
6. Allow → `downstream.py:50` forwards the call to the fake server. Deny → an error result. With the defences on, `pseudonyms.py` `redact` swaps PII in the result for Pseudonyms and logs a second entry, Decision `redact`. The Decision rides back in `_meta`.
7. The Agent emits a `Decided` step (`agent.py:215`). The web flow strip draws it (`live.py`, `FlowStrip.tsx`).

## Conventions
- GitHub Issues via `gh`, one ticket per feature branch. Labels: `docs/agents/triage-labels.md`.
- Python 3.13, ruff, pyright strict, pytest. TS: tsc, vitest, Playwright.
- Every module starts with a one-line docstring that says its role. Keep it so.
- Commit subjects are one plain sentence about what a user can now do.

## Hot zones (v1)
- Redact: Pseudonyms in results done (#16). Next: authorised reveal in the chat UI (#17), via `pseudonyms.reveal`.
- Holds #18 and Taint #19/#21: approved Holds run at approval (ADR 0003).
- Deploy: Oracle free VM + Neon (ADR 0004).
- `eval/results/claude-cli_haiku/defences-on/` is partial (8 of 42 Attacks). Do not quote defences-on numbers yet.
- Before the #26 defences-on run, delete `eval/results/claude-cli_haiku/defences-on/items/`. Resume reuses old results: the fingerprint covers only the Attack's file, not the Gateway code.

## Gotchas
- `make dev` needs Postgres up. Tests and the eval reset the bank.
- Without `GATEWAY_SIGNING_KEY`, tokens die when the Gateway restarts.
- `pii.py` loads spaCy `en_core_web_md`: slow first import. A guarded Gateway loads it at startup.
- The eval scores `reply` checks on revealed replies (`harness.py`); saved replies keep their Pseudonyms.
