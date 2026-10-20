set shell := ["pwsh", "-NoLogo", "-NoProfile", "-Command"]

default: check

install:
    uv sync --extra dev

test:
    uv run python -c "from pathlib import Path; Path('artifacts').mkdir(exist_ok=True)"
    uv run --extra dev pytest --junitxml=artifacts/junit.xml --cov=parentsquare --cov-report=term-missing --cov-report=xml:artifacts/coverage.xml

lint:
    uv run --extra dev ruff check .

format:
    uv run --extra dev ruff format --check .

typecheck:
    uv run --extra dev pyrefly check

prek:
    uvx prek run --all-files

check: lint format typecheck test
