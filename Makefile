.PHONY: test test-cov schema lint format format-check clean

test:
	.venv/bin/python -m pytest -q

test-cov:
	.venv/bin/python -m pytest --cov=apps --cov-fail-under=100 --cov-report=term-missing

schema:
	.venv/bin/python tools/generate_schema.py --write

lint:
	.venv/bin/ruff check apps tests tools

format:
	.venv/bin/ruff format apps tests tools

format-check:
	.venv/bin/ruff format --check apps tests tools
