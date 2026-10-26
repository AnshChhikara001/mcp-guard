.PHONY: install db db-stop reset-bank gateway chat eval lint typecheck test check

# Install Python, all packages and dev tools from the lockfile
install:
	uv sync --locked

# Start local Postgres and wait until it accepts connections
db:
	docker compose up -d --wait postgres

db-stop:
	docker compose stop postgres

# Restore the fake bank in Postgres (DATABASE_URL, default local) to its seeded state
reset-bank:
	uv run $(if $(wildcard .env),--env-file .env) bank-reset

# Run the Gateway at http://127.0.0.1:8000/mcp, with the fake bank servers behind it
gateway:
	uv run $(if $(wildcard .env),--env-file .env) mcp-guard-gateway

# Chat with the Agent in the terminal as one Customer (needs make gateway). The model is MODEL,
# e.g. gemini:gemini-3.5-flash (see .env.example); without it, the scripted fake model.
# make chat CUSTOMER=C001 [CONVERSATION=<id from an earlier chat>] [MODEL=<provider:model>]
CUSTOMER ?= C001
chat:
	uv run $(if $(wildcard .env),--env-file .env) mcp-guard-chat --customer $(CUSTOMER) $(if $(CONVERSATION),--conversation $(CONVERSATION))

# Run the Red-team eval (needs make db; it resets the bank in DATABASE_URL for every task).
# Running it again carries on where it stopped and retries tasks that ended in an error.
# make eval DEFENCES=off|on [MODEL=<provider:model>] [ONLY=<Attack class or benign>] [LIMIT=<n>]
DEFENCES ?= off
eval:
	uv run $(if $(wildcard .env),--env-file .env) mcp-guard-eval --defences $(DEFENCES) $(if $(MODEL),--model $(MODEL)) $(foreach class,$(ONLY),--only $(class)) $(if $(LIMIT),--limit $(LIMIT)) $(if $(PRICE),--price $(PRICE))

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run pyright

test:
	uv run pytest

# CI runs the same targets
check: lint typecheck test
