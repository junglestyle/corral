"""Leases jobs from the server and runs them as configured. Knows nothing about SQLite."""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from corral.client import Client, LeaseLost
from corral.duration import parse_duration

HEARTBEAT_SECONDS = 15
RETRY_SECONDS = 5
KILL_GRACE_SECONDS = 10

log = logging.getLogger("corral.worker")


@dataclass(frozen=True)
class Kind:
    argv: list[str]
    timeout: float | None


@dataclass(frozen=True)
class Config:
    name: str
    offers: list[str]
    kinds: dict[str, Kind]
    ignore: list[str]


def load_config(path: Path) -> Config:
    """Reads the worker's config, plus any kinds in `kinds.d/*.toml` next to it.

    A bad config exits with the file and the problem, not a traceback: the worker runs under systemd, and the
    journal should say which file to fix.
    """
    raw = _read(path)
    if "name" not in raw:
        raise SystemExit(f"{path}: no name")
    kinds = _kinds(path, raw)
    # Drop-ins let each project install its own kinds without editing a file another project owns.
    for drop_in in sorted((path.parent / "kinds.d").glob("*.toml")):
        for name, kind in _kinds(drop_in, _read(drop_in)).items():
            if name in kinds:
                raise SystemExit(f"{drop_in}: kind {name} is already configured")
            kinds[name] = kind
    return Config(name=raw["name"], offers=raw.get("offers", []), kinds=kinds, ignore=raw.get("ignore", []))


def _read(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise SystemExit(f"{path}: {e}") from None


def _kinds(path: Path, raw: dict[str, Any]) -> dict[str, Kind]:
    kinds = {}
    for name, k in raw.get("kinds", {}).items():
        argv = k.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise SystemExit(f"{path}: kind {name} needs argv, a non-empty list of strings")
        try:
            timeout = parse_duration(k["timeout"]) if "timeout" in k else None
        except ValueError as e:
            raise SystemExit(f"{path}: kind {name}: {e}") from None
        kinds[name] = Kind(argv=argv, timeout=timeout)
    return kinds


def run(config: Config, client: Client) -> None:
    if "gpu" in config.offers:
        foreign_gpu_procs(config.ignore)  # Fail at startup, not on the first lease, if nvidia-smi is missing.
    log.info("worker %s offering %s, kinds %s", config.name, config.offers, sorted(config.kinds))
    if not config.kinds:
        # Not an error: projects add their kinds.d/ drop-ins to a worker that's already running.
        log.warning("no kinds configured, so this worker takes nothing until some are added and it restarts")
    while True:
        try:
            run_once(config, client)
        except httpx.TransportError as e:
            log.warning("server unreachable (%s), retrying in %ss", e, RETRY_SECONDS)
            time.sleep(RETRY_SECONDS)


def run_once(config: Config, client: Client, wait: float = 30) -> bool:
    """Leases and runs at most one job. Returns whether it ran one."""
    foreign = foreign_gpu_procs(config.ignore) if "gpu" in config.offers else []
    offers = [o for o in config.offers if not (o == "gpu" and foreign)]
    job = client.lease(config.name, offers, sorted(config.kinds), foreign, wait)
    if job is None:
        return False
    run_job(config, client, job)
    return True


def run_job(config: Config, client: Client, job: dict[str, Any]) -> None:
    kind = config.kinds[job["kind"]]
    lease_id = job["lease_id"]
    log.info("job %s (%s) started, attempt %s", job["id"], job["kind"], job["attempts"])
    with tempfile.TemporaryDirectory(prefix="corral-") as tmp:
        result_path = Path(tmp) / "result.json"
        env = os.environ | {
            "CORRAL_JOB_ID": str(job["id"]),
            "CORRAL_ARGS": json.dumps(job["args"]),
            "CORRAL_RESULT": str(result_path),
        }
        if job.get("suggested_items") is not None:  # An older server sends none.
            env["CORRAL_SUGGESTED_ITEMS"] = str(job["suggested_items"])
        # Its own session, so we can find its GPU processes and kill everything it started.
        proc = subprocess.Popen(kind.argv, env=env, start_new_session=True)
        exit_code = _supervise(config, client, job, kind, proc)
        if exit_code is None:
            return
        result = _read_result(result_path, job["id"])

    log.info("job %s exited %s", job["id"], exit_code)
    while True:
        try:
            client.finish(lease_id, exit_code, result)
            return
        except LeaseLost:
            log.warning("job %s: lease lost before finish, outcome dropped", job["id"])
            return
        except httpx.TransportError as e:
            log.warning("job %s: finish failed (%s), retrying in %ss", job["id"], e, RETRY_SECONDS)
            time.sleep(RETRY_SECONDS)


def _supervise(
    config: Config, client: Client, job: dict[str, Any], kind: Kind, proc: subprocess.Popen[bytes]
) -> int | None:
    """Waits for the job, heartbeating. Returns its exit code, or None when the lease was lost."""
    started = time.monotonic()
    lease_ok_until = started + job["lease_seconds"]
    while True:
        now = time.monotonic()
        deadline = started + kind.timeout if kind.timeout is not None else None
        wait = HEARTBEAT_SECONDS if deadline is None else max(0.0, min(HEARTBEAT_SECONDS, deadline - now))
        try:
            return proc.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            pass

        if deadline is not None and time.monotonic() >= deadline:
            log.warning("job %s: timed out after %ss, killing", job["id"], kind.timeout)
            return _kill(proc)
        foreign = foreign_gpu_procs(config.ignore, own_session=proc.pid) if "gpu" in config.offers else []
        try:
            stop = client.heartbeat(job["lease_id"], foreign)
            lease_ok_until = time.monotonic() + job["lease_seconds"]
        except LeaseLost:
            log.warning("job %s: lease lost, killing", job["id"])
            _kill(proc)
            return None
        except httpx.TransportError as e:
            if time.monotonic() < lease_ok_until:
                log.warning("job %s: heartbeat failed (%s), will retry", job["id"], e)
                continue
            log.warning(
                "job %s: no heartbeat for a full lease, killing; the server has requeued it", job["id"]
            )
            _kill(proc)
            return None
        if stop:
            log.info("job %s: server asked to stop it, killing", job["id"])
            return _kill(proc)


def _kill(proc: subprocess.Popen[bytes]) -> int:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            break
        try:
            return proc.wait(timeout=KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            continue
    return proc.wait()


def _read_result(path: Path, job_id: int) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        result = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        log.warning("job %s: result file isn't JSON (%s), ignoring it", job_id, e)
        return None
    if not isinstance(result, dict):
        log.warning("job %s: result isn't a JSON object, ignoring it", job_id)
        return None
    return result


def foreign_gpu_procs(ignore: list[str], own_session: int | None = None) -> list[dict[str, Any]]:
    """GPU compute processes that are neither ours nor on the ignore list."""
    out = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=pid,process_name", "--format=csv,noheader"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    procs = []
    for line in out.splitlines():
        if not line.strip():
            continue
        pid_text, name = (part.strip() for part in line.split(",", 1))
        pid = int(pid_text)
        if Path(name).name in ignore:
            continue
        if own_session is not None and _session(pid) == own_session:
            continue
        procs.append({"pid": pid, "name": name})
    return procs


def _session(pid: int) -> int | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return None
    # Fields after the parenthesized command: state, ppid, pgrp, session, ...
    return int(stat.rsplit(")", 1)[1].split()[3])
