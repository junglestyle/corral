"""The server's settings, read from its TOML config."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from corral.duration import parse_duration


class Event(StrEnum):
    JOB_FINISHED = "job.finished"
    WORKER_BLOCKED = "worker.blocked"
    WORKER_UNBLOCKED = "worker.unblocked"


@dataclass(frozen=True)
class Webhook:
    url: str
    events: frozenset[str] = frozenset()
    kinds: frozenset[str] = frozenset()

    def wants(self, event: Event, kind: str | None) -> bool:
        """An empty filter matches everything; a kind filter never matches an event that isn't about a job."""
        return (not self.events or event in self.events) and (not self.kinds or kind in self.kinds)


@dataclass(frozen=True)
class Settings:
    aging_seconds: float = 3600
    block_alert_after: float = 15 * 60
    window: float = 10 * 60
    windows: Mapping[str, float] = field(default_factory=dict)
    webhooks: tuple[Webhook, ...] = ()

    def window_for(self, kind: str) -> float:
        return self.windows.get(kind, self.window)


def load_settings(path: Path) -> Settings:
    """Reads the server config. A bad one exits naming the file and the problem, not with a traceback."""
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise SystemExit(f"{path}: {e}") from None
    defaults = Settings()
    try:
        return Settings(
            aging_seconds=_duration(raw, "aging", defaults.aging_seconds),
            block_alert_after=_duration(raw, "block_alert_after", defaults.block_alert_after),
            window=_duration(raw, "window", defaults.window),
            windows={
                kind: _duration(k, "window", defaults.window)
                for kind, k in raw.get("kinds", {}).items()
                if "window" in k
            },
            webhooks=tuple(_webhook(w) for w in raw.get("webhooks", [])),
        )
    except ValueError as e:
        raise SystemExit(f"{path}: {e}") from None


def _duration(raw: Mapping[str, Any], key: str, default: float) -> float:
    return parse_duration(str(raw[key])) if key in raw else default


def _webhook(raw: Mapping[str, Any]) -> Webhook:
    if not isinstance(raw.get("url"), str):
        raise ValueError("each webhook needs a url")
    events = frozenset(raw.get("events", []))
    if unknown := events - set(Event):
        raise ValueError(f"unknown events {sorted(unknown)!r}; known: {sorted(Event)!r}")
    return Webhook(url=raw["url"], events=events, kinds=frozenset(raw.get("kinds", [])))
