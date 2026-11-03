"""Where are we? One classification of a captured frame, shared by every bot's Start and by
the den gate on the bot pages (William, 2026-11-03: all bots start from the den).

Order matters: an open panel or menu sits over the room and the room title still reads, so
the overlays are checked first; the Best Guess arena and the Derby track have no room title;
then the title decides den vs. room. Every check is driven by the profiles and guarded: a
profile field that is missing just skips that check, it never raises into the GUI.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import numpy as np
import numpy.typing as npt
from rapidfuzz import fuzz

from jamcrammer.core.config import AppConfig
from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import Profile
from jamcrammer.vision.ocr import crop, normalize_text, ocr_image

BgrImage = npt.NDArray[np.uint8]
ProfileFn = Callable[[str], Profile]
ROOM_TITLE_UPSCALE = 2.0
FARMER_MODES: tuple[str, ...] = ("farm", "adventure", "opportunist")
DEN_GATE_MESSAGE = "Return to Den to enable this Farmer"
DEFAULT_DEN_KEYWORDS = ["'s den"]
DEFAULT_DEN_FUZZ = 80.0


class Situation(StrEnum):
    ARENA = "arena"  # the Best Guess arena (the room is the game)
    DERBY = "derby"  # the Jamaa Derby track, start line or leaderboard
    PARTIES_OPEN = "parties_open"  # My Parties is open over the room
    GAMES_MENU = "games_menu"  # the games menu is open over the room
    DEN = "den"  # a room whose title ends with "'s Den"
    ROOM = "room"  # any other room with a readable title
    UNKNOWN = "unknown"  # nothing recognised (a dialog, an adventure, a loading screen)


@dataclass(frozen=True)
class Sighting:
    situation: Situation
    room: str = ""  # the OCR'd room title, when one was read
    note: str = ""  # which check decided

    def __str__(self) -> str:
        return f"{self.situation}" + (f" ({self.room})" if self.room else "")


def is_den_title(text: str, keywords: list[str], fuzz_threshold: float) -> bool:
    """'Bingusthewizard's Den' -> True; 'Jamaa Township' -> False. The name before the
    suffix is the player's, so only the suffix is matched (fuzzily: OCR drops apostrophes)."""
    norm = normalize_text(text)
    if not norm:
        return False
    for kw in keywords:
        want = normalize_text(str(kw))
        if want and want in norm:
            return True
        if want and fuzz.partial_ratio(want, norm) >= fuzz_threshold:
            return True
    return False


def room_title(image: BgrImage, nav: Profile) -> str:
    if not nav.has("room_name_roi"):
        return ""
    return ocr_image(crop(image, nav.roi("room_name_roi")), upscale=ROOM_TITLE_UPSCALE).text.strip()


class Situator:
    """Classifies frames; the detectors it needs are built once, lazily, and a detector that
    cannot be built (missing template, uncalibrated field) is simply left out."""

    def __init__(self, profiles: ProfileFn, cfg: AppConfig, paths: Paths | None = None) -> None:
        self._profiles = profiles
        self._cfg = cfg
        self._paths = paths or Paths()
        self._analyzer: Any = None
        self._derby: Any = None
        self._built = False

    def _build(self) -> None:
        self._built = True
        try:
            from jamcrammer.modes.party_watcher import BannerLibrary, PanelAnalyzer

            self._analyzer = PanelAnalyzer(
                self._profiles("parties"),
                BannerLibrary(self._paths.assets / "banners"),
                paths=self._paths,
            )
        except Exception:
            self._analyzer = None
        try:
            from jamcrammer.modes.arcade.derby import DerbyDetector

            self._derby = DerbyDetector(self._profiles("jamaa_derby"), self._cfg.derby)
        except Exception:
            self._derby = None

    def classify(self, image: BgrImage) -> Sighting:
        """Cheap checks (templates, colours) run before the OCR of the room title."""
        if not self._built:
            self._build()
        for check in (self._parties_open, self._games_menu, self._arena, self._derby_screen):
            try:
                found = check(image)
            except Exception:
                found = None
            if found is not None:
                return found
        try:
            nav = self._profiles("navigation")
            title = room_title(image, nav)
        except Exception:
            return Sighting(Situation.UNKNOWN, note="room title unreadable")
        if not title:
            return Sighting(Situation.UNKNOWN, note="no room title")
        keywords = (
            [str(k) for k in nav.items("den_title_keywords")]
            if nav.has("den_title_keywords")
            else DEFAULT_DEN_KEYWORDS
        )
        thr = nav.number("den_title_fuzz") if nav.has("den_title_fuzz") else DEFAULT_DEN_FUZZ
        if is_den_title(title, keywords, thr):
            return Sighting(Situation.DEN, room=title, note="room title")
        return Sighting(Situation.ROOM, room=title, note="room title")

    def _parties_open(self, image: BgrImage) -> Sighting | None:
        if self._analyzer is not None and self._analyzer.is_open(image) is not None:
            return Sighting(Situation.PARTIES_OPEN, note="My Parties title")
        return None

    def _games_menu(self, image: BgrImage) -> Sighting | None:
        from jamcrammer.modes.planner import games_menu_open

        if games_menu_open(image, self._profiles("navigation")):
            return Sighting(Situation.GAMES_MENU, note="games menu scrollbar")
        return None

    def _arena(self, image: BgrImage) -> Sighting | None:
        from jamcrammer.modes.bestguess.state import in_arena

        if in_arena(image, self._profiles("bestguess"), self._cfg.bestguess):
            return Sighting(Situation.ARENA, note="parchment banner + gem icon")
        return None

    def _derby_screen(self, image: BgrImage) -> Sighting | None:
        from jamcrammer.modes.arcade.derby import Screen

        if self._derby is None:
            return None
        screen = self._derby.screen(image)
        if screen != Screen.UNKNOWN:
            return Sighting(Situation.DERBY, note=f"Derby {screen}")
        return None


# --- the den gate (bot pages, tray, F9, dashboard) ------------------------------------------------


def start_gate(
    mode: str,
    situation: Situation | None,
    *,
    running: bool,
    internal: bool = False,
) -> tuple[bool, str]:
    """May a Start of ``mode`` go ahead? ``(True, "")`` or ``(False, why)``.

    Rules (William, 2026-11-03): the gate is a convenience for the user and must never trip a
    bot. It applies only to a user-initiated Start while nothing runs; never to an internal
    hand-off (farmer -> Best Guess Farmer), never while any mode runs, never to Stop (not
    routed here). The Best Guess Farmer may start inside the arena. The Arcade may start on
    the Derby track (it farms what is on screen). An unknown screen or no capture does not
    block: the bot's own start classification deals with it."""
    if internal or running:
        return (True, "")
    if situation is None or situation == Situation.UNKNOWN:
        return (True, "")
    if situation == Situation.DEN:
        return (True, "")
    if mode == "hunt" and situation == Situation.ARENA:
        return (True, "")
    if mode == "farm" and situation == Situation.DERBY:
        return (True, "")
    where = {
        Situation.ARENA: "you are in the Best Guess arena",
        Situation.DERBY: "you are in the Jamaa Derby",
        Situation.PARTIES_OPEN: "My Parties is open",
        Situation.GAMES_MENU: "the games menu is open",
        Situation.ROOM: "you are in a room, not your den",
    }.get(situation, str(situation))
    return (False, f"{DEN_GATE_MESSAGE} ({where})")
