# Working on corral

Priority job scheduler for opportunistic local compute.

Read before changing behavior:

- [Specification](docs/SPEC.md): what corral does
- [Coding standards](docs/standards.md): how the code should read, on top of the org's [principles](https://github.com/junglestyle/.github/blob/main/PRINCIPLES.md) and standards (`docs/org-standards*.md`)
- [Stack and decisions](docs/stack.md): tools and the decisions behind them
- [Running corral](docs/running.md): configuration and deployment

Docs live in `docs/` and change with behavior. Never commit hostnames, tokens, or deployment config (see
[Privacy](docs/standards.md#privacy)).

Run `make check` before committing. It runs everything CI runs, except the org standards check: `make standards` (needs the network).
