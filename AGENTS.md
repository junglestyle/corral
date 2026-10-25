# Working on corral

Corral is a flexible, resource-aware work queue.

Read before changing behavior:

- [Specification](docs/SPEC.md)
- [Coding standards](docs/standards.md)
- [Stack and decisions](docs/stack.md)

Docs live in `docs/` and change with behavior. Never commit hostnames, tokens, or deployment config.

## Mechanics

Python 3.12, uv with a committed lockfile, Ruff, strict mypy, pytest, configuration in `pyproject.toml`, one
command for local and CI checks.

Queue state changes happen in one SQLite transaction. Timestamps are UTC. Workers run fixed argv from their own
config with `shell=False`; the server never sends a command line.
