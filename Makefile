.PHONY: check
check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	uv run pytest -q
