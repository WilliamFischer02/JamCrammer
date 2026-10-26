"""Pure calibration helpers used by the Calibration canvas: ROI hit-testing and dragging, HSV
range sampling for color fields, template cropping. No Qt here so it is unit-testable."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import cv2
import numpy as np
import numpy.typing as npt

BgrImage = npt.NDArray[np.uint8]
Roi = tuple[int, int, int, int]
HandleMode = Literal["move", "nw", "ne", "sw", "se", "n", "s", "w", "e"]

HANDLE_PX = 10
MIN_SIZE = 4
DEFAULT_H_TOL = 10
DEFAULT_S_TOL = 60
DEFAULT_V_TOL = 60


def hit_test(  # noqa: PLR0911 - one return per handle reads clearest
    roi: Roi, x: int, y: int, handle_px: int = HANDLE_PX
) -> HandleMode | None:
    """Which part of ``roi`` is under (x, y): a corner/edge handle, the body, or nothing."""
    rx, ry, rw, rh = roi
    if not (
        rx - handle_px <= x <= rx + rw + handle_px and ry - handle_px <= y <= ry + rh + handle_px
    ):
        return None
    left = abs(x - rx) <= handle_px
    right = abs(x - (rx + rw)) <= handle_px
    top = abs(y - ry) <= handle_px
    bottom = abs(y - (ry + rh)) <= handle_px
    if top and left:
        return "nw"
    if top and right:
        return "ne"
    if bottom and left:
        return "sw"
    if bottom and right:
        return "se"
    if top:
        return "n"
    if bottom:
        return "s"
    if left:
        return "w"
    if right:
        return "e"
    if rx <= x <= rx + rw and ry <= y <= ry + rh:
        return "move"
    return None


def apply_drag(  # noqa: PLR0917
    roi: Roi,
    mode: HandleMode,
    dx: int,
    dy: int,
    bounds: tuple[int, int] = (1920, 1080),
    min_size: int = MIN_SIZE,
) -> Roi:
    """Move or resize ``roi`` by (dx, dy) according to the handle, clamped to ``bounds``."""
    x, y, w, h = roi
    x0, y0, x1, y1 = x, y, x + w, y + h
    if mode == "move":
        x0, y0, x1, y1 = x0 + dx, y0 + dy, x1 + dx, y1 + dy
    else:
        if "w" in mode:
            x0 = min(x0 + dx, x1 - min_size)
        if "e" in mode:
            x1 = max(x1 + dx, x0 + min_size)
        if "n" in mode:
            y0 = min(y0 + dy, y1 - min_size)
        if "s" in mode:
            y1 = max(y1 + dy, y0 + min_size)
    bw, bh = bounds
    if mode == "move":
        w, h = x1 - x0, y1 - y0
        x0 = max(0, min(x0, bw - w))
        y0 = max(0, min(y0, bh - h))
        return (int(x0), int(y0), int(w), int(h))
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(bw, x1), min(bh, y1)
    return (int(x0), int(y0), int(max(min_size, x1 - x0)), int(max(min_size, y1 - y0)))


def normalize_rect(x0: int, y0: int, x1: int, y1: int) -> Roi:
    """Rubber-band corners in any order → (x, y, w, h)."""
    ax, bx = sorted((x0, x1))
    ay, by = sorted((y0, y1))
    return (int(ax), int(ay), int(max(1, bx - ax)), int(max(1, by - ay)))


def hsv_range(  # noqa: PLR0917
    image: BgrImage,
    cx: int,
    cy: int,
    radius: int = 3,
    h_tol: int = DEFAULT_H_TOL,
    s_tol: int = DEFAULT_S_TOL,
    v_tol: int = DEFAULT_V_TOL,
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """Sample the pixels around (cx, cy) and return an OpenCV HSV (lo, hi) range that contains
    them with tolerance. Hue wraps are not handled (reds near 0/180 get a wide range)."""
    h_img, w_img = image.shape[:2]
    x0, y0 = max(0, cx - radius), max(0, cy - radius)
    x1, y1 = min(w_img, cx + radius + 1), min(h_img, cy + radius + 1)
    patch = cv2.cvtColor(image[y0:y1, x0:x1], cv2.COLOR_BGR2HSV).reshape(-1, 3)
    hmin, smin, vmin = (int(v) for v in patch.min(axis=0))
    hmax, smax, vmax = (int(v) for v in patch.max(axis=0))
    lo = (max(0, hmin - h_tol), max(0, smin - s_tol), max(0, vmin - v_tol))
    hi = (min(180, hmax + h_tol), min(255, smax + s_tol), min(255, vmax + v_tol))
    return (lo, hi)


def crop_template(image: BgrImage, roi: Roi, path: Path) -> Path:
    x, y, w, h = roi
    crop = image[max(0, y) : y + h, max(0, x) : x + w]
    if crop.size == 0:
        msg = f"empty crop for {roi}"
        raise ValueError(msg)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), crop)
    return path
