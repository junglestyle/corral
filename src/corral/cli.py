"""The `corral` command."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any

import uvicorn

from corral.client import Client
from corral.duration import parse_duration
from corral.queue import ALL, DEFAULT_AGING_SECONDS, Queue
from corral.server import create_app
from corral.worker import load_config, run


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="corral", description="Priority job scheduler")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("server", help="run the server")
    p.add_argument("--db", default="corral.sqlite", help="SQLite file (default: %(default)s)")
    p.add_argument("--host", default="127.0.0.1", help="address to bind (default: %(default)s)")
    p.add_argument("--port", type=int, default=8700, help="(default: %(default)s)")
    p.add_argument(
        "--aging", type=parse_duration, default=DEFAULT_AGING_SECONDS, help="wait worth one priority point"
    )

    p = sub.add_parser("worker", help="run a worker")
    p.add_argument("config", type=Path, help="worker TOML config")

    p = sub.add_parser("enqueue", help="add a job")
    p.add_argument("kind")
    p.add_argument("--needs", default="", help="comma-separated capability labels")
    p.add_argument("--priority", type=int, default=5)
    p.add_argument("--args", default="{}", help="JSON object")
    p.add_argument("--key", help="coalescing key")
    p.add_argument("--then", default="[]", help="JSON list of job specs to enqueue on success")

    p = sub.add_parser("ls", help="list workers and jobs")
    p.add_argument("--state", action="append", help="job state to show; repeatable (default: queued, leased)")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("pause", help="stop a worker taking new jobs")
    p.add_argument("worker", help=f"worker name, or {ALL}")
    p.add_argument("--now", action="store_true", help="also kill and requeue the running job")
    p.add_argument("--for", dest="duration", type=parse_duration, help="resume after this long, e.g. 2h")

    p = sub.add_parser("resume", help="let a worker take jobs again")
    p.add_argument("worker", help=f"worker name, or {ALL}")

    for name, text in (("kill", "stop a running job and requeue it"), ("cancel", "remove a job for good")):
        p = sub.add_parser(name, help=text)
        p.add_argument("job", type=int)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.command == "server":
        serve(args.db, args.host, args.port, args.aging)
        return
    if args.command == "worker":
        run(load_config(args.config), Client.from_env())
        return

    client = Client.from_env()
    if args.command == "enqueue":
        spec = {
            "kind": args.kind,
            "needs": [n for n in args.needs.split(",") if n],
            "priority": args.priority,
            "args": json.loads(args.args),
            "key": args.key,
            "then": json.loads(args.then),
        }
        job, created = client.enqueue(spec)
        print(job["id"] if created else f"{job['id']} (already queued with key {job['key']})")
    elif args.command == "ls":
        show(client.workers(), client.jobs(args.state or ["queued", "leased"], args.limit))
    elif args.command == "pause":
        client.pause(args.worker, args.now, args.duration)
    elif args.command == "resume":
        client.resume(args.worker)
    elif args.command == "kill":
        client.kill(args.job)
    elif args.command == "cancel":
        client.cancel(args.job)


def serve(db: str, host: str, port: int, aging_seconds: float) -> None:
    token = os.environ.get("CORRAL_TOKEN")
    if not token:
        raise SystemExit("set CORRAL_TOKEN")
    uvicorn.run(create_app(Queue(db, aging_seconds), token), host=host, port=port)


def show(workers: list[dict[str, Any]], jobs: list[dict[str, Any]]) -> None:
    rows = [["WORKER", "STATE", "JOB", "OFFERS", "NOTE"]]
    for w in workers:
        note = []
        if w["paused_until"]:
            note.append(f"until {w['paused_until']}")
        if w["foreign"]:
            note.append("gpu in use by " + ", ".join(f"{p['name']} ({p['pid']})" for p in w["foreign"]))
        note.append(f"seen {w['last_seen']}")
        rows.append([w["name"], w["state"], str(w["job"] or ""), ",".join(w["offers"]), "; ".join(note)])
    _table(rows)
    print()
    rows = [["JOB", "STATE", "PRI", "KIND", "WORKER", "TRIES", "ENQUEUED"]]
    for j in jobs:
        state = j["state"] + (" (stopping)" if j["stopping"] else "")
        worker = j["worker"] if j["state"] == "leased" else ""
        tries = f"{j['attempts']}/{j['max_attempts']}"
        rows.append(
            [str(j["id"]), state, str(j["priority"]), j["kind"], worker or "", tries, j["enqueued_at"]]
        )
    _table(rows)


def _table(rows: list[list[str]]) -> None:
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip())
