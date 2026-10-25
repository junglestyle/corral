# Specification

## Problem

Hearsay, Idea Machine and friends run on fixed timers across two machines. The NAS reprocesses at :30. `eeyore`,
the dev box with the GPU, transcribes at :00. Idea Machine runs at :45. While the pendant is streaming, work arrives
all hour and waits for the next tick at every step, so a conversation takes one to two hours to reach Idea Machine.
Meanwhile the GPU sits idle most of the hour.

Corral runs work when it arrives, in priority order, on whichever machine can do it, and gets out of the way when I
want the GPU myself.

## Requirements

1. **Pause and drain.** A paused worker finishes its current job, takes nothing new, and stays paused until resumed.
2. **Completion contract.** A finished job can enqueue follow-on jobs and notify anything that subscribes, so
   pipelines chain without clocks.
3. **Priorities with aging.** Higher priority runs first. Waiting raises a job's effective priority, so low work
   can't starve.
4. **Chunking hints.** A job can be told how much work it can likely finish within a time window, so a worker frees
   up predictably. The hint is advisory.

Out of scope for now: auto-pause on foreign GPU use, more than one job per worker at a time, job
dependencies beyond `then`, and multi-user anything.

## Pieces

- **Server.** One process on the NAS. It owns the SQLite queue, the HTTP API on the tailnet, and outgoing events.
- **Worker.** One process per machine. It leases one job at a time that matches its capabilities, runs it, and
  reports back.
- **Dashboard** (milestone 5). Pages served by the server. It reads and pauses through the same API as the CLI, so
  it never talks to workers directly.
- **CLI.** `corral` talks to the server for everything: `server`, `worker`, `enqueue`, `ls`, `pause`, `resume`,
  `cancel`.

Every call uses one bearer token from the environment. The API listens only on the tailnet. Jobs carry references
(paths, ids), never payloads. Hearsay audio stays where it is.

## Jobs

A job has:

| Field | Meaning |
| --- | --- |
| `id` | Server-assigned |
| `kind` | Names what to run, e.g. `hearsay.transcribe`. Workers map kinds to argv. |
| `needs` | `gpu` or `cpu`. A worker offers one or both. |
| `priority` | Integer 0-9, higher first. Default 5. |
| `args` | Small JSON object passed to the job |
| `key` | Optional coalescing key. Enqueueing a key that is already queued does nothing and returns the queued job. |
| `then` | Optional list of job specs to enqueue on success |
| `state` | `queued`, `leased`, `succeeded`, `failed`, `cancelled` |
| `attempts` | Leases so far. Default max 3. |

`key` is what makes streaming cheap. Ten conversations closing in a minute enqueue ten transcribes but only one
`hearsay.reprocess` while it's still waiting.

### Ordering and aging

`effective = priority + waited_seconds / aging_seconds`, with `aging_seconds` set per server (default 600). The
waiting job with the highest effective priority that a worker can run goes first. Ties go to the oldest. Aging is
uncapped, so anything waiting long enough eventually wins.

### Leases

A worker asks for a job with its name and capabilities and long-polls up to 30 s. The server leases the best match
for 60 s. While the job runs, the worker heartbeats every 15 s to extend it. If the lease expires, because the
worker died or lost the network, the job goes back to `queued`. After `max_attempts` it is `failed`.

Jobs must therefore be safe to run twice. The pipelines here already are, since they rebuild from raw and cache by
content.

## Running a job

The worker's config maps each kind to an argv and an optional timeout:

```toml
[kinds."hearsay.transcribe"]
argv = ["/home/me/.local/share/hearsay/gpu-venv/bin/python", "-m", "hearsay.transcribe"]
timeout = "30m"
```

The job gets its context in the environment:

| Variable | Meaning |
| --- | --- |
| `CORRAL_JOB_ID` | The job's id |
| `CORRAL_ARGS` | `args` as JSON |
| `CORRAL_SUGGESTED_ITEMS` | Chunking hint (below); absent when there's no history yet |
| `CORRAL_RESULT` | Path where the job may write a JSON result |

Exit 0 means success, anything else failure. The result file is optional. Two of its fields mean something to
corral:

- `items`: how many items the job processed. This feeds the chunking hint.
- `more`: `true` re-enqueues the same job (same kind, args and priority, aging reset) because work remains.

Everything else in the result is passed through to events untouched.

## Pause and drain

Pause is per worker, or `all`, and is stored on the server so it survives restarts on both sides.

- `corral pause eeyore` stops leasing to eeyore. The running job finishes. `corral ls` shows `draining` until it
  does, then `paused`.
- `corral pause eeyore --now` also kills the running job and requeues it without counting an attempt.
- `corral resume eeyore` starts leasing again.

Because jobs chunk to a window, a plain pause frees the GPU within about that window.

## Completion

When a job ends, in one transaction, the server records the outcome, enqueues each spec in `then` (success only),
and queues one event per matching webhook:

```json
{"event": "job.finished", "job": {"id": 42, "kind": "hearsay.transcribe", "state": "succeeded",
 "args": {}, "attempts": 1, "worker": "eeyore", "started_at": "...", "finished_at": "..."},
 "result": {"items": 7}}
```

Webhooks are configured on the server with an optional kind filter. Delivery is at-least-once with retries, so
receivers dedupe on `job.id`. A plain ntfy topic works as a receiver.

`then` covers chains whose next step is known up front. Webhooks cover everything else: a service outside corral
that decides for itself what to enqueue.

## Chunking hints

For each kind the server keeps an exponentially weighted average of seconds per item, from the `items` and runtime
of successful jobs. Each kind has a window in the server config (default 10 min). At lease time:

`CORRAL_SUGGESTED_ITEMS = max(1, floor(window / seconds_per_item))`

A job that processes a backlog takes that many items, writes `{"items": n, "more": true}`, and exits. The worker is
then free to take something more urgent, or to pause, before the backlog continues. Jobs that can't split ignore
the hint.

## API

| Method | Path | Does |
| --- | --- | --- |
| `POST` | `/jobs` | Enqueue (honors `key`) |
| `GET` | `/jobs?state=` | List |
| `GET` | `/jobs/{id}` | One job |
| `POST` | `/jobs/{id}/cancel` | Cancel a queued job, or kill and cancel a leased one |
| `POST` | `/lease` | Worker asks for a job (long poll) |
| `POST` | `/leases/{id}/heartbeat` | Extend |
| `POST` | `/leases/{id}/finish` | Exit status and result |
| `GET` | `/workers` | Workers, their state, current job |
| `POST` | `/workers/{name}/pause` | `{"now": bool}` |
| `POST` | `/workers/{name}/resume` | |

A cancel or `pause --now` reaches a running job through the heartbeat response, which tells the worker to kill it.

## First user: the Hearsay pipeline

| Today | With corral |
| --- | --- |
| Transcribe on eeyore at :00 | Capture receiver enqueues `hearsay.transcribe` (`needs: gpu`, `key: hearsay.transcribe`) when a conversation closes |
| Reprocess on the NAS at :30 | Transcribe's `then` enqueues `hearsay.reprocess` (`needs: cpu`, `key: hearsay.reprocess`) |
| Idea Machine at :45 | Reprocess's `then` enqueues `ideamachine.run` (`key: ideamachine.run`) |

The existing timers stay at a lower frequency as a safety net, each enqueueing its job with its key so nothing
doubles up. Transcribe reads `CORRAL_SUGGESTED_ITEMS` to cap how many conversations it takes per run.

## Milestones

1. Spec and skeleton (this document).
2. Server, worker, and CLI: queue, aging, leases, pause, `then`, `key`.
3. Webhooks and chunking hints.
4. Hearsay and Idea Machine enqueue through corral. The timers become a fallback.
5. Dashboard, served by the server over the same API: queue, history, workers, pause and resume.
