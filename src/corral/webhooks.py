"""Posts queued events to webhooks. The queue decides what goes where; this only carries it."""

from __future__ import annotations

import logging

import httpx

from corral.queue import Queue

TIMEOUT_SECONDS = 10

log = logging.getLogger("corral.webhooks")


async def deliver(queue: Queue, http: httpx.AsyncClient) -> None:
    """Sends every delivery that's due, once each. Any 2xx counts; anything else is retried later."""
    for d in queue.due_deliveries():
        try:
            response = await http.post(d.url, json=d.body, timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
        except httpx.HTTPError as e:
            if queue.retry(d):
                log.warning("event %s to %s failed (%s), will retry", d.event_id, d.url, e)
            else:
                log.warning("event %s to %s failed (%s), giving up", d.event_id, d.url, e)
            continue
        queue.delivered(d)
