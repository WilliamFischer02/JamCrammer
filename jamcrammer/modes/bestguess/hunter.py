"""Best Guess Hunter, Phases A (hunt) and B (join), KICKOFF 8.1-8.2.

The loop grabs frames continuously so the watchdog runs at its rate; every ``hunt_interval_s``
it performs a scan cycle: anti-AFK pair → open My Parties → identify banners → log sightings →
close. When the target appears it raises a HIGH alert and (if allowed) joins.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from jamcrammer.core.config import AppConfig, Level
from jamcrammer.core.events import Event, EventBus
from jamcrammer.core.logging import get_logger
from jamcrammer.core.safety import PresenceDetector
from jamcrammer.modes.party_watcher import Banner, JoinResult, PartyWatcher, ScanResult
from jamcrammer.platform.capture import Frame
from jamcrammer.services.antiafk import AntiAfk, RoomDriftGuard
from jamcrammer.services.watchdog import Watchdog

log = get_logger("hunter")

GrabFrame = Callable[[], Frame]


@dataclass
class HunterStats:
    cycles: int = 0
    scans_ok: int = 0
    scans_failed: int = 0
    target_seen: int = 0
    joined: bool = False
    last_scan_ts: float = 0.0
    last_labels: list[str] = field(default_factory=list)


class Hunter:
    def __init__(
        self,
        cfg: AppConfig,
        watcher: PartyWatcher,
        grab: GrabFrame,
        *,
        watchdog: Watchdog | None = None,
        antiafk: AntiAfk | None = None,
        guard: RoomDriftGuard | None = None,
        bus: EventBus | None = None,
        no_join: bool = False,
        presence: PresenceDetector | None = None,
        on_joined: Callable[[Banner], None] | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cfg = cfg
        self.watcher = watcher
        self._grab = grab
        self.watchdog = watchdog
        self.antiafk = antiafk
        self.guard = guard
        self._bus = bus
        self.no_join = no_join
        self.presence = presence
        self._on_joined = on_joined
        self._clock = clock
        self._sleep = sleep
        self.stats = HunterStats()
        self.state = "IDLE"
        self._next_scan = 0.0

    # --- one cycle -------------------------------------------------------------------------

    def cycle(self) -> ScanResult:
        self.stats.cycles += 1
        self.state = "SCANNING"
        if self.antiafk is not None and self.cfg.hunt.antiafk_each_cycle:
            self.antiafk.cycle(lambda: self._grab().image)
        result = self.watcher.scan()
        self.stats.last_scan_ts = result.ts
        self.stats.last_labels = result.labels
        if result.errors:
            self.stats.scans_failed += 1
            self._emit("hunter.scan_failed", Level.NOTICE, errors=result.errors)
        else:
            self.stats.scans_ok += 1
        if result.target is not None:
            self.stats.target_seen += 1
            self._emit(
                "hunter.best_guess_found",
                Level.HIGH,
                label=result.target.label,
                slot=result.target.index,
                message=f"Best Guess is in My Parties (slot {result.target.index}).",
            )
            if self.cfg.auto_join and not self.no_join:
                self.join(result.target)
        self.state = "HUNTING"
        return result

    def join(self, banner: Banner) -> JoinResult:
        self.state = "JOINING"
        # The scan closed the panel; reopen it and re-identify the slot so GO NOW is fresh.
        image = self.watcher.open_panel()
        if image is None:
            self._emit("hunter.join_failed", Level.HIGH, reason="panel did not reopen")
            return JoinResult(False, False, error="panel did not reopen")
        self.watcher._sleep(self.cfg.hunt.panel_settle_s)
        _, banners = self.watcher.analyzer.analyze(self.watcher._grab())
        fresh = next((b for b in banners if self.watcher.analyzer.is_target(b)), None)
        if fresh is None or fresh.go_now_point is None:
            self._emit("hunter.join_failed", Level.HIGH, reason="target not found on reopen")
            self.watcher.ensure_closed()
            return JoinResult(False, False, error="target not found on reopen")
        baseline = self.watcher._grab()
        if self.guard is not None:
            self.guard.set_baseline(baseline)

        def room_changed(im):  # type: ignore[no-untyped-def]
            return self.guard.check(im).drifted if self.guard is not None else True

        res = self.watcher.join(fresh, room_changed)
        if res.arrived:
            self.stats.joined = True
            self.state = "JOINED"
            self._emit("hunter.joined", Level.HIGH, waits=res.waits, message="Joined Best Guess.")
            if self._on_joined is not None:
                self._on_joined(fresh)
        else:
            self._emit("hunter.join_failed", Level.HIGH, reason=res.error, waits=res.waits)
        return res

    # --- loop ------------------------------------------------------------------------------

    def run(
        self, should_stop: Callable[[], bool], status: Callable[[str], None] | None = None
    ) -> None:
        period = 1.0 / (self.watchdog.cfg.fps if self.watchdog is not None else self.cfg.fps)
        self._next_scan = self._clock()
        self.state = "HUNTING"
        while not should_stop():
            frame = self._grab()
            if self.watchdog is not None:
                self.watchdog.tick(frame)
                if self.watchdog.paused:
                    self.state = "PAUSED"
                    self._sleep(period)
                    continue
            now = self._clock()
            if self.presence is not None and self.presence.user_active():
                # William is back (KICKOFF 7.4): hands off until the input has been idle.
                if self.state != "PAUSED_USER":
                    self._emit("hunter.paused_user", Level.NOTICE, message="Paused - user active")
                self.state = "PAUSED_USER"
                self._next_scan = max(self._next_scan, now + self.cfg.resume_after_idle_s)
                self._sleep(period)
                continue
            if self.state == "PAUSED_USER":
                if not self.presence.may_resume():  # type: ignore[union-attr]
                    self._sleep(period)
                    continue
                self.state = "HUNTING"
                self._emit("hunter.resumed", Level.INFO)
            if now >= self._next_scan:
                self.cycle()
                if self.stats.joined:
                    return
                self._next_scan = self._clock() + self.cfg.hunt_interval_s
            if status is not None:
                status(f"{self.state} · next scan in {max(0, int(self._next_scan - now))}s")
            self._sleep(period)

    def _emit(self, event_type: str, level: Level = Level.INFO, **p: object) -> None:
        if self._bus is not None:
            self._bus.publish(Event(type=event_type, source="hunter", level=level, payload=dict(p)))
