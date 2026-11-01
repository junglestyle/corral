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

## Prose

- Real consumers appear only as examples. Corral is a general tool, and the docs assume no single owner.
- "X, not Y" is fine where it prevents a real confusion, such as blocked versus paused.

## Known deviations

Where Corral doesn't follow the org standards yet. These are debt to pay down, not overrides: each one should go.

- Containers: `examples/compose.yaml` uses host networking (line 12) and has the server listen on `CORRAL_HOST` (line
  15), not on `0.0.0.0` behind a port published to a named address. The file says why, but nothing here overrides
  the rule.
- Containers: `examples/compose.yaml` sets no `logging:`.
