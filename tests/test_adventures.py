"""Adventure invite bubbles ("Join Me" / The Forgotten Desert) on live Jamaa Township frames."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from jamcrammer.core.config import AdventureConfig
from jamcrammer.modes.adventures.invites import find_invites
from jamcrammer.vision.ocr import OcrLine, OcrResult, OcrWord, ocr_image

FIX = Path(__file__).parent / "fixtures"
CFG = AdventureConfig()


def _load(rel: str) -> np.ndarray:
    img = cv2.imread(str(FIX / rel))
    assert img is not None, rel
    return np.asarray(img, dtype=np.uint8)


@pytest.fixture(scope="module", autouse=True)
def _needs_ocr() -> None:
    if ocr_image(np.full((40, 120, 3), 255, dtype=np.uint8)).error:
        pytest.skip("Windows OCR unavailable")


def test_laptop_township_bubble_half_covered_by_the_room_label() -> None:
    """2026-10-29 laptop: the room label hides "...otten", OCR reads "The Forg" / "Desert"."""
    invites = find_invites(_load("adventures/township_join_forgotten_desert.jpg"), CFG)
    assert len(invites) == 1
    inv = invites[0]
    assert inv.adventure == "forgotten desert" and inv.coverage == 1.0
    assert inv.join_point is not None
    x, y = inv.join_point
    assert 1270 <= x <= 1400 and 15 <= y <= 48  # the blue "Join Me" header


def test_pc_township_bubble() -> None:
    invites = find_invites(_load("navigation/township.jpg"), CFG)
    assert [i.adventure for i in invites] == ["forgotten desert"]
    assert invites[0].join_point is not None
    x, y = invites[0].join_point
    assert abs(x - 975) <= 40 and abs(y - 136) <= 15


@pytest.mark.parametrize("rel", ["navigation/den.jpg", "my_parties_full_2026-10-26.png"])
def test_no_invite_without_a_bubble(rel: str) -> None:
    assert find_invites(_load(rel), CFG) == []


def _line(text: str, box: tuple[int, int, int, int]) -> OcrLine:
    return OcrLine(text=text, words=(OcrWord(text=text, box=box),))


def test_chat_mentioning_the_desert_is_never_clickable() -> None:
    """A plain chat bubble ("selling desert stuff") has no Join header above it."""

    def ocr(img: np.ndarray, upscale: float) -> OcrResult:
        if upscale == 1.0:
            return OcrResult(
                "selling desert stuff", (_line("selling desert stuff", (400, 300, 220, 24)),)
            )
        return OcrResult("", ())

    invites = find_invites(np.zeros((1080, 1920, 3), dtype=np.uint8), CFG, ocr=ocr)
    assert len(invites) == 1 and invites[0].join_point is None and invites[0].coverage == 0.5
