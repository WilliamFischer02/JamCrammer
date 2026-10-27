"""Moving in the Best Guess arena and reading the crowd.

William moves with the arrow keys (his Learn session: Left/Right held ~1-1.5 s per move), so
the farmer does the same: a held arrow walks the animal until a wall stops it, which never
clicks a player (clicking one opens their card). The crowd heuristic counts the white name
tags on each half of the floor: when the fact is unknown, the fuller side is the better bet.
Our own tag ("Admiral Angrypotato") is looked up by OCR as a best-effort check of which side
we actually reached.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
import numpy.typing as npt
from rapidfuzz import fuzz

from jamcrammer.core.config import BestGuessConfig
from jamcrammer.vision.ocr import normalize_text, ocr_image

BgrImage = npt.NDArray[np.uint8]
Roi = tuple[int, int, int, int]
STAGE_MID_X = 960


@dataclass(frozen=True)
class Crowd:
    left: int  # name tags on the left (green/True) half
    right: int
    mass_left: float = 0.0  # share of the half that is not floor (animals, tags, bubbles)
    mass_right: float = 0.0

    @staticmethod
    def _pick(a: float, b: float, min_ratio: float) -> str | None:
        if a >= b * min_ratio and a > b:
            return "T"
        if b >= a * min_ratio and b > a:
            return "F"
        return None

    def side(self, min_ratio: float) -> str | None:
        """'T' (left/green) or 'F' (right/purple) when one side is clearly fuller: by the
        non-floor mass first (animals are big and always visible), then by name tags."""
        by_mass = self._pick(self.mass_left, self.mass_right, min_ratio)
        if by_mass is not None:
            return by_mass
        return self._pick(self.left, self.right, min_ratio)


def name_tags(floor: BgrImage, x0: int, cfg: BestGuessConfig) -> list[tuple[int, int, int, int]]:
    """Name tags are white rounded pills ~120-420 px wide and ~25-60 px tall on the floor."""
    hsv = cv2.cvtColor(floor, cv2.COLOR_BGR2HSV)
    white = ((hsv[..., 1] < cfg.tag_max_sat) & (hsv[..., 2] > cfg.tag_min_val)).astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 5))
    closed = cv2.morphologyEx(white, cv2.MORPH_CLOSE, kernel)
    _n, _lab, stats, _cen = cv2.connectedComponentsWithStats(closed)
    out = []
    for x, y, w, h, area in stats[1:]:
        size_ok = cfg.tag_min_w <= w <= cfg.tag_max_w and cfg.tag_min_h <= h <= cfg.tag_max_h
        if size_ok and area >= 0.5 * w * h:
            out.append((int(x0 + x), int(y), int(w), int(h)))
    return out


def count_crowd(image: BgrImage, floor_roi: Roi, cfg: BestGuessConfig) -> Crowd:
    x, y, w, h = floor_roi
    floor = image[y : y + h, x : x + w]
    tags = name_tags(floor, x, cfg)
    left = sum(1 for tx, _ty, tw, _th in tags if tx + tw / 2 < STAGE_MID_X)
    hsv = cv2.cvtColor(floor, cv2.COLOR_BGR2HSV)
    lo, hi = cfg.green_floor_hsv
    green = cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8)) > 0
    lo, hi = cfg.purple_floor_hsv
    purple = cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8)) > 0
    not_floor = ~(green | purple)
    mid = max(1, min(w - 1, STAGE_MID_X - x))
    return Crowd(
        left,
        len(tags) - left,
        float(not_floor[:, :mid].mean()),
        float(not_floor[:, mid:].mean()),
    )


def find_own_tag(
    image: BgrImage, floor_roi: Roi, own_name: str, cfg: BestGuessConfig
) -> int | None:
    """Stage x of our own name tag, by OCR over the floor (None when not readable)."""
    if not own_name:
        return None
    x, y, w, h = floor_roi
    res = ocr_image(image[y : y + h, x : x + w], upscale=cfg.own_tag_upscale)
    want = normalize_text(own_name)
    best: tuple[float, int] | None = None
    for text, (bx, _by, bw, _bh) in res.boxes_in_source():
        norm = normalize_text(text)
        if not norm:
            continue
        score = fuzz.partial_ratio(want, norm)
        if score >= cfg.own_tag_fuzz and (best is None or score > best[0]):
            best = (score, int(x + bx + bw / 2))
    return best[1] if best else None


def side_of_x(x: int) -> str:
    return "T" if x < STAGE_MID_X else "F"


def key_for(side: str) -> str:
    return "left" if side == "T" else "right"
