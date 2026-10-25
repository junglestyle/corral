# Working on corral

Priority job scheduler for opportunistic local compute.

Read before changing behavior:

- [Specification](docs/SPEC.md): what corral does
- [Coding standards](docs/standards.md): how the code should read
- [Stack and decisions](docs/stack.md): tools and the decisions behind them
- [Running corral](docs/running.md): configuration and deployment

Docs live in `docs/` and change with behavior. Never commit hostnames, tokens, or deployment config (see
[Privacy](docs/standards.md#privacy)).

`make check` runs everything CI runs.
