# Roadmap

The order corral gets built in. Each slice is usable on its own, names what a user would notice and the
[spec](SPEC.md) requirements it delivers, and has a done-when someone can demonstrate. A requirement not yet in a
slice stays in the spec and waits for one here.

## 1. Queue, worker and CLI

Status: done

A pipeline that ran on timers runs on corral instead. Each step enqueues the next when it succeeds, work goes to
whichever worker has what it needs, a worker can be paused without losing anything, and a GPU worker stops taking
GPU work while someone else is using the GPU.

Delivers: QUEUE-1, QUEUE-2, QUEUE-3, CONTROL-1, CONTROL-2, CHAIN-1, GPU-1, ACCESS-1.

Done when: the [speech pipeline](SPEC.md#example-a-speech-pipeline) runs end to end on `then` and `key`, with
captures that arrive while the chain is waiting adding nothing to it.

## 2. Events and hints

Status: done

Other services hear about finished jobs without polling, a phone gets a push when foreign GPU use holds up the
queue, and a backlog job is told how much it can finish in its kind's window.

Delivers: CHAIN-2, GPU-2, HINT-1.

Done when: foreign GPU use that holds up queued GPU work past `block_alert_after` pushes a `worker.blocked` alert
to an ntfy topic, and a backlog job in the speech pipeline gets `CORRAL_SUGGESTED_ITEMS` from that pipeline's own
timings.

## 3. Dashboard

Status: done

Someone can see workers, the queue and recent history in a browser, and pause and resume workers there, without
the CLI.

Delivers: DASH-1.

Done when: opening the server's address and giving it the token shows the workers and the queue, and pausing and
resuming a worker from the page shows up in `corral ls`.

## 4. Held jobs

Status: built in corral and wired in hearsay; not yet deployed

A step that processes everything pending waits while anything would still feed it, up to a set time, so a backlog
or a burst of input is processed in one run instead of one per chunk or per item.

Delivers: QUEUE-4.

Done when: in the [speech pipeline](SPEC.md#example-a-speech-pipeline), a transcription backlog taken in chunks is
folded once when it ends, and at least every `wait_seconds` while it lasts, not after every chunk.
