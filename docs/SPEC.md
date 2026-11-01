# Specification

## Problem

Home and small-lab pipelines tend to run on fixed timers: one step at :00, the next at :30, the next at :45. Work that
arrives between ticks waits at every step, and the machines sit idle in between. When a person also wants one of
those machines for themselves, the timers don't know.

Corral is a resource-aware work queue. It runs work when it arrives, in priority order, on whichever machine has what
the work needs, and gets out of the way of a machine that's been paused.

## Requirements

Each requirement has an ID the [roadmap](ROADMAP.md) cites. A deferred requirement stays here and gets a later slice
there. The sections after this one describe the behavior in full.

### Queue

- **QUEUE-1: Priorities with aging.** Higher priority runs first. Waiting raises a job's effective priority, so low
  work can't starve.
- **QUEUE-2: Coalescing.** Enqueueing a key that's already queued adds nothing and returns the queued job.
- **QUEUE-3: Crash recovery.** A job whose worker dies or loses the network goes back to the queue, until it has
  used its attempts.

### Control

- **CONTROL-1: Pause and drain.** A paused worker finishes its current job, takes nothing new, and stays paused
  until resumed or until a set time runs out.
- **CONTROL-2: Kill and cancel.** A running job can be stopped and requeued, and any job removed for good.

### Chains

- **CHAIN-1: Follow-on jobs.** A job that succeeds enqueues the specs in its `then`, so pipelines chain without
  clocks.
- **CHAIN-2: Completion events.** A finished job notifies the webhooks that subscribe to it.

### GPU

- **GPU-1: Foreign GPU use.** A worker stops taking GPU work while something other than corral uses its GPU.
- **GPU-2: Block alert.** The server raises an alert when foreign use holds up queued GPU work for too long.

### Hints

- **HINT-1: Chunking hints.** A job can be told how much work it can likely finish within a time window, so a
  worker frees up predictably. The hint is advisory.

### Access

- **ACCESS-1: One token.** Every API call needs the bearer token. The dashboard page is the one exception.

### Dashboard

- **DASH-1: Dashboard.** A page on the server shows workers, the queue and history, and pauses and resumes.

## Pieces

- **Server.** One process on an always-on host, such as a NAS. It owns the SQLite queue, the HTTP API, and outgoing events.
- **Worker.** One process per machine. It advertises capability labels (`gpu`, `cpu`, anything else) and leases
  one job at a time whose needs it has and whose kind it has configured, runs it, and reports back.
- **Dashboard.** A page served by the server: workers, the queue, recent history, and pause and resume. It reads
  and pauses through the same API and token as the CLI, so it never talks to workers directly.
- **CLI.** `corral` talks to the server for everything: `server`, `worker`, `enqueue`, `ls`, `pause`, `resume`,
  `kill`, `cancel`.

Every call uses one bearer token from the environment, and the API is meant for a private network. Jobs carry
references (paths, ids), never payloads, so data stays where it lives.

## Jobs

A job has:

| Field | Meaning |
| --- | --- |
| `id` | Server-assigned |
| `kind` | Names what to run, e.g. `hearsay.transcribe`. Workers map kinds to argv. |
| `needs` | Capability labels, e.g. `["gpu"]`. A worker can run the job if it offers all of them and has the kind configured. |
| `priority` | Integer 0-9, higher first. Default 5. |
| `args` | Small JSON object passed to the job |
| `key` | Optional coalescing key. Enqueueing a key that is already queued does nothing and returns the queued job. |
| `then` | Optional list of job specs to enqueue on success. Each is a full spec: fields it leaves out take the defaults above, not the parent's values. |
| `state` | `queued`, `leased`, `succeeded`, `failed`, `cancelled` |
| `attempts` | Leases so far |
| `max_attempts` | Default 3. A job that exits non-zero or loses its lease is requeued until it has used them all, then `failed`. |

`key` is what makes streaming input cheap. Ten items arriving in a minute can each enqueue a step that processes
everything pending, and only one of those waits in the queue.

### Ordering and aging

`effective = priority + waited_seconds / aging_seconds`, with `aging_seconds` set in the server config (default 1 h). The
waiting job with the highest effective priority that a worker can run goes first. Ties go to the oldest.

Each `aging_seconds` of waiting is worth one priority point, and aging is uncapped. With the default, a priority-0
job that has waited nine hours ties a brand-new priority-9 job. That is the intent: priority decides the order of
work that arrives together, and nothing waits forever.

### Leases

A worker asks for a job with its name, capabilities and configured kinds, and long-polls up to 30 s. The server
leases the best match for 60 s under a fresh lease id, unique to that attempt. While the job runs, the worker
heartbeats every 15 s with the lease id to extend it, and reports the outcome with it.

If the lease expires, because the worker died or lost the network, the job goes back to `queued` (or `failed`
after `max_attempts`). A worker that asks for a new lease is by definition not running anything, so any lease
it still holds is settled the same way straight away; this is how a restarted worker frees its old job.

A heartbeat or finish with a lease id that is no longer current is rejected, and the worker kills the job and
drops its outcome. A stale finish therefore never records a result or enqueues `then`. A worker that can't
reach the server kills its job once the lease would have expired, so after a lease is lost, the old run
overlaps a new one by at most one heartbeat interval if the worker is reachable, or not at all if it is not.

Jobs must still be safe to run twice, because a job that finished its work but died before reporting runs
again. Being safe to overlap for a few seconds is a stronger property and worth having, but corral keeps it
rare rather than impossible.

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
  This is an ordinary enqueue, so it coalesces with an already-queued job that has the same `key`. The job's `then`
  still runs after each chunk, as after any success, so later steps see the backlog as it shrinks.

Everything else in the result is passed through to events untouched.

## Pause and drain

Pause is per worker, or `all`, and is stored on the server so it survives restarts on both sides.

- `corral pause gpu-host` stops leasing to that worker. The running job finishes. `corral ls` shows `draining` until it
  does, then `paused`.
- `corral pause gpu-host --now` also kills the running job, as `corral kill` does.
- `corral pause gpu-host --for 2h` does the same, then resumes on its own when the time is up. Any duration;
  `--now` combines with it.
- `corral resume gpu-host` starts leasing again, early or not. A pause on `all` holds every worker until
  `corral resume all`, which also clears the per-worker pauses.

`corral kill 42` stops one running job and puts it back in the queue, without counting an attempt and keeping its
place in line (aging continues from when it was first enqueued). The job stays `leased` until the worker reports
it stopped, or until its lease expires if the worker never does, so it never runs in two places because of a
kill. The worker stays free to lease again, so the same
job may come straight back unless the worker is paused or blocked. `cancel` is the one that removes a job for good.

A plain pause frees the machine when the running job ends. For jobs that follow the chunking hint that's within
about their kind's window; a job that ignores it runs until it finishes or hits its timeout.

## Foreign GPU use

Before each lease and with each heartbeat, a worker that offers `gpu` lists the GPU's compute processes
(`nvidia-smi --query-compute-apps`). Any process that isn't the worker's own job or on the worker's `ignore` list
is foreign. Graphics-only use, like a desktop compositor, doesn't show up as compute, so it doesn't count.

While anything foreign is there, the worker is `blocked`. It drops `gpu` from what it offers, so it can still take
work that doesn't need the GPU. When the GPU is clear again it unblocks on its own. Blocked isn't paused: nobody
has to resume it.

A job already running when foreign use appears is left alone; `corral kill` it by hand if it's in the way. Two workloads may then share the GPU, and if one
runs out of memory, that's what the alert is for.

The server raises a `worker.blocked` event when a worker has had foreign GPU use for `block_alert_after` (default
15 min) while queued jobs it has the kind for need `gpu`. It raises `worker.unblocked` when the GPU clears, but only
after a `worker.blocked`. These go to webhooks like any other event, so an ntfy topic gets a push:

```json
{"id": 1188, "event": "worker.blocked", "worker": "gpu-host", "foreign": [{"pid": 4242, "name": "/usr/bin/python3"}],
 "since": "...", "waiting": 3}
{"id": 1191, "event": "worker.unblocked", "worker": "gpu-host"}
```

A block that holds nothing up stays quiet: nothing waiting, the worker paused or running a job, or the worker not
seen for a lease's length. One block raises one alert, however long it lasts.

## Completion

When a job ends (`succeeded`, `failed` or `cancelled`, not when it's requeued for another attempt), in one transaction, the server records the outcome, enqueues each spec in `then` (success only),
and queues one event per matching webhook:

```json
{"id": 1187, "event": "job.finished", "job": {"id": 42, "kind": "hearsay.transcribe", "state": "succeeded",
 "args": {}, "attempts": 1, "worker": "gpu-host", "started_at": "...", "finished_at": "..."},
 "result": {"items": 7}}
```

Webhooks are configured on the server with optional filters on event, kind and state; a kind or state filter passes
only job events. `states = ["failed"]` sends a phone only the jobs that gave up. Each event is a JSON `POST`, and any 2xx answer takes it. Delivery is at-least-once: a failed delivery is
retried after 5 s, then twice as long each time up to 10 min, and dropped once the event is a day old. Receivers
dedupe on the event's `id`, which every event carries and every retry of it repeats. A plain ntfy topic works as a
receiver.

`then` covers chains whose next step is known up front. Webhooks cover everything else: a service outside corral
that decides for itself what to enqueue.

## Chunking hints

For each kind the server keeps an exponentially weighted average of seconds per item, from the `items` and runtime
of successful jobs, with the newest run weighted 0.3. Runtime is from lease to finish, so startup counts. Each kind has a window in the server config (default 10 min). At lease time:

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
| `POST` | `/jobs/{id}/kill` | Kill a leased job and requeue it |
| `POST` | `/jobs/{id}/cancel` | Cancel a queued job, or kill and cancel a leased one |
| `POST` | `/lease` | Worker asks for a job (long poll) |
| `POST` | `/leases/{id}/heartbeat` | Extend |
| `POST` | `/leases/{id}/finish` | Exit status and result |
| `GET` | `/workers` | Workers, their state (`idle`, `running`, `draining`, `paused`, `blocked`), current job, foreign processes |
| `POST` | `/workers/{name}/pause` | `{"now": bool, "for_seconds": int?}` |
| `POST` | `/workers/{name}/resume` | |
| `GET` | `/` | The dashboard page. The one path that needs no token: the page holds no data and asks for the token. |

A kill, cancel or `pause --now` reaches a running job through the heartbeat response, which tells the worker to kill it.
Heartbeat and finish answer `409` when the lease id is no longer current. Enqueue answers `201` for a new job and
`200` when `key` coalesced it into one already queued.

## Example: a speech pipeline

[Hearsay](https://github.com/junglestyle/hearsay) assembles captured audio into conversations on a NAS,
transcribes them on a GPU host, folds the transcripts back in on the NAS, and hands off to
[Idea Machine](https://github.com/junglestyle/ideamachine).

| Step | Kind | Needs |
| --- | --- | --- |
| Assemble new audio into conversation WAVs | `hearsay.reprocess` | `nas` |
| Transcribe the WAVs that changed | `hearsay.transcribe` | `gpu` |
| Fold the transcripts into the stream | `hearsay.reprocess` | `nas` |
| Read the stream | `ideamachine.run` | `nas` |

When a capture arrives, the receiver enqueues the first step with the rest nested in `then`. Each step has its own
`key`, so captures that arrive while the chain is waiting add nothing, and coalescing never drops a later step. The
same kind appears twice under two keys. An hourly timer enqueues the same chain as a safety net.

## Unknowns

What the spec assumes without having checked:

- That `nvidia-smi --query-compute-apps` lists every foreign compute process, including ones in containers, with
  PIDs the worker can tell apart from its own job's.
- That SQLite in WAL mode behaves on a NAS's data pool (ZFS, say), fsync included.
- That a kind's seconds per item is steady enough for an average weighted 0.3 to the newest run to predict a window.
- That up to 30 s before a GPU job notices foreign use is short enough for the person who just took the GPU.
- That on a private network, one shared token over plain HTTP is enough.

## Open questions

Raised here, not decided:

- **Foreign use and a running job.** A job already running when foreign GPU use appears is left alone, with the
  alert as the backstop. Killing it automatically would free the GPU sooner, at the cost of lost work.
- **Workers that go away.** A worker that stops appearing stays in `corral ls` with its last-seen time. It could
  drop out after a while.
- **Dropped events.** A webhook delivery that's still failing after a day is dropped silently. It could raise
  something.
- **Fan-in.** `then` only chains forward. A job can't wait on more than one other job.
- **More than one job per worker.** A worker runs one job at a time.
- **More than one user.** Everyone shares one token and one queue.
