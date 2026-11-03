"""The adventure menu driver (modes/adventures/opening.py) on the two live frames that broke
it on 2026-11-03: the adventure list scrolled to The Mystery Below (Windows OCR read nothing
from the whole frame, the driver called it a room and clicked the globe, which on the open
world map is a sea stage) and the "You must be a land animal" dialog."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import load_profile
from jamcrammer.modes.adventures.level import Level, level_dir
from jamcrammer.modes.adventures.opening import classify, robust_ocr
from jamcrammer.vision.ocr import ocr_image

FIX = Path(__file__).parent / "fixtures" / "adventure_menus"
LIST = FIX / "list_scrolled_to_mystery_below.jpg"
NOT_LAND = FIX / "not_land_animal.jpg"


def _setup() -> tuple[object, object]:
    paths = Paths()
    prof = load_profile("adventure", paths)
    lvl = Level.load(level_dir(paths.profiles, "mystery_below"))
    if lvl is None:
        pytest.skip("the Mystery Below level model is not here")
    return prof, lvl


def _frame(path: Path) -> np.ndarray:
    raw = cv2.imread(str(path))
    assert raw is not None
    return np.asarray(raw, np.uint8)


def test_the_scrolled_list_is_the_list_and_the_entry_is_found() -> None:
    prof, lvl = _setup()
    frame = _frame(LIST)
    seen = classify(frame, robust_ocr(frame, ocr_image), lvl, prof)  # type: ignore[arg-type]
    assert seen.state == "adventures"
    assert seen.entry is not None
    x, y = seen.entry
    assert 300 <= x <= 480 and 790 <= y <= 880  # the banner's centre


def test_the_entry_is_found_without_any_ocr() -> None:
    """The banner template alone names the state: OCR may read nothing at all."""
    from jamcrammer.vision.ocr import OcrResult

    prof, lvl = _setup()
    frame = _frame(LIST)
    seen = classify(frame, OcrResult(text=""), lvl, prof)  # type: ignore[arg-type]
    assert seen.state == "adventures" and seen.entry is not None


def test_the_land_animal_dialog_is_recognised() -> None:
    prof, lvl = _setup()
    frame = _frame(NOT_LAND)
    seen = classify(frame, robust_ocr(frame, ocr_image), lvl, prof)  # type: ignore[arg-type]
    assert seen.state == "not_land_animal"
