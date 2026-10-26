from __future__ import annotations

import threading

from jamcrammer.core.config import Level
from jamcrammer.core.events import Event, EventBus


def test_publish_reaches_exact_and_wildcard() -> None:
    bus = EventBus()
    seen: list[str] = []
    bus.subscribe("a.b", lambda e: seen.append("exact:" + e.type))
    bus.subscribe("*", lambda e: seen.append("any:" + e.type))
    bus.emit("a.b", source="t", level=Level.NOTICE, x=1)
    bus.emit("c", source="t")
    assert seen == ["exact:a.b", "any:a.b", "any:c"]


def test_bad_handler_is_isolated() -> None:
    bus = EventBus()

    def boom(_: Event) -> None:
        raise RuntimeError("boom")

    got: list[Event] = []
    bus.subscribe("x", boom)
    bus.subscribe("x", got.append)
    bus.emit("x", source="t")
    assert len(got) == 1
    assert len(bus.errors) == 1


def test_unsubscribe() -> None:
    bus = EventBus()
    got: list[Event] = []
    off = bus.subscribe("x", got.append)
    bus.emit("x", source="t")
    off()
    bus.emit("x", source="t")
    assert len(got) == 1


def test_threaded_publish_is_safe() -> None:
    bus = EventBus()
    count = 0
    lock = threading.Lock()

    def inc(_: Event) -> None:
        nonlocal count
        with lock:
            count += 1

    def burst() -> None:
        for _ in range(100):
            bus.emit("tick", source="t")

    bus.subscribe("tick", inc)
    threads = [threading.Thread(target=burst) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert count == 800
