#!/usr/bin/env bash
# Runs a corral worker as a root system service, for a host where user units or changes to /etc don't survive an
# upgrade (TrueNAS SCALE, for one). Everything lives in one directory on a data pool:
#
#   DIR/env          CORRAL_URL and CORRAL_TOKEN
#   DIR/worker.toml  the worker config; kinds can also go in DIR/kinds.d/*.toml
#
# Usage: sudo examples/system-worker.sh DIR COMMIT
#
# COMMIT is the corral commit to run. Run it at boot (on TrueNAS: a post-init command under System > Advanced >
# Init/Shutdown Scripts) and again after changing the config or the commit. It's safe to re-run: it installs uv and
# corral under DIR when they're missing or corral is at another commit, then restarts the worker.
set -euo pipefail

UV_VERSION=0.13.1

if [[ $# -ne 2 ]]; then
    echo "usage: system-worker.sh DIR COMMIT" >&2
    exit 2
fi
DIR=$1
COMMIT=$2
if [[ ! $COMMIT =~ ^[0-9a-f]{40}$ ]]; then
    echo "COMMIT must be a full 40-character commit SHA, got: $COMMIT" >&2
    exit 2
fi

export UV_TOOL_BIN_DIR="$DIR/bin" UV_TOOL_DIR="$DIR/tools" UV_PYTHON_INSTALL_DIR="$DIR/python" \
    UV_CACHE_DIR="$DIR/cache"

if [[ ! -x "$DIR/bin/uv" ]] || [[ "$("$DIR/bin/uv" --version)" != "uv $UV_VERSION "* ]]; then
    arch=$(uname -m)
    # From the release's .sha256 files.
    case $arch in
        x86_64) sha256=eadd9d27c134ab5ac1ffd4473b97a741407f3ea238288f03121fc4deddf90220 ;;
        aarch64) sha256=9041c73c99fdc5892cf128140beb354eca9231d502068cc36e6cb53a8e59ad14 ;;
        *)
            echo "no uv $UV_VERSION checksum for $arch" >&2
            exit 1
            ;;
    esac
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    tarball="uv-$arch-unknown-linux-gnu.tar.gz"
    curl -fsSL -o "$tmp/$tarball" "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$tarball"
    echo "$sha256  $tmp/$tarball" | sha256sum -c --quiet
    tar -xzf "$tmp/$tarball" -C "$tmp"
    mkdir -p "$DIR/bin"
    install -m 755 "$tmp/uv-$arch-unknown-linux-gnu/uv" "$tmp/uv-$arch-unknown-linux-gnu/uvx" "$DIR/bin/"
fi

# uv records where a tool came from, so a different commit is a reinstall and the same one is a no-op.
"$DIR/bin/uv" tool install --python 3.15 "git+https://github.com/junglestyle/corral@$COMMIT"

# A transient unit, so nothing is written to /etc. Stopping it also stops the job it's running.
systemctl stop corral-worker 2>/dev/null || true
systemctl reset-failed corral-worker 2>/dev/null || true
systemd-run --unit corral-worker --property Restart=always --property RestartSec=5 \
    --property EnvironmentFile="$DIR/env" "$DIR/bin/corral" worker "$DIR/worker.toml"
