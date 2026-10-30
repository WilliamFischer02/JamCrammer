"""From any room into the adventure, by menu state rather than by replayed clicks: the state
is read from the words on screen (full-frame OCR), each click must bring the next panel
within ``menu_step_s``, and the dialog closer is never used here (the Join/Host dialog has
the same blue X as Cosmo's, which is how the first supervised run closed its own menu).

States: room -> world_map -> adventures (scroll until the entry shows) -> adventure_dialog
(Join / Host) -> difficulty (Normal / Hard) -> lobby (PLAY) -> loading -> in_adventure.
Checked on both recordings: every state is read where it shows, with a one-frame "room"
while a panel slides in."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt
from rapidfuzz import fuzz

from jamcrammer.core.config import Level, LevelConfig
from jamcrammer.core.events import Event, EventBus
from jamcrammer.core.profiles import Profile
from jamcrammer.modes.adventures.level import Level as LevelModel
from jamcrammer.vision.ocr import OcrResult

BgrImage = npt.NDArray[np.uint8]
Word = tuple[str, int, int]  # text (lower), centre x, centre y
LOADING_MAX_MEAN = 12.0  # a black loading screen
ENTRY_FUZZY = 75  # the adventure's name in the list (OCR reads "IWJSterY guow")
WORD_FUZZY = 80
HOST_FUZZY = 70  # "NOSt"
TOP_Y = 110  # panel headers
DIALOG_HEADER_Y = (330, 440)  # the Adventure (Join / Host) dialog's header
TRANSIENT_S = 0.25  # a state must hold this long before it is acted on (panels slide in)
NAMES = {"mystery_below": "the mystery below"}


@dataclass
class Seen:
    state: str
    entry: tuple[int, int] | None = None  # the adventure's list entry, when listed


def words_of(res: OcrResult) -> list[Word]:
    out: list[Word] = []
    for line in res.lines:
        for w in line.words:
            x, y, bw, bh = w.box
            out.append(
                (w.text.lower(), int((x + bw / 2) / res.scale), int((y + bh / 2) / res.scale))
            )
    return out


def _has(
    ws: list[Word],
    word: str,
    where: Callable[[int, int], bool] = lambda x, y: True,
    fuzzy: int = WORD_FUZZY,
) -> bool:
    return any(fuzz.ratio(t, word) >= fuzzy and where(x, y) for t, x, y in ws)


def _mid(x: int, y: int) -> bool:
    return DIALOG_HEADER_Y[0] <= y <= DIALOG_HEADER_Y[1] and 700 <= x <= 1220


def _entry(ws: list[Word], prof: Profile, target: str) -> tuple[int, int] | None:
    lx, ly, lw, lh = prof.roi("menu_list_roi")
    name = NAMES.get(target, target.replace("_", " ")).split()
    key = name[1] if len(name) > 1 else name[0]  # "mystery": the one word OCR gets right
    for t, x, y in ws:
        inside = lx <= x < lx + lw and ly <= y < ly + lh and len(t) >= 4
        if inside and fuzz.partial_ratio(t, key) >= ENTRY_FUZZY:
            return (x, y)
    return None


def classify(frame: BgrImage, res: OcrResult, lvl: LevelModel, prof: Profile) -> Seen:
    """What menu the screen shows (see the module doc)."""
    if float(frame.mean()) < LOADING_MAX_MEAN:
        return Seen("loading")
    ws = words_of(res)
    bx, by, bw, bh = prof.roi("banner_roi")
    banner = " ".join(t for t, x, y in ws if bx <= x < bx + bw and by <= y < by + bh)
    top = [w for w in ws if w[2] < TOP_Y]
    state = "room"
    if lvl.phase_of(banner) is not None:
        state = "in_adventure"
    elif _has(ws, "difficulty") or (_has(ws, "normal") and _has(ws, "hard")):
        state = "difficulty"
    elif _has(ws, "host", fuzzy=HOST_FUZZY) or _has(ws, "join") or _has(ws, "adventure", _mid):
        state = "adventure_dialog"  # OCR reads Host as "NOSt"; the dialog's header is mid-screen
    elif _has(top, "mystery") or _has(ws, "play", lambda x, y: y > prof.point("menu_play")[1] - 80):
        state = "lobby"  # the lobby panel is headed by the adventure's name; PLAY reads badly
    elif _has(ws, "adventures", lambda x, y: x < 700 and y < 220):
        return Seen("adventures", _entry(ws, prof, lvl.target))
    elif _has(top, "world") and _has(top, "map"):
        state = "world_map"
    return Seen(state)


class Opening:
    """Drives the menus; ``run`` returns True once the adventure's first banner shows."""

    def __init__(
        self,
        lvl: LevelModel,
        prof: Profile,
        cfg: LevelConfig,
        *,
        click: Callable[[int, int], bool],
        grab: Callable[[], BgrImage],
        ocr: Callable[[BgrImage], OcrResult],
        bus: EventBus | None = None,
        clock: Callable[[], float],
        sleep: Callable[[float], None],
        poll_s: float = 0.4,
        shots: Path | None = None,
    ) -> None:
        """``shots``: a folder for a screenshot of every state change (what the menus looked
        like when it went wrong)."""
        self.lvl, self.prof, self.cfg = lvl, prof, cfg
        self._click, self._grab, self._ocr = click, grab, ocr
        self._bus, self._clock, self._sleep, self._poll = bus, clock, sleep, poll_s
        self._shots = shots
        self.steps: list[str] = []
        self._frame: BgrImage | None = None

    def see(self) -> Seen:
        self._frame = frame = self._grab()
        return classify(frame, self._ocr(frame), self.lvl, self.prof)

    def _shot(self, state: str) -> None:
        if self._shots is None or self._frame is None:
            return
        try:
            self._shots.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            name = f"{stamp}_{len(self.steps):02d}_{state}.jpg"
            cv2.imwrite(str(self._shots / name), self._frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        except OSError:
            pass

    def run(self, stop: Callable[[], bool]) -> bool:
        c = self.cfg
        tries: dict[str, int] = {}
        deadline = self._clock() + c.menu_step_s * (c.menu_retries + 1) * 6 + c.load_max_s
        last = ""
        played_at: float | None = None
        while not stop() and self._clock() < deadline:
            seen = self.see()
            if seen.state != last:
                self.steps.append(seen.state)
                self._shot(seen.state)
                self._emit("adventure.menu", state=seen.state)
                last = seen.state
            if seen.state == "in_adventure":
                return True
            loading = played_at is not None and self._clock() - played_at < c.load_max_s
            if seen.state == "loading" or (loading and seen.state != "lobby"):
                self._sleep(self._poll)  # the splash after PLAY reads as a room: never click it
                continue
            # a panel sliding in or away reads as a room for a moment: a room must hold
            # longer than other states before the globe is clicked (a stray globe click
            # closes the panel that was there)
            self._sleep(c.room_hold_s if seen.state == "room" else TRANSIENT_S)
            if self.see().state != seen.state:
                continue
            n = tries.get(seen.state, 0)
            if n > c.menu_retries:
                self._emit(
                    "adventure.menu_stuck",
                    Level.HIGH,
                    state=seen.state,
                    message=f"The menus do not move on from {seen.state}: please take over",
                )
                return False
            tries[seen.state] = n + 1
            self._act(seen)
            if seen.state == "lobby":
                played_at = self._clock()
            if self._wait_change(seen.state, stop):
                tries[seen.state] = 0  # it worked; a later return to this state starts afresh
        return False

    def _act(self, seen: Seen) -> None:
        c, p = self.cfg, self.prof
        if seen.state == "adventures":
            if seen.entry is not None:
                self._click(*seen.entry)
            else:  # scroll by the arrow, a few clicks per look: the thumb's place varies and
                # a drag released elsewhere lands on whatever is there (a lobby animal slot)
                for _ in range(c.list_clicks):
                    self._click(*p.point("menu_list_down"))
                    self._sleep(TRANSIENT_S)
            return
        points = {
            "room": "menu_globe",
            "world_map": "menu_adventures",
            "adventure_dialog": "menu_join" if c.join else "menu_host",
            "difficulty": "menu_hard" if c.hard else "menu_normal",
            "lobby": "menu_play",
        }
        key = points.get(seen.state)
        if key is not None:
            self._click(*p.point(key))

    def _wait_change(self, state: str, stop: Callable[[], bool]) -> bool:
        wait = self.cfg.load_max_s if state == "lobby" else self.cfg.menu_step_s
        end = self._clock() + wait
        while not stop() and self._clock() < end:
            self._sleep(self._poll)
            if self.see().state != state:
                return True
        return False

    def _emit(self, event_type: str, level: Level = Level.INFO, **p: object) -> None:
        if self._bus is not None:
            self._bus.publish(
                Event(type=event_type, source="adventure", level=level, payload=dict(p))
            )
