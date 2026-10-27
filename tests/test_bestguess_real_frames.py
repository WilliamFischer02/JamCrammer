"""Best Guess detector primitives against crops of William's 2026-10-26 recording."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from jamcrammer.core.config import BestGuessConfig
from jamcrammer.modes.bestguess.state import (
    color_fill,
    dominant_side,
    reveal_side_from_text,
)
from jamcrammer.vision.ocr import normalize_text, ocr_image, parse_int

FIX = Path(__file__).parent / "fixtures" / "bestguess"
FACT_ROI = (500, 10, 940, 200)
WORD_ROI = (700, 15, 540, 95)  # inside FACT_ROI
CFG = BestGuessConfig()


def _load(name: str) -> np.ndarray:
    img = cv2.imread(str(FIX / name))
    assert img is not None, name
    return np.asarray(img, dtype=np.uint8)


def _word(banner: np.ndarray) -> np.ndarray:
    x = WORD_ROI[0] - FACT_ROI[0]
    y = WORD_ROI[1] - FACT_ROI[1]
    return banner[y : y + WORD_ROI[3], x : x + WORD_ROI[2]]


def test_reveal_word_colour_true_false_question() -> None:
    assert dominant_side(_word(_load("banner_true.png")), CFG)[0] == "T"
    assert dominant_side(_word(_load("banner_false.png")), CFG)[0] == "F"
    assert dominant_side(_word(_load("banner_question.png")), CFG)[0] is None


def test_parchment_banner_marks_the_arena() -> None:
    for name in ("banner_question.png", "banner_true.png", "banner_false.png"):
        assert color_fill(_load(name), CFG.parchment_hsv) >= CFG.banner_min_parchment, name
    assert (
        color_fill(_load("banner_outside_game.png"), CFG.parchment_hsv) < CFG.banner_min_parchment
    )


def test_reveal_word_from_ocr_noise() -> None:
    assert reveal_side_from_text(normalize_text("Fuse! Wolverines are solitary animals.")) == "F"
    assert reveal_side_from_text(normalize_text("True!")) == "T"
    assert reveal_side_from_text(normalize_text("Truel")) == "T"
    assert reveal_side_from_text(normalize_text("Bulldog ants eat meat.")) is None
    assert reveal_side_from_text(normalize_text("Tarantulas are omnivores.")) is None


def test_ocr_reads_fact_and_score() -> None:
    probe = ocr_image(np.full((40, 120, 3), 255, dtype=np.uint8))
    if probe.error:
        pytest.skip("winocr unavailable")
    text = normalize_text(ocr_image(_load("banner_question.png"), upscale=CFG.ocr_upscale).text)
    assert "wolverines" in text and "social" in text, text
    assert parse_int(ocr_image(_load("score_30.png"), upscale=CFG.score_upscale).text) == 30
    assert parse_int(ocr_image(_load("score_60.png"), upscale=CFG.score_upscale).text) == 60
