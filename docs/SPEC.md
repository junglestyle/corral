# Specification

## Problem

Home and small-lab pipelines tend to run on fixed timers: one step at :00, the next at :30, the next at :45. Work that
arrives between ticks waits at every step, and the machines sit idle in between. When a person also wants one of
those machines for themselves, the timers don't know.

Corral is a resource-aware work queue. It runs work when it arrives, in priority order, on whichever machine has what
the work needs, and gets out of the way of a machine that's been paused.

## Requirements

1. **Pause and drain.** A paused worker finishes its current job, takes nothing new, and stays paused until resumed
   or until a set time runs out.
2. **Completion contract.** A finished job can enqueue follow-on jobs and notify anything that subscribes, so
   pipelines chain without clocks.
3. **Priorities with aging.** Higher priority runs first. Waiting raises a job's effective priority, so low work
   can't starve.
4. **Workload awareness.** A worker notices when something other than corral is using its GPU, stops taking GPU
   work while that lasts, and raises an alert when it blocks queued work for too long.
5. **Chunking hints.** A job can be told how much work it can likely finish within a time window, so a worker frees
   up predictably. The hint is advisory.

Out of scope for now: more than one job per worker at a time, job
dependencies beyond `then`, and multi-user anything.

## Pieces

- **Server.** One process on an always-on host, such as a NAS. It owns the SQLite queue, the HTTP API, and outgoing events.
- **Worker.** One process per machine. It advertises capability labels (`gpu`, `cpu`, anything else) and leases
  one job at a time whose needs it has, runs it, and
  reports back.
- **Dashboard** (milestone 5). Pages served by the server. It reads and pauses through the same API as the CLI, so
  it never talks to workers directly.
- **CLI.** `corral` talks to the server for everything: `server`, `worker`, `enqueue`, `ls`, `pause`, `resume`,
  `cancel`.

Every call uses one bearer token from the environment, and the API is meant for a private network. Jobs carry
references (paths, ids), never payloads, so data stays where it lives.

## Jobs

A job has:

| Field | Meaning |
| --- | --- |
| `id` | Server-assigned |
| `kind` | Names what to run, e.g. `hearsay.transcribe`. Workers map kinds to argv. |
| `needs` | Capability labels, e.g. `["gpu"]`. A worker can run the job if it offers all of them. |
| `priority` | Integer 0-9, higher first. Default 5. |
| `args` | Small JSON object passed to the job |
| `key` | Optional coalescing key. Enqueueing a key that is already queued does nothing and returns the queued job. |
| `then` | Optional list of job specs to enqueue on success |
| `state` | `queued`, `leased`, `succeeded`, `failed`, `cancelled` |
| `attempts` | Leases so far. Default max 3. |

`key` is what makes streaming input cheap. Ten items arriving in a minute can each enqueue a step that processes
everything pending, and only one of those waits in the queue.

### Ordering and aging

`effective = priority + waited_seconds / aging_seconds`, with `aging_seconds` set per server (default 600). The
waiting job with the highest effective priority that a worker can run goes first. Ties go to the oldest. Aging is
uncapped, so anything waiting long enough eventually wins.

### Leases

A worker asks for a job with its name and capabilities and long-polls up to 30 s. The server leases the best match
for 60 s. While the job runs, the worker heartbeats every 15 s to extend it. If the lease expires, because the
worker died or lost the network, the job goes back to `queued`. After `max_attempts` it is `failed`.

Jobs must therefore be safe to run twice. Pipelines that rebuild from source and cache by content already
are.

## Running a job

The worker's config maps each kind to an argv and an optional timeout:

```toml
[kinds."hearsay.transcribe"]
argv = ["/opt/hearsay/venv/bin/python", "-m", "hearsay.transcribe"]
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

- `corral pause gpu-host` stops leasing to that worker. The running job finishes. `corral ls` shows `draining` until it
  does, then `paused`.
- `corral pause gpu-host --now` also kills the running job and requeues it without counting an attempt.
- `corral pause gpu-host --for 2h` does the same, then resumes on its own when the time is up. Any duration;
  `--now` combines with it.
- `corral resume gpu-host` starts leasing again, early or not.

Because jobs chunk to a window, a plain pause frees the machine within about that window.

## Foreign GPU use

Before each lease and with each heartbeat, a worker that offers `gpu` lists the GPU's compute processes
(`nvidia-smi --query-compute-apps`). Any process that isn't the worker's own job or on the worker's `ignore` list
is foreign. Graphics-only use, like a desktop compositor, doesn't show up as compute, so it doesn't count.

While anything foreign is there, the worker is `blocked`. It drops `gpu` from what it offers, so it can still take
work that doesn't need the GPU. When the GPU is clear again it unblocks on its own. Blocked isn't paused: nobody
has to resume it.

A job already running when foreign use appears is left alone. Two workloads may then share the GPU, and if one
runs out of memory, that's what the alert is for.

The server raises a `worker.blocked` event when a worker has been blocked for `block_alert_after` (default 15 min)
while GPU jobs are waiting, naming the foreign processes. It raises `worker.unblocked` when it clears. These go to
webhooks like any other event, so an ntfy topic gets a push. A block with nothing waiting stays quiet.

## Completion

When a job ends, in one transaction, the server records the outcome, enqueues each spec in `then` (success only),
and queues one event per matching webhook:

```json
{"event": "job.finished", "job": {"id": 42, "kind": "hearsay.transcribe", "state": "succeeded",
 "args": {}, "attempts": 1, "worker": "gpu-host", "started_at": "...", "finished_at": "..."},
 "result": {"items": 7}}
```

Webhooks are configured on the server with an optional filter on event and kind. Delivery is at-least-once with retries, so
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
| `GET` | `/workers` | Workers, their state (`idle`, `running`, `draining`, `paused`, `blocked`), current job, foreign processes |
| `POST` | `/workers/{name}/pause` | `{"now": bool, "for_seconds": int?}` |
| `POST` | `/workers/{name}/resume` | |

A cancel or `pause --now` reaches a running job through the heartbeat response, which tells the worker to kill it.

## Example: a speech pipeline

[Hearsay](https://github.com/junglestyle/hearsay) transcribes on a GPU host, reprocesses on a NAS, and hands off to
[Idea Machine](https://github.com/junglestyle/ideamachine), all on hourly timers today.

| Today | With corral |
| --- | --- |
| Transcribe on the GPU host at :00 | The capture receiver enqueues `hearsay.transcribe` (`needs: [gpu]`, `key: hearsay.transcribe`) when a conversation closes |
| Reprocess on the NAS at :30 | Transcribe's `then` enqueues `hearsay.reprocess` (`needs: [nas]`, `key: hearsay.reprocess`) |
| Idea Machine at :45 | Reprocess's `then` enqueues `ideamachine.run` (`key: ideamachine.run`) |

The timers can stay at a lower frequency as a safety net, each enqueueing its job with its key so nothing doubles
up. Transcribe reads `CORRAL_SUGGESTED_ITEMS` to cap how many conversations it takes per run.

## Milestones

1. Spec and skeleton (this document).
2. Server, worker, and CLI: queue, aging, leases, pause, `then`, `key`.
3. Webhooks and chunking hints.
4. First real consumer: the speech pipeline above.
5. Dashboard, served by the server over the same API: queue, history, workers, pause and resume.
