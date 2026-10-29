"""Adventure invites in Jamaa rooms: the "Join Me" bubble a host carries (docs/ADVENTURES.md).

A host's bubble is a translucent rounded box: a blue "Join Me" header (the button) over the
adventure name in grey. Windows OCR on the whole room reads the name lines, though sometimes
only fragments (the room-name label is drawn over bubbles near the top right: "The Forg" /
"Desert" on the 2026-10-29 laptop frame), and it rarely reads the pale header. So:

1. full-frame OCR → words that match a token of an adventure name (fuzzy, or a prefix/suffix of
   at least ``invite_min_chars`` for occluded fragments), grouped into bubbles by proximity;
2. an upscaled crop above each group is OCR'd again; a "join" line above the name is the
   button and its centre is the click point. Without it the invite is reported, never clicked.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from rapidfuzz import fuzz

from jamcrammer.core.config import AdventureConfig
from jamcrammer.vision.ocr import OcrResult, normalize_text, ocr_image

BgrImage = npt.NDArray[np.uint8]
Box = tuple[int, int, int, int]
Ocr = Callable[[BgrImage, float], OcrResult]


@dataclass(frozen=True)
class Invite:
    adventure: str  # the configured keyword this bubble names
    name_box: Box  # stage box around the matched name lines
    coverage: float  # share of the keyword's tokens that were read
    join_point: tuple[int, int] | None  # centre of the "Join" header; None = not confirmed


def _ocr(image: BgrImage, upscale: float) -> OcrResult:
    return ocr_image(image, upscale=upscale)


def _token_hit(word: str, token: str, cfg: AdventureConfig) -> bool:
    if fuzz.ratio(word, token) >= cfg.invite_fuzzy:
        return True
    return len(word) >= cfg.invite_min_chars and (token.startswith(word) or token.endswith(word))


def _union(boxes: list[Box]) -> Box:
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return (x0, y0, x1 - x0, y1 - y0)


def _near(a: Box, b: Box, gap_lines: float) -> bool:
    """Lines of one bubble: stacked within ``gap_lines`` line heights, overlapping horizontally."""
    line_h = max(a[3], b[3])
    v_gap = max(a[1], b[1]) - min(a[1] + a[3], b[1] + b[3])
    h_overlap = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    return v_gap <= gap_lines * line_h and h_overlap > -line_h


def _groups(boxes: list[tuple[set[str], Box]], gap_lines: float) -> list[tuple[set[str], Box, int]]:
    """(matched tokens, union box, tallest line height) per bubble."""
    groups: list[tuple[set[str], list[Box]]] = []
    for tokens, box in sorted(boxes, key=lambda tb: tb[1][1]):
        for g_tokens, g_boxes in groups:
            if any(_near(box, other, gap_lines) for other in g_boxes):
                g_tokens.update(tokens)
                g_boxes.append(box)
                break
        else:
            groups.append((set(tokens), [box]))
    return [(t, _union(b), max(bx[3] for bx in b)) for t, b in groups]


def _join_point(
    image: BgrImage, name_box: Box, line_h: int, cfg: AdventureConfig, ocr: Ocr
) -> tuple[int, int] | None:
    x, y, w, h = name_box
    cx0 = max(0, int(x - cfg.invite_crop_side * line_h))
    cx1 = min(image.shape[1], int(x + w + cfg.invite_crop_side * line_h))
    cy0 = max(0, int(y - cfg.invite_crop_up * line_h))
    cy1 = y + h
    if cx1 <= cx0 or cy1 <= cy0:
        return None
    res = ocr(np.ascontiguousarray(image[cy0:cy1, cx0:cx1]), cfg.invite_upscale)
    best: tuple[int, int] | None = None
    for text, (lx, ly, lw, lh) in res.boxes_in_source():
        if cy0 + ly + lh > y + lh // 2:  # must sit above the name's first line
            continue
        words = normalize_text(text).split()
        joins = cfg.invite_join_words
        hit = any(fuzz.ratio(wd, jw) >= cfg.invite_fuzzy for wd in words for jw in joins)
        if hit and (best is None or cy0 + ly > best[1]):  # the lowest header above the name
            best = (cx0 + lx + lw // 2, cy0 + ly + lh // 2)
    return best


def find_invites(image: BgrImage, cfg: AdventureConfig, ocr: Ocr = _ocr) -> list[Invite]:
    """Every bubble in a stage frame that names a configured adventure, best coverage first."""
    full = ocr(image, 1.0)
    invites: list[Invite] = []
    for keyword in cfg.invite_keywords:
        tokens = normalize_text(keyword).split()
        hits: list[tuple[set[str], Box]] = []
        for text, box in full.boxes_in_source():
            words = normalize_text(text).split()
            matched = {t for t in tokens for wd in words if _token_hit(wd, t, cfg)}
            if matched:
                hits.append((matched, box))
        for matched, box, line_h in _groups(hits, cfg.invite_line_gap):
            join = _join_point(image, box, line_h, cfg, ocr)
            invites.append(Invite(keyword, box, len(matched) / len(tokens), join))
    return sorted(invites, key=lambda i: (i.join_point is None, -i.coverage))
