# Coding standards

Corral follows the [org standards](org-standards.md) and the org's standards for [Python](org-standards-python.md),
[SQL](org-standards-sql.md), [containers](org-standards-containers.md) and [shell scripts](org-standards-shell.md),
unchanged copies of [junglestyle's](https://github.com/junglestyle/.github/tree/main/code-standards). `.org-standards`
lists the topics it copies beyond the general one. This file adds corral's own rules and examples. Where it differs from
an org rule, it says so, and it wins.

## Dependencies

- Lean on SQLite's transactions, the OS's process model, and systemd's restarts before writing your own.

## Structure

- One way to enqueue, one way to pause, one client the CLI and the worker share.
- Queue logic doesn't know about HTTP; the worker doesn't know about SQLite.

## Testing

- The behaviors worth a test: ordering and aging, lease expiry, pause and drain, kill, `then`, coalescing, crash
  recovery, the bearer token, which events reach which webhooks and when the block alert fires, and the chunking
  hint.

## Running it

- There's no install script: install and deployment steps live in [running.md](running.md) and `examples/`.

## Privacy

- Config shape lives in `examples/`.

## Docs

- The [spec](SPEC.md) is where the docs describe what a user can observe. It doesn't carry a second copy of the logic.
- A deferred requirement stays in the [spec](SPEC.md) and gets a later slice in the roadmap, which for now is the
  spec's [milestones](SPEC.md#milestones) (see Known deviations).

## Prose

- Real consumers appear only as examples. Corral is a general tool, and the docs assume no single owner.
- "X, not Y" is fine where it prevents a real confusion, such as blocked versus paused.

## Known deviations

Where Corral doesn't follow the org standards yet. These are debt to pay down, not overrides: each one should go.

- Logging: `corral.cli` configures its own timestamps (`%(asctime)s`). python.md leaves timestamps to journald or
  Docker.
- Typing: jobs travel as `dict[str, Any]` from the HTTP client through the worker (`client.py`, `worker.py`), not
  as a typed record parsed at the edge.
- Dependencies: FastAPI and uvicorn have no ADR in [stack.md](stack.md), only a row in its table.
- SQL: `jobs.state` holds a closed set of names with no `CHECK`, and they're literals, not a `StrEnum`.
- Containers: the image gets uv from `ghcr.io/astral-sh/uv:python3.12-bookworm-slim`, whose tag doesn't pin uv's
  version.
- Containers: the image has no `USER`, so the server runs as root.
- Containers: `examples/compose.yaml` sets no `logging:`.
- Shell: `examples/system-worker.sh` is `sh` with `set -eu`, not bash with `set -euo pipefail`, and its header doesn't
  show how to call it.
- Shell: `examples/system-worker.sh` pipes uv's installer into `sh` and installs corral from `main`, unpinned.
- Shell: `make check` doesn't run shellcheck.
- Docs: the build order is the [spec](SPEC.md)'s [milestones](SPEC.md#milestones), not slices in a `docs/ROADMAP.md`.
