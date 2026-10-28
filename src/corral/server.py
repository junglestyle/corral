"""HTTP API over the queue."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from corral.queue import Conflict, JobSpec, NotFound, Queue
from corral.records import Job, JobState, Lease, Proc, Worker
from corral.webhooks import deliver

MAX_WAIT_SECONDS = 30
POLL_SECONDS = 1.0
HOUSEKEEPING_SECONDS = 1.0

log = logging.getLogger("corral.server")


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

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(housekeeping(queue))
        yield
        task.cancel()

    app = FastAPI(title="corral", lifespan=lifespan)
    api = APIRouter(dependencies=[Depends(authorize)])

    @app.exception_handler(NotFound)
    def not_found(_: Request, e: NotFound) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=404)

    @app.exception_handler(Conflict)
    def conflict(_: Request, e: Conflict) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=409)

    # The page holds no data and asks for the token itself, then reads and pauses through the API below.
    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return files("corral").joinpath("dashboard.html").read_text()

    @api.post("/jobs")
    def enqueue(spec: JobSpec, response: Response) -> Job:
        job, created = queue.enqueue(spec)
        response.status_code = 201 if created else 200
        return job

    @api.get("/jobs")
    def jobs(state: Annotated[list[JobState], Query()] = [], limit: int = 100) -> list[Job]:  # noqa: B006
        return queue.jobs(state, limit)

    @api.get("/jobs/{job_id}")
    def job(job_id: int) -> Job:
        return queue.get(job_id)

    @api.post("/jobs/{job_id}/kill")
    def kill(job_id: int) -> Job:
        return queue.kill(job_id)

    @api.post("/jobs/{job_id}/cancel")
    def cancel(job_id: int) -> Job:
        return queue.cancel(job_id)

    @api.post("/lease", response_model=None)
    async def lease(req: LeaseRequest) -> Lease | Response:
        deadline = time.monotonic() + req.wait
        while True:
            job = queue.lease(req.worker, req.offers, req.kinds, req.foreign)
            if job is not None:
                return job
            if time.monotonic() >= deadline:
                return Response(status_code=204)
            await asyncio.sleep(POLL_SECONDS)

    @api.post("/leases/{lease_id}/heartbeat")
    def heartbeat(lease_id: str, body: Heartbeat) -> dict[str, bool]:
        return {"kill": queue.heartbeat(lease_id, body.foreign)}

    @api.post("/leases/{lease_id}/finish")
    def finish(lease_id: str, body: Finish) -> Job:
        return queue.finish(lease_id, body.exit_code, body.result)

    @api.get("/workers")
    def workers() -> list[Worker]:
        return queue.workers()

    @api.post("/workers/{name}/pause")
    def pause(name: str, body: Pause) -> dict[str, str]:
        queue.pause(name, body.now, body.for_seconds)
        return {"paused": name}

    @api.post("/workers/{name}/resume")
    def resume(name: str) -> dict[str, str]:
        queue.resume(name)
        return {"resumed": name}

    app.include_router(api)
    return app


async def housekeeping(queue: Queue) -> None:
    """Expires leases, raises block alerts and sends events, whether or not any request comes in."""
    async with httpx.AsyncClient() as http:
        while True:
            try:
                queue.housekeep()
                await deliver(queue, http)
            except Exception:
                log.exception("housekeeping failed, trying again")
            await asyncio.sleep(HOUSEKEEPING_SECONDS)
