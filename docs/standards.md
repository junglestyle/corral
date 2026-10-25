# Coding standards

Six ideas, each turned into something you can check in review. When they pull against each other, pick what
works and is easiest to undo.

## DRY

One fact, one place. DRY is about knowledge, not text.

- A rule has one executable implementation. The spec still describes the behavior a user can observe, so a reader
  never has to open the code to learn what corral does; it just doesn't carry a second copy of the logic.
- Two blocks that look alike but would change for different reasons stay separate. Duplication is cheaper than the
  wrong abstraction.
- Lean toward extracting on the third real use, not the second similar-looking one. A critical invariant is the
  exception: two implementations of it are already one too many.

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
- Be honest about sharp edges. A known limit goes in the docs or one comment; a workaround is fine when it's small
  and says what it works around.
- Don't gold-plate output, logging, or UI. Good enough to read is good enough.

## Wu wei

Effortless action: work with the grain, don't force it.

- Lean on what's already there: the stdlib, SQLite's transactions, the OS's process model, systemd's restarts.
- Prefer state the system converges to (expired leases requeue, blocks clear themselves) over steps someone must
  remember.
- Minimize recurring operational work, and make unavoidable maintenance explicit. A feature that quietly creates
  chores for its owner is a bug; so is elaborate automation to avoid an occasional manual step.

## Pragmatic Programmer

- Tracer bullets: get a thin slice working end to end before widening any layer.
- Crash early: an impossible state raises; it isn't logged and continued.
- Fix broken windows: leave each file you touch a little cleaner, within the change's scope.
- Reversible decisions: prefer the choice that's easy to undo, and write down the ones that aren't in
  [stack.md](stack.md).
- Orthogonality: queue logic doesn't know about HTTP; the worker doesn't know about SQLite.

## Testing

Focused tests of consequential behavior, at boundaries that stay put. Count is not the goal.

- Test what a user of the queue would notice: ordering and aging, lease expiry, pause and drain, kill, `then`,
  coalescing, crash recovery.
- Don't test plumbing, getters, wording, or internal call order. Don't mock half the system to prove a call
  happened.
- Before adding a test, ask what realistic regression it catches and whether it survives a refactor. If the answer is
  weak, skip it.

## Comments and names

- Names say what; comments say why.
- Keep a comment only if a maintainer would lose a fact that the code, types, and tests don't already state. No
  comments that restate the code, and no commented-out code.
- The facts worth a comment are constraints the code can't show: concurrency and lifecycle rules, security
  boundaries, protocol quirks, and the tradeoff behind a non-obvious choice.

## Prose

For docs, commit messages, and PR descriptions. Follow
[agent-style](https://github.com/yzhao062/agent-style/blob/7da99e0b0610aca4e4d340351bd802bfd448c894/RULES.md),
with these house rules taking precedence:

- Describe the product as it is meant to be. No dates, approval trails, or "replacing X"; history lives in git.
- Link instead of restating. Each fact has one home, the same as in code.
- Arrange sections in the order a reader needs them.
- Name roles, not machines: "the NAS" or "a GPU host", never a hostname, token, or path from a real deployment.
- Real consumers appear only as examples. Corral is a general tool, and the docs assume no single owner.
- Error messages, log lines, and CLI output are interface. Changing their wording is an interface change, not a
  style edit.
- Contractions are fine, and headings use sentence case.
- "X, not Y" is fine where it prevents a real confusion, such as blocked versus paused.
