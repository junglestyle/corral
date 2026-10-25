.PHONY: check sync-standards
check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	uv run pytest -q
	uv run python scripts/org_standards.py check

sync-standards:
	uv run python scripts/org_standards.py sync
