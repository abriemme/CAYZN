# Lint, format, lockfile et types
check:
    uv run pre-commit run --all-files

# Tests
test:
    ENV=test uv run pytest -x -n auto
