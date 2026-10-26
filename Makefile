.PHONY: check standards sync-standards
SHELL := bash
ORG = https://github.com/junglestyle/.github

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	uv run pytest -q

standards:
	@tmp=$$(mktemp -d) && trap 'rm -rf "$$tmp"' EXIT && \
	git clone -q --depth 1 --branch main $(ORG) "$$tmp" && \
	{ diff -u "$$tmp/code-standards/README.md" <(tail -n +2 docs/org-standards.md) \
		|| { echo "docs/org-standards.md differs from the org's code-standards/README.md: run make sync-standards"; exit 1; }; }

sync-standards:
	@tmp=$$(mktemp -d) && trap 'rm -rf "$$tmp"' EXIT && \
	git clone -q --depth 1 --branch main $(ORG) "$$tmp/org" && \
	sha=$$(git -C "$$tmp/org" rev-parse HEAD) && \
	{ echo "<!-- junglestyle/.github code-standards/README.md at $$sha. Change it there, then run make sync-standards. -->"; \
	  cat "$$tmp/org/code-standards/README.md"; } > "$$tmp/copy" && \
	mv "$$tmp/copy" docs/org-standards.md
