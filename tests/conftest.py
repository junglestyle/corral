from pathlib import Path
from typing import Any

import pytest

from corral.queue import JobSpec, Queue
from corral.records import Proc
from corral.server import JobRequest


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def q(tmp_path: Path, clock: Clock) -> Queue:
    return Queue(str(tmp_path / "q.sqlite"), clock=clock)


def spec(kind: str = "k", **kw: object) -> JobSpec:
    """A spec built the way the server builds one, defaults included."""
    return JobRequest.model_validate({"kind": kind, **kw}).spec()


def add(q: Queue, kind: str = "k", **kw: object) -> int:
    job, _ = q.enqueue(spec(kind, **kw))
    return job.id


def lease(
    q: Queue,
    worker: str = "w",
    offers: tuple[str, ...] = (),
    kinds: tuple[str, ...] = ("k",),
    foreign: tuple[Proc, ...] = (),
) -> Any:
    return q.lease(worker, list(offers), list(kinds), list(foreign))
