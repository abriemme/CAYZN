# CAYZN - justfile
# Install just: https://github.com/casey/just

# List all available commands
default:
    @just --list

install:
    uv sync

run:
    uv run uvicorn cayzn.main:app --port 8000 --reload

lint:
    uv run ruff check .

lint-fix:
    uv run ruff check --fix .

check:
    uv run pre-commit run --all-files

# Tests
test:
    ENV=test uv run pytest -x -n auto

# Applique les migrations Alembic
migrate:
    uv run alembic upgrade head

# Importe le plan de transport (trains, stops, ods, prices .csv)
import-plan dir="files":
    uv run python -m cayzn.importer {{dir}}


# Setup pre-commit hooks
pre-commit-install:
    uv pip install pre-commit && uv run pre-commit install
