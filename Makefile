.PHONY: check standards sync-standards
SHELL := bash
ORG = https://github.com/junglestyle/.github

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	uv run pytest -q
	@# Every tracked script: a .sh name, or a shell on its shebang line.
	git ls-files -z | LC_ALL=C xargs -0 awk 'FNR == 1 { if (FILENAME ~ /\.sh$$/ || /^#!.*[\/ ](ba|da|z)?sh$$/) print FILENAME; nextfile }' | \
		xargs uvx --from shellcheck-py==0.11.0.1 shellcheck

standards:
	@tmp=$$(mktemp -d) && trap 'rm -rf "$$tmp"' EXIT && \
	git clone -q --depth 1 --branch main $(ORG) "$$tmp" && \
	"$$tmp/code-standards/sync.sh" check "$$tmp"

sync-standards:
	@tmp=$$(mktemp -d) && trap 'rm -rf "$$tmp"' EXIT && \
	git clone -q --depth 1 --branch main $(ORG) "$$tmp" && \
	"$$tmp/code-standards/sync.sh" sync "$$tmp" "$$(git -C "$$tmp" rev-parse HEAD)"
