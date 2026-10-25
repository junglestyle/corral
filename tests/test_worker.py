import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from corral import worker
from corral.client import Client
from corral.queue import LEASE_SECONDS, Queue
from corral.server import create_app
from corral.worker import Config, Kind, run_once

WRITE_RESULT = (
    "import json, os; "
    "json.dump({'items': 3, 'args': os.environ['CORRAL_ARGS']}, open(os.environ['CORRAL_RESULT'], 'w'))"
)


class Clock:
    def __init__(self) -> None:
        self.t = time.time()

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def app(tmp_path: Path, clock: Clock) -> FastAPI:
    return create_app(Queue(str(tmp_path / "q.sqlite"), clock=clock), "secret")


def connect(app: FastAPI) -> Client:
    return Client(TestClient(app, headers={"Authorization": "Bearer secret"}))


@pytest.fixture
def client(app: FastAPI) -> Client:
    return connect(app)


def config(offers: list[str], **kinds: list[str]) -> Config:
    return Config(
        name="w",
        offers=offers,
        kinds={k: Kind(argv=argv, timeout=None) for k, argv in kinds.items()},
        ignore=["Xorg"],
    )


def test_enqueue_lease_run_finish_chain(client: Client) -> None:
    py = sys.executable
    cfg = config(["cpu"], first=[py, "-c", WRITE_RESULT], second=[py, "-c", "pass"])
    job, _ = client.enqueue({"kind": "first", "args": {"path": "/x"}, "then": [{"kind": "second"}]})

    assert run_once(cfg, client, wait=0)
    done = client.http.get(f"/jobs/{job['id']}").json()
    assert done["state"] == "succeeded"
    assert done["result"] == {"items": 3, "args": '{"path": "/x"}'}

    assert run_once(cfg, client, wait=0)
    assert [j["state"] for j in client.jobs(["succeeded"], 10)] == ["succeeded", "succeeded"]
    assert not run_once(cfg, client, wait=0)


def test_unauthorized_is_rejected(app: FastAPI) -> None:
    assert TestClient(app).get("/jobs").status_code == 401


def test_foreign_gpu_process_blocks_gpu_work_only(
    client: Client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    smi = tmp_path / "bin" / "nvidia-smi"
    smi.parent.mkdir()
    smi.write_text("#!/bin/sh\necho '4242, /usr/bin/python3'\necho '17, /usr/lib/Xorg'\n")
    smi.chmod(0o755)
    monkeypatch.setenv("PATH", f"{smi.parent}:/usr/bin:/bin")

    py = sys.executable
    cfg = config(["gpu", "cpu"], train=[py, "-c", "pass"], tidy=[py, "-c", "pass"])
    gpu_job, _ = client.enqueue({"kind": "train", "needs": ["gpu"], "priority": 9})
    cpu_job, _ = client.enqueue({"kind": "tidy", "needs": ["cpu"], "priority": 0})

    assert run_once(cfg, client, wait=0)
    assert client.http.get(f"/jobs/{cpu_job['id']}").json()["state"] == "succeeded"
    assert client.http.get(f"/jobs/{gpu_job['id']}").json()["state"] == "queued"
    (worker,) = client.workers()
    assert worker["state"] == "blocked"
    assert worker["foreign"] == [{"pid": 4242, "name": "/usr/bin/python3"}]

    smi.write_text("#!/bin/sh\necho '17, /usr/lib/Xorg'\n")
    assert run_once(cfg, client, wait=0)
    assert client.http.get(f"/jobs/{gpu_job['id']}").json()["state"] == "succeeded"
    assert client.workers()[0]["state"] == "idle"


def run_in_background(app: FastAPI, cfg: Config) -> threading.Thread:
    thread = threading.Thread(target=run_once, args=(cfg, connect(app)), kwargs={"wait": 0})
    thread.start()
    return thread


def wait_until(check: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 10
    while not check():
        assert time.monotonic() < deadline
        time.sleep(0.05)


@pytest.fixture
def fast_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "HEARTBEAT_SECONDS", 0.1)


@pytest.mark.usefixtures("fast_heartbeat")
def test_kill_stops_the_process_and_requeues(app: FastAPI, client: Client) -> None:
    job, _ = client.enqueue({"kind": "slow"})
    thread = run_in_background(app, config([], slow=["sleep", "30"]))
    wait_until(lambda: client.jobs(["leased"], 1) != [])
    client.kill(job["id"])
    thread.join(timeout=10)
    assert not thread.is_alive()
    after = client.http.get(f"/jobs/{job['id']}").json()
    assert after["state"] == "queued" and after["attempts"] == 0


@pytest.mark.usefixtures("fast_heartbeat")
def test_lost_lease_stops_the_process_and_drops_the_outcome(
    app: FastAPI, client: Client, clock: Clock
) -> None:
    job, _ = client.enqueue({"kind": "slow", "then": [{"kind": "next"}]})
    thread = run_in_background(app, config([], slow=["sleep", "30"]))
    wait_until(lambda: client.jobs(["leased"], 1) != [])
    clock.t += LEASE_SECONDS + 1
    thread.join(timeout=10)
    assert not thread.is_alive()
    after = client.http.get(f"/jobs/{job['id']}").json()
    assert after["state"] == "queued" and after["attempts"] == 1
    assert [j["kind"] for j in client.jobs(["queued"], 10)] == ["slow"]
