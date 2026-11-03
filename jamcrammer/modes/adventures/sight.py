"""What the route farmer sees in an adventure (stage pixels, profile ``adventure``).

- the player's light-blue dot on the fixed minimap: an absolute position in the current area,
  even in the cave's darkness;
- scene alignment: how far the live view is shifted from a recorded frame (phase correlation
  on a high-passed, downscaled grey image with the HUD and our animal masked out, so the cave's
  vignette and a different animal do not count). The camera follows the animal, so content
  shifted right (+dx) means the animal is left of where it was: walk right;
- an anchor patch of the recorded run found near where a click should land;
- the blue X of an open dialog; the objective banner text; changes in the item slots;
- the cursor's shape (Windows draws it outside the game's pixels): it changes while an item
  is held.
"""

from __future__ import annotations

from collections.abc import Callable

import cv2
import numpy as np
import numpy.typing as npt

from jamcrammer.core.profiles import Profile
from jamcrammer.vision.ocr import normalize_text

BgrImage = npt.NDArray[np.uint8]
Gray = npt.NDArray[np.float32]
Roi = tuple[int, int, int, int]


def _crop(image: BgrImage, roi: Roi) -> BgrImage:
    x, y, w, h = roi
    return image[max(0, y) : y + h, max(0, x) : x + w]


def minimap_dot(image: BgrImage, prof: Profile) -> tuple[float, float] | None:
    """Centre of the player dot on the minimap (stage px), or None (minimap hidden/zoomed)."""
    mx, my, mw, mh = prof.roi("minimap_roi")
    roi = _crop(image, (mx, my, mw, mh))
    if roi.size == 0:
        return None
    lo, hi = prof.color("dot_hsv")
    mask = cv2.inRange(cv2.cvtColor(roi, cv2.COLOR_BGR2HSV), np.array(lo), np.array(hi))
    ex, ey, ew, eh = prof.roi("minimap_exclude_roi")
    mask[max(0, ey - my) : ey - my + eh, max(0, ex - mx) : ex - mx + ew] = 0
    n, _lab, stats, cents = cv2.connectedComponentsWithStats(mask)
    a_min, a_max = prof.items("dot_area")
    best: tuple[int, tuple[float, float]] | None = None
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if a_min <= area <= a_max and (best is None or area > best[0]):
            best = (area, (float(cents[i][0]) + mx, float(cents[i][1]) + my))
    return best[1] if best else None


def masked_gray(image: BgrImage, prof: Profile, scale: float, sigma: float) -> Gray:
    """High-passed grey image at ``scale`` with the HUD and our animal zeroed (downscaled
    first: the blur runs on the small image)."""
    g = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA).astype(np.float32)
    small = small - cv2.GaussianBlur(small, (0, 0), sigma)
    for x, y, w, h in [*prof.items("hud_rois"), prof.roi("avatar_roi")]:
        x0, y0 = max(0, int(x * scale)), max(0, int(y * scale))
        small[y0 : int((y + h) * scale) + 1, x0 : int((x + w) * scale) + 1] = 0
    return np.asarray(small, dtype=np.float32)


_WINDOWS: dict[tuple[int, int], Gray] = {}


def scene_shift(ref: Gray, live: Gray, scale: float) -> tuple[float, float, float]:
    """(dx, dy, response): the live view is the recorded one moved by (dx, dy) stage px."""
    key = (ref.shape[1], ref.shape[0])
    win = _WINDOWS.get(key)
    if win is None:
        win = np.asarray(cv2.createHanningWindow(key, cv2.CV_32F), dtype=np.float32)
        _WINDOWS[key] = win
    (sx, sy), resp = cv2.phaseCorrelate(ref, live, win)
    return (sx / scale, sy / scale, float(resp))


def thumb(image: BgrImage, prof: Profile, w: int, h: int) -> npt.NDArray[np.float32]:
    """Zero-mean unit vector of a small grey thumbnail for localisation. HUD pixels are left
    out entirely (not zeroed before the mean, which gave every frame the same HUD-shaped
    pattern: a black loading screen correlated ~0.3 with anything). A flat image is the zero
    vector: it matches nothing."""
    g = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (w, h), interpolation=cv2.INTER_AREA)
    v = g.astype(np.float32).reshape(-1)
    keep = _thumb_mask(prof, image.shape[1], image.shape[0], w, h)
    v = np.where(keep, v - float(v[keep].mean()) if keep.any() else 0.0, 0.0).astype(np.float32)
    n = float(np.linalg.norm(v))
    return np.asarray(v / n if n > 1e-3 else np.zeros_like(v), dtype=np.float32)


_MASKS: dict[tuple[int, int, int, int, int], npt.NDArray[np.bool_]] = {}


def _thumb_mask(prof: Profile, iw: int, ih: int, w: int, h: int) -> npt.NDArray[np.bool_]:
    key = (id(prof), iw, ih, w, h)
    m = _MASKS.get(key)
    if m is None:
        full = np.ones((ih, iw), np.uint8)
        for x, y, rw, rh in prof.items("hud_rois"):
            full[max(0, y) : y + rh, max(0, x) : x + rw] = 0
        small = cv2.resize(full, (w, h), interpolation=cv2.INTER_AREA)
        m = np.asarray(small.reshape(-1) > 0, dtype=np.bool_)
        _MASKS[key] = m
    return m


def find_patch(
    image: BgrImage, patch: BgrImage, center: tuple[int, int], radius: int
) -> tuple[int, int, float] | None:
    """Best match of ``patch`` whose centre lies within ``radius`` of ``center``."""
    ph, pw = patch.shape[:2]
    cx, cy = center
    x0, y0 = max(0, cx - radius - pw // 2), max(0, cy - radius - ph // 2)
    x1 = min(image.shape[1], cx + radius + pw - pw // 2)
    y1 = min(image.shape[0], cy + radius + ph - ph // 2)
    win = image[y0:y1, x0:x1]
    if win.shape[0] < ph or win.shape[1] < pw:
        return None
    res = cv2.matchTemplate(win, patch, cv2.TM_CCOEFF_NORMED)
    _mn, score, _mnl, loc = cv2.minMaxLoc(res)
    return (x0 + loc[0] + pw // 2, y0 + loc[1] + ph // 2, float(score))


def find_dialog_x(
    image: BgrImage, tmpl: BgrImage, min_score: float, roi: Roi | None = None
) -> tuple[int, int] | None:
    """Centre of an open dialog's blue X (searched inside ``roi`` when given: every tick)."""
    x0, y0 = (max(0, roi[0]), max(0, roi[1])) if roi else (0, 0)
    win = _crop(image, roi) if roi else image
    if win.shape[0] < tmpl.shape[0] or win.shape[1] < tmpl.shape[1]:
        return None
    res = cv2.matchTemplate(win, tmpl, cv2.TM_CCOEFF_NORMED)
    _mn, score, _mnl, loc = cv2.minMaxLoc(res)
    if score < min_score:
        return None
    return (x0 + loc[0] + tmpl.shape[1] // 2, y0 + loc[1] + tmpl.shape[0] // 2)


def find_template(
    image: BgrImage, tmpl: BgrImage, min_score: float, roi: Roi | None = None
) -> tuple[int, int, float] | None:
    """Centre and score of the best match of ``tmpl`` (inside ``roi`` when given), or None
    below ``min_score``: our animal's name tag, a button, a pin."""
    x0, y0 = (max(0, roi[0]), max(0, roi[1])) if roi else (0, 0)
    win = _crop(image, roi) if roi else image
    if win.shape[0] < tmpl.shape[0] or win.shape[1] < tmpl.shape[1]:
        return None
    res = cv2.matchTemplate(win, tmpl, cv2.TM_CCOEFF_NORMED)
    _mn, score, _mnl, loc = cv2.minMaxLoc(res)
    if score < min_score:
        return None
    return (x0 + loc[0] + tmpl.shape[1] // 2, y0 + loc[1] + tmpl.shape[0] // 2, float(score))


def player_on_screen(
    image: BgrImage, prof: Profile, tag: BgrImage | None
) -> tuple[tuple[int, int], tuple[int, int, int, int], float]:
    """Where our animal is in this frame: (reference point, the box covering the animal and
    its tag, the tag's score). Found by the name tag (``nametag_template``); without one, or
    when the tag is not seen, ``avatar_xy`` and ``avatar_roi`` (score 0)."""
    if tag is not None and prof.has("nametag_template"):
        found = find_template(image, tag, prof.number("nametag_min"))
        if found is not None:
            tx, ty, score = found
            ox, oy = prof.point("player_from_tag")
            bx, by, bw, bh = prof.roi("nametag_box")
            return (tx + ox, ty + oy), (tx + bx, ty + by, bw, bh), score
    return prof.point("avatar_xy"), prof.roi("avatar_roi"), 0.0


def banner_text(image: BgrImage, prof: Profile, ocr: Callable[[BgrImage], str]) -> str:
    return normalize_text(ocr(_crop(image, prof.roi("banner_roi"))))


def region_change(a: BgrImage, b: BgrImage, roi: Roi) -> float:
    """Mean absolute grey difference of ``roi`` between two frames."""
    ga = cv2.cvtColor(_crop(a, roi), cv2.COLOR_BGR2GRAY).astype(np.float32)
    gb = cv2.cvtColor(_crop(b, roi), cv2.COLOR_BGR2GRAY).astype(np.float32)
    return float(np.mean(np.abs(ga - gb))) if ga.shape == gb.shape and ga.size else 0.0


def local_similarity(
    a: BgrImage, b: BgrImage, center: tuple[int, int], half: tuple[int, int]
) -> float:
    """Normalised correlation of the window around ``center`` in two frames."""
    cx, cy = center
    hx, hy = half
    roi = (cx - hx, cy - hy, 2 * hx, 2 * hy)
    ga = cv2.cvtColor(_crop(a, roi), cv2.COLOR_BGR2GRAY).astype(np.float32).reshape(-1)
    gb = cv2.cvtColor(_crop(b, roi), cv2.COLOR_BGR2GRAY).astype(np.float32).reshape(-1)
    if ga.shape != gb.shape or ga.size == 0:
        return -1.0
    ga, gb = ga - ga.mean(), gb - gb.mean()
    den = float(np.linalg.norm(ga) * np.linalg.norm(gb))
    return float(ga @ gb / den) if den > 0 else -1.0


def cursor_handle() -> int:
    """The current cursor's handle: it changes when the game holds an item on the pointer."""
    import win32gui

    try:
        return int(win32gui.GetCursorInfo()[1])
    except Exception:
        return 0
