"""Where are we (modes/situation.py): the classifier on real frames and the den gate rules."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from jamcrammer.core.config import AppConfig
from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import load_profile
from jamcrammer.modes.situation import (
    DEN_GATE_MESSAGE,
    FARMER_MODES,
    Situation,
    Situator,
    is_den_title,
    start_gate,
)
from jamcrammer.vision.ocr import ocr_image

FIX = Path(__file__).parent / "fixtures"
PATHS = Paths()


def _fx(rel: str) -> np.ndarray:
    img = cv2.imread(str(FIX / rel))
    assert img is not None, rel
    return np.asarray(img, dtype=np.uint8)


def _ocr_ok() -> bool:
    return not ocr_image(np.full((40, 120, 3), 255, dtype=np.uint8)).error


def _situator() -> Situator:
    return Situator(lambda name: load_profile(name, PATHS), AppConfig(), PATHS)


def test_den_title_matches_the_suffix_not_the_name() -> None:
    kws = ["'s den"]
    assert is_den_title("Bingusthewizard's Den", kws, 80)
    assert is_den_title("Admiral Angrypotato's Den", kws, 80)
    assert is_den_title("Bingusthewizards Den", kws, 80)  # OCR dropped the apostrophe
    assert not is_den_title("Jamaa Township", kws, 80)
    assert not is_den_title("", kws, 80)
    assert not is_den_title("Crystal Sands", kws, 80)


@pytest.mark.parametrize(
    ("rel", "want"),
    [
        ("navigation/den_live_2026-11-03.jpg", Situation.DEN),  # the live den, 2026-11-03
        ("navigation/den.jpg", Situation.DEN),
        ("navigation/township.jpg", Situation.ROOM),
        ("adventures/township_join_forgotten_desert.jpg", Situation.ROOM),
        ("navigation/games_scrolled.jpg", Situation.GAMES_MENU),  # the den title still reads
        ("navigation/tray_open.jpg", Situation.PARTIES_OPEN),
        ("bestguess/arena_live.jpg", Situation.ARENA),
    ],
)
def test_classifier_on_real_frames(rel: str, want: Situation) -> None:
    if want in (Situation.DEN, Situation.ROOM) and not _ocr_ok():
        pytest.skip("winocr unavailable")
    sight = _situator().classify(_fx(rel))
    assert sight.situation == want, (rel, sight)
    if want == Situation.DEN:
        assert "den" in sight.room.lower()


def test_classifier_never_raises_on_a_blank_frame() -> None:
    sight = _situator().classify(np.zeros((1080, 1920, 3), dtype=np.uint8))
    assert sight.situation == Situation.UNKNOWN


# --- the gate -----------------------------------------------------------------------------


def test_gate_opens_in_the_den_and_closes_elsewhere_for_farmers() -> None:
    for mode in FARMER_MODES:
        assert start_gate(mode, Situation.DEN, running=False) == (True, "")
        for sit in (Situation.ROOM, Situation.ARENA, Situation.PARTIES_OPEN, Situation.GAMES_MENU):
            ok, why = start_gate(mode, sit, running=False)
            assert not ok and why.startswith(DEN_GATE_MESSAGE), (mode, sit, why)


def test_gate_never_blocks_the_best_guess_farmer_in_the_arena() -> None:
    assert start_gate("hunt", Situation.ARENA, running=False) == (True, "")
    assert start_gate("hunt", Situation.DEN, running=False) == (True, "")
    ok, why = start_gate("hunt", Situation.ROOM, running=False)
    assert not ok and "room" in why


def test_gate_lets_the_arcade_start_on_the_derby_track() -> None:
    assert start_gate("farm", Situation.DERBY, running=False) == (True, "")
    assert not start_gate("adventure", Situation.DERBY, running=False)[0]


def test_gate_is_off_for_internal_handoffs_running_modes_and_unknown_screens() -> None:
    for mode in ("hunt", *FARMER_MODES):
        assert start_gate(mode, Situation.ROOM, running=False, internal=True) == (True, "")
        assert start_gate(mode, Situation.ROOM, running=True) == (True, "")
        assert start_gate(mode, None, running=False) == (True, "")  # no capture yet
        assert start_gate(mode, Situation.UNKNOWN, running=False) == (True, "")
