import re
import sqlite3
from enum import StrEnum
from pathlib import Path

import pytest
from conftest import Clock, add, lease, spec
from pydantic import ValidationError

import corral
from corral.queue import ALL, LEASE_SECONDS, Queue, QueueError, Stop
from corral.records import JobState


def test_priority_then_aging_then_age(q: Queue, clock: Clock) -> None:
    low = add(q, priority=0)
    clock.t += 9 * 3600 + 1
    high = add(q, priority=9)
    mid_a = add(q, priority=5)
    mid_b = add(q, priority=5)
    # Nine hours of waiting is worth nine points, so the old priority-0 job just beats a fresh priority-9 one.
    assert [lease(q, f"w{i}").id for i in range(4)] == [low, high, mid_a, mid_b]


def test_needs_and_kinds_decide_eligibility(q: Queue) -> None:
    gpu = add(q, needs=["gpu"])
    other = add(q, "other")
    assert lease(q, "cpu", offers=("cpu",), kinds=("k", "other")).id == other
    assert lease(q, "gpu-without-kind", offers=("gpu",), kinds=("other",)) is None
    assert lease(q, "gpu", offers=("gpu", "cpu")).id == gpu


def test_expired_lease_requeues_then_fails(q: Queue, clock: Clock) -> None:
    job = add(q, max_attempts=2)
    for attempt in (1, 2):
        got = lease(q, f"w{attempt}")
        assert got.attempts == attempt
        clock.t += LEASE_SECONDS + 1
    assert q.get(job).state == "failed"


def test_stale_lease_is_rejected_and_never_chains(q: Queue, clock: Clock) -> None:
    job = add(q, then=[{"kind": "next"}])
    old = lease(q, "a")
    clock.t += LEASE_SECONDS + 1
    new = lease(q, "b")
    assert new.id == job and new.lease_id != old.lease_id
    with pytest.raises(QueueError):
        q.heartbeat(old.lease_id, [])
    with pytest.raises(QueueError):
        q.finish(old.lease_id, 0, {"items": 1})
    assert q.jobs(["queued"]) == []
    q.finish(new.lease_id, 0, None)
    assert [j.kind for j in q.jobs(["queued"])] == ["next"]


def test_then_runs_on_success_only_with_its_own_defaults(q: Queue) -> None:
    add(q, priority=9, then=[{"kind": "next"}], max_attempts=1)
    add(q, priority=0, then=[{"kind": "never"}], max_attempts=1)
    q.finish(lease(q).lease_id, 0, None)
    q.finish(lease(q).lease_id, 1, None)
    (follow,) = q.jobs(["queued"])
    assert follow.kind == "next" and follow.priority == 5


def test_failure_retries_until_max_attempts(q: Queue) -> None:
    job = add(q, max_attempts=2)
    q.finish(lease(q).lease_id, 1, None)
    assert q.get(job).state == "queued"
    q.finish(lease(q).lease_id, 1, None)
    assert q.get(job).state == "failed"


def test_key_coalesces_only_while_queued(q: Queue) -> None:
    first, created = q.enqueue(spec(key="batch"))
    again, created_again = q.enqueue(spec(key="batch"))
    assert created and not created_again and again.id == first.id
    lease(q)
    # Once it's running it may already have missed new input, so the next one queues.
    _, created_while_running = q.enqueue(spec(key="batch"))
    assert created_while_running


def test_held_job_waits_while_anything_would_enqueue_its_key(q: Queue) -> None:
    # The key sits two steps down the chain, so the whole chain counts.
    add(q, "a", then=[{"kind": "b", "then": [{"kind": "k", "key": "fold"}]}])
    held = add(q, key="fold", wait_seconds=1800)
    assert q.get(held).held_until is not None
    for kind in ("a", "b"):
        assert lease(q) is None
        q.finish(lease(q, kinds=(kind,)).lease_id, 0, None)
    # b's success coalesced into the held job, and nothing left would enqueue its key.
    assert [j.id for j in q.jobs(["queued"])] == [held]
    assert lease(q).id == held


def test_held_job_fires_at_its_deadline_and_the_next_trigger_waits_anew(q: Queue, clock: Clock) -> None:
    add(q, "a", then=[{"kind": "k", "key": "fold", "wait_seconds": 1800}])  # never leased: always upstream
    held = add(q, key="fold", wait_seconds=1800)
    clock.t += 1799
    add(q, key="fold", wait_seconds=1800)  # coalescing doesn't push the deadline back
    assert lease(q) is None
    clock.t += 1
    assert lease(q).id == held
    later, created = q.enqueue(spec(key="fold", wait_seconds=1800))
    assert created and later.held_until is not None and lease(q, "w2") is None


def test_release_ends_a_hold_and_only_applies_while_queued(q: Queue) -> None:
    add(q, "a", then=[{"kind": "k", "key": "fold"}])  # never leased: always upstream
    held = add(q, key="fold", wait_seconds=1800, quiet_seconds=600)
    assert lease(q) is None
    q.release(held)
    assert lease(q).id == held
    with pytest.raises(QueueError):
        q.release(held)


def test_quiet_holds_until_enqueues_stop_but_not_past_the_wait(q: Queue, clock: Clock) -> None:
    settles = add(q, key="settles", wait_seconds=600, quiet_seconds=120)
    busy = add(q, "busy", key="busy", wait_seconds=600, quiet_seconds=120)
    for _ in range(5):
        clock.t += 100
        add(q, "busy", key="busy", wait_seconds=600, quiet_seconds=120)
    # settles was last enqueued 500 s ago; busy 0 s ago, but its 600 s are up at the next tick.
    assert lease(q).id == settles
    assert lease(q, "w2", kinds=("busy",)) is None
    clock.t += 100
    assert lease(q, "w2", kinds=("busy",)).id == busy


@pytest.mark.parametrize("fields", [{"wait_seconds": 60}, {"key": "k", "quiet_seconds": 60}])
def test_holding_needs_a_key_and_a_wait(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        spec(**fields)


def test_pause_drains_then_holds(q: Queue) -> None:
    add(q)
    add(q)
    running = lease(q)
    q.pause("w")
    assert q.workers()[0].state == "draining"
    assert q.heartbeat(running.lease_id, []) is False
    q.finish(running.lease_id, 0, None)
    assert lease(q) is None
    assert q.workers()[0].state == "paused"
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
    assert q.heartbeat(got.lease_id, []) is True
    q.finish(got.lease_id, -15, None)
    after = q.get(job)
    assert after.state == "queued" and after.attempts == 0
    assert after.enqueued_at == got.enqueued_at


def test_kill_never_confirmed_requeues_on_expiry(q: Queue, clock: Clock) -> None:
    job = add(q, max_attempts=1)
    lease(q)
    q.kill(job)
    clock.t += LEASE_SECONDS + 1
    assert q.get(job).state == "queued"


def test_pause_now_kills_running_job(q: Queue) -> None:
    add(q)
    got = lease(q)
    q.pause("w", now=True)
    assert q.heartbeat(got.lease_id, []) is True


def test_cancel(q: Queue) -> None:
    queued = add(q)
    running = add(q)
    got = lease(q, "a")
    assert got.id == queued
    q.cancel(running)
    assert q.get(running).state == "cancelled"
    q.cancel(queued)
    assert q.heartbeat(got.lease_id, []) is True
    q.finish(got.lease_id, -15, None)
    assert q.get(queued).state == "cancelled"
    with pytest.raises(QueueError):
        q.cancel(queued)


def test_restarted_worker_frees_its_old_job(q: Queue) -> None:
    job = add(q)
    add(q, "other")
    lease(q)
    # The worker crashed and came back asking for work before the lease ran out.
    nxt = lease(q, kinds=("other",))
    assert nxt.kind == "other"
    assert q.get(job).state == "queued"


def test_a_database_from_before_migrations_upgrades_in_place(tmp_path: Path, clock: Clock) -> None:
    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.executescript((Path(corral.__file__).parent / "schema" / "001_initial.sql").read_text())
    db.execute("INSERT INTO workers VALUES ('w', '[]', '[\"k\"]', '[]', 0)")
    db.execute(
        """INSERT INTO jobs (kind, needs, priority, args, then_specs, max_attempts, state, enqueued_at)
           VALUES ('k', '[]', 5, '{}', '[]', 3, 'queued', 0)"""
    )
    db.commit()
    db.close()
    q = Queue(str(path), clock=clock)
    add(q)
    assert [lease(q, f"w{i}").id for i in range(2)] == [1, 2]
    assert Queue(str(path), clock=clock).workers()[0].name == "w"


@pytest.mark.parametrize(("column", "enum"), [("state", JobState), ("stop", Stop)])
def test_each_enum_is_exactly_its_columns_check_list(q: Queue, column: str, enum: type[StrEnum]) -> None:
    db = sqlite3.connect(q.path)
    (sql,) = db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'jobs'").fetchone()
    db.close()
    check = re.search(rf"CHECK \({column} IN \(([^)]*)\)\)", sql)
    assert check is not None
    assert set(re.findall(r"'([^']*)'", check.group(1))) == {str(v) for v in enum}
