from __future__ import annotations

import cv2
import numpy as np
import pytest

from jamcrammer.vision.ocr import (
    contains_any,
    crop,
    normalize_text,
    ocr_image,
    parse_int,
    preprocess,
)


def test_normalize_text() -> None:
    assert normalize_text("  Cats  PURR, loudly!  ") == "cats purr loudly"
    assert normalize_text("A l0ve|y c5t") == "a lovely cst".replace("cst", "cst")
    assert normalize_text("Lions have 4 legs.") == "lions have 4 legs"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Gems: 1,234", 1234),
        ("12O5", 1205),
        ("no digits", None),
        ("  987 ", 987),
        ("l00", 100),
        ("", None),
    ],
)
def test_parse_int(text: str, expected: int | None) -> None:
    assert parse_int(text) == expected


def test_contains_any() -> None:
    assert (
        contains_any("Are You Still Playing?", ["still playing", "logged out"]) == "still playing"
    )
    assert contains_any("hello", ["x"]) is None


def test_preprocess_and_crop_shapes() -> None:
    img = np.zeros((40, 60, 3), dtype=np.uint8)
    up = preprocess(img, upscale=2.0)
    assert up.shape == (80, 120, 3)
    binar = preprocess(img, threshold=128, invert=True)
    assert binar.shape == (40, 60, 3)
    assert int(binar[0, 0, 0]) == 255
    assert crop(img, (50, 30, 100, 100)).shape == (10, 10, 3)


def test_windows_ocr_reads_rendered_text() -> None:
    """Live Windows OCR on a rendered string; skipped if the engine is unavailable."""
    img = np.full((120, 700, 3), 255, dtype=np.uint8)
    cv2.putText(
        img,
        "Are you still playing?",
        (20, 80),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.6,
        (0, 0, 0),
        3,
        cv2.LINE_AA,
    )
    res = ocr_image(img, upscale=1.5)
    if res.error:
        pytest.skip(f"winocr unavailable: {res.error}")
    assert "still" in res.lowered
    assert res.lines and res.lines[0].words
    _text, box = res.boxes_in_source()[0]
    assert box[2] > 0 and box[2] <= 700
