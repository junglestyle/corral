#!/bin/sh
# Runs a corral worker as a root system service, for a host where user units or changes to /etc don't survive an
# upgrade (TrueNAS SCALE, for one). Everything lives in one directory on a data pool:
#
#   DIR/env          CORRAL_URL and CORRAL_TOKEN
#   DIR/worker.toml  the worker config; kinds can also go in DIR/kinds.d/*.toml
#
# Run it at boot (on TrueNAS: a post-init command under System > Advanced > Init/Shutdown Scripts) and again after
# changing the config. It installs uv and corral under DIR the first time; `DIR/bin/uv tool upgrade corral`, then
# this again, updates them.
set -eu
DIR="${1:?usage: system-worker.sh DIR}"
export UV_INSTALL_DIR="$DIR/bin" UV_TOOL_BIN_DIR="$DIR/bin" UV_TOOL_DIR="$DIR/tools" \
    UV_PYTHON_INSTALL_DIR="$DIR/python" UV_CACHE_DIR="$DIR/cache" UV_NO_MODIFY_PATH=1
[ -x "$DIR/bin/uv" ] || curl -LsSf https://astral.sh/uv/install.sh | sh
[ -x "$DIR/bin/corral" ] || "$DIR/bin/uv" tool install --python 3.15 git+https://github.com/junglestyle/corral

# A transient unit, so nothing is written to /etc. Stopping it also stops the job it's running.
systemctl stop corral-worker 2>/dev/null || true
systemctl reset-failed corral-worker 2>/dev/null || true
systemd-run --unit corral-worker --property Restart=always --property RestartSec=5 \
    --property EnvironmentFile="$DIR/env" "$DIR/bin/corral" worker "$DIR/worker.toml"
