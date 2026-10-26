# CAYZN - justfile
# Install just: https://github.com/casey/just

# List all available commands
default:
    @just --list

install:
    uv sync

run:
    uv run uvicorn cayzn.main:app --port 8000 --reload

# Run the forecast and optimization jobs queued by POST /bookings
worker:
    uv run python -m cayzn.worker

lint:
    uv run ruff check .

lint-fix:
    uv run ruff check --fix .

check:
    uv run pre-commit run --all-files

# Tests
test:
    uv run pytest -x -n auto

# Apply Alembic migrations
migrate:
    uv run alembic upgrade head

# Check that migrations cover the models (requires Postgres)
migrate-check:
    uv run alembic check

# Import the transport plan (trains, stops, ods, prices .csv) and reload the cache
import-plan dir="files":
    uv run python -m cayzn.importer {{dir}}

# Setup pre-commit hooks
pre-commit-install:
    uv run pre-commit install
