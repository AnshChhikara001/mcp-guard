# Approved Holds run at approval, with idempotency keys

When a Supervisor approves a Hold, the Gateway runs the stored Tool call in the same request. It first claims the Hold with a conditional status change in Postgres (waiting → approved), so of two simultaneous approvals only one wins. It then checks the policy again, refuses Holds older than 24 hours, and sends the Hold's ID as an idempotency key, so a refund or email takes effect once even if the network drops after the downstream server acted and the call is tried again.

## Considered Options

- **A Postgres queue table and a worker:** approval and the job written in one transaction, then run in the background. It survives a Gateway crash between approval and the call, but adds a table, a worker process and crash-restart tests. The idempotency key already makes a retried call safe, so the worker buys little for a demo.
- **A separate queue service (Redis, a hosted queue):** one more service to host, and the approval and the queued job could disagree after a crash because they'd live in two systems.

## Consequences

- Write tools on downstream servers must accept and honour an idempotency key.
- Write Tool calls are never retried automatically without that key.
- If the Gateway stops between claiming a Hold and finishing the call, the Hold stays approved without an outcome; a Supervisor can run it again safely because the key is the same.
