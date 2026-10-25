# Stack and decisions

| Layer | Choice |
| --- | --- |
| Language | Python 3.12 |
| Server | FastAPI, uvicorn |
| State | SQLite (WAL) |
| Worker | One process per machine, subprocess with `shell=False` |
| Deployment | Docker Compose for the server, systemd user unit for workers |
| Transport | HTTP on a private network, bearer token |
| Development | uv, Ruff, mypy, pytest |
| License | MIT |

## ADR-001: Server on an always-on host, workers pull

The queue lives on a host that's always up, typically the NAS that already holds the data. Workers pull leases
rather than the server pushing work, so the server needs no address book and a new machine joins by starting a
worker.

## ADR-002: SQLite, no broker

A handful of machines and a few jobs a minute. A single SQLite file with transactions is the whole queue: no Redis, no
RabbitMQ, nothing else to back up or upgrade.

## ADR-003: Workers own their commands

A job names a kind, and the worker's own config turns the kind into argv. A compromised or confused server can
queue the wrong kind but can't make a worker run arbitrary commands.

## ADR-004: At-least-once, idempotent jobs

Leases expire and jobs rerun. Exactly-once would need coordination most workloads don't need: ones that rebuild
from source and cache by content are already safe to rerun.
