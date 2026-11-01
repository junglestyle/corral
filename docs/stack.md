# Stack and decisions

| Layer | Choice |
| --- | --- |
| Language | Python 3.15 |
| Server | FastAPI, uvicorn |
| State | SQLite (WAL) |
| Worker | One process per machine, subprocess with `shell=False` |
| Deployment | Docker Compose for the server, a systemd unit on the host for workers |
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

## ADR-005: Server in a container, workers on the host

The server is an HTTP service and a SQLite file, so a container is the simplest thing that survives reboots and
upgrades on a NAS. A worker spawns jobs on its host, uses its Docker, and watches its GPU for other processes. In a
container it would need the Docker socket, the GPU, and the host's PID namespace, so it runs on the host.

## ADR-006: FastAPI and uvicorn for the API

The server holds lease requests open for up to 30 s, runs housekeeping (lease expiry, block alerts, webhook
delivery) whether or not requests come in, and validates every request body. FastAPI on uvicorn covers all three
in one process: an async handler holds a long poll without tying up a thread, the lifespan hook runs housekeeping
beside the requests, and pydantic checks bodies at the edge. The stdlib's `http.server` would mean writing routing,
validation and token checks by hand, plus a thread per waiting worker. uvicorn also handles SIGTERM, so the server
stops cleanly as PID 1 in Docker.
