# Coding standards

For the reviewer (`/code-review` reads this file). Only judgement calls live here. Ruff, ruff format, pyright strict and tsc already enforce style and types, so this file does not repeat them. Terms are from `GLOSSARY.md`. Decisions are in `docs/adr/`.

## Security (the Gateway's job)
- **Fail closed.** When the Gateway cannot decide or cannot record a Tool call, the Decision is deny. A new error path that ends in allow, or forwards the call anyway, is a hard violation (`gateway/server.py`: an Audit write failure gives `AUDIT_FAILED`, a deny).
- **Every Tool call goes through the Gateway.** The Agent never reaches a fake server directly (ADR 0001). A new tool path that skips `call_tool` is a hard violation.
- **Every Decision is recorded.** An allow, redact, hold or deny without an Audit entry is a hard violation.
- **Restore Pseudonyms before Cedar.** Cedar judges the real values, so `restore` runs before `decide` (`gateway/server.py` `call_tool`, #16).
- **Token audience separates the Agent from the chat UI** (ADR 0005). Do not accept an Agent token on a UI route, or the reverse.
- **A Cedar policy change comes with a test for both outcomes:** one call it allows and one call it denies.

## Measurement
- **Published numbers come from committed eval results** in `eval/results/`. Do not quote a partial run (for example, defences-on before #26) as a result.
- **Never weaken a scoring check to improve a number.** A change to `eval/scoring.py` or a task file needs its own reason in the PR.

## Design
- **Every module starts with a one-line docstring that says its role.** Keep it true when the module changes.
- **Comments explain why, not what.** A choice with a rejected alternative gets one line saying why.
- **Use the glossary's words in names**: Person, Tool call, Decision, Audit entry, Pseudonym, Hold, Taint.
- **Write plain code a reviewer can explain line by line.** Prefer a clear loop over a clever one-liner.

## Tests and commits
- **Test through the public interface.** Gateway behaviour is tested with real MCP calls against the fake servers, not by patching internals.
- **A commit subject is one plain sentence about what a user can now do.**
