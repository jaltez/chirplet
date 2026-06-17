.PHONY: test test-cov schema lint clean

test:
	.venv/bin/python -m pytest -q

test-cov:
	.venv/bin/python -m pytest --cov=apps --cov-fail-under=100 --cov-report=term-missing

schema:
	.venv/bin/python tools/generate_schema.py --write

lint:
	.venv/bin/ruff check apps tests tools
