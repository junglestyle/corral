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

- Dependencies: FastAPI and uvicorn have no ADR in [stack.md](stack.md), only a row in its table.
- Versions and pins: workers run corral from `main`, not a commit: [running.md](running.md) installs and upgrades with
  `uv tool install git+https://github.com/junglestyle/corral` and `uv tool upgrade corral` (lines 88 and 95), and so
  does `examples/system-worker.sh` (line 16).
- Versions and pins: `pyproject.toml` has no lower bound for pydantic, which `client.py`, `queue.py` and `server.py`
  import directly; it comes in only through FastAPI.
- Versions and pins: `pyproject.toml` has no `[tool.uv] required-version`, so CI's `setup-uv`
  (`.github/workflows/check.yml` line 11) installs the newest uv, and `examples/system-worker.sh` (line 15) runs uv's
  latest installer with no checksum.
- Privacy: examples use host names that aren't under `example.com`: `http://nas:8700` in [running.md](running.md)
  (lines 7 and 132), and `http://consumer:8000` in `examples/server.toml` (line 22).
- Docs: [SPEC.md](SPEC.md)'s requirements are numbered 1–5 under one heading, not grouped by area, each opening with
  an ID and a name (`**QUEUE-1: Name.**`), and it has no Unknowns or Open questions section.
- Docs: the build order is the [spec](SPEC.md)'s [milestones](SPEC.md#milestones), not numbered slices in a
  `docs/ROADMAP.md` with `Status:`, `Delivers:` and `Done when:`.
- Prose: [SPEC.md](SPEC.md)'s speech pipeline example says Hearsay runs "on hourly timers today" (line 221), a dated
  claim that's no longer true: Hearsay's install docs run that pipeline on corral.
- Containers: `examples/compose.yaml` uses host networking (line 12) and has the server listen on `CORRAL_HOST` (line
  15), not on `0.0.0.0` behind a port published to a named address. The file says why, but nothing here overrides
  the rule.
- Containers: `examples/compose.yaml` sets no `logging:`.
- Python: `JobSpec` (`queue.py` line 40) is a pydantic model, and the queue takes it as its input, so pydantic reaches
  past the HTTP edge. The other request bodies are unpacked into plain values in `server.py`.
- Python: `tests/test_queue.py` (line 191) checks only that the `CHECK`s on `jobs.state` and `jobs.stop` accept every
  `JobState` and `Stop` value and refuse one other, not that each `StrEnum` is exactly its column's `CHECK` list.
- Python: the queue raises two exception types, `NotFound` and `Conflict` (`queue.py` lines 59 and 63), and the client
  raises `LeaseLost` (`client.py` line 21), not one `<Thing>Error(Exception)` per boundary.
- Python: worker config errors put the kind's name in without `!r` (`worker.py` lines 58, 75 and 79).
- Python: `corral.cli` configures its own timestamps (`%(asctime)s`, `cli.py` line 65). python.md leaves timestamps to
  journald or Docker.
- Shell: `examples/system-worker.sh` is `sh` with `set -eu`, not bash with `set -euo pipefail`, and its header doesn't
  show how to call it.
- Shell: `examples/system-worker.sh` pipes uv's installer into `sh` and installs corral from `main`, unpinned.
- Shell: `make check` doesn't run shellcheck.
