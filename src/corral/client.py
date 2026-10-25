"""The one way to talk to a corral server, shared by the CLI and the worker."""

from __future__ import annotations

import os
from typing import Any

import httpx


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

    def enqueue(self, spec: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        response = self._call("POST", "/jobs", json=spec)
        return response.json(), response.status_code == 201

    def jobs(self, states: list[str], limit: int) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._call(
            "GET", "/jobs", params={"state": states, "limit": limit}
        ).json()
        return result

    def kill(self, job_id: int) -> dict[str, Any]:
        result: dict[str, Any] = self._call("POST", f"/jobs/{job_id}/kill").json()
        return result

    def cancel(self, job_id: int) -> dict[str, Any]:
        result: dict[str, Any] = self._call("POST", f"/jobs/{job_id}/cancel").json()
        return result

    def workers(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._call("GET", "/workers").json()
        return result

    def pause(self, name: str, now: bool, for_seconds: float | None) -> None:
        self._call("POST", f"/workers/{name}/pause", json={"now": now, "for_seconds": for_seconds})

    def resume(self, name: str) -> None:
        self._call("POST", f"/workers/{name}/resume")

    def lease(
        self, worker: str, offers: list[str], kinds: list[str], foreign: list[dict[str, Any]], wait: float
    ) -> dict[str, Any] | None:
        body = {"worker": worker, "offers": offers, "kinds": kinds, "foreign": foreign, "wait": wait}
        response = self._call("POST", "/lease", json=body)
        if response.status_code == 204:
            return None
        result: dict[str, Any] = response.json()
        return result

    def heartbeat(self, lease_id: str, foreign: list[dict[str, Any]]) -> bool:
        """Returns True when the server wants the job killed."""
        response = self.http.post(f"/leases/{lease_id}/heartbeat", json={"foreign": foreign})
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
