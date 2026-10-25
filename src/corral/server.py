"""HTTP API over the queue."""

from __future__ import annotations

import asyncio
import secrets
import time
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from corral.queue import Conflict, JobSpec, NotFound, Queue

MAX_WAIT_SECONDS = 30
POLL_SECONDS = 1.0


class Proc(BaseModel):
    pid: int
    name: str


class LeaseRequest(BaseModel):
    worker: str = Field(min_length=1)
    offers: list[str]
    kinds: list[str]
    foreign: list[Proc] = []
    wait: float = Field(MAX_WAIT_SECONDS, ge=0, le=MAX_WAIT_SECONDS)


class Heartbeat(BaseModel):
    foreign: list[Proc] = []


class Finish(BaseModel):
    exit_code: int
    result: dict[str, Any] | None = None


class Pause(BaseModel):
    now: bool = False
    for_seconds: float | None = Field(None, gt=0)


def create_app(queue: Queue, token: str) -> FastAPI:
    def authorize(authorization: Annotated[str, Header()] = "") -> None:
        if not secrets.compare_digest(authorization, f"Bearer {token}"):
            raise HTTPException(401, "bad or missing bearer token")

    app = FastAPI(title="corral", dependencies=[Depends(authorize)])

    @app.exception_handler(NotFound)
    def not_found(_: Request, e: NotFound) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=404)

    @app.exception_handler(Conflict)
    def conflict(_: Request, e: Conflict) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=409)

    @app.post("/jobs")
    def enqueue(spec: JobSpec, response: Response) -> dict[str, Any]:
        job, created = queue.enqueue(spec)
        response.status_code = 201 if created else 200
        return job

    @app.get("/jobs")
    def jobs(state: Annotated[list[str], Query()] = [], limit: int = 100) -> list[dict[str, Any]]:  # noqa: B006
        return queue.jobs(state, limit)

    @app.get("/jobs/{job_id}")
    def job(job_id: int) -> dict[str, Any]:
        return queue.get(job_id)

    @app.post("/jobs/{job_id}/kill")
    def kill(job_id: int) -> dict[str, Any]:
        return queue.kill(job_id)

    @app.post("/jobs/{job_id}/cancel")
    def cancel(job_id: int) -> dict[str, Any]:
        return queue.cancel(job_id)

    @app.post("/lease", response_model=None)
    async def lease(req: LeaseRequest) -> dict[str, Any] | Response:
        foreign = [p.model_dump() for p in req.foreign]
        deadline = time.monotonic() + req.wait
        while True:
            job = queue.lease(req.worker, req.offers, req.kinds, foreign)
            if job is not None:
                return job
            if time.monotonic() >= deadline:
                return Response(status_code=204)
            await asyncio.sleep(POLL_SECONDS)

    @app.post("/leases/{lease_id}/heartbeat")
    def heartbeat(lease_id: str, body: Heartbeat) -> dict[str, bool]:
        return {"kill": queue.heartbeat(lease_id, [p.model_dump() for p in body.foreign])}

    @app.post("/leases/{lease_id}/finish")
    def finish(lease_id: str, body: Finish) -> dict[str, Any]:
        return queue.finish(lease_id, body.exit_code, body.result)

    @app.get("/workers")
    def workers() -> list[dict[str, Any]]:
        return queue.workers()

    @app.post("/workers/{name}/pause")
    def pause(name: str, body: Pause) -> dict[str, str]:
        queue.pause(name, body.now, body.for_seconds)
        return {"paused": name}

    @app.post("/workers/{name}/resume")
    def resume(name: str) -> dict[str, str]:
        queue.resume(name)
        return {"resumed": name}

    return app
