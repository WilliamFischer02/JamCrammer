from __future__ import annotations

import time
from pathlib import Path

from jamcrammer.core.config import Level, NotifyConfig
from jamcrammer.core.events import EventBus
from jamcrammer.services.notify import Notifier


def _fake_senders(calls: list[tuple[str, Level, str]], fail: set[str] | None = None):  # type: ignore[no-untyped-def]
    fail = fail or set()

    def make(ch: str):  # type: ignore[no-untyped-def]
        def send(level: Level, title: str, message: str, shot: Path | None) -> None:
            if ch in fail:
                raise RuntimeError("boom")
            calls.append((ch, level, title))

        return send

    return {ch: make(ch) for ch in ("toast", "sound", "ntfy", "discord")}


def test_routing_by_level_and_toggles() -> None:
    cfg = NotifyConfig(toast=True, sound=True, ntfy_topic="t", discord_webhook="")
    calls: list[tuple[str, Level, str]] = []
    n = Notifier(cfg, senders=_fake_senders(calls))
    assert n.channels_for(Level.INFO) == []
    assert n.channels_for(Level.NOTICE) == ["toast"]
    assert n.channels_for(Level.HIGH) == ["toast", "sound", "ntfy"]
    n.deliver(Level.HIGH, "t", "m")
    assert [c[0] for c in calls] == ["toast", "sound", "ntfy"]


def test_failures_are_isolated_and_reported() -> None:
    cfg = NotifyConfig(toast=True, sound=True)
    calls: list[tuple[str, Level, str]] = []
    n = Notifier(cfg, senders=_fake_senders(calls, fail={"toast"}))
    results = n.deliver(Level.HIGH, "t", "m")
    assert [(r.channel, r.ok) for r in results] == [("toast", False), ("sound", True)]
    assert "boom" in results[0].error


def test_test_all_reports_disabled_channels() -> None:
    cfg = NotifyConfig(toast=False, sound=True)
    calls: list[tuple[str, Level, str]] = []
    n = Notifier(cfg, senders=_fake_senders(calls))
    results = {r.channel: r for r in n.test_all()}
    assert not results["toast"].enabled
    assert results["sound"].ok
    assert not results["ntfy"].enabled


def test_bus_attachment_forwards_notice_and_high_only() -> None:
    cfg = NotifyConfig(toast=True, sound=False)
    calls: list[tuple[str, Level, str]] = []
    n = Notifier(cfg, senders=_fake_senders(calls))
    bus = EventBus()
    n.attach(bus)
    bus.emit("x.info", source="t")
    bus.emit("x.notice", source="t", level=Level.NOTICE, message="hello")
    bus.emit("x.high", source="t", level=Level.HIGH)
    deadline = time.time() + 2.0
    while len(calls) < 2 and time.time() < deadline:
        time.sleep(0.02)
    n.stop()
    assert [c[2] for c in calls] == ["JamCrammer NOTICE: x.notice", "JamCrammer HIGH: x.high"]
