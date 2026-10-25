# Running corral

Everything talks to the server with two environment variables:

| Variable | Meaning |
| --- | --- |
| `CORRAL_URL` | The server, e.g. `http://nas:8700` |
| `CORRAL_TOKEN` | Shared bearer token. The server refuses to start without it. |

## Server

```sh
corral server --db /data/corral.sqlite --host 0.0.0.0 --port 8700 --aging 1h
```

`--aging` is how much waiting is worth one priority point (see [ordering](SPEC.md#ordering-and-aging)). The
SQLite file is the whole state; back it up like any other file, or don't, since losing it loses only the queue.

Run it in Docker with [`examples/compose.yaml`](../examples/compose.yaml), which builds the repo's `Dockerfile`:

```sh
docker compose --env-file /path/to/env -f examples/compose.yaml up -d --build
```

The env file sets three variables, and a worker on the same host can share it for `CORRAL_URL` and
`CORRAL_TOKEN`:

| Variable | Meaning |
| --- | --- |
| `CORRAL_TOKEN` | The shared bearer token |
| `CORRAL_HOST` | The one address the server listens on, such as the host's VPN address |
| `CORRAL_DATA` | Host directory for the SQLite file |

The container uses host networking so the server binds `CORRAL_HOST` and nothing else; a NAS often has a public
IPv6 address that a published port would also answer on. Docker restarts the server at boot, and again until
`CORRAL_HOST` exists if its interface comes up late. Re-run the same command after pulling to update it.

## Worker

```sh
corral worker ~/.config/corral/worker.toml
```

The config names the worker, the capability labels it offers, and the kinds it can run. See
[`examples/worker.toml`](../examples/worker.toml).

| Key | Meaning |
| --- | --- |
| `name` | Unique per worker; what `pause` and `resume` take |
| `offers` | Capability labels. Offering `gpu` turns on foreign-GPU detection and needs `nvidia-smi`. |
| `ignore` | Executable names of GPU compute processes that don't count as foreign |
| `kinds.<kind>.argv` | Command to run, without a shell |
| `kinds.<kind>.timeout` | Optional, e.g. `30m`. A job that runs longer is killed and counts as a failed attempt. |

Kinds can also go in `kinds.d/*.toml` next to the config, in the same `[kinds.<kind>]` form, so each project that
runs on a worker installs its own file. A kind configured twice is an error. The worker reads its config at start,
so restart it after changing either.

Run it as a systemd user unit, as in
[`examples/corral-worker.service`](../examples/corral-worker.service), with `CORRAL_URL` and `CORRAL_TOKEN` in
the unit's `EnvironmentFile`. systemd restarts the worker if it dies, and stopping the unit stops its job too, so
nothing keeps running under a lease the server has given away. Run `loginctl enable-linger` so the worker
starts at boot without a login.

On a host where user units don't survive an upgrade, such as a TrueNAS NAS, run
[`examples/system-worker.sh`](../examples/system-worker.sh) at boot instead. It installs corral under one directory
on a data pool and starts the worker as a root system service, so its jobs can use Docker. The env file there can
be the server's too.

Workers run on the host, not in a container (see [ADR-005](stack.md#adr-005-server-in-a-container-workers-on-the-host)).

## CLI

```sh
corral enqueue hearsay.transcribe --needs gpu --key hearsay.transcribe
corral enqueue hearsay.reprocess --priority 7 --args '{"since": "2026-10-01"}' \
  --then '[{"kind": "ideamachine.run", "key": "ideamachine.run"}]'
corral ls                      # workers, then queued and running jobs
corral ls --state failed
corral pause gpu-host --for 2h
corral pause all --now
corral resume all
corral kill 42                 # stop and requeue
corral cancel 42               # remove for good
```

## Known limits

- The worker learns about foreign GPU use only before each lease and on heartbeats, and a lease long-polls for
  up to 30 s, so a GPU job can start up to 30 s after something else took the GPU.
- A lease long-poll checks the queue once a second, so a new job waits up to a second for an idle worker.
- Workers that stop appearing stay in `corral ls` with their last-seen time.
