"""What the server says about jobs and workers: built by the queue, read by the client."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class JobState(StrEnum):
    """The values of `jobs.state`, which its CHECK lists too."""

    QUEUED = "queued"
    LEASED = "leased"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


FINISHED_STATES = frozenset({JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED})


class WorkerState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    BLOCKED = "blocked"
    PAUSED = "paused"
    DRAINING = "draining"


@dataclass(frozen=True)
class Proc:
    """A GPU compute process that isn't corral's."""

    pid: int
    name: str


@dataclass(frozen=True)
class Job:
    id: int
    kind: str
    needs: list[str]
    priority: int
    # The job's own JSON, which corral passes through without reading.
    args: dict[str, Any]
    key: str | None
    then: list[dict[str, Any]]
    state: JobState
    attempts: int
    max_attempts: int
    worker: str | None
    stopping: bool
    enqueued_at: str
    started_at: str | None
    finished_at: str | None
    exit_code: int | None
    result: dict[str, Any] | None


@dataclass(frozen=True)
class Lease(Job):
    """A job just leased, and what its worker needs to run it."""

    lease_id: str
    lease_seconds: float
    suggested_items: int | None = None  # An older server sends none.


@dataclass(frozen=True)
class Worker:
    name: str
    state: WorkerState
    job: int | None
    offers: list[str]
    kinds: list[str]
    foreign: list[Proc]
    paused_until: str | None
    last_seen: str
