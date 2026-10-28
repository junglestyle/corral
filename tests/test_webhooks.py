import asyncio
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import Clock, add, lease
from fastapi.testclient import TestClient

from corral.queue import Queue
from corral.records import Proc
from corral.server import create_app
from corral.settings import Event, Settings, Webhook, load_settings
from corral.webhooks import deliver

EVERYTHING = Webhook("http://all")
BLOCKS = Webhook("http://alerts", events=frozenset({"worker.blocked", "worker.unblocked"}))
TRANSCRIBES = Webhook("http://next-step", kinds=frozenset({"transcribe"}))
FAILURES = Webhook("http://phone", states=frozenset({"failed"}))
GPU_HOG = Proc(4242, "/usr/bin/python3")


@pytest.fixture
def q(tmp_path: Path, clock: Clock) -> Queue:
    return Queue(str(tmp_path / "q.sqlite"), Settings(webhooks=(EVERYTHING, BLOCKS, TRANSCRIBES)), clock)


def sent(q: Queue) -> list[tuple[str, dict[str, Any]]]:
    """Delivers everything due, successfully, and returns it in order."""
    out = []
    for d in q.due_deliveries(limit=100):
        out.append((d.url, d.body))
        q.delivered(d)
    return out


def test_finished_jobs_reach_the_webhooks_that_want_them(q: Queue) -> None:
    job = add(q, "transcribe", args={"path": "/x"})
    q.finish(lease(q, kinds=("transcribe",)).lease_id, 0, {"items": 2, "note": "kept"})
    out = sent(q)
    assert [url for url, _ in out] == ["http://all", "http://next-step"]
    # One event, so both receivers see the same id to dedupe on.
    (_, body), (_, same) = out
    assert body == same
    assert body["event"] == "job.finished"
    assert body["job"]["id"] == job and body["job"]["state"] == "succeeded"
    assert body["job"]["args"] == {"path": "/x"} and body["job"]["worker"] == "w"
    assert body["result"] == {"items": 2, "note": "kept"}
    assert sent(q) == []


def test_a_state_filter_passes_only_jobs_that_ended_that_way(tmp_path: Path, clock: Clock) -> None:
    q = Queue(str(tmp_path / "q.sqlite"), Settings(webhooks=(FAILURES,)), clock)
    add(q, max_attempts=1)
    q.finish(lease(q).lease_id, 0, None)
    add(q, max_attempts=1)
    q.finish(lease(q).lease_id, 1, None)
    assert [b["job"]["state"] for _, b in sent(q)] == ["failed"]
    assert not FAILURES.wants(Event.WORKER_BLOCKED, None)


def test_only_a_job_ending_is_an_event(q: Queue) -> None:
    add(q, max_attempts=2)
    q.finish(lease(q).lease_id, 1, None)
    assert sent(q) == []  # Requeued, not ended.
    q.finish(lease(q).lease_id, 1, None)
    cancelled = add(q)
    q.cancel(cancelled)
    assert [b["job"]["state"] for _, b in sent(q)] == ["failed", "cancelled"]


def test_failed_delivery_backs_off_then_gives_up(q: Queue, clock: Clock) -> None:
    add(q, "other")
    q.finish(lease(q, kinds=("other",)).lease_id, 0, None)
    (d,) = q.due_deliveries()
    assert q.retry(d)
    assert q.due_deliveries() == []
    clock.t += 5
    (again,) = q.due_deliveries()
    assert again.body == d.body
    assert q.retry(again)
    clock.t += 9
    assert q.due_deliveries() == []
    clock.t += 86400
    assert not q.retry(q.due_deliveries()[0])
    assert q.due_deliveries() == []


def test_deliver_posts_and_retries_on_error(q: Queue, clock: Clock) -> None:
    add(q, "other")
    q.finish(lease(q, kinds=("other",)).lease_id, 0, None)
    posted: list[Any] = []
    status = 500

    def receive(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(status)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as http:
            await deliver(q, http)

    asyncio.run(run())
    assert len(posted) == 1
    clock.t += 5
    status = 204
    asyncio.run(run())
    assert len(posted) == 2 and posted[0] == posted[1]
    clock.t += 3600
    asyncio.run(run())
    assert len(posted) == 2


def block(q: Queue, clock: Clock, minutes: float) -> None:
    """Keeps a gpu worker polling with something foreign on its GPU, as a blocked worker does."""
    for _ in range(int(minutes * 2)):
        lease(q, "gpu-host", offers=("cpu",), kinds=("train",), foreign=(GPU_HOG,))
        clock.t += 30


def alerts(q: Queue) -> list[dict[str, Any]]:
    return [b for url, b in sent(q) if url == BLOCKS.url]


def test_a_long_block_with_gpu_work_waiting_alerts_once_then_clears(q: Queue, clock: Clock) -> None:
    add(q, "train", needs=["gpu"])
    block(q, clock, 14)
    assert alerts(q) == []
    block(q, clock, 2)
    (alert,) = alerts(q)
    assert alert["event"] == "worker.blocked" and alert["worker"] == "gpu-host"
    assert alert["foreign"] == [asdict(GPU_HOG)] and alert["waiting"] == 1
    block(q, clock, 30)
    assert alerts(q) == []
    lease(q, "gpu-host", offers=("gpu", "cpu"), kinds=("train",))
    assert [a["event"] for a in alerts(q)] == ["worker.unblocked"]


@pytest.mark.parametrize("why", ["nothing waits", "paused", "gone"])
def test_a_block_that_holds_nothing_up_stays_quiet(q: Queue, clock: Clock, why: str) -> None:
    if why != "nothing waits":
        add(q, "train", needs=["gpu"])
    if why == "paused":
        q.pause("gpu-host")
    block(q, clock, 1)
    if why == "gone":
        clock.t += 15 * 60
    else:
        block(q, clock, 20)
    q.housekeep()
    assert alerts(q) == []


def test_a_block_cleared_before_the_alert_says_nothing(q: Queue, clock: Clock) -> None:
    add(q, "train", needs=["gpu"])
    block(q, clock, 10)
    lease(q, "gpu-host", offers=("cpu",), kinds=("other",))
    block(q, clock, 10)
    assert alerts(q) == []


def test_settings_file(tmp_path: Path) -> None:
    path = tmp_path / "server.toml"
    path.write_text(
        'aging = "2h"\nblock_alert_after = "5m"\n'
        '[kinds.transcribe]\nwindow = "1m"\n'
        '[[webhooks]]\nurl = "http://alerts"\nevents = ["worker.blocked"]\n'
    )
    s = load_settings(path)
    assert (s.aging_seconds, s.block_alert_after, s.window_for("transcribe"), s.window_for("x")) == (
        7200,
        300,
        60,
        600,
    )
    assert s.webhooks == (Webhook("http://alerts", events=frozenset({"worker.blocked"})),)

    path.write_text('[[webhooks]]\nurl = "http://x"\nevents = ["job.done"]\n')
    with pytest.raises(SystemExit, match=r"job\.done"):
        load_settings(path)

    path.write_text('[[webhooks]]\nurl = "http://x"\nstates = ["failed", "done"]\n')
    with pytest.raises(SystemExit, match=r"'done'"):
        load_settings(path)


def test_the_server_runs_housekeeping_while_it_serves(q: Queue, clock: Clock) -> None:
    add(q, "other")
    q.finish(lease(q, kinds=("other",)).lease_id, 0, None)
    with TestClient(create_app(q, "secret")):
        deadline = time.monotonic() + 5
        # Nothing listens at the urls, so each delivery fails and is pushed back.
        while q.due_deliveries():
            assert time.monotonic() < deadline
            time.sleep(0.05)
