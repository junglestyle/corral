import sqlite3
from pathlib import Path

import pytest
from conftest import Clock, add, lease

import corral
from corral.queue import ALL, LEASE_SECONDS, Conflict, JobSpec, Queue


def test_priority_then_aging_then_age(q: Queue, clock: Clock) -> None:
    low = add(q, priority=0)
    clock.t += 9 * 3600 + 1
    high = add(q, priority=9)
    mid_a = add(q, priority=5)
    mid_b = add(q, priority=5)
    # Nine hours of waiting is worth nine points, so the old priority-0 job just beats a fresh priority-9 one.
    assert [lease(q, f"w{i}")["id"] for i in range(4)] == [low, high, mid_a, mid_b]


def test_needs_and_kinds_decide_eligibility(q: Queue) -> None:
    gpu = add(q, needs=["gpu"])
    other = add(q, "other")
    assert lease(q, "cpu", offers=("cpu",), kinds=("k", "other"))["id"] == other
    assert lease(q, "gpu-without-kind", offers=("gpu",), kinds=("other",)) is None
    assert lease(q, "gpu", offers=("gpu", "cpu"))["id"] == gpu


def test_expired_lease_requeues_then_fails(q: Queue, clock: Clock) -> None:
    job = add(q, max_attempts=2)
    for attempt in (1, 2):
        got = lease(q, f"w{attempt}")
        assert got["attempts"] == attempt
        clock.t += LEASE_SECONDS + 1
    assert q.get(job)["state"] == "failed"


def test_stale_lease_is_rejected_and_never_chains(q: Queue, clock: Clock) -> None:
    job = add(q, then=[{"kind": "next"}])
    old = lease(q, "a")
    clock.t += LEASE_SECONDS + 1
    new = lease(q, "b")
    assert new["id"] == job and new["lease_id"] != old["lease_id"]
    with pytest.raises(Conflict):
        q.heartbeat(old["lease_id"], [])
    with pytest.raises(Conflict):
        q.finish(old["lease_id"], 0, {"items": 1})
    assert q.jobs(["queued"]) == []
    q.finish(new["lease_id"], 0, None)
    assert [j["kind"] for j in q.jobs(["queued"])] == ["next"]


def test_then_runs_on_success_only_with_its_own_defaults(q: Queue) -> None:
    add(q, priority=9, then=[{"kind": "next"}], max_attempts=1)
    add(q, priority=0, then=[{"kind": "never"}], max_attempts=1)
    q.finish(lease(q)["lease_id"], 0, None)
    q.finish(lease(q)["lease_id"], 1, None)
    (follow,) = q.jobs(["queued"])
    assert follow["kind"] == "next" and follow["priority"] == 5


def test_failure_retries_until_max_attempts(q: Queue) -> None:
    job = add(q, max_attempts=2)
    q.finish(lease(q)["lease_id"], 1, None)
    assert q.get(job)["state"] == "queued"
    q.finish(lease(q)["lease_id"], 1, None)
    assert q.get(job)["state"] == "failed"


def test_key_coalesces_only_while_queued(q: Queue) -> None:
    first, created = q.enqueue(JobSpec(kind="k", key="batch"))
    again, created_again = q.enqueue(JobSpec(kind="k", key="batch"))
    assert created and not created_again and again["id"] == first["id"]
    lease(q)
    # Once it's running it may already have missed new input, so the next one queues.
    _, created_while_running = q.enqueue(JobSpec(kind="k", key="batch"))
    assert created_while_running


def test_pause_drains_then_holds(q: Queue) -> None:
    add(q)
    add(q)
    running = lease(q)
    q.pause("w")
    assert q.workers()[0]["state"] == "draining"
    assert q.heartbeat(running["lease_id"], []) is False
    q.finish(running["lease_id"], 0, None)
    assert lease(q) is None
    assert q.workers()[0]["state"] == "paused"
    q.resume("w")
    assert lease(q) is not None


def test_timed_pause_resumes_itself(q: Queue, clock: Clock) -> None:
    add(q)
    q.pause("w", for_seconds=7200)
    assert lease(q) is None
    clock.t += 7201
    assert lease(q) is not None


def test_pause_all_and_resume_all(q: Queue) -> None:
    add(q)
    q.pause(ALL)
    q.pause("w")
    q.resume("w")
    assert lease(q) is None
    q.resume(ALL)
    assert lease(q) is not None


def test_pause_survives_restart(q: Queue, tmp_path: Path, clock: Clock) -> None:
    add(q)
    q.pause("w")
    reopened = Queue(q.path, clock=clock)
    assert lease(reopened) is None


def test_kill_requeues_after_worker_confirms_without_counting(q: Queue, clock: Clock) -> None:
    job = add(q)
    clock.t += 10
    got = lease(q)
    q.kill(job)
    # Still leased until the worker says it stopped, so it can't start anywhere else meanwhile.
    assert lease(q, "other") is None
    assert q.heartbeat(got["lease_id"], []) is True
    q.finish(got["lease_id"], -15, None)
    after = q.get(job)
    assert after["state"] == "queued" and after["attempts"] == 0
    assert after["enqueued_at"] == got["enqueued_at"]


def test_kill_never_confirmed_requeues_on_expiry(q: Queue, clock: Clock) -> None:
    job = add(q, max_attempts=1)
    lease(q)
    q.kill(job)
    clock.t += LEASE_SECONDS + 1
    assert q.get(job)["state"] == "queued"


def test_pause_now_kills_running_job(q: Queue) -> None:
    add(q)
    got = lease(q)
    q.pause("w", now=True)
    assert q.heartbeat(got["lease_id"], []) is True


def test_cancel(q: Queue) -> None:
    queued = add(q)
    running = add(q)
    got = lease(q, "a")
    assert got["id"] == queued
    q.cancel(running)
    assert q.get(running)["state"] == "cancelled"
    q.cancel(queued)
    assert q.heartbeat(got["lease_id"], []) is True
    q.finish(got["lease_id"], -15, None)
    assert q.get(queued)["state"] == "cancelled"
    with pytest.raises(Conflict):
        q.cancel(queued)


def test_restarted_worker_frees_its_old_job(q: Queue) -> None:
    job = add(q)
    add(q, "other")
    lease(q)
    # The worker crashed and came back asking for work before the lease ran out.
    nxt = lease(q, kinds=("other",))
    assert nxt["kind"] == "other"
    assert q.get(job)["state"] == "queued"


def test_a_database_from_before_migrations_upgrades_in_place(tmp_path: Path, clock: Clock) -> None:
    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.executescript((Path(corral.__file__).parent / "schema" / "001_initial.sql").read_text())
    db.execute("INSERT INTO workers VALUES ('w', '[]', '[\"k\"]', '[]', 0)")
    db.commit()
    db.close()
    q = Queue(str(path), clock=clock)
    add(q)
    assert lease(q)["kind"] == "k"
    assert Queue(str(path), clock=clock).workers()[0]["name"] == "w"
