from pathlib import Path
from typing import Any

import pytest

from corral.queue import JobSpec, Queue
from corral.records import Proc


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


def add(q: Queue, kind: str = "k", **kw: object) -> int:
    job, _ = q.enqueue(JobSpec.model_validate({"kind": kind, **kw}))
    return job.id


def lease(
    q: Queue,
    worker: str = "w",
    offers: tuple[str, ...] = (),
    kinds: tuple[str, ...] = ("k",),
    foreign: tuple[Proc, ...] = (),
) -> Any:
    return q.lease(worker, list(offers), list(kinds), list(foreign))
