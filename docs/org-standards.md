<!-- junglestyle/.github STANDARDS.md at 2ca7c47fbb752bf58b66ee45df927555fb6e0162, sha256 ea07617ad0f646f5928a74d2d7818f8a30e226b7d1f43b67fa931ce348a38997. Change it there, then run make sync-standards. -->
# Standards

Each principle from [PRINCIPLES.md](https://github.com/junglestyle/.github/blob/main/PRINCIPLES.md) turned into rules you can check in review. When they pull against each other, pick what works and is easiest to undo.

Each repo keeps this file unchanged as `docs/org-standards.md`, headed by the commit it came from, and puts its own rules and examples in `docs/standards.md`. Where the two differ, the repo's file wins and names the rule it overrides. The repo's checks fail if the copy was edited by hand or this file has moved on; resyncing is one command.

## Jungle Style

Use what's already racked.

- Use the stdlib first, then what's already in the stack, then the OS, the database, and the init system. A new dependency comes last and gets a one-line reason in the commit.
- Lean on the database's transactions, the OS's process model, and the supervisor's restarts before writing your own.

## DRY

One fact, one place. DRY is about knowledge, not text.

- A rule has one executable implementation. The docs still describe the behavior a user can observe (a default, a state name, an env var), so a reader never has to open the code to learn what the project does. They just don't carry a second copy of the logic.
- Two blocks that look alike but would change for different reasons stay separate. Duplication is cheaper than the wrong abstraction.
- Extract on the third real use, not the second similar-looking one. A critical invariant is the exception: two implementations of it are already one too many.

## YAGNI

Build the current milestone, not the imagined product.

- No plugin systems, factories, registries, base classes, config frameworks, or extension points without two real cases that need them today.
- No option, flag, or parameter that nothing sets.
- A deferred requirement gets a line in the spec or `docs/ROADMAP.md`, not a stub in code.

## Zen

From the Zen of Python, the parts that bite:

- Explicit over implicit: no import-time side effects, no hidden globals, no magic defaults that differ by environment.
- Flat over nested: early returns, shallow control flow, one module per concept.
- Errors never pass silently: catch only what you can handle, and say what you did with it.
- One obvious way: one code path that the CLI, API, and anything else share.

## Wabi sabi

Accept imperfection that's honest about itself.

- Plain, finished, and slightly rough beats polished and unfinished. Ship the small thing that works.
- Be honest about sharp edges. A known limit goes in the docs or one comment; a workaround is fine when it's small and says what it works around.
- Don't gold-plate output, logging, or UI. Good enough to read is good enough.

## Wu wei

Work with the grain. Don't force it.

- Prefer state the system converges to (expired leases requeue, stale locks clear) over steps someone must remember.
- Minimize recurring operational work, and make unavoidable maintenance explicit. A feature that quietly creates chores for its owner is a bug; so is elaborate automation to avoid an occasional manual step.

## Pragmatic Programmer

- Tracer bullets: get a thin slice working end to end before widening any layer.
- Crash early: an impossible state raises. It isn't logged and continued.
- Fix broken windows: leave each file you touch a little cleaner, within the change's scope.
- Reversible decisions: prefer the choice that's easy to undo, and record the ones that aren't as ADRs in `docs/stack.md`.
- Orthogonality: layers talk through explicit interfaces (an API, a schema, a file format) and don't know each other's internals.

## Testing

Few, focused tests of consequential behavior, at boundaries that stay put.

- Test what a user would notice: the behaviors the spec promises, recovery after a crash, and the security boundaries.
- Don't test plumbing, getters, or internal call order. Don't mock half the system to prove a call happened.
- Don't pin prose: check what a message means, not its words. Output another program reads is interface, and a test may pin it.
- Before adding a test, ask what realistic regression it catches and whether it survives a refactor. If the answer is weak, skip it.

## Running it

- One command runs local checks and CI. Installing takes as few maintained scripts as the job needs. Setup steps don't get scattered across docs.
- The README says what the project is and how to run it, in that order.

## Privacy

- Never commit hostnames, tokens, personal context, or deployment config. Use `.env.example` for config shape. In docs, name roles, not machines: "the NAS" or "a GPU host".

## Comments and names

- Names say what. Comments say why.
- Keep a comment only if a maintainer would lose a fact that the code, types, and tests don't already state. No comments that restate the code, and no commented-out code.
- The facts worth a comment are constraints the code can't show: concurrency and lifecycle rules, security boundaries, protocol quirks, and the tradeoff behind a non-obvious choice.
- Project names are dumb and Linuxy: short, lowercase, nothing producty.

## Prose

For docs, commit messages, and PR descriptions. Follow the rules in
[agent-style](https://github.com/yzhao062/agent-style/blob/7da99e0b0610aca4e4d340351bd802bfd448c894/RULES.md)
but skip its House Style section, which is for academic papers. These rules take precedence:

- Describe the project as it is meant to be. No dates, approval trails, or "replacing X"; history lives in git.
- Link instead of restating. Each fact has one home, the same as in code.
- Arrange sections in the order a reader needs them.
- Error messages, log lines, and CLI output are interface. Changing their wording is an interface change, not a style edit.
- Contractions are fine, and headings use sentence case.
- "X, not Y" is fine where it prevents a real confusion.
- When writing up a problem, separate evidence, inference, and unknowns, and label which is which.
