# Stack and decisions

| Layer | Choice |
| --- | --- |
| Language | Python 3.12 |
| Server | FastAPI, uvicorn |
| State | SQLite (WAL), on the NAS |
| Worker | One process per machine, subprocess with `shell=False` |
| Deployment | Docker Compose on the NAS, systemd user unit on workers |
| Transport | Tailscale; bearer token |
| Development | uv, Ruff, mypy, pytest |
| License | Apache-2.0 |

## ADR-001: Server on the NAS, workers pull

The NAS is always on and already holds the data the pipeline reads, so the queue lives there. Workers pull leases
rather than the server pushing work, so the server needs no address book and a new machine joins by starting a
worker.

## ADR-002: SQLite, no broker

One owner and a few jobs a minute. A single SQLite file with transactions is the whole queue: no Redis, no
RabbitMQ, nothing else to back up or upgrade.

## ADR-003: Workers own their commands

A job names a kind, and the worker's own config turns the kind into argv. A compromised or confused server can
queue the wrong kind but can't make a worker run arbitrary commands.

## ADR-004: At-least-once, idempotent jobs

Leases expire and jobs rerun. Exactly-once would need coordination the workloads don't need, since they already
rebuild from raw and cache by content.
