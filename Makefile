.PHONY: install db db-stop reset-bank lint typecheck test check

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

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run pyright

test:
	uv run pytest

# CI runs the same targets
check: lint typecheck test
