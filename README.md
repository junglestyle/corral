# corral

Priority job scheduler for opportunistic local compute.

My pipelines used to run on clocks: transcribe at :00, reprocess at :30, extract at :45. Work that arrived at :01
waited most of an hour at each step. Corral replaces the clocks with a queue. A step enqueues the next one when it
finishes, so work flows through as soon as a machine is free, and a machine I'm using myself can be paused without
losing anything.

```mermaid
flowchart LR
  C[Clients<br/>CLI, hooks, other services] -- enqueue --> S[(corral server<br/>NAS, SQLite)]
  S -- lease --> W1[worker: eeyore<br/>gpu]
  S -- lease --> W2[worker: NAS<br/>cpu]
  W1 -- heartbeat, finish --> S
  W2 -- heartbeat, finish --> S
  S -- finished event --> H[Webhooks]
  S -- then --> S
```

## What it does

- **Priorities with aging.** Urgent work goes first, and waiting work gains priority until it runs.
- **Pause that stays paused.** `corral pause eeyore` lets the running job finish, takes nothing new, and holds
  until `corral resume`, across restarts.
- **Chained work.** A job can name the jobs to enqueue when it succeeds, and the server posts a finished event to
  any webhook that wants it.
- **Sized to a window.** A job is told how many items it can likely finish in its kind's time window, based on how
  fast that kind has run before. The job may ignore it.
- **Workers pull.** Each worker leases jobs it has the hardware for, so adding a machine is starting a worker.

> **In development:** this repository holds the specification. There is no runnable release yet.

See the [specification](docs/SPEC.md) and [stack and decisions](docs/stack.md).

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
