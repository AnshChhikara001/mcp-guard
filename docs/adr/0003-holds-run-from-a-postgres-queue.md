# Approved Holds run from a Postgres queue with idempotency keys

When a Supervisor approves a Hold, the approval and the job that runs the stored Tool call are written in one Postgres transaction, and a worker takes jobs from that table. Each Hold's ID is sent as an idempotency key, so a retried refund or email takes effect once even if the network drops after the downstream server acted. Before running, the worker checks the policy again and skips Holds older than 24 hours.

## Considered Options

- **A separate queue service (Redis, a hosted queue):** a recognizable keyword, but one more service to host, and the approval and the queued job could disagree after a crash because they'd live in two systems.

## Consequences

- Write tools on downstream servers must accept and honour an idempotency key.
- Write Tool calls are never retried automatically without that key.
