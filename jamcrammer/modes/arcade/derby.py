"""Jamaa Derby farmer (KICKOFF §9.3), rebuilt on William's 2026-10-26 recording.

The track scrolls right-to-left under a horse that never moves horizontally (its nose sits at
``horse_front_x``). Obstacles are full-height vertical bands in four colours: yellow hay, brown
mud, teal water, dark-green hedge / tall grass. The checkered finish stretch is also darker
green but only half of every column is dark, so the hedge class needs a higher column fill.
Space jumps; the rule is "press as the nose reaches the band's left edge", so the lead is the
scroll speed (estimated from the band's motion) times the capture+input latency. Three carrot
boosts sit in the HUD at the bottom; all three are used on clear stretches. Races end on a
"Next Race In N" leaderboard that restarts by itself, so nothing is clicked between races.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np
import numpy.typing as npt

from jamcrammer.core.config import DerbyConfig, Level
from jamcrammer.core.events import Event, EventBus
from jamcrammer.core.logging import get_logger
from jamcrammer.core.profiles import Profile
from jamcrammer.core.store import Store
from jamcrammer.modes.arcade.derby_learn import (
    BandRecord,
    LearnedDerby,
    StallMonitor,
    width_class,
)
from jamcrammer.platform.capture import Frame
from jamcrammer.vision.ocr import crop, ocr_image, parse_int

log = get_logger("derby")

BgrImage = npt.NDArray[np.uint8]
GrabFn = Callable[[], BgrImage]
HsvRange = tuple[tuple[int, int, int], tuple[int, int, int]]
Roi = tuple[int, int, int, int]
REQUIRED = (
    "strip_roi",
    "horse_front_x",
    "jump_trigger_x",
    "horse_box_roi",
    "boost_button_point",
    "boost_box_roi",
    "results_box_roi",
    "results_rows_roi",
    "startline_roi",
    "progress_bar_roi",
)
PLACE_WORDS = {"1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5, "6th": 6}
PAYOUT_NORMAL = {1: 100, 2: 75, 3: 50, 4: 40, 5: 30, 6: 25}
PAYOUT_PRO = {1: 150, 2: 125, 3: 100, 4: 75, 5: 50, 6: 25}
PRO_UNLOCK_WINS = 10
BAND_KINDS = ("hay", "mud", "water", "hedge")


class Screen(StrEnum):
    WAITING = "WAITING"  # start line on screen ("Waiting for other horses", "Set?", "GO!")
    RACING = "RACING"
    RESULTS = "RESULTS"  # "Next Race In N" leaderboard
    UNKNOWN = "UNKNOWN"  # not the Derby track at all


class Inputs(Protocol):
    def click(self, cx: int, cy: int) -> bool: ...

    def key_tap(self, key: str, hold_ms: int) -> None: ...


@dataclass(frozen=True)
class Band:
    kind: str
    left: int  # stage x of the left edge
    right: int

    @property
    def width(self) -> int:
        return self.right - self.left


def _mask(hsv: BgrImage, rng: HsvRange) -> npt.NDArray[np.bool_]:
    lo, hi = rng
    return np.asarray(cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8)) > 0)


def _runs(col: npt.NDArray[np.bool_], min_width: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    start: int | None = None
    for i, c in enumerate(col):
        if c and start is None:
            start = i
        elif not c and start is not None:
            if i - start >= min_width:
                out.append((start, i))
            start = None
    if start is not None and len(col) - start >= min_width:
        out.append((start, len(col)))
    return out


def _merge(runs: list[tuple[int, int]], gap: int) -> list[tuple[int, int]]:
    """Pieces of one band separated by a horse standing in front of it (gap < any real
    distance between two bands) become one run again."""
    out: list[tuple[int, int]] = []
    for a, b in runs:
        if out and a - out[-1][1] <= gap:
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


def _hysteresis(
    colfill: npt.NDArray[np.float64], seed: float, grow: float
) -> npt.NDArray[np.bool_]:
    """Columns at/above ``grow`` that touch a column at/above ``seed`` (tall grass has lighter
    stripes; the checkered finish never reaches ``seed`` and its columns are narrow)."""
    weak = colfill >= grow
    strong = colfill >= seed
    out = np.zeros_like(weak)
    start: int | None = None
    for i, w in enumerate(weak):
        if w and start is None:
            start = i
        elif not w and start is not None:
            if strong[start:i].any():
                out[start:i] = True
            start = None
    if start is not None and strong[start:].any():
        out[start:] = True
    return out


def default_ranges(cfg: DerbyConfig) -> dict[str, HsvRange]:
    return {"hay": cfg.hay_hsv, "mud": cfg.mud_hsv, "water": cfg.water_hsv, "hedge": cfg.hedge_hsv}


def detect_bands(
    strip: BgrImage,
    x0: int,
    cfg: DerbyConfig,
    ranges: dict[str, HsvRange] | None = None,
    hsv: BgrImage | None = None,
) -> list[Band]:
    """Obstacle bands in a horizontal strip of the track (``x0`` = the strip's stage x)."""
    rng_of = ranges or default_ranges(cfg)
    if hsv is None:
        hsv = np.asarray(cv2.cvtColor(strip, cv2.COLOR_BGR2HSV), dtype=np.uint8)
    bands: list[Band] = []
    for kind in ("hay", "mud", "water"):
        col = _mask(hsv, rng_of[kind]).mean(axis=0) >= cfg.band_fill
        runs = _merge(_runs(col, cfg.band_min_width_px), cfg.band_merge_gap_px)
        bands.extend(Band(kind, x0 + a, x0 + b) for a, b in runs)
    dark = _mask(hsv, rng_of["hedge"]).mean(axis=0)
    col = _hysteresis(dark, cfg.hedge_seed_fill, cfg.hedge_grow_fill)
    runs = _merge(_runs(col, cfg.hedge_min_width_px), cfg.band_merge_gap_px)
    # the checkered finish alternates dark and light columns: its runs are not dark on average
    runs = [(a, b) for a, b in runs if float(dark[a:b].mean()) >= cfg.hedge_mean_fill]
    bands.extend(Band("hedge", x0 + a, x0 + b) for a, b in runs)
    return sorted(bands, key=lambda b: b.left)


def fill(image: BgrImage, rng: HsvRange) -> float:
    hsv = np.asarray(cv2.cvtColor(image, cv2.COLOR_BGR2HSV), dtype=np.uint8)
    return float(_mask(hsv, rng).mean())


def horse_top(box: BgrImage, cfg: DerbyConfig, hay_overlap: bool = False) -> int | None:
    """Height of our horse inside its box, whatever its colour: the mean row of pixels that
    are neither grass nor an obstacle band. It drops by ~60 px while airborne. None when a
    band fills the box (nothing of the horse can be told apart) or too few pixels remain.
    Yellow counts as horse (William's yellow horse and the glowing horseshoe under it) unless
    a hay band is crossing the box, which is what ``hay_overlap`` says."""
    hsv = np.asarray(cv2.cvtColor(box, cv2.COLOR_BGR2HSV), dtype=np.uint8)
    scenery = _mask(hsv, cfg.grass_hsv)
    kinds = (cfg.mud_hsv, cfg.water_hsv, cfg.hedge_hsv) + ((cfg.hay_hsv,) if hay_overlap else ())
    for rng in kinds:
        scenery |= _mask(hsv, rng)
    if scenery.mean() > cfg.horse_box_max_fill:
        return None
    rider = ~scenery
    ys = np.where(rider)[0]
    if len(ys) < cfg.horse_min_px:
        return None
    return int(ys.mean())


def highlighted_row(rows_region: BgrImage, cfg: DerbyConfig) -> int | None:
    """y (within the region) of the yellow-framed leaderboard row, i.e. our own result."""
    hsv = np.asarray(cv2.cvtColor(rows_region, cv2.COLOR_BGR2HSV), dtype=np.uint8)
    yellow = _mask(hsv, cfg.highlight_hsv).mean(axis=1)
    # the frame's top and bottom edges are long yellow lines; rosettes and ribbons are not
    ys = np.where(yellow >= cfg.highlight_row_fill)[0]
    if len(ys) < cfg.highlight_min_rows or ys[-1] - ys[0] > cfg.highlight_max_height_px:
        return None
    return int((ys[0] + ys[-1]) // 2)


@dataclass
class Observation:
    screen: Screen
    bands: list[Band] = field(default_factory=list)
    horse_top: int | None = None
    carrots_left: bool = False
    strip: BgrImage | None = field(default=None, repr=False)
    strip_hsv: BgrImage | None = field(default=None, repr=False)


class DerbyDetector:
    """Screen classification + perception, profile driven (stage pixels)."""

    def __init__(
        self, profile: Profile, cfg: DerbyConfig, ranges: dict[str, HsvRange] | None = None
    ) -> None:
        self.profile = profile
        self.cfg = cfg
        self.ranges = ranges or default_ranges(cfg)

    def screen(self, image: BgrImage) -> Screen:
        p, c = self.profile, self.cfg
        if fill(crop(image, p.roi("progress_bar_roi")), c.progress_hsv) < c.progress_min_fill:
            return Screen.UNKNOWN
        if fill(crop(image, p.roi("results_box_roi")), c.beige_hsv) >= c.results_min_fill:
            return Screen.RESULTS
        line = crop(image, p.roi("startline_roi"))
        hsv = np.asarray(cv2.cvtColor(line, cv2.COLOR_BGR2HSV), dtype=np.uint8)
        white_cols = _mask(hsv, c.white_hsv).mean(axis=0) >= c.startline_col_fill
        if float(white_cols.mean()) >= c.startline_min_cols:
            return Screen.WAITING
        return Screen.RACING

    def observe(self, image: BgrImage) -> Observation:
        kind = self.screen(image)
        obs = Observation(kind)
        if kind != Screen.RACING:
            return obs
        p = self.profile
        sx, _sy, _sw, _sh = p.roi("strip_roi")
        obs.strip = crop(image, p.roi("strip_roi"))
        obs.strip_hsv = np.asarray(cv2.cvtColor(obs.strip, cv2.COLOR_BGR2HSV), dtype=np.uint8)
        obs.bands = detect_bands(obs.strip, sx, self.cfg, self.ranges, obs.strip_hsv)
        hx, _hy, hw, _hh = p.roi("horse_box_roi")
        hay_overlap = any(b.kind == "hay" and b.left < hx + hw and b.right > hx for b in obs.bands)
        obs.horse_top = horse_top(crop(image, p.roi("horse_box_roi")), self.cfg, hay_overlap)
        obs.carrots_left = (
            fill(crop(image, p.roi("boost_box_roi")), self.cfg.carrot_hsv)
            >= self.cfg.carrot_min_fill
        )
        return obs

    def detect(self, frame: Frame) -> str:
        """StateDetector protocol for the replay harness."""
        return str(self.screen(frame.image))


@dataclass
class RaceResult:
    started_ts: float
    duration_s: float = 0.0
    place: int | None = None
    gems: int | None = None
    jumps: int = 0
    retries: int = 0
    boosts: int = 0
    bands: int = 0
    hits: int = 0
    notes: str = ""


@dataclass
class DerbyStats:
    races: int = 0
    first_places: int = 0
    gems: int = 0
    jumps: int = 0
    hits: int = 0
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
            "hits": self.hits,
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
        detector: DerbyDetector | None = None,
        store: Store | None = None,
        bus: EventBus | None = None,
        ocr: Callable[[BgrImage], str] | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        learned_path: Path | None = None,
        hits_dir: Path | None = None,
    ) -> None:
        missing = profile.missing(list(REQUIRED))
        if missing:
            msg = f"jamaa_derby profile missing {missing}"
            raise ValueError(msg)
        self.profile = profile
        self.cfg = cfg
        self._in = inputs
        self._grab = grab
        self.learned = LearnedDerby.load(learned_path if cfg.learn_enabled else None)
        ranges = {k: self.learned.range_for(k, v) for k, v in default_ranges(cfg).items()}
        self.detector = detector or DerbyDetector(profile, cfg, ranges)
        self._hits_dir = hits_dir
        self._stall = StallMonitor(cfg)
        self._pending: BandRecord | None = None  # the band that just passed the hooves
        self._last_press: tuple[float, str, int] | None = None  # (ts, kind, edge) of our press
        self._tracked_colour: list[list[int]] | None = None
        self._tracked_crop: BgrImage | None = None
        self._boost_click_ts = -1e9
        self._store = store
        self._bus = bus
        self._ocr = ocr or (lambda img: ocr_image(img, upscale=cfg.ocr_upscale).text)
        self._clock = clock
        self._sleep = sleep
        self.stats = DerbyStats()
        self.stats.pro = (
            bool(profile.number("pro_unlocked")) if profile.has("pro_unlocked") else False
        )
        self.state = Screen.UNKNOWN
        self.paused_reason = ""
        self._race: RaceResult | None = None
        self._results_seen_ts: float | None = None
        self._results_read = False
        self._unknown_since: float | None = None
        self._last_jump = -1e9
        self._jump_retry_pending = False
        self._jump_baseline: int | None = None
        self._last_boost = -1e9
        self._boosts_left = 0
        self._speed = float(profile.number("scroll_px_s"))
        self._tracked: tuple[float, Band] | None = None  # last sighting of the nearest band
        self._tracked_kind: tuple[str, int] | None = None
        self._pending_crop: BgrImage | None = None
        self._ticks = 0
        self._frame_age = 0.0
        self._last_tick_ts: float | None = None
        self.period_s = 1.0 / cfg.fps  # measured loop period (capture + processing + sleep)
        self._obs_since_jump = 0
        self._first_seen: tuple[float, int] | None = None  # (ts, left) when tracking began

    @property
    def screen_known(self) -> bool:
        """The last frame was the Derby track, line or leaderboard (watchdog hint); before
        the first observation the mode is given the benefit of the doubt."""
        return self._ticks == 0 or self.state != Screen.UNKNOWN

    # --- one tick ---------------------------------------------------------------------------------

    def step(self) -> Observation:
        t_grab = self._clock()
        image = self._grab()
        now = self._clock()
        self._frame_age = now - t_grab  # the pixels are this old by the time we look at them
        if self._last_tick_ts is not None and now > self._last_tick_ts:
            dt = min(now - self._last_tick_ts, self.cfg.period_max_s)
            self.period_s = (1 - self.cfg.period_alpha) * self.period_s + self.cfg.period_alpha * dt
        self._last_tick_ts = now
        self._ticks += 1
        obs = self.detector.observe(image)
        if obs.screen == Screen.UNKNOWN:
            self._on_unknown(now)
            return obs
        self._unknown_since = None
        if obs.screen == Screen.RESULTS:
            self._on_results(image, now)
        elif obs.screen == Screen.WAITING:
            self._on_waiting(now)
        else:
            self._on_racing(obs, now)
        self.state = obs.screen
        return obs

    def run(
        self, should_stop: Callable[[], bool], status: Callable[[str], None] | None = None
    ) -> None:
        period = 1.0 / self.cfg.fps
        while not should_stop() and not self.paused_reason:
            t0 = self._clock()
            self.step()
            if status is not None:
                s = self.stats
                status(
                    f"{self.state} · race {s.races + (self._race is not None)} · "
                    f"{s.first_places}/{s.races} firsts · {s.gems} gems · "
                    f"{s.hits} hits · {self._speed:.0f} px/s · {self.period_s * 1000:.0f} ms/tick"
                )
            self._sleep(max(0.0, period - (self._clock() - t0)))

    # --- screens ----------------------------------------------------------------------------------

    def _on_unknown(self, now: float) -> None:
        if self._unknown_since is None:
            self._unknown_since = now
            self._emit("derby.unknown", Level.NOTICE)
        elif now - self._unknown_since >= self.cfg.unknown_pause_s:
            self.paused_reason = "unknown_screen"
            self._emit(
                "derby.paused",
                Level.HIGH,
                message=f"Derby track not seen for {self.cfg.unknown_pause_s:.0f}s; paused.",
            )

    def _on_waiting(self, now: float) -> None:
        if self._race is None or self.state == Screen.RESULTS:
            self._new_race(now)

    def _new_race(self, now: float) -> RaceResult:
        self._race = RaceResult(started_ts=now)
        self._boosts_left = self.cfg.boosts_per_race
        self._last_jump = -1e9
        self._last_boost = -1e9
        self._tracked = None
        self._first_seen = None
        self._stall.reset()
        self._pending = None
        self._last_press = None
        self._jump_retry_pending = False
        self._results_seen_ts = None
        self._results_read = False
        self._emit("derby.race_start")
        return self._race

    def _on_results(self, image: BgrImage, now: float) -> None:
        if self._results_seen_ts is None:
            self._results_seen_ts = now
        if self._results_read or now - self._results_seen_ts < self.cfg.results_settle_s:
            return
        self._results_read = True
        if self._race is None:
            self._race = RaceResult(started_ts=now, notes="joined mid-race")
        res = self._race
        res.place, res.gems = self.read_results(image)
        if res.gems is None and res.place is not None:
            res.gems = (PAYOUT_PRO if self.stats.pro else PAYOUT_NORMAL).get(res.place)
        self._finish(res)
        self._race = None

    def read_results(self, image: BgrImage) -> tuple[int | None, int | None]:
        """Our row is the yellow-framed one; OCR it for the place word and the gem count.
        Without OCR the row index gives the place."""
        p = self.profile
        rx, ry, rw, rh = p.roi("results_rows_roi")
        cy = highlighted_row(crop(image, (rx, ry, rw, rh)), self.cfg)
        if cy is None:
            return (None, None)
        half = int(p.number("results_row_h")) // 2
        y0 = max(0, ry + cy - half)
        text = self._ocr(crop(image, (rx, y0, rw, 2 * half))).lower()
        place = next((v for w, v in PLACE_WORDS.items() if w in text), None)
        gems_w = int(p.number("results_gems_w"))
        gems_text = self._ocr(crop(image, (rx + rw, y0, gems_w, 2 * half)))
        gems = parse_int(gems_text) if "gem" in gems_text.lower() else None
        if place is None:
            row = (cy - p.number("results_first_row_cy")) / p.number("results_row_pitch")
            place = min(6, max(1, 1 + round(row)))
        return (place, gems)

    # --- racing -----------------------------------------------------------------------------------

    def _on_racing(self, obs: Observation, now: float) -> None:
        res = self._race
        if res is None:
            res = self._new_race(now)
            res.notes = "joined mid-race"
        front = int(self.profile.number("jump_trigger_x"))  # the hooves: where the game judges
        ahead = [b for b in obs.bands if b.right > front]
        nearest = self._track(min(ahead, key=lambda b: b.left, default=None), now)
        self._remember_colour(obs, nearest, front)
        self._judge_passing(obs, nearest, front, now)
        self._check_jump_result(obs, now)
        if nearest is not None and nearest.left > front - self.cfg.band_pass_px:
            # where the edge is *now* (the frame is frame_age old) and when it reaches the hooves
            edge_now = nearest.left - self._speed * self._frame_age
            lead_px, lead_s = self._lead_for(nearest, now)
            gap = edge_now - front - lead_px
            t_hit = gap / self._speed - lead_s  # minus input latency
            # press if it gets there before we can look again; wait out the remainder so the
            # press lands at the edge instead of up to one loop period early
            if t_hit <= self.period_s and now - self._last_jump >= self.profile.number(
                "jump_cooldown_s"
            ):
                if t_hit > 0:
                    self._sleep(min(t_hit, self.cfg.period_max_s))
                self._jump(obs, self._clock(), nearest, res)
                return
        clear = nearest is None or (nearest.left - front) > self.profile.number("boost_clear_px")
        if (
            clear
            and self._boosts_left > 0
            and obs.carrots_left
            and now - self._last_boost >= self.cfg.boost_gap_s
            and now - self._last_jump >= self.cfg.boost_after_jump_s
            and now - res.started_ts >= self.cfg.boost_earliest_s
        ):
            bx, by = self.profile.point("boost_button_point")
            self._in.click(bx, by)
            self._boosts_left -= 1
            self._last_boost = now
            self._boost_click_ts = now
            res.boosts += 1
            self._emit("derby.boost", left=self._boosts_left)

    # --- timing policy and learning --------------------------------------------------------------

    def _boosted(self, now: float) -> bool:
        nominal = self.profile.number("scroll_px_s")
        return (
            self._speed >= self.cfg.boost_speed_ratio * nominal
            or now - self._boost_click_ts <= self.cfg.boost_effect_s
        )

    def _lead_for(self, band: Band, now: float) -> tuple[float, float]:
        """(extra pixels, extra seconds) of lead for this band: wide bands are jumped right at
        the edge; one- and two-column bands a little earlier so the horse has landed before the
        next one; a boosted horse earlier still; plus whatever the learner adjusted."""
        p = self.profile
        wclass = width_class(band.width, self.cfg.column_px)
        lead_px = p.number("jump_lead_px") + self.learned.lead_for(band.kind, wclass)
        if wclass != "3col":
            lead_px += p.number("jump_lead_px_narrow")
        lead_s = p.number("jump_lead_s") + (p.number("boost_lead_s") if self._boosted(now) else 0.0)
        return (lead_px, lead_s)

    def _remember_colour(self, obs: Observation, band: Band | None, front: int) -> None:
        """Keep the colour (and a crop) of the band we are approaching while it is whole."""
        if band is None or obs.strip_hsv is None or obs.strip is None:
            return
        sx, _sy, sw, _sh = self.profile.roi("strip_roi")
        if band.left <= front + 50 or band.right >= sx + sw - 5:
            return
        if self._tracked_colour is not None and self._tracked_kind == (band.kind, band.width):
            return
        a, b = band.left - sx + 8, band.right - sx - 8
        if b - a < 20:
            return
        px = obs.strip_hsv[:, a:b].reshape(-1, 3)
        self._tracked_colour = [[int(v) for v in np.percentile(px, q, axis=0)] for q in (5, 50, 95)]
        self._tracked_crop = obs.strip[:, max(0, a - 8) : b + 8].copy()
        self._tracked_kind = (band.kind, band.width)

    def _judge_passing(
        self, obs: Observation, nearest: Band | None, front: int, now: float
    ) -> None:
        """When a band's edge crosses the hooves open a record; while it passes watch for a
        stall; close the record as hit or cleared."""
        c = self.cfg
        if nearest is not None and nearest.left <= front and self._pending is None:
            press = self._last_press
            use_press = press is not None and now - press[0] <= c.press_match_s
            self._pending = BandRecord(
                ts_pass=now,
                kind=nearest.kind,
                width=nearest.width,
                wclass=width_class(nearest.width, c.column_px),
                press_edge=press[2] if use_press and press is not None else None,
                press_ts=press[0] if use_press and press is not None else None,
                speed=round(self._speed),
                boosted=self._boosted(now),
                colour=self._tracked_colour,
            )
            self._pending_crop = self._tracked_crop
            self._tracked_colour = None
            self._tracked_crop = None
            self._stall.reset()
        rec = self._pending
        if rec is None:
            return
        passing = [b for b in obs.bands if b.left <= front < b.right + c.band_pass_px]
        edge = max((b.right for b in passing), default=None)
        sx, _sy, sw, _sh = self.profile.roi("strip_roi")
        if edge is not None and edge >= sx + sw - 5:
            edge = None  # clipped by the strip: no speed from it
        stalled = self._stall.update(now, edge, self.profile.number("scroll_px_s"))
        if stalled:
            rec.outcome = "hit"
            self._close_record(rec, now)
        elif now - rec.ts_pass > c.hit_window_s:
            rec.outcome = "cleared"
            self._close_record(rec, now)

    def _close_record(self, rec: BandRecord, now: float) -> None:
        self._pending = None
        trigger = int(self.profile.number("jump_trigger_x"))
        default = default_ranges(self.cfg)[rec.kind]
        note = self.learned.note(rec, self.cfg, trigger, default) if self.cfg.learn_enabled else ""
        if rec.outcome == "hit":
            self.stats.hits += 1
            if self._race is not None:
                self._race.hits += 1
            if self.learned.ranges.get(rec.kind):
                self.detector.ranges[rec.kind] = self.learned.range_for(rec.kind, default)
            shot = self._save_hit_crop(rec, now)
            self._emit(
                "derby.hit",
                Level.NOTICE,
                kind=rec.kind,
                width=rec.width,
                wclass=rec.wclass,
                press_edge=rec.press_edge,
                boosted=rec.boosted,
                learned=note,
                crop=str(shot) if shot else None,
            )
            if note:
                self._emit("derby.learned", change=note, kind=rec.kind, wclass=rec.wclass)

    def _save_hit_crop(self, rec: BandRecord, now: float) -> Path | None:
        crop_img = getattr(self, "_pending_crop", None)
        if self._hits_dir is None or crop_img is None:
            return None
        self._hits_dir.mkdir(parents=True, exist_ok=True)
        path = self._hits_dir / f"{time.strftime('%Y%m%d_%H%M%S')}_{rec.kind}_{rec.width}.png"
        cv2.imwrite(str(path), crop_img)
        return path

    def _track(self, seen: Band | None, now: float) -> Band | None:
        """Follow the nearest band between frames: update the scroll-speed estimate while it
        moves left, and when the stacked horses hide its edge (it stalls or vanishes while
        it should still be ahead) fall back to the edge predicted from the last sighting."""
        c = self.cfg
        if self._tracked is not None:
            ts, last = self._tracked
            dt = now - ts
            predicted = round(last.left - self._speed * dt)
            stale = dt > c.track_hold_s
            if seen is not None and last.left - seen.left > 0 and dt <= c.speed_max_dt_s:
                self._tracked = (now, seen)
                if self._first_seen is None:
                    self._first_seen = (ts, last.left)
                t0, x0 = self._first_seen
                if now - t0 >= c.speed_min_baseline_s and x0 - seen.left < c.speed_max_step_px * 8:
                    v = (x0 - seen.left) / (now - t0)
                    v = min(max(v, c.speed_min_px_s), c.speed_max_px_s)
                    self._speed = (1 - c.speed_alpha) * self._speed + c.speed_alpha * v
                return seen
            if seen is not None and seen.left > last.left + c.track_new_band_px:
                self._tracked = (now, seen)  # a new band further right
                self._first_seen = (now, seen.left)
                return seen
            occluded = seen is None or seen.left > predicted + c.track_stuck_px
            if occluded and not stale and predicted + last.width > 0:
                return Band(last.kind, predicted, predicted + last.width)
            if seen is not None:
                self._tracked = (now, seen)
            elif stale:
                self._tracked = None
            return seen
        if seen is not None:
            self._tracked = (now, seen)
            self._first_seen = (now, seen.left)
        return seen

    def _jump(self, obs: Observation, now: float, band: Band, res: RaceResult) -> None:
        self._in.key_tap(self.cfg.jump_key, self.cfg.jump_hold_ms)
        self._last_jump = now
        age = now - self._last_tick_ts if self._last_tick_ts else 0.0
        edge_at_press = round(band.left - self._speed * age)
        self._last_press = (now, band.kind, int(edge_at_press))
        self._jump_baseline = obs.horse_top
        self._jump_retry_pending = True
        self._obs_since_jump = 0
        res.jumps += 1
        res.bands += 1
        self.stats.jumps += 1
        self._emit(
            "derby.jump",
            kind=band.kind,
            edge=band.left,
            edge_at_press=int(edge_at_press),
            width=band.width,
            wclass=width_class(band.width, self.cfg.column_px),
            speed=round(self._speed),
            boosted=self._boosted(now),
        )

    def _check_jump_result(self, obs: Observation, now: float) -> None:
        """Postcondition: the horse rises after a press; if it has not within jump_retry_s,
        press once more (a swallowed key is the usual cause)."""
        if not self._jump_retry_pending:
            return
        self._obs_since_jump += 1
        if (
            obs.horse_top is not None
            and self._jump_baseline is not None
            and self._jump_baseline - obs.horse_top >= self.cfg.jump_rise_px
        ):
            self._jump_retry_pending = False
            return
        if (
            now - self._last_jump >= self.cfg.jump_retry_s
            and self._obs_since_jump >= self.cfg.jump_retry_min_obs
        ):
            self._jump_retry_pending = False
            if self._race is not None and (obs.horse_top is None or self._jump_baseline is None):
                return  # could not tell (mud band through the box): no blind retry
            self._in.key_tap(self.cfg.jump_key, self.cfg.jump_hold_ms)
            if self._race is not None:
                self._race.retries += 1
            self._emit("derby.jump_retry", Level.NOTICE)

    # --- bookkeeping ------------------------------------------------------------------------------

    def _finish(self, res: RaceResult) -> RaceResult:
        res.duration_s = round(self._clock() - res.started_ts, 2)
        self.stats.races += 1
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
                mismatches=res.retries,
                notes=(
                    f"place={res.place} jumps={res.jumps} boosts={res.boosts} "
                    f"bands={res.bands} {res.notes}"
                ).strip(),
            )
        self._emit(
            "derby.race",
            place=res.place,
            gems=res.gems,
            jumps=res.jumps,
            hits=res.hits,
            boosts=res.boosts,
            duration_s=res.duration_s,
        )
        return res

    def _emit(self, event_type: str, level: Level = Level.INFO, **p: object) -> None:
        if self._bus is not None:
            self._bus.publish(Event(type=event_type, source="derby", level=level, payload=dict(p)))
