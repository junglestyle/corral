# Coding standards

Six ideas, each turned into something you can check in review. When they pull against each other, pick what
works and is easiest to undo.

## DRY

One fact, one place. DRY is about knowledge, not text.

- A rule that the code and docs both state (a default, a state name, an env var) is defined once in code, and the
  docs name it rather than restate its logic.
- Two blocks that look alike but would change for different reasons stay separate. Duplication is cheaper than the
  wrong abstraction.
- Extract on the third real use, not the second similar-looking one.

## YAGNI

Build the current milestone, not the imagined product.

- No plugin systems, factories, registries, base classes, config frameworks, or extension points without two real
  cases that need them today.
- No option, flag, or parameter that nothing sets.
- A deferred requirement gets a line in [SPEC](SPEC.md), not a stub in code.

## Zen

From the Zen of Python, the parts that bite here:

- Explicit over implicit: no import-time side effects, no hidden globals, no magic defaults that differ by
  environment.
- Flat over nested: early returns, shallow control flow, one module per concept.
- Errors never pass silently: catch only what you can handle, and say what you did with it.
- One obvious way: one way to enqueue, one way to pause, one code path the CLI and the API share.

## Wabi sabi

Accept imperfection that's honest about itself.

- Plain, finished, and slightly rough beats polished and unfinished. Ship the small thing that works.
- Leave a sharp edge exposed rather than papering over it: a known limit goes in the docs or one comment, not in a
  workaround.
- Don't gold-plate output, logging, or UI. Good enough to read is good enough.

## Wu wei

Effortless action: work with the grain, don't force it.

- Lean on what's already there: the stdlib, SQLite's transactions, the OS's process model, systemd's restarts.
- Prefer state the system converges to (expired leases requeue, blocks clear themselves) over steps someone must
  remember.
- Nothing should need tending. A feature that creates recurring work for its owner is a bug.

## Pragmatic Programmer

- Tracer bullets: get a thin slice working end to end before widening any layer.
- Crash early: an impossible state raises; it isn't logged and continued.
- Fix broken windows: leave each file you touch a little cleaner, within the change's scope.
- Reversible decisions: prefer the choice that's easy to undo, and write down the ones that aren't in
  [stack.md](stack.md).
- Orthogonality: queue logic doesn't know about HTTP; the worker doesn't know about SQLite.

## Testing

Few tests, at boundaries that stay put.

- Test what a user of the queue would notice: ordering and aging, lease expiry, pause and drain, kill, `then`,
  coalescing, crash recovery.
- Don't test plumbing, getters, wording, or internal call order. Don't mock half the system to prove a call
  happened.
- Before adding a test, ask what realistic regression it catches and whether it survives a refactor. If the answer is
  weak, skip it.

## Comments and names

- Names say what; comments say why, and only when the why isn't obvious: a constraint, an invariant, a tradeoff.
- No comments that restate the code, and no commented-out code.
