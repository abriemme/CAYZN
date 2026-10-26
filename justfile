# CAYZN - justfile
# Install just: https://github.com/casey/just

# List all available commands
default:
    @just --list

install:
    uv sync

run:
    uv run uvicorn cayzn.main:app --port 8000 --reload

# Run the forecast and optimization jobs requested
worker:
    uv run python -m cayzn.worker

# Request a forecast and optimization of every service still to depart
request-all:
    uv run python -m cayzn.worker request-all

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

# Import the transport plan (trains, stops, ods, prices .csv), reload the cache, request the daily runs
import-plan dir="files": && request-all
    uv run python -m cayzn.importer {{dir}}

# Setup pre-commit hooks
pre-commit-install:
    uv run pre-commit install
