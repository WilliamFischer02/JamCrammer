"""Thread-safe event bus. Workers publish; the GUI (or CLI) subscribes.

Qt integration lives in ``jamcrammer.app``; this module has no Qt dependency so it can be
used headless and in tests.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jamcrammer.core.config import Level

Payload = dict[str, Any]
Handler = Callable[["Event"], None]

WILDCARD = "*"


@dataclass(frozen=True)
class Event:
    """One thing that happened. ``type`` is dotted, e.g. ``capture.started``."""

    type: str
    source: str
    level: Level = Level.INFO
    payload: Payload = field(default_factory=dict)
    screenshot_path: Path | None = None
    ts: float = field(default_factory=time.time)


class EventBus:
    """Synchronous fan-out with a lock. Handlers run on the publisher's thread.

    Handlers must be quick and must not raise; exceptions are collected into
    ``errors`` instead of propagating so one bad subscriber cannot kill a worker.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self.errors: list[tuple[Event, BaseException]] = []

    def subscribe(self, event_type: str, handler: Handler) -> Callable[[], None]:
        """Subscribe to an exact type or ``"*"``. Returns an unsubscribe callable."""
        with self._lock:
            self._handlers[event_type].append(handler)

        def unsubscribe() -> None:
            with self._lock:
                handlers = self._handlers.get(event_type, [])
                if handler in handlers:
                    handlers.remove(handler)

        return unsubscribe

    def publish(self, event: Event) -> None:
        with self._lock:
            targets = list(self._handlers.get(event.type, [])) + list(
                self._handlers.get(WILDCARD, [])
            )
        for handler in targets:
            try:
                handler(event)
            except Exception as exc:
                with self._lock:
                    self.errors.append((event, exc))

    def emit(
        self,
        event_type: str,
        source: str,
        level: Level = Level.INFO,
        screenshot_path: Path | None = None,
        **payload: Any,
    ) -> Event:
        """Convenience: build and publish in one call."""
        event = Event(
            type=event_type,
            source=source,
            level=level,
            payload=payload,
            screenshot_path=screenshot_path,
        )
        self.publish(event)
        return event
