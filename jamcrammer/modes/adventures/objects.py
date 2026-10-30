"""Things on screen in The Mystery Below, found by colour: unlit crystals (dark navy), lit
crystals (a near-white cyan glow), purple decoys, the green glow fungi. Each is a blob with
its centre in stage pixels; the HUD is left out."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
import numpy.typing as npt

from jamcrammer.core.profiles import Profile

BgrImage = npt.NDArray[np.uint8]
EDGE_PX = 70  # blobs this close to the left / right edge are tabs, not things
BOTTOM_PX = 110  # ... and this close to the bottom are the bar
CRYSTAL_KINDS = {
    "unlit": "crystal_unlit_hsv",
    "lit": "crystal_lit_hsv",
    "decoy": "crystal_decoy_hsv",
}


@dataclass(frozen=True)
class Blob:
    kind: str
    x: int
    y: int
    area: int

    def dist(self, pt: tuple[float, float]) -> float:
        return float(np.hypot(self.x - pt[0], self.y - pt[1]))


def _hud_mask(prof: Profile, shape: tuple[int, ...]) -> npt.NDArray[np.uint8]:
    keep = np.full(shape[:2], 255, np.uint8)
    for x, y, w, h in prof.items("hud_rois"):
        keep[max(0, y) : y + h, max(0, x) : x + w] = 0
    keep[:, :EDGE_PX] = 0  # the side tabs and the bottom bar spill a little past hud_rois
    keep[:, shape[1] - EDGE_PX :] = 0
    keep[shape[0] - BOTTOM_PX :, :] = 0
    return keep


def find_blobs(frame: BgrImage, prof: Profile, color_key: str, kind: str) -> list[Blob]:
    """Connected blobs of the profile colour range within ``blob_area``, biggest first."""
    lo, hi = prof.color(color_key)
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(lo), np.array(hi))
    mask = cv2.bitwise_and(mask, _hud_mask(prof, frame.shape))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    n, _lab, stats, cents = cv2.connectedComponentsWithStats(mask)
    a_min, a_max = (int(v) for v in prof.items("blob_area"))
    out = [
        Blob(kind, int(cents[i][0]), int(cents[i][1]), int(stats[i, cv2.CC_STAT_AREA]))
        for i in range(1, n)
        if a_min <= int(stats[i, cv2.CC_STAT_AREA]) <= a_max
    ]
    return sorted(out, key=lambda b: -b.area)


def crystals(frame: BgrImage, prof: Profile) -> list[Blob]:
    out: list[Blob] = []
    for kind, key in CRYSTAL_KINDS.items():
        out.extend(find_blobs(frame, prof, key, kind))
    return out


def fungi(frame: BgrImage, prof: Profile) -> list[Blob]:
    return find_blobs(frame, prof, "fungus_hsv", "fungus")


def nearest(blobs: list[Blob], pt: tuple[float, float], kind: str | None = None) -> Blob | None:
    cands = [b for b in blobs if kind is None or b.kind == kind]
    return min(cands, key=lambda b: b.dist(pt)) if cands else None


MARKER_HUD = (420, 300)  # the item slots (white squares) score 0.79 against the pin: masked
MARKER_WIN = 30


def find_marker(
    frame: BgrImage, tmpl: BgrImage, prof: Profile, near: tuple[float, float] | None = None
) -> Blob | None:
    """The paw pin (white teardrop, yellow paw) that floats over a fungus in reach: the best
    template match with enough yellow inside, nearest ``near`` when several would do."""
    img = frame.copy()
    img[: MARKER_HUD[1], : MARKER_HUD[0]] = 0
    img[frame.shape[0] - BOTTOM_PX :, :] = 0
    res = cv2.matchTemplate(img, tmpl, cv2.TM_CCOEFF_NORMED)
    th, tw = tmpl.shape[:2]
    lo, hi = prof.color("marker_yellow_hsv")
    need = int(prof.number("marker_yellow_px"))
    cands: list[Blob] = []
    for _ in range(4):
        _mn, score, _mnl, loc = cv2.minMaxLoc(res)
        if score < prof.number("paw_marker_min"):
            break
        x, y = loc[0] + tw // 2, loc[1] + th // 2
        res[max(0, loc[1] - th) : loc[1] + th, max(0, loc[0] - tw) : loc[0] + tw] = -1
        win = frame[
            max(0, y - MARKER_WIN) : y + MARKER_WIN, max(0, x - MARKER_WIN) : x + MARKER_WIN
        ]
        yellow = cv2.inRange(cv2.cvtColor(win, cv2.COLOR_BGR2HSV), np.array(lo), np.array(hi))
        if int(np.count_nonzero(yellow)) >= need:
            cands.append(Blob("marker", x, y, int(score * 1000)))
    if not cands:
        return None
    return min(cands, key=lambda b: b.dist(near)) if near is not None else cands[0]
