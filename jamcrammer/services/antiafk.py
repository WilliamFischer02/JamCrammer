"""Anti-AFK (KICKOFF §7.2): cancelling movement pairs, room-drift guard, in-game heartbeat."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

from jamcrammer.core.config import AntiAfkConfig, Direction, DriftConfig, Level
from jamcrammer.core.events import EventBus
from jamcrammer.platform.input import InputController

BgrImage = npt.NDArray[np.uint8]
Roi = tuple[int, int, int, int]


@dataclass(frozen=True)
class DriftResult:
    score: float
    drifted: bool
    has_baseline: bool


class RoomDriftGuard:
    """Fingerprint of the room background (HUD and animal area masked out).

    The fingerprint is a small grayscale downscale; drift is the mean absolute difference
    against the baseline. Large values mean a room change, a panel covering the room, etc.
    """

    def __init__(self, cfg: DriftConfig, exclude: list[Roi]) -> None:
        self.cfg = cfg
        self.exclude = exclude
        self._baseline: npt.NDArray[np.float32] | None = None
        self.baseline_ts = 0.0

    def fingerprint(self, image: BgrImage) -> npt.NDArray[np.float32]:
        masked = image.copy()
        for x, y, w, h in self.exclude:
            masked[max(0, y) : y + h, max(0, x) : x + w] = 0
        gray = cv2.cvtColor(masked, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, tuple(self.cfg.downscale), interpolation=cv2.INTER_AREA)
        return small.astype(np.float32)

    def set_baseline(self, image: BgrImage, ts: float | None = None) -> None:
        self._baseline = self.fingerprint(image)
        self.baseline_ts = time.time() if ts is None else ts

    @property
    def has_baseline(self) -> bool:
        return self._baseline is not None

    def score(self, image: BgrImage) -> float:
        if self._baseline is None:
            return 0.0
        return float(np.abs(self.fingerprint(image) - self._baseline).mean())

    def check(self, image: BgrImage) -> DriftResult:
        if self._baseline is None:
            return DriftResult(0.0, False, False)
        s = self.score(image)
        return DriftResult(s, s >= self.cfg.threshold, True)


@dataclass(frozen=True)
class CycleResult:
    pair: tuple[Direction, Direction]
    drift: DriftResult
    sent: bool


class AntiAfk:
    """Rotates through cancelling pairs; each cycle is verified against the drift guard."""

    def __init__(
        self,
        cfg: AntiAfkConfig,
        inp: InputController,
        guard: RoomDriftGuard,
        *,
        bus: EventBus | None = None,
        ensure_focus: Callable[[], bool] | None = None,
        screenshot: Callable[[str], Path | None] | None = None,
    ) -> None:
        self.cfg = cfg
        self._input = inp
        self.guard = guard
        self._bus = bus
        self._ensure_focus = ensure_focus
        self._screenshot = screenshot
        self._index = 0
        self.cycles = 0
        self.drift_events = 0
        self.enabled = True

    def next_pair(self) -> tuple[Direction, Direction]:
        pair = self.cfg.pattern[self._index % len(self.cfg.pattern)]
        self._index += 1
        return pair

    def cycle(self, grab: Callable[[], BgrImage]) -> CycleResult:
        """Send one cancelling pair, then verify the room did not drift."""
        pair = self.next_pair()
        if not self.enabled:
            return CycleResult(pair, self.guard.check(grab()), sent=False)
        if self._ensure_focus is not None and not self._ensure_focus():
            self._emit("antiafk.focus_failed", Level.NOTICE)
            return CycleResult(pair, self.guard.check(grab()), sent=False)
        self._input.movement_pair(pair, self.cfg.tap_ms, self.cfg.gap_ms)
        self.cycles += 1
        time.sleep(self.cfg.settle_ms / 1000.0)
        drift = self.guard.check(grab())
        if drift.drifted:
            self.drift_events += 1
            self.enabled = False
            shot = self._screenshot("room_drift") if self._screenshot else None
            self._emit(
                "antiafk.room_drift",
                Level.HIGH,
                screenshot_path=shot,
                score=round(drift.score, 2),
                threshold=self.guard.cfg.threshold,
                message="Room fingerprint changed after a movement pair; movement stopped.",
            )
        else:
            self._emit("antiafk.cycle", pair=list(pair), drift=round(drift.score, 2))
        return CycleResult(pair, drift, sent=True)

    def _emit(
        self,
        event_type: str,
        level: Level = Level.INFO,
        screenshot_path: Path | None = None,
        **p: object,
    ) -> None:
        if self._bus is not None:
            self._bus.emit(
                event_type, source="antiafk", level=level, screenshot_path=screenshot_path, **p
            )


class Heartbeat:
    """Each mode declares how it stays active; this just answers "is it time?"."""

    def __init__(self, interval_s: float, last_bot_input_ts: Callable[[], float]) -> None:
        self.interval_s = interval_s
        self._last = last_bot_input_ts

    def due(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return now - self._last() >= self.interval_s
