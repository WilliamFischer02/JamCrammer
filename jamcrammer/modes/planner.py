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
from typing import Literal, Protocol

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
BLUE_HSV = ((95, 100, 120), (125, 255, 255))  # the games menu's scrollbar arrow
ARROW_PATCH_PX = 12  # half-size of the patch sampled around each scrollbar arrow point
ARROW_MIN_FILL = 0.5  # share of that patch that must be blue (menu open: 0.92 / 1.00)
PRO_SEARCH_PX = 120  # look this far left and right of the guessed PRO point for green
PRO_MAX_WIDTH_PX = 260  # a green run wider than this is the PLAY! button
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


def _blue_fill(hsv: BgrImage, x: int, y: int) -> float:
    h, w = hsv.shape[:2]
    x0, x1 = max(0, x - ARROW_PATCH_PX), min(w, x + ARROW_PATCH_PX)
    y0, y1 = max(0, y - ARROW_PATCH_PX), min(h, y + ARROW_PATCH_PX)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    patch = hsv[y0:y1, x0:x1]
    lo, hi = BLUE_HSV
    mask = cv2.inRange(patch, np.array(lo, np.uint8), np.array(hi, np.uint8))
    return float(mask.mean() / 255.0)


def games_menu_open(image: BgrImage, profile: Profile) -> bool:
    """The games menu shows its blue scrollbar arrows: both the down arrow at
    ``games_scroll_down_point`` and the up arrow at ``games_scroll_up_point`` must be blue
    (one blue spot is not enough: the arena's floor and a join dialog paint the lower spot
    blue too, measured on the fixtures 2026-11-03)."""
    if not profile.has("games_scroll_down_point"):
        return False
    points = [profile.point("games_scroll_down_point")]
    if profile.has("games_scroll_up_point"):
        points.append(profile.point("games_scroll_up_point"))
    min_fill = (
        profile.number("games_arrow_min_fill")
        if profile.has("games_arrow_min_fill")
        else ARROW_MIN_FILL
    )
    hsv = np.asarray(cv2.cvtColor(image, cv2.COLOR_BGR2HSV), dtype=np.uint8)
    return all(_blue_fill(hsv, x, y) >= min_fill for x, y in points)


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
        self._sleep(self.cfg.tray_settle_s)  # the panel slides back before its X is in place
        if not self.watcher.ensure_closed():
            note += "; My Parties would not close"
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

    def games_menu_open(self, image: BgrImage | None = None) -> bool:
        """The games menu shows its blue scrollbar arrow at ``games_scroll_down_point``."""
        return games_menu_open(self._grab() if image is None else image, self.profile)

    def find_derby_tile_scrolling(self) -> tuple[int, int] | None:
        """Scroll the games list by its down arrow a few clicks at a time until the Jamaa
        Derby tile matches (a scrollbar drag depends on where the thumb is; the arrow does
        not)."""
        rounds = int(self.profile.number("games_scroll_rounds"))
        clicks = int(self.profile.number("games_scroll_clicks"))
        arrow = self.profile.point("games_scroll_down_point")
        if self.profile.has("games_scroll_up_point"):  # the list keeps its position: top first
            for _ in range(rounds):
                self._in.click(*self.profile.point("games_scroll_up_point"))
                self._sleep(0.1)
            self._sleep(0.5)
        for _round in range(rounds):
            tile = self.find_derby_tile(self._grab())
            if tile is not None:
                return tile
            for _ in range(clicks):
                self._in.click(*arrow)
                self._sleep(0.15)
            self._sleep(0.5)
        return self.find_derby_tile(self._grab())

    def open_derby(self, track_visible: Callable[[BgrImage], bool]) -> NavResult:
        """Games icon -> scroll until the Jamaa Derby tile shows -> tile -> PRO -> track."""
        for attempt in range(self.cfg.max_attempts):
            if not self.games_menu_open():
                self._in.click(*self.profile.point("games_icon_point"))
                self._sleep(self.cfg.games_settle_s)
            tile = self.find_derby_tile_scrolling()
            if tile is None:
                self._emit("planner.tile_missing", Level.NOTICE, attempt=attempt + 1)
                self._in.click(*self.profile.point("games_close_point"))
                self._sleep(self.cfg.games_settle_s)
                continue
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
                return self._nearest_green(np.asarray(hsv, dtype=np.uint8), px, py) or (px, py)
        return None

    def _nearest_green(self, hsv: BgrImage, px: int, py: int) -> tuple[int, int] | None:
        """The centre of the green run nearest to (px, py) along its row, looking right
        first (PRO sits right of PLAY!): the button's place differs a little with the
        window's geometry."""
        lo, hi = GREEN_HSV
        h, w = hsv.shape[:2]
        if not (0 <= py < h):
            return None
        row = np.asarray(hsv[py], dtype=np.int32)
        green = (row[:, 0] >= lo[0]) & (row[:, 0] <= hi[0]) & (row[:, 1] >= lo[1])
        for x in [
            *range(px, min(w, px + PRO_SEARCH_PX)),
            *range(px - 1, max(-1, px - PRO_SEARCH_PX), -1),
        ]:
            if 0 <= x < w and green[x]:
                a = b = x
                while a > 0 and green[a - 1]:
                    a -= 1
                while b < w - 1 and green[b + 1]:
                    b += 1
                if b - a > PRO_MAX_WIDTH_PX:  # the PLAY! button, not PRO
                    continue
                return ((a + b) // 2, py)
        return None

    def _emit(self, event_type: str, level: Level = Level.INFO, **p: object) -> None:
        if self._bus is not None:
            self._bus.publish(
                Event(type=event_type, source="planner", level=level, payload=dict(p))
            )


# --- the shared "Hunt Best Guess" orchestration -------------------------------------------------
#
# William (2026-11-03): "Hunt Best Guess" is a checkbox on every farmer bot. Checked, the bot
# reads when Best Guess is due from the party tray, farms its own game until ``leave_lead_s``
# before that, leaves to the den at a round boundary, joins Best Guess and hands over to the
# Best Guess Farmer. This is the old Den Planner, generalised: the bot plugs in through a
# ``FarmPlugin`` (enter / farm / can_pause_now / leave) and the plan owns the timing.

PlanOutcome = Literal["joined", "stopped", "failed", "farmed"]
ImagePredicate = Callable[[BgrImage], bool]
StopFn = Callable[[], bool]
HUNT_NOW = "hunt_now"


@dataclass
class FarmPlugin:
    """What a farmer bot gives the plan. Every callable is run on the mode thread."""

    name: str  # "farm", "adventure", "opportunist", "wait"
    in_game: ImagePredicate  # the game is on this frame (track, board, adventure ...)
    enter: Callable[[], NavResult]  # from the den: open the game; postcondition in_game
    farm: Callable[[StopFn], None]  # farm until the predicate says so (or the game vanished)
    can_pause_now: Callable[[], bool]  # a round boundary: the predicate may fire now
    interrupted: Callable[[], str]  # why farm() returned early ("" = the predicate)
    leave: Callable[[], NavResult]  # leave the game to the den; postcondition in_den


class BestGuessPlan:
    def __init__(
        self,
        cfg: PlannerConfig,
        nav: Navigator,
        *,
        eta_s: Callable[[], float | None],
        set_eta: Callable[[float | None, str], None],
        should_stop: StopFn,
        hunt: Callable[[], bool],  # scan My Parties until joined or stopped; True = joined
        status: Callable[[str], None] = lambda _s: None,
        bus: EventBus | None = None,
        blocked: StopFn = lambda: False,  # the watchdog or a pause holds the input
    ) -> None:
        self.cfg = cfg
        self.nav = nav
        self._eta_s = eta_s
        self._set_eta = set_eta
        self._should_stop = should_stop
        self._hunt = hunt
        self._status = status
        self._bus = bus
        self._blocked = blocked
        self.last_error = ""

    # --- predicates ---------------------------------------------------------------------------

    def due(self) -> bool:
        eta = self._eta_s()
        return eta is not None and eta <= self.cfg.leave_lead_s

    def _halt(self) -> bool:
        return self._should_stop() or self._blocked()

    # --- steps ----------------------------------------------------------------------------------

    def read_eta(self) -> str:
        """Read the tray. '' = carry on with the countdown (read, kept or defaulted);
        ``HUNT_NOW`` = Best Guess is already in My Parties."""
        self._status("PLANNER · reading the party tray")
        eta, note = self.nav.read_best_guess_eta()
        if eta is not None:
            self._set_eta(eta, note)
            self._emit("planner.eta_read", seconds=eta, note=note)
            return ""
        self._emit("planner.eta_unread", Level.NOTICE, message=note)
        if self.cfg.scan_when_unread and not self._halt():
            self._status("PLANNER · looking for Best Guess in My Parties")
            scan = self.nav.watcher.scan()
            if scan.target is not None:
                self._emit(
                    "planner.best_guess_showing",
                    Level.HIGH,
                    message="Best Guess is in My Parties now; joining it",
                )
                return HUNT_NOW
        if self._eta_s() is None:
            default_s = float(self.cfg.eta_default_min * 60)
            self._set_eta(default_s, "default")
            self._emit(
                "planner.eta_default",
                Level.NOTICE,
                seconds=default_s,
                message=f"tray unread; assuming Best Guess in {self.cfg.eta_default_min} min",
            )
        return ""

    def _enter(self, plugin: FarmPlugin) -> bool:
        self._status("PLANNER · going to the den")
        res = self.nav.go_to_den()
        self._emit("planner.den", Level.INFO if res.ok else Level.HIGH, message=res.note)
        if not res.ok:
            self.last_error = res.note
            return False
        if self._halt():
            return False
        self._status(f"PLANNER · opening {plugin.name}")
        res = plugin.enter()
        self._emit("planner.enter", Level.INFO if res.ok else Level.HIGH, message=res.note)
        if not res.ok:
            self.last_error = res.note
        return res.ok

    def farm_until_due(self, plugin: FarmPlugin) -> PlanOutcome | None:
        """Farm until ``due`` at a round boundary. None = due now and the game is paused at
        a boundary; otherwise the outcome that ends the plan."""
        for _n in range(self.cfg.reentries_max + 1):
            if not plugin.in_game(self.nav._grab()) and not self._enter(plugin):
                return "stopped" if self._halt() else "failed"
            self._status(f"PLANNER · farming {plugin.name} until Best Guess is due")
            plugin.farm(lambda: self.due() and plugin.can_pause_now())
            if self._halt():
                return "stopped"
            if self.due():
                return None
            reason = plugin.interrupted()
            self._emit("planner.farm_interrupted", Level.NOTICE, reason=reason)
            self._status("PLANNER · waiting for the game to come back")
            if self.nav._wait(plugin.in_game, self.cfg.ad_wait_s) is not None:
                continue  # an advert passed; the game is back
            self._status("PLANNER · back to the game")
        self._emit("planner.reentries_exhausted", Level.HIGH, message="the game kept closing")
        self.last_error = "the game kept closing"
        return "failed"

    def run(self, plugin: FarmPlugin) -> PlanOutcome:
        if not plugin.in_game(self.nav._grab()):
            if self.read_eta() == HUNT_NOW:
                return self._join()
            if self._halt():
                return "stopped"
        out = self.farm_until_due(plugin)
        if out is not None:
            return out
        self._status("PLANNER · Best Guess is due; leaving to the den")
        res = plugin.leave()
        self._emit("planner.leave", Level.INFO if res.ok else Level.HIGH, message=res.note)
        if not res.ok:
            self.last_error = res.note
            return "failed"
        self._emit("planner.left_game", Level.NOTICE, message="Best Guess is due; hunting it now")
        return self._join()

    def _join(self) -> PlanOutcome:
        self._status("PLANNER · hunting Best Guess in My Parties")
        if self._hunt():
            return "joined"
        return "stopped" if self._halt() else "failed"

    def _emit(self, event_type: str, level: Level = Level.INFO, **p: object) -> None:
        if self._bus is not None:
            self._bus.publish(
                Event(type=event_type, source="planner", level=level, payload=dict(p))
            )
