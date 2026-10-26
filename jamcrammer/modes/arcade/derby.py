"""Jamaa Derby farmer (KICKOFF §9.3).

Obstacles are detected as foreign objects in the lane ROI against a rolling background
(no templates needed for v1; templates sharpen the class later). Space is pressed at a
calibrated lead distance per obstacle class; carrot boosts are used on clear straights and
never overlap; the results screen is OCR'd for place and gems.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import cv2
import numpy as np
import numpy.typing as npt

from jamcrammer.core.config import DerbyConfig, Level
from jamcrammer.core.events import Event, EventBus
from jamcrammer.core.logging import get_logger
from jamcrammer.core.profiles import Profile
from jamcrammer.core.store import Store
from jamcrammer.vision.ocr import crop, ocr_image, parse_int

log = get_logger("derby")

BgrImage = npt.NDArray[np.uint8]
GrabFn = Callable[[], BgrImage]
REQUIRED = (
    "lane_roi",
    "horse_x",
    "boost_button_point",
    "results_place_roi",
    "results_gems_roi",
    "race_again_point",
)
PLACE_WORDS = {
    "1st": 1,
    "first": 1,
    "2nd": 2,
    "second": 2,
    "3rd": 3,
    "third": 3,
    "4th": 4,
    "5th": 5,
    "6th": 6,
}
PAYOUT_NORMAL = {1: 100, 2: 75, 3: 50, 4: 40, 5: 30, 6: 25}
PAYOUT_PRO = {1: 150, 2: 125, 3: 100, 4: 75, 5: 50, 6: 25}
PRO_UNLOCK_WINS = 10


class Inputs(Protocol):
    def click(self, cx: int, cy: int) -> bool: ...

    def key_tap(self, key: str, hold_ms: int) -> None: ...


@dataclass(frozen=True)
class Obstacle:
    left: int  # x of the left edge within the lane ROI
    width: int

    @property
    def long(self) -> bool:  # set by the detector via width threshold
        return False


def detect_obstacles(
    lane: BgrImage, background: npt.NDArray[np.float32] | None, horse_x: int, cfg: DerbyConfig
) -> list[Obstacle]:
    """Foreign objects to the right of the horse: columns where at least ``column_fraction_min``
    of the pixels differ from the rolling background by ``pixel_diff_min``. Consecutive
    columns form one obstacle."""
    gray = cv2.cvtColor(lane, cv2.COLOR_BGR2GRAY).astype(np.float32)
    if background is None:
        return []
    differing = np.abs(gray - background) > cfg.pixel_diff_min
    hot = differing.mean(axis=0) > cfg.column_fraction_min  # per column, height-independent
    out: list[Obstacle] = []
    start: int | None = None
    for x, h in enumerate(hot):
        if h and start is None:
            start = x
        elif not h and start is not None:
            if x - start >= cfg.obstacle_min_px and start > horse_x:
                out.append(Obstacle(start, x - start))
            start = None
    if start is not None and len(hot) - start >= cfg.obstacle_min_px and start > horse_x:
        out.append(Obstacle(start, len(hot) - start))
    return out


@dataclass
class RaceResult:
    started_ts: float
    duration_s: float = 0.0
    place: int | None = None
    gems: int | None = None
    jumps: int = 0
    boosts: int = 0
    notes: str = ""


@dataclass
class DerbyStats:
    races: int = 0
    first_places: int = 0
    gems: int = 0
    jumps: int = 0
    pro: bool = False
    race_times: list[float] = field(default_factory=list)
    last: RaceResult | None = None

    @property
    def first_place_rate(self) -> float:
        return self.first_places / self.races if self.races else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "races": self.races,
            "first_places": self.first_places,
            "first_place_rate": round(self.first_place_rate, 3),
            "gems": self.gems,
            "jumps": self.jumps,
            "pro": self.pro,
        }


class DerbyFarmer:
    def __init__(
        self,
        profile: Profile,
        cfg: DerbyConfig,
        inputs: Inputs,
        grab: GrabFn,
        *,
        store: Store | None = None,
        bus: EventBus | None = None,
        ocr: Callable[[BgrImage], str] | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        missing = profile.missing(list(REQUIRED))
        if missing:
            msg = f"jamaa_derby profile missing {missing}"
            raise ValueError(msg)
        self.profile = profile
        self.cfg = cfg
        self._in = inputs
        self._grab = grab
        self._store = store
        self._bus = bus
        self._ocr = ocr or (lambda img: ocr_image(img, upscale=2.0).text)
        self._clock = clock
        self._sleep = sleep
        self.stats = DerbyStats()
        self.stats.pro = (
            bool(profile.number("pro_unlocked")) if profile.has("pro_unlocked") else False
        )
        self.state = "WAITING"
        self._background: npt.NDArray[np.float32] | None = None
        self._last_jump = -1e9
        self._boost_until = -1e9
        self._boosts_left = cfg.boosts_per_race

    # --- perception ----------------------------------------------------------------------------

    def _lane(self, image: BgrImage) -> BgrImage:
        return crop(image, self.profile.roi("lane_roi"))

    def _update_background(self, lane: BgrImage) -> None:
        gray = cv2.cvtColor(lane, cv2.COLOR_BGR2GRAY).astype(np.float32)
        if self._background is None:
            self._background = gray
        else:
            a = self.cfg.background_alpha
            self._background = (1 - a) * self._background + a * gray

    def racing(self, image: BgrImage) -> bool:
        """Racing when the lane shows motion-rich content and no results text."""
        text = self._ocr(crop(image, self.profile.roi("results_place_roi"))).lower()
        return not any(w in text for w in PLACE_WORDS)

    def results(self, image: BgrImage) -> tuple[int | None, int | None]:
        text = self._ocr(crop(image, self.profile.roi("results_place_roi"))).lower()
        place = next((v for w, v in PLACE_WORDS.items() if w in text), None)
        gems = parse_int(self._ocr(crop(image, self.profile.roi("results_gems_roi"))))
        return (place, gems)

    # --- control ---------------------------------------------------------------------------------

    def _lead_for(self, ob: Obstacle) -> int:
        long = ob.width >= self.profile.number("long_obstacle_min_px")
        return int(self.profile.number("jump_lead_long_px" if long else "jump_lead_short_px"))

    def control_step(self, image: BgrImage, res: RaceResult) -> None:
        lane = self._lane(image)
        horse_x = int(self.profile.number("horse_x"))
        obstacles = detect_obstacles(lane, self._background, horse_x, self.cfg)
        self._update_background(lane)
        now = self._clock()
        nearest = min(obstacles, key=lambda o: o.left, default=None)
        if nearest is not None:
            gap = nearest.left - horse_x
            if gap <= self._lead_for(nearest) and now - self._last_jump >= self.profile.number(
                "jump_cooldown_s"
            ):
                self._in.key_tap(self.cfg.jump_key, self.cfg.jump_hold_ms)
                self._last_jump = now
                res.jumps += 1
                return
        clear = nearest is None or (nearest.left - horse_x) > self.profile.number("boost_clear_px")
        if (
            clear
            and self._boosts_left > 0
            and now >= self._boost_until
            and now - self._last_jump > self.cfg.boost_after_jump_s
        ):
            bx, by = self.profile.point("boost_button_point")
            self._in.click(bx, by)
            self._boosts_left -= 1
            res.boosts += 1
            self._boost_until = now + self.profile.number("boost_duration_s")

    def play_race(self) -> RaceResult:
        res = RaceResult(started_ts=self._clock())
        self.state = "RACING"
        self._background = None
        self._boosts_left = self.cfg.boosts_per_race
        self._last_jump = -1e9
        self._boost_until = -1e9
        period = 1.0 / self.cfg.fps
        deadline = self._clock() + self.cfg.race_timeout_s
        warm = 0
        while self._clock() < deadline:
            image = self._grab()
            if warm >= self.cfg.background_warmup_frames and not self.racing(image):
                break
            if warm < self.cfg.background_warmup_frames:
                self._update_background(self._lane(image))
                warm += 1
            else:
                self.control_step(image, res)
            self._sleep(period)
        self.state = "RESULTS"
        self._sleep(self.cfg.results_settle_s)
        res.place, res.gems = self.results(self._grab())
        if res.gems is None and res.place is not None:
            res.gems = (PAYOUT_PRO if self.stats.pro else PAYOUT_NORMAL).get(res.place)
        rx, ry = self.profile.point("race_again_point")
        self._in.click(rx, ry)
        return self._finish(res)

    def _finish(self, res: RaceResult) -> RaceResult:
        res.duration_s = round(self._clock() - res.started_ts, 2)
        self.stats.races += 1
        self.stats.jumps += res.jumps
        self.stats.race_times.append(res.duration_s)
        if res.place == 1:
            self.stats.first_places += 1
            if not self.stats.pro and self.stats.first_places >= PRO_UNLOCK_WINS:
                self.stats.pro = True
                self.profile.set_value("pro_unlocked", 1, status="calibrated")
                self._emit(
                    "derby.pro_unlocked",
                    Level.NOTICE,
                    message="10 first places: Pro unlocked (150 for 1st).",
                )
        if res.gems:
            self.stats.gems += res.gems
        self.stats.last = res
        if self._store is not None:
            self._store.add_arcade_round(
                ts=res.started_ts,
                game="jamaa_derby",
                duration_s=res.duration_s,
                gems_reported=res.gems,
                mismatches=0,
                notes=(
                    f"place={res.place} jumps={res.jumps} boosts={res.boosts} {res.notes}".strip()
                ),
            )
        self._emit(
            "derby.race",
            place=res.place,
            gems=res.gems,
            jumps=res.jumps,
            boosts=res.boosts,
            duration_s=res.duration_s,
        )
        self.state = "WAITING"
        return res

    def run(
        self, should_stop: Callable[[], bool], status: Callable[[str], None] | None = None
    ) -> None:
        while not should_stop():
            res = self.play_race()
            if status is not None:
                status(
                    f"RACING · race {self.stats.races} · place {res.place} · "
                    f"{self.stats.first_place_rate:.0%} firsts · {self.stats.gems} gems"
                )

    def _emit(self, event_type: str, level: Level = Level.INFO, **p: object) -> None:
        if self._bus is not None:
            self._bus.publish(Event(type=event_type, source="derby", level=level, payload=dict(p)))
