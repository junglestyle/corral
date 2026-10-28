"""The one way to talk to a corral server, shared by the CLI and the worker."""

from __future__ import annotations

import os
from dataclasses import asdict
from typing import Any

import httpx
from pydantic import TypeAdapter

from corral.records import Job, JobState, Lease, Proc, Worker

# The server's replies are parsed here, at the edge, so nothing past the client handles raw JSON.
JOB = TypeAdapter(Job)
JOBS = TypeAdapter(list[Job])
LEASE = TypeAdapter(Lease)
WORKERS = TypeAdapter(list[Worker])


class LeaseLost(Exception):
    """The server no longer considers this lease ours."""


class Client:
    def __init__(self, http: httpx.Client) -> None:
        self.http = http

    @classmethod
    def from_env(cls) -> Client:
        url = os.environ.get("CORRAL_URL")
        token = os.environ.get("CORRAL_TOKEN")
        if not url or not token:
            raise SystemExit("set CORRAL_URL and CORRAL_TOKEN")
        # Leases long-poll for up to 30 s, so reads need more than that.
        return cls(httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=45))

    def _call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self.http.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise SystemExit(f"{method} {path}: {response.status_code} {response.text}")
        return response

    def enqueue(self, spec: dict[str, Any]) -> tuple[Job, bool]:
        response = self._call("POST", "/jobs", json=spec)
        return JOB.validate_json(response.content), response.status_code == 201

    def jobs(self, states: list[JobState], limit: int) -> list[Job]:
        return JOBS.validate_json(
            self._call("GET", "/jobs", params={"state": states, "limit": limit}).content
        )

    def kill(self, job_id: int) -> Job:
        return JOB.validate_json(self._call("POST", f"/jobs/{job_id}/kill").content)

    def cancel(self, job_id: int) -> Job:
        return JOB.validate_json(self._call("POST", f"/jobs/{job_id}/cancel").content)

    def workers(self) -> list[Worker]:
        return WORKERS.validate_json(self._call("GET", "/workers").content)

    def pause(self, name: str, now: bool, for_seconds: float | None) -> None:
        self._call("POST", f"/workers/{name}/pause", json={"now": now, "for_seconds": for_seconds})

    def resume(self, name: str) -> None:
        self._call("POST", f"/workers/{name}/resume")

    def lease(
        self, worker: str, offers: list[str], kinds: list[str], foreign: list[Proc], wait: float
    ) -> Lease | None:
        body = {"worker": worker, "offers": offers, "kinds": kinds, "foreign": _procs(foreign), "wait": wait}
        response = self._call("POST", "/lease", json=body)
        if response.status_code == 204:
            return None
        return LEASE.validate_json(response.content)

    def heartbeat(self, lease_id: str, foreign: list[Proc]) -> bool:
        """Returns True when the server wants the job killed."""
        response = self.http.post(f"/leases/{lease_id}/heartbeat", json={"foreign": _procs(foreign)})
        if response.status_code == 409:
            raise LeaseLost(lease_id)
        response.raise_for_status()
        return bool(response.json()["kill"])

    def finish(self, lease_id: str, exit_code: int, result: dict[str, Any] | None) -> None:
        response = self.http.post(
            f"/leases/{lease_id}/finish", json={"exit_code": exit_code, "result": result}
        )
        if response.status_code == 409:
            raise LeaseLost(lease_id)
        response.raise_for_status()


def _procs(procs: list[Proc]) -> list[dict[str, Any]]:
    return [asdict(p) for p in procs]
