# Working on corral

Corral is a small job queue for one owner's home machines. Keep it austere: the fewest concepts that meet the
[specification](docs/SPEC.md), no extension points for imagined needs, duplication over a wrong abstraction.

Read before changing behavior:

- [Specification](docs/SPEC.md)
- [Stack and decisions](docs/stack.md)

Docs live in `docs/` and change with behavior. Never commit hostnames, tokens, or deployment config.

## Standards

Python 3.12, uv with a committed lockfile, Ruff, strict mypy, pytest, configuration in `pyproject.toml`, one
command for local and CI checks. Stdlib first; add a dependency when it clearly simplifies.

Queue state changes happen in one SQLite transaction. Timestamps are UTC. Workers run fixed argv from their own
config with `shell=False`; the server never sends a command line.

Test observable behavior at stable boundaries: ordering and aging, lease expiry and requeue, pause and drain,
`then` chaining, coalescing, crash recovery. Don't test plumbing or wording.
