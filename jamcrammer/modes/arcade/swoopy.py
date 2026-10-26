"""Swoopy Eagle prototype (KICKOFF §9.4): small-ROI high-fps loop, bird y + next gap center,
flap rule plus a PD controller. Logs the gaps-per-run distribution."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import cv2
import numpy as np
import numpy.typing as npt

from jamcrammer.core.config import Level, SwoopyConfig
from jamcrammer.core.events import Event, EventBus
from jamcrammer.core.logging import get_logger
from jamcrammer.core.profiles import Profile
from jamcrammer.vision.ocr import crop

log = get_logger("swoopy")

BgrImage = npt.NDArray[np.uint8]
GrabFn = Callable[[], BgrImage]
REQUIRED = ("play_roi", "bird_hsv", "pipe_hsv")


class Tapper(Protocol):
    def key_tap(self, key: str, hold_ms: int) -> None: ...


@dataclass(frozen=True)
class Track:
    bird_y: float | None
    bird_x: float | None
    gap_center_y: float | None
    gap_x: float | None


def track(
    play: BgrImage,
    bird_hsv: tuple[tuple[int, int, int], tuple[int, int, int]],
    pipe_hsv: tuple[tuple[int, int, int], tuple[int, int, int]],
    cfg: SwoopyConfig,
) -> Track:
    """Bird = centroid of the bird-colored mask; next gap = the vertical opening in the first
    pipe column ahead of the bird."""
    hsv = cv2.cvtColor(play, cv2.COLOR_BGR2HSV)
    bird = cv2.inRange(hsv, np.array(bird_hsv[0], np.uint8), np.array(bird_hsv[1], np.uint8))
    pipes = cv2.inRange(hsv, np.array(pipe_hsv[0], np.uint8), np.array(pipe_hsv[1], np.uint8))
    ys, xs = np.nonzero(bird)
    if len(xs) < cfg.min_bird_px:
        return Track(None, None, None, None)
    bird_y, bird_x = float(ys.mean()), float(xs.mean())
    col_density = pipes.mean(axis=0) / 255.0
    ahead = np.flatnonzero(col_density[int(bird_x) + cfg.bird_lead_px :] > cfg.pipe_col_min)
    if len(ahead) == 0:
        return Track(bird_y, bird_x, None, None)
    x = int(bird_x) + cfg.bird_lead_px + int(ahead[0])
    column = pipes[:, x : x + cfg.pipe_probe_px].mean(axis=1) > 127
    free = np.flatnonzero(~column)
    if len(free) == 0:
        return Track(bird_y, bird_x, None, float(x))
    # the largest free run is the gap
    runs = np.split(free, np.where(np.diff(free) != 1)[0] + 1)
    gap = max(runs, key=len)
    return Track(bird_y, bird_x, float((gap[0] + gap[-1]) / 2.0), float(x))


class PDController:
    """Flap when the bird is below the target (plus a velocity term), not more often than
    ``min_flap_interval_s``."""

    def __init__(self, cfg: SwoopyConfig) -> None:
        self.cfg = cfg
        self._last_y: float | None = None
        self._last_t: float | None = None
        self._last_flap = -1e9

    def should_flap(self, bird_y: float, target_y: float, now: float) -> bool:
        vy = 0.0
        if self._last_y is not None and self._last_t is not None and now > self._last_t:
            vy = (bird_y - self._last_y) / (now - self._last_t)  # px/s, positive = falling
        self._last_y, self._last_t = bird_y, now
        error = bird_y - (target_y + self.cfg.target_offset_px)  # positive = below target
        control = self.cfg.kp * error + self.cfg.kd * vy
        if (
            control > self.cfg.flap_threshold
            and now - self._last_flap >= self.cfg.min_flap_interval_s
        ):
            self._last_flap = now
            return True
        return False


@dataclass
class SwoopyStats:
    runs: int = 0
    gaps_per_run: list[int] = field(default_factory=list)
    flaps: int = 0

    @property
    def best(self) -> int:
        return max(self.gaps_per_run, default=0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "runs": self.runs,
            "gaps_per_run": self.gaps_per_run,
            "best": self.best,
            "flaps": self.flaps,
        }


class SwoopyPilot:
    def __init__(
        self,
        profile: Profile,
        cfg: SwoopyConfig,
        tapper: Tapper,
        grab: GrabFn,
        *,
        bus: EventBus | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        missing = profile.missing(list(REQUIRED))
        if missing:
            msg = f"swoopy_eagle profile missing {missing}"
            raise ValueError(msg)
        self.profile = profile
        self.cfg = cfg
        self._tap = tapper
        self._grab = grab
        self._bus = bus
        self._clock = clock
        self._sleep = sleep
        self.controller = PDController(cfg)
        self.stats = SwoopyStats()
        self._gaps = 0
        self._last_gap_x: float | None = None

    def step(self, image: BgrImage) -> Track:
        tr = track(
            crop(image, self.profile.roi("play_roi")),
            self.profile.color("bird_hsv"),
            self.profile.color("pipe_hsv"),
            self.cfg,
        )
        now = self._clock()
        if tr.bird_y is None:
            return tr
        target = tr.gap_center_y if tr.gap_center_y is not None else self.cfg.default_target_y
        if self.controller.should_flap(tr.bird_y, target, now):
            self._tap.key_tap(self.cfg.flap_key, self.cfg.flap_hold_ms)
            self.stats.flaps += 1
        if (
            tr.gap_x is not None
            and self._last_gap_x is not None
            and tr.gap_x > self._last_gap_x + self.cfg.gap_reset_px
        ):
            self._gaps += 1  # the previous gap scrolled past: a new one is ahead
        self._last_gap_x = tr.gap_x
        return tr

    def run(
        self, should_stop: Callable[[], bool], crashed: Callable[[BgrImage], bool]
    ) -> SwoopyStats:
        period = 1.0 / self.cfg.fps
        self._gaps, self._last_gap_x = 0, None
        in_crash = False
        while not should_stop():
            image = self._grab()
            if crashed(image):
                if not in_crash:  # count a run once per crash, however long the screen stays
                    in_crash = True
                    self.stats.runs += 1
                    self.stats.gaps_per_run.append(self._gaps)
                    self._emit("swoopy.run", gaps=self._gaps, flaps=self.stats.flaps)
                    self._gaps, self._last_gap_x = 0, None
                self._sleep(self.cfg.restart_s)
                continue
            in_crash = False
            self.step(image)
            self._sleep(period)
        return self.stats

    def _emit(self, event_type: str, level: Level = Level.INFO, **p: object) -> None:
        if self._bus is not None:
            self._bus.publish(Event(type=event_type, source="swoopy", level=level, payload=dict(p)))
