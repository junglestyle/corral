# Coding standards

Corral follows the [org standards](org-standards.md), an unchanged copy of
[junglestyle's](https://github.com/junglestyle/.github/blob/main/code-standards/README.md). This file adds corral's
own rules and examples. Where it differs from an org rule, it says so, and it wins.

## Jungle Style

- Lean on SQLite's transactions, the OS's process model, and systemd's restarts before writing your own.

## DRY

- The [spec](SPEC.md) is where the docs describe what a user can observe. It doesn't carry a second copy of the logic.

## YAGNI

- A deferred requirement gets a line in the [spec](SPEC.md)'s milestones.

## Zen

- One way to enqueue, one way to pause, one client the CLI and the worker share.

## Pragmatic Programmer

- Orthogonality: queue logic doesn't know about HTTP; the worker doesn't know about SQLite.

## Testing

- The behaviors worth a test: ordering and aging, lease expiry, pause and drain, kill, `then`, coalescing, crash
  recovery, and the bearer token.

## Running it

- `make check` runs everything CI runs except the org standards diff. There's no install script: install and
  deployment steps live in [running.md](running.md) and `examples/`.
- `make standards` diffs [org-standards.md](org-standards.md) against the org's current file, so a hand edit or a
  stale copy fails. It needs the network, so it isn't part of `make check`; CI runs it as its own job.
  `make sync-standards` refreshes the copy.

## Privacy

- Overrides the org rule's `.env.example`: config shape lives in `examples/`.

## Prose

- Real consumers appear only as examples. Corral is a general tool, and the docs assume no single owner.
- "X, not Y" is fine where it prevents a real confusion, such as blocked versus paused.
