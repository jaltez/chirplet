.PHONY: test test-cov schema clean

test:
	.venv/bin/python -m pytest -q

test-cov:
	.venv/bin/python -m pytest --cov=apps --cov-report=term-missing

schema:
	.venv/bin/python tools/generate_schema.py --write
