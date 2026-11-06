"""The job queue: SQLite state and every rule for how it changes. Knows nothing about HTTP."""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from enum import StrEnum
from importlib import resources
from math import floor
from typing import Any

from corral.records import Job, JobState, Lease, Proc, Worker, WorkerState
from corral.settings import Event, Settings

ALL = "all"
LEASE_SECONDS = 60
# Weight of the newest run in a kind's seconds-per-item average.
TIMING_WEIGHT = 0.3
# A webhook that fails is retried after 5 s, doubling up to this, until the event is this old.
RETRY_MAX_SECONDS = 600
GIVE_UP_SECONDS = 86400
# What a job.finished event says about the job.
FINISHED_FIELDS = ("id", "kind", "state", "args", "attempts", "worker", "started_at", "finished_at")


class Stop(StrEnum):
    """Why a running job is being killed: the values of `jobs.stop`, which its CHECK lists too."""

    REQUEUE = "requeue"
    CANCEL = "cancel"


@dataclass(frozen=True)
class JobSpec:
    """A job to enqueue, already validated: the server's request body checks it and fills in the defaults."""

    kind: str
    needs: list[str]
    priority: int
    args: dict[str, Any]
    key: str | None
    then: list[JobSpec]
    max_attempts: int
    # A held job waits for at most wait_seconds from when it's created; see _held_until.
    wait_seconds: float | None
    quiet_seconds: float


@dataclass(frozen=True)
class Delivery:
    """One event on its way to one webhook. `body` carries the event's id, the same on every retry."""

    event_id: int
    url: str
    body: dict[str, Any]


class QueueError(Exception):
    """The job isn't in a state that allows this, or the lease is no longer the caller's."""


class Queue:
    def __init__(
        self,
        path: str,
        settings: Settings = Settings(),  # noqa: B008  # Frozen, so sharing the default is safe.
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = path
        self.settings = settings
        self.clock = clock
        db = sqlite3.connect(path, isolation_level=None)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            _migrate(db)
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

    def enqueue(self, spec: JobSpec) -> tuple[Job, bool]:
        """Returns the job and whether it was created; an existing queued job with the same key wins."""
        with self._tx() as db:
            job_id, created = self._enqueue(db, spec)
            return self._view(self._row(db, job_id), self._upstream(db)), created

    def get(self, job_id: int) -> Job:
        with self._tx() as db:
            self._expire(db)
            return self._view(self._row(db, job_id), self._upstream(db))

    def jobs(self, states: list[JobState], limit: int = 100) -> list[Job]:
        with self._tx() as db:
            self._expire(db)
            where = f"WHERE state IN ({','.join('?' * len(states))})" if states else ""
            sql = f"SELECT * FROM jobs {where} ORDER BY id DESC LIMIT ?"
            rows = db.execute(sql, (*states, limit)).fetchall()
            upstream = self._upstream(db)
            return [self._view(r, upstream) for r in rows]

    def lease(self, worker: str, offers: list[str], kinds: list[str], foreign: list[Proc]) -> Lease | None:
        now = self.clock()
        with self._tx() as db:
            self._expire(db)
            self._see(db, worker, offers, kinds, foreign)
            # A worker asking for work isn't running anything, so whatever it still holds was lost in a crash.
            for row in db.execute(
                "SELECT * FROM jobs WHERE state = ? AND worker = ?", (JobState.LEASED, worker)
            ).fetchall():
                self._settle(db, row, exit_code=None, result=None)
            if self._paused(db, worker):
                return None

            have = set(offers)
            upstream = self._upstream(db)
            candidates = [
                r
                for r in db.execute(
                    f"SELECT * FROM jobs WHERE state = ? AND kind IN ({','.join('?' * len(kinds))})",
                    (JobState.QUEUED, *kinds),
                )
                if set(json.loads(r["needs"])) <= have and _held_until(r, now, upstream) is None
            ]
            if not candidates:
                return None
            best = max(candidates, key=lambda r: (self._effective(r, now), -r["enqueued_at"], -r["id"]))
            lease_id = secrets.token_urlsafe(16)
            db.execute(
                """UPDATE jobs SET state = ?, worker = ?, lease_id = ?, lease_expires = ?,
                   attempts = attempts + 1, started_at = ?, stop = NULL WHERE id = ?""",
                (JobState.LEASED, worker, lease_id, now + LEASE_SECONDS, now, best["id"]),
            )
            return Lease(
                **vars(_job(self._row(db, best["id"]))),
                lease_id=lease_id,
                lease_seconds=LEASE_SECONDS,
                suggested_items=self._suggested_items(db, best["kind"]),
            )

    def heartbeat(self, lease_id: str, foreign: list[Proc]) -> bool:
        """Extends the lease. Returns True when the job should be killed."""
        now = self.clock()
        with self._tx() as db:
            self._expire(db)
            row = self._leased(db, lease_id)
            db.execute("UPDATE jobs SET lease_expires = ? WHERE id = ?", (now + LEASE_SECONDS, row["id"]))
            self._observe(db, row["worker"], foreign)
            return row["stop"] is not None

    def finish(self, lease_id: str, exit_code: int, result: dict[str, Any] | None) -> Job:
        with self._tx() as db:
            self._expire(db)
            row = self._leased(db, lease_id)
            self._settle(db, row, exit_code, result)
            return _job(self._row(db, row["id"]))

    def kill(self, job_id: int) -> Job:
        with self._tx() as db:
            self._expire(db)
            row = self._row(db, job_id)
            if row["state"] != JobState.LEASED:
                raise QueueError(f"job {job_id} is {row['state']}, not running")
            db.execute("UPDATE jobs SET stop = ? WHERE id = ? AND stop IS NULL", (Stop.REQUEUE, job_id))
            return _job(self._row(db, job_id))

    def cancel(self, job_id: int) -> Job:
        now = self.clock()
        with self._tx() as db:
            self._expire(db)
            row = self._row(db, job_id)
            if row["state"] == JobState.QUEUED:
                db.execute(
                    "UPDATE jobs SET state = ?, finished_at = ? WHERE id = ?",
                    (JobState.CANCELLED, now, job_id),
                )
                self._finished(db, self._row(db, job_id))
            elif row["state"] == JobState.LEASED:
                db.execute("UPDATE jobs SET stop = ? WHERE id = ?", (Stop.CANCEL, job_id))
            else:
                raise QueueError(f"job {job_id} already {row['state']}")
            return _job(self._row(db, job_id))

    def release(self, job_id: int) -> Job:
        """Ends a queued job's hold, so it's leased as soon as a worker can take it."""
        with self._tx() as db:
            self._expire(db)
            row = self._row(db, job_id)
            if row["state"] != JobState.QUEUED:
                raise QueueError(f"job {job_id} is {row['state']}, not queued")
            db.execute("UPDATE jobs SET wait_seconds = NULL, quiet_seconds = 0 WHERE id = ?", (job_id,))
            return _job(self._row(db, job_id))

    def pause(self, name: str, now: bool = False, for_seconds: float | None = None) -> None:
        until = self.clock() + for_seconds if for_seconds is not None else None
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO pauses (name, until) VALUES (?, ?)", (name, until))
            if now:
                db.execute(
                    "UPDATE jobs SET stop = ? WHERE state = ? AND stop IS NULL"
                    + ("" if name == ALL else " AND worker = ?"),
                    (Stop.REQUEUE, JobState.LEASED) + (() if name == ALL else (name,)),
                )

    def resume(self, name: str) -> None:
        """Resuming `all` clears every pause; resuming one worker leaves a pause on `all` in force."""
        with self._tx() as db:
            if name == ALL:
                db.execute("DELETE FROM pauses")
            else:
                db.execute("DELETE FROM pauses WHERE name = ?", (name,))

    def workers(self) -> list[Worker]:
        with self._tx() as db:
            self._expire(db)
            out = []
            for w in db.execute("SELECT * FROM workers ORDER BY name").fetchall():
                job = db.execute(
                    "SELECT id FROM jobs WHERE state = ? AND worker = ?", (JobState.LEASED, w["name"])
                ).fetchone()
                foreign = _procs(w["foreign_procs"])
                pause = self._paused(db, w["name"])
                if pause:
                    state = WorkerState.DRAINING if job else WorkerState.PAUSED
                elif job:
                    state = WorkerState.RUNNING
                elif foreign:
                    state = WorkerState.BLOCKED
                else:
                    state = WorkerState.IDLE
                out.append(
                    Worker(
                        name=w["name"],
                        state=state,
                        job=job["id"] if job else None,
                        offers=json.loads(w["offers"]),
                        kinds=json.loads(w["kinds"]),
                        foreign=foreign,
                        paused_until=_iso_or_none(pause["until"]) if pause else None,
                        last_seen=_iso(w["last_seen"]),
                    )
                )
            return out

    def housekeep(self) -> None:
        """Does what time alone triggers, for when no request comes in to do it on the way."""
        with self._tx() as db:
            self._expire(db)

    def due_deliveries(self, limit: int = 20) -> list[Delivery]:
        with self._tx() as db:
            rows = db.execute(
                """SELECT d.event_id, d.url, e.body FROM deliveries d JOIN events e ON e.id = d.event_id
                   WHERE d.next_at <= ? ORDER BY d.next_at, d.event_id LIMIT ?""",
                (self.clock(), limit),
            )
            return [
                Delivery(r["event_id"], r["url"], {"id": r["event_id"]} | json.loads(r["body"])) for r in rows
            ]

    def delivered(self, d: Delivery) -> None:
        with self._tx() as db:
            self._drop(db, d)

    def retry(self, d: Delivery) -> bool:
        """Schedules the next try. Returns False when the event is too old and was dropped instead."""
        now = self.clock()
        with self._tx() as db:
            row = db.execute(
                """SELECT d.attempts, e.created_at FROM deliveries d JOIN events e ON e.id = d.event_id
                   WHERE d.event_id = ? AND d.url = ?""",
                (d.event_id, d.url),
            ).fetchone()
            if row is None:
                return False
            if now - row["created_at"] >= GIVE_UP_SECONDS:
                self._drop(db, d)
                return False
            delay = min(5 * 2 ** row["attempts"], RETRY_MAX_SECONDS)
            db.execute(
                "UPDATE deliveries SET attempts = attempts + 1, next_at = ? WHERE event_id = ? AND url = ?",
                (now + delay, d.event_id, d.url),
            )
            return True

    def _drop(self, db: sqlite3.Connection, d: Delivery) -> None:
        db.execute("DELETE FROM deliveries WHERE event_id = ? AND url = ?", (d.event_id, d.url))
        db.execute(
            "DELETE FROM events WHERE id = ? AND NOT EXISTS (SELECT 1 FROM deliveries WHERE event_id = ?)",
            (d.event_id, d.event_id),
        )

    def _enqueue(self, db: sqlite3.Connection, spec: JobSpec) -> tuple[int, bool]:
        now = self.clock()
        if spec.key is not None:
            hit = db.execute(
                "SELECT id FROM jobs WHERE state = ? AND key = ? ORDER BY id LIMIT 1",
                (JobState.QUEUED, spec.key),
            ).fetchone()
            if hit:
                # The one thing a coalesced enqueue changes: a held job's quiet period starts again.
                db.execute("UPDATE jobs SET last_enqueued_at = ? WHERE id = ?", (now, hit["id"]))
                return int(hit["id"]), False
        cur = db.execute(
            """INSERT INTO jobs
               (kind, needs, priority, args, key, then_specs, max_attempts, wait_seconds, quiet_seconds,
                state, enqueued_at, last_enqueued_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                spec.kind,
                json.dumps(spec.needs),
                spec.priority,
                json.dumps(spec.args),
                spec.key,
                json.dumps([asdict(t) for t in spec.then]),
                spec.max_attempts,
                spec.wait_seconds,
                spec.quiet_seconds,
                JobState.QUEUED,
                now,
                now,
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
        if row["stop"] == Stop.REQUEUE:
            state, attempts = JobState.QUEUED, attempts - 1
        elif row["stop"] == Stop.CANCEL:
            state = JobState.CANCELLED
        elif exit_code == 0:
            state = JobState.SUCCEEDED
        elif attempts < row["max_attempts"]:
            state = JobState.QUEUED
        else:
            state = JobState.FAILED
        db.execute(
            """UPDATE jobs SET state = ?, attempts = ?, lease_id = NULL, lease_expires = NULL, stop = NULL,
               exit_code = ?, result = ?, finished_at = ? WHERE id = ?""",
            (
                state,
                attempts,
                exit_code,
                json.dumps(result) if result is not None else None,
                None if state == JobState.QUEUED else now,
                row["id"],
            ),
        )
        if state == JobState.QUEUED:
            return
        if state == JobState.SUCCEEDED:
            self._time(db, row["kind"], now - row["started_at"], (result or {}).get("items"))
            if (result or {}).get("more") is True:
                self._enqueue(db, _spec(row))
            for spec in json.loads(row["then_specs"]):
                self._enqueue(db, _stored_spec(spec))
        self._finished(db, self._row(db, row["id"]))

    def _finished(self, db: sqlite3.Connection, row: sqlite3.Row) -> None:
        job = _job(row)
        body = {"job": {k: getattr(job, k) for k in FINISHED_FIELDS}, "result": job.result}
        self._emit(db, Event.JOB_FINISHED, body, job.kind, job.state)

    def _time(self, db: sqlite3.Connection, kind: str, seconds: float, items: object) -> None:
        """Folds a successful run into its kind's average seconds per item."""
        if not isinstance(items, int) or isinstance(items, bool) or items <= 0:
            return
        db.execute(
            """INSERT INTO kind_timings (kind, seconds_per_item) VALUES (?, ?)
               ON CONFLICT (kind) DO UPDATE SET
               seconds_per_item = seconds_per_item + ? * (excluded.seconds_per_item - seconds_per_item)""",
            (kind, seconds / items, TIMING_WEIGHT),
        )

    def _suggested_items(self, db: sqlite3.Connection, kind: str) -> int | None:
        row = db.execute("SELECT seconds_per_item FROM kind_timings WHERE kind = ?", (kind,)).fetchone()
        if row is None:
            return None
        return max(1, floor(self.settings.window_for(kind) / max(float(row["seconds_per_item"]), 1e-6)))

    def _emit(
        self,
        db: sqlite3.Connection,
        event: Event,
        body: dict[str, Any],
        kind: str | None = None,
        state: str | None = None,
    ) -> None:
        """Queues the event for each webhook that wants it, in the caller's transaction."""
        urls = [w.url for w in self.settings.webhooks if w.wants(event, kind, state)]
        if not urls:
            return
        now = self.clock()
        cur = db.execute(
            "INSERT INTO events (body, created_at) VALUES (?, ?)", (json.dumps({"event": event} | body), now)
        )
        db.executemany(
            "INSERT OR IGNORE INTO deliveries (event_id, url, next_at) VALUES (?, ?, ?)",
            [(cur.lastrowid, url, now) for url in urls],
        )

    def _expire(self, db: sqlite3.Connection) -> None:
        """Catches up on everything that happens with time: lost leases, timed pauses, block alerts."""
        now = self.clock()
        leased = "SELECT * FROM jobs WHERE state = ? AND lease_expires < ?"
        for row in db.execute(leased, (JobState.LEASED, now)).fetchall():
            self._settle(db, row, exit_code=None, result=None)
        db.execute("DELETE FROM pauses WHERE until IS NOT NULL AND until <= ?", (now,))
        self._alert_blocks(db, now)

    def _alert_blocks(self, db: sqlite3.Connection, now: float) -> None:
        """Raises worker.blocked for a live, unpaused, idle worker blocked long enough that GPU work waits."""
        blocked = db.execute(
            """SELECT * FROM workers WHERE block_alerted = 0 AND blocked_since <= ? AND last_seen >= ?
               AND name NOT IN (SELECT worker FROM jobs WHERE state = ?)""",
            (now - self.settings.block_alert_after, now - LEASE_SECONDS, JobState.LEASED),
        ).fetchall()
        upstream = self._upstream(db) if blocked else {}
        for w in blocked:
            if self._paused(db, w["name"]):
                continue
            kinds = json.loads(w["kinds"])
            marks = ",".join("?" * len(kinds))
            queued = db.execute(
                f"SELECT * FROM jobs WHERE state = ? AND kind IN ({marks})", (JobState.QUEUED, *kinds)
            )
            waiting = sum(
                "gpu" in json.loads(r["needs"]) and _held_until(r, now, upstream) is None for r in queued
            )
            if not waiting:
                continue
            self._emit(
                db,
                Event.WORKER_BLOCKED,
                {
                    "worker": w["name"],
                    "foreign": json.loads(w["foreign_procs"]),
                    "since": _iso(w["blocked_since"]),
                    "waiting": waiting,
                },
            )
            db.execute("UPDATE workers SET block_alerted = 1 WHERE name = ?", (w["name"],))

    def _see(
        self,
        db: sqlite3.Connection,
        worker: str,
        offers: list[str],
        kinds: list[str],
        foreign: list[Proc],
    ) -> None:
        db.execute(
            """INSERT INTO workers (name, offers, kinds, foreign_procs, last_seen) VALUES (?, ?, ?, '[]', ?)
               ON CONFLICT (name) DO UPDATE SET offers = excluded.offers, kinds = excluded.kinds""",
            (worker, json.dumps(offers), json.dumps(kinds), self.clock()),
        )
        self._observe(db, worker, foreign)

    def _observe(self, db: sqlite3.Connection, worker: str, foreign: list[Proc]) -> None:
        """Records what else is on the worker's GPU, and says so when a block that was alerted clears."""
        now = self.clock()
        if not foreign:
            alerted = db.execute(
                "SELECT 1 FROM workers WHERE name = ? AND block_alerted = 1", (worker,)
            ).fetchone()
            if alerted:
                self._emit(db, Event.WORKER_UNBLOCKED, {"worker": worker})
        db.execute(
            """UPDATE workers SET foreign_procs = ?, last_seen = ?,
               blocked_since = CASE WHEN ? THEN COALESCE(blocked_since, ?) END,
               block_alerted = CASE WHEN ? THEN block_alerted ELSE 0 END
               WHERE name = ?""",
            (json.dumps([asdict(p) for p in foreign]), now, bool(foreign), now, bool(foreign), worker),
        )

    def _paused(self, db: sqlite3.Connection, worker: str) -> sqlite3.Row | None:
        row: sqlite3.Row | None = db.execute(
            "SELECT * FROM pauses WHERE name IN (?, ?) ORDER BY until IS NOT NULL, until DESC LIMIT 1",
            (worker, ALL),
        ).fetchone()
        return row

    def _effective(self, row: sqlite3.Row, now: float) -> float:
        return float(row["priority"] + (now - row["enqueued_at"]) / self.settings.aging_seconds)

    def _upstream(self, db: sqlite3.Connection) -> dict[str, set[int]]:
        """Each key some queued or running job would enqueue on success, anywhere in its `then`, and those
        jobs' ids."""
        out: dict[str, set[int]] = {}
        rows = db.execute(
            "SELECT id, then_specs FROM jobs WHERE state IN (?, ?) AND then_specs != '[]'",
            (JobState.QUEUED, JobState.LEASED),
        )
        for r in rows:
            pending = json.loads(r["then_specs"])
            while pending:
                s = pending.pop()
                if s["key"] is not None:
                    out.setdefault(s["key"], set()).add(r["id"])
                pending.extend(s["then"])
        return out

    def _view(self, row: sqlite3.Row, upstream: dict[str, set[int]]) -> Job:
        return _job(row, _held_until(row, self.clock(), upstream))

    def _row(self, db: sqlite3.Connection, job_id: int) -> sqlite3.Row:
        row: sqlite3.Row | None = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise LookupError(f"no job {job_id}")
        return row

    def _leased(self, db: sqlite3.Connection, lease_id: str) -> sqlite3.Row:
        row: sqlite3.Row | None = db.execute(
            "SELECT * FROM jobs WHERE lease_id = ? AND state = ?", (lease_id, JobState.LEASED)
        ).fetchone()
        if row is None:
            raise QueueError("lease expired or already finished")
        return row


def _held_until(row: sqlite3.Row, now: float, upstream: dict[str, set[int]]) -> float | None:
    """When a held job's wait runs out, or None if it may be leased now.

    A job with wait_seconds is held until it's first leased, while another queued or running job would
    enqueue its key or it was enqueued less than quiet_seconds ago, but never past wait_seconds after it was
    created.
    """
    if row["wait_seconds"] is None or row["state"] != JobState.QUEUED or row["started_at"] is not None:
        return None
    until = float(row["enqueued_at"] + row["wait_seconds"])
    if now >= until:
        return None
    quiet = now < row["last_enqueued_at"] + row["quiet_seconds"]
    feeding = upstream.get(row["key"], set()) - {row["id"]}
    return until if quiet or feeding else None


def _job(row: sqlite3.Row, held_until: float | None = None) -> Job:
    return Job(
        id=row["id"],
        kind=row["kind"],
        needs=json.loads(row["needs"]),
        priority=row["priority"],
        args=json.loads(row["args"]),
        key=row["key"],
        then=json.loads(row["then_specs"]),
        state=JobState(row["state"]),
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        wait_seconds=row["wait_seconds"],
        quiet_seconds=row["quiet_seconds"],
        held_until=_iso_or_none(held_until),
        worker=row["worker"],
        stopping=row["stop"] is not None,
        enqueued_at=_iso(row["enqueued_at"]),
        started_at=_iso_or_none(row["started_at"]),
        finished_at=_iso_or_none(row["finished_at"]),
        exit_code=row["exit_code"],
        result=json.loads(row["result"]) if row["result"] is not None else None,
    )


def _procs(text: str) -> list[Proc]:
    return [Proc(**p) for p in json.loads(text)]


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _iso_or_none(ts: float | None) -> str | None:
    return None if ts is None else _iso(ts)


def _migrate(db: sqlite3.Connection) -> None:
    """Applies each schema/NNN_*.sql the database hasn't seen, in order, each in its own transaction."""
    (version,) = db.execute("PRAGMA user_version").fetchone()
    files = sorted(
        (f for f in (resources.files("corral") / "schema").iterdir() if f.name.endswith(".sql")),
        key=lambda f: f.name,
    )
    for f in files:
        number = int(f.name.split("_", 1)[0])
        if number > version:
            db.executescript(f"BEGIN IMMEDIATE; {f.read_text()}; PRAGMA user_version = {number}; COMMIT;")


def _spec(row: sqlite3.Row) -> JobSpec:
    """The job's own spec, to run it again."""
    return JobSpec(
        kind=row["kind"],
        needs=json.loads(row["needs"]),
        priority=row["priority"],
        args=json.loads(row["args"]),
        key=row["key"],
        then=[_stored_spec(t) for t in json.loads(row["then_specs"])],
        max_attempts=row["max_attempts"],
        wait_seconds=row["wait_seconds"],
        quiet_seconds=row["quiet_seconds"],
    )


def _stored_spec(d: dict[str, Any]) -> JobSpec:
    """A `then` spec as the queue stored it, checked when it was enqueued. Ones stored before held jobs lack
    their fields."""
    return JobSpec(
        kind=d["kind"],
        needs=d["needs"],
        priority=d["priority"],
        args=d["args"],
        key=d["key"],
        then=[_stored_spec(t) for t in d["then"]],
        max_attempts=d["max_attempts"],
        wait_seconds=d.get("wait_seconds"),
        quiet_seconds=d.get("quiet_seconds", 0),
    )
