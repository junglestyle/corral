"""The job queue: SQLite state and every rule for how it changes. Knows nothing about HTTP."""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from pydantic import BaseModel, Field

ALL = "all"
LEASE_SECONDS = 60
DEFAULT_AGING_SECONDS = 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    needs TEXT NOT NULL,
    priority INTEGER NOT NULL,
    args TEXT NOT NULL,
    key TEXT,
    then_specs TEXT NOT NULL,
    max_attempts INTEGER NOT NULL,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    enqueued_at REAL NOT NULL,
    worker TEXT,
    lease_id TEXT UNIQUE,
    lease_expires REAL,
    stop TEXT,
    started_at REAL,
    finished_at REAL,
    exit_code INTEGER,
    result TEXT
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs (state);
CREATE INDEX IF NOT EXISTS jobs_key ON jobs (key) WHERE state = 'queued';
CREATE TABLE IF NOT EXISTS workers (
    name TEXT PRIMARY KEY,
    offers TEXT NOT NULL,
    kinds TEXT NOT NULL,
    foreign_procs TEXT NOT NULL,
    last_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pauses (
    name TEXT PRIMARY KEY,
    until REAL
);
"""


class JobSpec(BaseModel):
    kind: str = Field(min_length=1)
    needs: list[str] = []
    priority: int = Field(5, ge=0, le=9)
    args: dict[str, Any] = {}
    key: str | None = None
    then: list[JobSpec] = []
    max_attempts: int = Field(3, ge=1)


class NotFound(Exception):
    pass


class Conflict(Exception):
    """The job isn't in a state that allows this, or the lease is no longer the caller's."""


class Queue:
    def __init__(
        self,
        path: str,
        aging_seconds: float = DEFAULT_AGING_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = path
        self.aging_seconds = aging_seconds
        self.clock = clock
        db = sqlite3.connect(path)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
        finally:
            db.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, isolation_level=None, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def enqueue(self, spec: JobSpec) -> tuple[dict[str, Any], bool]:
        """Returns the job and whether it was created; an existing queued job with the same key wins."""
        with self._tx() as db:
            job_id, created = self._enqueue(db, spec)
            return _job(self._row(db, job_id)), created

    def get(self, job_id: int) -> dict[str, Any]:
        with self._tx() as db:
            self._expire(db)
            return _job(self._row(db, job_id))

    def jobs(self, states: list[str], limit: int = 100) -> list[dict[str, Any]]:
        with self._tx() as db:
            self._expire(db)
            where = f"WHERE state IN ({','.join('?' * len(states))})" if states else ""
            rows = db.execute(f"SELECT * FROM jobs {where} ORDER BY id DESC LIMIT ?", (*states, limit))
            return [_job(r) for r in rows]

    def lease(
        self, worker: str, offers: list[str], kinds: list[str], foreign: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        now = self.clock()
        with self._tx() as db:
            self._expire(db)
            self._see(db, worker, offers, kinds, foreign)
            # A worker asking for work isn't running anything, so whatever it still holds was lost in a crash.
            for row in db.execute(
                "SELECT * FROM jobs WHERE state = 'leased' AND worker = ?", (worker,)
            ).fetchall():
                self._settle(db, row, exit_code=None, result=None)
            if self._paused(db, worker):
                return None

            have = set(offers)
            candidates = [
                r
                for r in db.execute(
                    f"SELECT * FROM jobs WHERE state = 'queued' AND kind IN ({','.join('?' * len(kinds))})",
                    kinds,
                )
                if set(json.loads(r["needs"])) <= have
            ]
            if not candidates:
                return None
            best = max(candidates, key=lambda r: (self._effective(r, now), -r["enqueued_at"], -r["id"]))
            lease_id = secrets.token_urlsafe(16)
            db.execute(
                """UPDATE jobs SET state = 'leased', worker = ?, lease_id = ?, lease_expires = ?,
                   attempts = attempts + 1, started_at = ?, stop = NULL WHERE id = ?""",
                (worker, lease_id, now + LEASE_SECONDS, now, best["id"]),
            )
            return _job(self._row(db, best["id"])) | {"lease_id": lease_id, "lease_seconds": LEASE_SECONDS}

    def heartbeat(self, lease_id: str, foreign: list[dict[str, Any]]) -> bool:
        """Extends the lease. Returns True when the job should be killed."""
        now = self.clock()
        with self._tx() as db:
            self._expire(db)
            row = self._leased(db, lease_id)
            db.execute("UPDATE jobs SET lease_expires = ? WHERE id = ?", (now + LEASE_SECONDS, row["id"]))
            db.execute(
                "UPDATE workers SET foreign_procs = ?, last_seen = ? WHERE name = ?",
                (json.dumps(foreign), now, row["worker"]),
            )
            return row["stop"] is not None

    def finish(self, lease_id: str, exit_code: int, result: dict[str, Any] | None) -> dict[str, Any]:
        with self._tx() as db:
            self._expire(db)
            row = self._leased(db, lease_id)
            self._settle(db, row, exit_code, result)
            return _job(self._row(db, row["id"]))

    def kill(self, job_id: int) -> dict[str, Any]:
        with self._tx() as db:
            self._expire(db)
            row = self._row(db, job_id)
            if row["state"] != "leased":
                raise Conflict(f"job {job_id} is {row['state']}, not running")
            db.execute("UPDATE jobs SET stop = 'requeue' WHERE id = ? AND stop IS NULL", (job_id,))
            return _job(self._row(db, job_id))

    def cancel(self, job_id: int) -> dict[str, Any]:
        now = self.clock()
        with self._tx() as db:
            self._expire(db)
            row = self._row(db, job_id)
            if row["state"] == "queued":
                db.execute("UPDATE jobs SET state = 'cancelled', finished_at = ? WHERE id = ?", (now, job_id))
            elif row["state"] == "leased":
                db.execute("UPDATE jobs SET stop = 'cancel' WHERE id = ?", (job_id,))
            else:
                raise Conflict(f"job {job_id} already {row['state']}")
            return _job(self._row(db, job_id))

    def pause(self, name: str, now: bool = False, for_seconds: float | None = None) -> None:
        until = self.clock() + for_seconds if for_seconds is not None else None
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO pauses (name, until) VALUES (?, ?)", (name, until))
            if now:
                db.execute(
                    "UPDATE jobs SET stop = 'requeue' WHERE state = 'leased' AND stop IS NULL"
                    + ("" if name == ALL else " AND worker = ?"),
                    () if name == ALL else (name,),
                )

    def resume(self, name: str) -> None:
        """Resuming `all` clears every pause; resuming one worker leaves a pause on `all` in force."""
        with self._tx() as db:
            if name == ALL:
                db.execute("DELETE FROM pauses")
            else:
                db.execute("DELETE FROM pauses WHERE name = ?", (name,))

    def workers(self) -> list[dict[str, Any]]:
        with self._tx() as db:
            self._expire(db)
            out = []
            for w in db.execute("SELECT * FROM workers ORDER BY name").fetchall():
                job = db.execute(
                    "SELECT id FROM jobs WHERE state = 'leased' AND worker = ?", (w["name"],)
                ).fetchone()
                foreign = json.loads(w["foreign_procs"])
                pause = self._paused(db, w["name"])
                if pause:
                    state = "draining" if job else "paused"
                elif job:
                    state = "running"
                elif foreign:
                    state = "blocked"
                else:
                    state = "idle"
                out.append(
                    {
                        "name": w["name"],
                        "state": state,
                        "job": job["id"] if job else None,
                        "offers": json.loads(w["offers"]),
                        "kinds": json.loads(w["kinds"]),
                        "foreign": foreign,
                        "paused_until": _iso(pause["until"]) if pause else None,
                        "last_seen": _iso(w["last_seen"]),
                    }
                )
            return out

    def _enqueue(self, db: sqlite3.Connection, spec: JobSpec) -> tuple[int, bool]:
        if spec.key is not None:
            hit = db.execute(
                "SELECT id FROM jobs WHERE state = 'queued' AND key = ? ORDER BY id LIMIT 1", (spec.key,)
            ).fetchone()
            if hit:
                return int(hit["id"]), False
        cur = db.execute(
            """INSERT INTO jobs
               (kind, needs, priority, args, key, then_specs, max_attempts, state, enqueued_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?)""",
            (
                spec.kind,
                json.dumps(spec.needs),
                spec.priority,
                json.dumps(spec.args),
                spec.key,
                json.dumps([t.model_dump() for t in spec.then]),
                spec.max_attempts,
                self.clock(),
            ),
        )
        assert cur.lastrowid is not None
        return cur.lastrowid, True

    def _settle(
        self, db: sqlite3.Connection, row: sqlite3.Row, exit_code: int | None, result: dict[str, Any] | None
    ) -> None:
        """Ends a lease. exit_code None means the lease was lost (expired or orphaned)."""
        now = self.clock()
        attempts = row["attempts"]
        if row["stop"] == "requeue":
            state, attempts = "queued", attempts - 1
        elif row["stop"] == "cancel":
            state = "cancelled"
        elif exit_code == 0:
            state = "succeeded"
        elif attempts < row["max_attempts"]:
            state = "queued"
        else:
            state = "failed"
        db.execute(
            """UPDATE jobs SET state = ?, attempts = ?, lease_id = NULL, lease_expires = NULL, stop = NULL,
               exit_code = ?, result = ?, finished_at = ? WHERE id = ?""",
            (
                state,
                attempts,
                exit_code,
                json.dumps(result) if result is not None else None,
                None if state == "queued" else now,
                row["id"],
            ),
        )
        if state == "succeeded":
            for spec in json.loads(row["then_specs"]):
                self._enqueue(db, JobSpec.model_validate(spec))

    def _expire(self, db: sqlite3.Connection) -> None:
        now = self.clock()
        leased = "SELECT * FROM jobs WHERE state = 'leased' AND lease_expires < ?"
        for row in db.execute(leased, (now,)).fetchall():
            self._settle(db, row, exit_code=None, result=None)
        db.execute("DELETE FROM pauses WHERE until IS NOT NULL AND until <= ?", (now,))

    def _see(
        self,
        db: sqlite3.Connection,
        worker: str,
        offers: list[str],
        kinds: list[str],
        foreign: list[dict[str, Any]],
    ) -> None:
        db.execute(
            "INSERT OR REPLACE INTO workers (name, offers, kinds, foreign_procs, last_seen)"
            " VALUES (?, ?, ?, ?, ?)",
            (worker, json.dumps(offers), json.dumps(kinds), json.dumps(foreign), self.clock()),
        )

    def _paused(self, db: sqlite3.Connection, worker: str) -> sqlite3.Row | None:
        row: sqlite3.Row | None = db.execute(
            "SELECT * FROM pauses WHERE name IN (?, ?) ORDER BY until IS NOT NULL, until DESC LIMIT 1",
            (worker, ALL),
        ).fetchone()
        return row

    def _effective(self, row: sqlite3.Row, now: float) -> float:
        return float(row["priority"] + (now - row["enqueued_at"]) / self.aging_seconds)

    def _row(self, db: sqlite3.Connection, job_id: int) -> sqlite3.Row:
        row: sqlite3.Row | None = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise NotFound(f"no job {job_id}")
        return row

    def _leased(self, db: sqlite3.Connection, lease_id: str) -> sqlite3.Row:
        row: sqlite3.Row | None = db.execute(
            "SELECT * FROM jobs WHERE lease_id = ? AND state = 'leased'", (lease_id,)
        ).fetchone()
        if row is None:
            raise Conflict("lease expired or already finished")
        return row


def _job(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "needs": json.loads(row["needs"]),
        "priority": row["priority"],
        "args": json.loads(row["args"]),
        "key": row["key"],
        "then": json.loads(row["then_specs"]),
        "state": row["state"],
        "attempts": row["attempts"],
        "max_attempts": row["max_attempts"],
        "worker": row["worker"],
        "stopping": row["stop"] is not None,
        "enqueued_at": _iso(row["enqueued_at"]),
        "started_at": _iso(row["started_at"]),
        "finished_at": _iso(row["finished_at"]),
        "exit_code": row["exit_code"],
        "result": json.loads(row["result"]) if row["result"] is not None else None,
    }


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
