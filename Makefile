.PHONY: install db db-stop reset-bank gateway chat agent-api web dev eval lint typecheck test e2e check

# Install Python, all packages and dev tools from the lockfile
install:
	uv sync --locked
	npm --prefix web ci

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

# Chat with the Agent in the terminal, logged in as one Customer, Staff member or Supervisor
# (needs make gateway). The model is MODEL, e.g. gemini:gemini-3.5-flash (see .env.example);
# without it, the scripted fake model.
# make chat PERSON=C001|S003|S004 [CONVERSATION=<id from an earlier chat>] [MODEL=<provider:model>]
PERSON ?= C001
chat:
	uv run $(if $(wildcard .env),--env-file .env) mcp-guard-chat --person $(PERSON) $(if $(CONVERSATION),--conversation $(CONVERSATION))

# Run the Agent API the web app chats through, at http://127.0.0.1:8001 (needs make gateway).
# The model is MODEL, as for make chat; MODEL_PRICE=<input>,<output> (dollars per million
# tokens) shows each turn's estimated cost.
agent-api:
	uv run $(if $(wildcard .env),--env-file .env) mcp-guard-agent-api

# Run the web app at http://localhost:5173 (needs make gateway and make agent-api; Vite passes
# /gateway/* to the Gateway and /agent/* to the Agent API)
web:
	npm --prefix web run dev

# Run everything the web app needs in one terminal: Postgres, then the Gateway, the Agent API
# and the web app, each output line marked with its name. Ctrl-C stops them all, and so does
# one of them crashing. Open http://localhost:5173. MODEL and MODEL_PRICE work as for agent-api.
dev: db
	web/node_modules/.bin/concurrently --kill-others --names gateway,agent,web \
		--prefix-colors blue,magenta,green "$(MAKE) gateway" "$(MAKE) agent-api" "$(MAKE) web"

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
	npm --prefix web run typecheck

test:
	uv run pytest
	npm --prefix web test

# The browser test: a Customer asks for someone else's balance and the flow strip shows the
# Gateway block it. It starts its own Gateway, Agent API (fake model) and web app over a fresh
# database (needs make db, and once: npx --prefix web playwright install chromium).
e2e:
	npm --prefix web run e2e

# CI runs the same targets
check: lint typecheck test e2e
