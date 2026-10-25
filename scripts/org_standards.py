"""Keeps docs/org-standards.md an unchanged copy of the org's STANDARDS.md.

check         the copy is unchanged since it was synced (offline; make check runs it)
behind        the org's main has moved past the copy's commit (CI warns on it)
sync [branch] copy the org file from a branch, main by default (make sync-standards)
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = "https://github.com/junglestyle/.github"
NAME = "docs/org-standards.md"
COPY = Path(__file__).resolve().parent.parent / NAME
HEADER = re.compile(
    r"<!-- junglestyle/\.github STANDARDS\.md at ([0-9a-f]{40}), sha256 ([0-9a-f]{64})\. .* -->"
)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def head_of(branch: str) -> str:
    ref = "refs/heads/" + branch
    out = subprocess.run(["git", "ls-remote", REPO + ".git", ref], capture_output=True, text=True, check=True)
    heads = out.stdout.split()
    if not heads:
        raise SystemExit(f"{REPO} has no branch {branch}")
    return heads[0]


def read_copy() -> tuple[str, str, str]:
    header, body = COPY.read_text().split("\n", 1)
    m = HEADER.fullmatch(header)
    if not m:
        raise SystemExit(f"{NAME}: the first line is not the org-standards header; run make sync-standards")
    return m[1], m[2], body


def main(args: list[str]) -> None:
    command = args[0] if args else ""
    if command == "check":
        _, recorded, body = read_copy()
        if digest(body) != recorded:
            raise SystemExit(f"{NAME} was edited; change the org file instead, then run make sync-standards")
    elif command == "behind":
        commit, _, _ = read_copy()
        main_commit = head_of("main")
        if commit != main_commit:
            message = f"the org standards moved on: copy at {commit[:7]}, main at {main_commit[:7]}"
            # In GitHub Actions, a note on a passing job: the org moving on shouldn't block unrelated work.
            if os.environ.get("GITHUB_ACTIONS") == "true":
                print(f"::warning title=org standards::{message}; run make sync-standards")
                return
            raise SystemExit(message)
    elif command == "sync":
        commit = head_of(args[1] if len(args) > 1 else "main")
        url = f"https://raw.githubusercontent.com/junglestyle/.github/{commit}/STANDARDS.md"
        with urllib.request.urlopen(url, timeout=30) as response:
            body = response.read().decode()
        COPY.write_text(
            f"<!-- junglestyle/.github STANDARDS.md at {commit}, sha256 {digest(body)}. "
            "Change it there, then run make sync-standards. -->\n" + body
        )
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
