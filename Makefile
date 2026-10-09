.PHONY: install db db-stop lint typecheck test check

# Install Python, all packages and dev tools from the lockfile
install:
	uv sync --locked

# Start local Postgres and wait until it accepts connections
db:
	docker compose up -d --wait postgres

db-stop:
	docker compose stop postgres

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run pyright

test:
	uv run pytest

# CI runs the same targets
check: lint typecheck test
