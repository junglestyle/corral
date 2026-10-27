import sys
from pathlib import Path

import pytest
from conftest import Clock, add, lease
from fastapi.testclient import TestClient

from corral.client import Client
from corral.queue import Queue
from corral.server import create_app
from corral.settings import Settings
from corral.worker import Config, Kind, run_once


@pytest.fixture
def q(tmp_path: Path, clock: Clock) -> Queue:
    return Queue(str(tmp_path / "q.sqlite"), Settings(window=600, windows={"short": 60}), clock)


def run(q: Queue, clock: Clock, kind: str, seconds: float, result: object) -> dict[str, object]:
    add(q, kind)
    job = lease(q, kinds=(kind,))
    for _ in range(int(seconds // 30)):
        clock.t += 30
        q.heartbeat(job["lease_id"], [])
    clock.t += seconds % 30
    q.finish(job["lease_id"], 0, result)
    return dict(job)


def test_hint_follows_recent_runs_within_the_kinds_window(q: Queue, clock: Clock) -> None:
    assert run(q, clock, "k", 100, {"items": 10})["suggested_items"] is None
    # 10 s an item: a 600 s window holds 60, the short kind's 60 s window holds 6.
    assert run(q, clock, "k", 1, None)["suggested_items"] == 60
    run(q, clock, "short", 100, {"items": 10})
    assert run(q, clock, "short", 1, None)["suggested_items"] == 6
    # Then a run at 20 s an item pulls the average up, part of the way.
    run(q, clock, "k", 200, {"items": 10})
    assert 30 < run(q, clock, "k", 1, None)["suggested_items"] < 60


@pytest.mark.parametrize("items", [0, -1, True, "7", 2.5])
def test_runs_without_a_count_teach_nothing(q: Queue, clock: Clock, items: object) -> None:
    run(q, clock, "k", 100, {"items": items})
    assert run(q, clock, "k", 1, None)["suggested_items"] is None


def test_a_slow_kind_still_gets_one_item(q: Queue, clock: Clock) -> None:
    run(q, clock, "k", 3600, {"items": 1})
    assert run(q, clock, "k", 1, None)["suggested_items"] == 1


def test_more_requeues_the_same_job_with_aging_reset(q: Queue, clock: Clock) -> None:
    job = add(q, needs=["gpu"], priority=2, args={"a": 1}, key="backlog", then=[{"kind": "next"}])
    got = lease(q, offers=("gpu",))
    clock.t += 30
    q.finish(got["lease_id"], 0, {"items": 5, "more": True})
    again, follow = q.jobs(["queued"])[::-1]
    assert again["id"] != job and follow["kind"] == "next"
    assert (again["kind"], again["needs"], again["priority"], again["args"], again["key"]) == (
        "k",
        ["gpu"],
        2,
        {"a": 1},
        "backlog",
    )
    assert again["enqueued_at"] > got["enqueued_at"] and again["then"] == got["then"]


def test_more_coalesces_with_a_queued_job_of_the_same_key(q: Queue) -> None:
    add(q, key="backlog")
    got = lease(q)
    waiting = add(q, key="backlog")
    q.finish(got["lease_id"], 0, {"more": True})
    assert [j["id"] for j in q.jobs(["queued"])] == [waiting]


def test_more_on_failure_is_ignored(q: Queue) -> None:
    add(q, max_attempts=1)
    q.finish(lease(q)["lease_id"], 1, {"more": True})
    assert q.jobs(["queued"]) == []


def test_worker_passes_the_hint_to_the_job(q: Queue, clock: Clock) -> None:
    client = Client(TestClient(create_app(q, "secret"), headers={"Authorization": "Bearer secret"}))
    write = (
        "import json, os; "
        "json.dump({'items': 10, 'saw': os.environ.get('CORRAL_SUGGESTED_ITEMS')}, "
        "open(os.environ['CORRAL_RESULT'], 'w'))"
    )
    cfg = Config(name="w", offers=[], kinds={"k": Kind([sys.executable, "-c", write], None)}, ignore=[])
    for _ in range(2):
        client.enqueue({"kind": "k"})
        assert run_once(cfg, client, wait=0)
    first, second = (j["result"]["saw"] for j in client.jobs(["succeeded"], 10)[::-1])
    # The fake clock stands still, so the first run took no time and the hint is as large as it gets.
    assert first is None and int(second) > 1000
