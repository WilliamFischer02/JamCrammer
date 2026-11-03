"""From any room into the adventure, by menu state rather than by replayed clicks: the state
is read from the words on screen (full-frame OCR), each click must bring the next panel
within ``menu_step_s``, and the in-game dialog closer is never used here (the Join/Host
dialog has the same blue X as Cosmo's, which is how the first supervised run closed its own
menu).

States: room -> world_map -> adventures (scroll by the arrow until the entry is listed) ->
adventure_dialog (Join / Host) -> difficulty (Normal / Hard) -> lobby (PLAY) -> loading ->
in_adventure. A list entry is the line whose nearest adventure name is the target
(``ADVENTURES``); the lobby is only PLAYed when its header is the target's name, a lobby of
another adventure is closed by its X. Every state change is saved as a screenshot."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt
from rapidfuzz import fuzz, process

from jamcrammer.core.config import Level, LevelConfig
from jamcrammer.core.events import Event, EventBus
from jamcrammer.core.profiles import Profile
from jamcrammer.modes.adventures import sight
from jamcrammer.modes.adventures.level import Level as LevelModel
from jamcrammer.vision.ocr import OcrLine, OcrResult, OcrWord

BgrImage = npt.NDArray[np.uint8]
Word = tuple[str, int, int]  # text (lower), centre x, centre y
LOADING_MAX_MEAN = 12.0  # a black loading screen
WORD_FUZZY = 80
HOST_FUZZY = 70  # "NOSt"
NAME_FUZZY = 50  # a list line / lobby header vs the nearest adventure name (measured 57-84
# on the recorded list; "tn iwjstery guow" scores 61 and "iwjstery guow" 53 against the
# mystery below; what matters is that the nearest name is the target)
TOP_Y = 110  # panel headers
DIALOG_HEADER_Y = (330, 440)  # the Adventure (Join / Host) dialog's header
TRANSIENT_S = 0.25  # a state must hold this long before it is acted on (panels slide in)
ROOM_LOOKS = 3  # a room must read as one this many looks running before the globe is clicked
# (a panel misread as a room got the globe click, which on the open world map is a sea
# stage: the animal was swapped for a water one, 2026-11-03)
LOBBY_X_MAX_Y = 220  # the lobby panel's blue X sits near its top right
OCR_HALF_OVERLAP = 120  # the halves of a frame OCR'd in parts overlap this much
NAMES = {"mystery_below": "the mystery below"}
ADVENTURES = [  # the list as it reads (the entries are the real names, OCR'd badly)
    "training grounds",
    "return of the phantoms",
    "the phantom portal",
    "meet cosmo",
    "the hive",
    "the great escape",
    "greely's inferno",
    "the search for greely",
    "bitter sweets",
    "the forgotten desert",
    "in too deep",
    "turning the tide",
    "the front lines",
    "the phantom badlands",
    "the phantom fortress",
    "storming the fortress",
    "the trials of zios",
    "the mystery below",
    "the river's heart",
    "jamaa derby",
]


def robust_ocr(frame: BgrImage, ocr: Callable[[BgrImage], OcrResult]) -> OcrResult:
    """Windows OCR sometimes reads nothing, or not the words that matter, from a frame it
    reads fine in parts (the scrolled adventure list, 2026-11-03): a lit frame is also read
    in two halves and all the lines are merged in frame coordinates."""
    res = ocr(frame)
    if float(frame.mean()) < LOADING_MAX_MEAN:
        return res
    w = frame.shape[1]
    parts = [(0, frame[:, : w // 2 + OCR_HALF_OVERLAP]), (w // 2, frame[:, w // 2 :])]
    lines: list[OcrLine] = list(res.lines)
    for x0, part in parts:
        for ln in ocr(np.ascontiguousarray(part)).lines:
            words = tuple(
                OcrWord(wd.text, (wd.box[0] + x0, wd.box[1], wd.box[2], wd.box[3]))
                for wd in ln.words
            )
            lines.append(OcrLine(ln.text, words))
    return OcrResult(" ".join(ln.text for ln in lines), tuple(lines), res.ms, "", 1.0)


@dataclass
class Seen:
    state: str
    entry: tuple[int, int] | None = None  # the adventure's list entry, when listed


def words_of(res: OcrResult) -> list[Word]:
    out: list[Word] = []
    for line in res.lines:
        for w in line.words:
            x, y, bw, bh = w.box
            cx, cy = int((x + bw / 2) / res.scale), int((y + bh / 2) / res.scale)
            out.append((w.text.lower(), cx, cy))
    return out


def lines_of(res: OcrResult) -> list[Word]:
    out: list[Word] = []
    for line in res.lines:
        x, y, bw, bh = line.box
        cx, cy = int((x + bw / 2) / res.scale), int((y + bh / 2) / res.scale)
        out.append((line.text.lower(), cx, cy))
    return out


def nearest_name(text: str) -> tuple[str, float]:
    """The adventure name a badly OCR'd line is nearest to, and how near (0-100)."""
    best = process.extractOne(text, ADVENTURES, scorer=fuzz.ratio)
    return (str(best[0]), float(best[1])) if best else ("", 0.0)


def _has(
    ws: list[Word],
    word: str,
    where: Callable[[int, int], bool] = lambda x, y: True,
    fuzzy: int = WORD_FUZZY,
) -> bool:
    return any(fuzz.ratio(t, word) >= fuzzy and where(x, y) for t, x, y in ws)


def _mid(x: int, y: int) -> bool:
    return DIALOG_HEADER_Y[0] <= y <= DIALOG_HEADER_Y[1] and 700 <= x <= 1220


def _entry(lines: list[Word], prof: Profile, name: str) -> tuple[int, int] | None:
    lx, ly, lw, lh = prof.roi("menu_list_roi")
    for t, x, y in lines:
        if lx <= x < lx + lw and ly <= y < ly + lh and len(t) >= 4:
            near, score = nearest_name(t)
            if near == name and score >= NAME_FUZZY:
                return (x, y)
    return None


def _header_name(lines: list[Word]) -> str:
    """The adventure named in a panel header (the lobby), or ''."""
    for t, _x, y in lines:
        if y < TOP_Y and len(t) >= 6:
            near, score = nearest_name(t)
            if score >= NAME_FUZZY:
                return near
    return ""


def _entry_by_template(frame: BgrImage, prof: Profile) -> tuple[int, int] | None:
    """The target's banner in the list, by its template (``menu_entry_template``)."""
    if not prof.has("menu_entry_template"):
        return None
    key = str(prof.template_path("menu_entry_template"))
    tmpl = _ENTRY_TEMPLATES.get(key)
    if tmpl is None:
        raw = cv2.imread(key)
        if raw is None:
            return None
        tmpl = np.asarray(raw, dtype=np.uint8)
        _ENTRY_TEMPLATES[key] = tmpl
    found = sight.find_template(
        frame, tmpl, prof.number("menu_entry_min"), prof.roi("menu_list_roi")
    )
    return (found[0], found[1]) if found else None


_ENTRY_TEMPLATES: dict[str, BgrImage] = {}


def classify(frame: BgrImage, res: OcrResult, lvl: LevelModel, prof: Profile) -> Seen:
    """What menu the screen shows (see the module doc)."""
    if float(frame.mean()) < LOADING_MAX_MEAN:
        return Seen("loading")
    ws, lines = words_of(res), lines_of(res)
    if _has(ws, "land") and _has(ws, "animal"):
        return Seen("not_land_animal")  # "You must be a land animal to go on this Adventure."
    entry = _entry_by_template(frame, prof)
    if entry is not None:
        return Seen("adventures", entry)  # the list is open and the target is in view
    name = NAMES.get(lvl.target, lvl.target.replace("_", " "))
    bx, by, bw, bh = prof.roi("banner_roi")
    banner = " ".join(t for t, x, y in ws if bx <= x < bx + bw and by <= y < by + bh)
    top = [w for w in ws if w[2] < TOP_Y]
    state = "room"
    header = _header_name(lines)
    if lvl.phase_of(banner) is not None:
        state = "in_adventure"
    elif _has(ws, "difficulty") or (_has(ws, "normal") and _has(ws, "hard")):
        state = "difficulty"
    elif _has(ws, "host", fuzzy=HOST_FUZZY) or _has(ws, "join") or _has(ws, "adventure", _mid):
        state = "adventure_dialog"  # OCR reads Host as "NOSt"; the dialog's header is mid-screen
    elif header:
        # the lobby panel is headed by the adventure's name (PLAY reads badly); another
        # adventure's lobby must be closed, never played
        state = "lobby" if header == name else "lobby_other"
    elif _has(ws, "adventures", lambda x, y: x < 700 and y < 220):
        return Seen("adventures", _entry(lines, prof, name))
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
        dialog_x: BgrImage | None = None,
        bus: EventBus | None = None,
        clock: Callable[[], float],
        sleep: Callable[[float], None],
        poll_s: float = 0.4,
        shots: Path | None = None,
    ) -> None:
        """``dialog_x``: the blue X template, to close a wrong adventure's lobby. ``shots``:
        a folder for a screenshot of every state change."""
        self.lvl, self.prof, self.cfg = lvl, prof, cfg
        self._click, self._grab, self._ocr, self._x = click, grab, ocr, dialog_x
        self._bus, self._clock, self._sleep, self._poll = bus, clock, sleep, poll_s
        self._shots = shots
        self.steps: list[str] = []
        self._frame: BgrImage | None = None

    def see(self) -> Seen:
        self._frame = frame = self._grab()
        return classify(frame, robust_ocr(frame, self._ocr), self.lvl, self.prof)

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
        deadline = self._clock() + c.menu_step_s * (c.menu_retries + 1) * 8 + c.load_max_s
        last = ""
        streak = 0
        played_at: float | None = None
        while not stop() and self._clock() < deadline:
            seen = self.see()
            streak = streak + 1 if seen.state == last else 1
            if seen.state != last:
                self.steps.append(seen.state)
                self._shot(seen.state)
                self._emit("adventure.menu", state=seen.state)
                last = seen.state
            if seen.state == "in_adventure":
                return True
            if seen.state == "not_land_animal":
                self._emit(
                    "adventure.menu_stuck",
                    Level.HIGH,
                    state=seen.state,
                    message="The game wants a land animal: please switch animals, then start again",
                )
                return False
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
            if seen.state == "room" and streak < ROOM_LOOKS:
                continue  # one more look: a panel read as a room must never get the globe
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
            self._sleep(c.menu_settle_s)  # let the panel open before looking again
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
        if seen.state == "lobby_other":
            self._close_panel()
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

    def _close_panel(self) -> None:
        """A wrong adventure's lobby: its blue X near the top right (never PLAY)."""
        if self._x is None or self._frame is None:
            return
        pt = sight.find_dialog_x(self._frame, self._x, self.prof.number("dialog_x_min"))
        if pt is not None and pt[1] <= LOBBY_X_MAX_Y:
            self._emit("adventure.menu_wrong_lobby", x=pt[0], y=pt[1])
            self._click(pt[0], pt[1])

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
            ev = Event(type=event_type, source="adventure", level=level, payload=dict(p))
            self._bus.publish(ev)
