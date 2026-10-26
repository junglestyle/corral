.PHONY: check standards sync-standards
SHELL := bash
ORG = https://github.com/junglestyle/.github
RAW = https://raw.githubusercontent.com/junglestyle/.github

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	uv run pytest -q

standards:
	@curl -fsSL $(RAW)/main/STANDARDS.md | diff -u - <(tail -n +2 docs/org-standards.md) \
		|| { echo "docs/org-standards.md differs from the org's STANDARDS.md: run make sync-standards"; exit 1; }

sync-standards:
	sha=$$(git ls-remote $(ORG) refs/heads/main | cut -f1) && \
	{ echo "<!-- junglestyle/.github STANDARDS.md at $$sha. Change it there, then run make sync-standards. -->"; \
	  curl -fsSL $(RAW)/$$sha/STANDARDS.md; } > docs/org-standards.md
