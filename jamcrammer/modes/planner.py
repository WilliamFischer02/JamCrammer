"""Den planner (William, 2026-10-27): read when Best Guess is due from the Upcoming Parties
tray, make sure we are in the den, open the games menu, scroll halfway, open Jamaa Derby in
PRO mode, farm it until Best Guess is due, leave, then hunt Best Guess in My Parties.

Everything here is screen-only navigation in stage pixels from ``profiles/navigation.yaml``:
OCR for the room name and the tray times, template matching for the Best Guess tray banner
and the Jamaa Derby tile, an OCR anchor ("Play to earn Gems") for the PRO button. Every click
has a postcondition and a timeout; failures return False and the engine reports them.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np
import numpy.typing as npt
from rapidfuzz import fuzz

from jamcrammer.core.config import Level, PlannerConfig
from jamcrammer.core.events import Event, EventBus
from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import Profile
from jamcrammer.modes.party_watcher import PartyWatcher, multiscale_match
from jamcrammer.vision.ocr import crop, ocr_image

BgrImage = npt.NDArray[np.uint8]
GrabFn = Callable[[], BgrImage]
GREEN_HSV = ((35, 90, 70), (75, 255, 255))
_HRS = re.compile(r"(\d+)\s*(?:hrs?|hours?)", re.IGNORECASE)
_MINS = re.compile(r"(\d+)\s*(?:mins?|minutes?)", re.IGNORECASE)


class Inputs(Protocol):
    def click(self, cx: int, cy: int) -> bool: ...

    def drag(self, x0: int, y0: int, x1: int, y1: int) -> bool: ...


def parse_eta(text: str) -> int | None:
    """'43 minutes' -> 2580, '1 hr 13 mins' -> 4380, 'GO NOW!' -> 0, else None."""
    low = text.lower()
    if "go now" in low:
        return 0
    hrs = _HRS.search(low)
    mins = _MINS.search(low)
    if hrs is None and mins is None:
        return None
    return int(hrs.group(1) if hrs else 0) * 3600 + int(mins.group(1) if mins else 0) * 60


def format_eta(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    seconds = max(0, int(seconds))
    return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m {seconds % 60:02d}s"


@dataclass
class NavResult:
    ok: bool
    note: str = ""


class Navigator:
    def __init__(  # noqa: PLR0917 - the navigator's collaborators
        self,
        profile: Profile,
        cfg: PlannerConfig,
        inputs: Inputs,
        grab: GrabFn,
        watcher: PartyWatcher,
        paths: Paths | None = None,
        *,
        bus: EventBus | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.profile = profile
        self.cfg = cfg
        self._in = inputs
        self._grab = grab
        self.watcher = watcher
        self._paths = paths or Paths()
        self._bus = bus
        self._clock = clock
        self._sleep = sleep
        self._tray_banner = self._template("tray_banner_template")
        self._derby_tile = self._template("derby_tile_template")

    def _template(self, key: str) -> BgrImage | None:
        if not self.profile.has(key):
            return None
        img = cv2.imread(str(self.profile.template_path(key, self._paths)), cv2.IMREAD_COLOR)
        return None if img is None else np.asarray(img, dtype=np.uint8)

    @property
    def scales(self) -> list[float]:
        return [float(s) for s in self.profile.items("template_scales")]

    def _wait(self, predicate: Callable[[BgrImage], bool], timeout_s: float) -> BgrImage | None:
        deadline = self._clock() + timeout_s
        while self._clock() < deadline:
            image = self._grab()
            if predicate(image):
                return image
            self._sleep(0.3)
        return None

    # --- room -------------------------------------------------------------------------------------

    def room_name(self, image: BgrImage | None = None) -> str:
        image = self._grab() if image is None else image
        return ocr_image(crop(image, self.profile.roi("room_name_roi")), upscale=2.0).text.strip()

    def in_den(self, image: BgrImage | None = None) -> bool:
        name = self.room_name(image).lower()
        return "den" in name and fuzz.partial_ratio("s den", name) >= 80

    def go_to_den(self) -> NavResult:
        if self.in_den():
            return NavResult(True, "already in the den")
        for attempt in range(self.cfg.max_attempts):
            self._in.click(*self.profile.point("den_button_point"))
            if self._wait(self.in_den, self.cfg.den_timeout_s) is not None:
                return NavResult(True, f"den reached (attempt {attempt + 1})")
            self._emit("planner.den_retry", Level.NOTICE, attempt=attempt + 1)
        return NavResult(False, "den button did not bring us home")

    # --- party tray: when is Best Guess due? ------------------------------------------------------

    def read_best_guess_eta(self) -> tuple[int | None, str]:
        """Open My Parties, open the tray, find the Best Guess row, read its time; tidy up."""
        if not self.watcher.ensure_closed():
            return (None, "My Parties would not close")
        if self.watcher.open_panel() is None:
            return (None, "My Parties did not open")
        self._sleep(self.cfg.panel_settle_s)
        self._in.click(*self.profile.point("tray_toggle_point"))
        self._sleep(self.cfg.tray_settle_s)
        image = self._grab()
        eta, note = self.eta_from_tray(image)
        self._in.click(*self.profile.point("tray_close_point"))
        self._sleep(0.5)
        self.watcher.ensure_closed()
        return (eta, note)

    def eta_from_tray(self, image: BgrImage) -> tuple[int | None, str]:
        if self._tray_banner is None:
            return (None, "no tray banner template")
        thr = self.profile.number("template_threshold")
        score, box, _ = multiscale_match(image, self._tray_banner, self.scales, thr)
        if box is None:
            lx, ly, lw, lh = self.profile.roi("tray_list_roi")
            text = ocr_image(crop(image, (lx, ly, lw, lh)), upscale=1.5).text
            return (
                None,
                f"Best Guess banner not in the tray (score {score:.2f}); tray says {text[:60]!r}",
            )
        x, y, w, h = box
        time_box = (x + int(w * 0.55), y, int(w * 0.45), h)
        text = ocr_image(crop(image, time_box), upscale=2.0).text
        eta = parse_eta(text)
        if eta is None:
            return (None, f"Best Guess row found but its time reads {text!r}")
        return (eta, f"tray says {text.strip()!r}")

    # --- games menu -> Jamaa Derby (PRO) ----------------------------------------------------------

    def open_derby(self, track_visible: Callable[[BgrImage], bool]) -> NavResult:
        """Games icon -> scroll halfway -> Jamaa Derby tile -> PRO -> the track is on screen."""
        for attempt in range(self.cfg.max_attempts):
            self._in.click(*self.profile.point("games_icon_point"))
            self._sleep(self.cfg.games_settle_s)
            self._in.drag(
                *self.profile.point("scroll_thumb_top_point"),
                *self.profile.point("scroll_mid_point"),
            )
            self._sleep(0.6)
            image = self._grab()
            tile = self.find_derby_tile(image)
            if tile is None:
                self._emit("planner.tile_missing", Level.NOTICE, attempt=attempt + 1)
                tile = self.profile.point("derby_tile_fallback_point")
            self._in.click(*tile)
            self._sleep(self.cfg.games_settle_s)
            pro = self.find_pro_button(self._grab())
            if pro is None:
                self._emit("planner.pro_missing", Level.NOTICE, attempt=attempt + 1)
                continue
            self._in.click(*pro)
            if self._wait(track_visible, self.cfg.derby_timeout_s) is not None:
                return NavResult(True, f"Derby PRO opened (attempt {attempt + 1})")
        return NavResult(False, "could not open Jamaa Derby from the games menu")

    def find_derby_tile(self, image: BgrImage) -> tuple[int, int] | None:
        if self._derby_tile is None:
            return None
        thr = self.profile.number("template_threshold")
        _score, box, _ = multiscale_match(image, self._derby_tile, self.scales, thr)
        if box is None:
            return None
        return (box[0] + box[2] // 2, box[1] + box[3] // 2)

    def find_pro_button(self, image: BgrImage) -> tuple[int, int] | None:
        """PRO sits right of PLAY! at a fixed offset from the 'Play to earn Gems' line."""
        res = ocr_image(image, upscale=1.5)
        for text, (x, y, w, h) in res.boxes_in_source():
            low = text.lower()
            if "earn" in low and fuzz.partial_ratio("play to earn gems", low) >= 70:
                dx, dy = (int(v) for v in self.profile.items("pro_offset"))
                px, py = x + w // 2 + dx, y + h // 2 + dy
                hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
                hh, ss, _vv = (
                    int(v) for v in hsv[min(py, hsv.shape[0] - 1), min(px, hsv.shape[1] - 1)]
                )
                lo, hi = GREEN_HSV
                if lo[0] <= hh <= hi[0] and ss >= lo[1]:
                    return (px, py)
                self._emit("planner.pro_not_green", Level.NOTICE, x=px, y=py, h=hh, s=ss)
                return (px, py)  # still the best guess at where PRO is
        return None

    def _emit(self, event_type: str, level: Level = Level.INFO, **p: object) -> None:
        if self._bus is not None:
            self._bus.publish(
                Event(type=event_type, source="planner", level=level, payload=dict(p))
            )
