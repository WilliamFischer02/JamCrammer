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


MARKER_HUD = (420, 300)  # the item slots (white squares) are pins to a colour test: masked


def find_marker(
    frame: BgrImage, prof: Profile, near: tuple[float, float] | None = None
) -> Blob | None:
    """The paw pin that floats over a fungus in reach: a white blob of the pin's size
    (``marker_area``) with at least ``marker_yellow_px`` of paw inside its box. The paw is
    bright yellow when the game is ready and paler just before, so the yellow test is
    loose; a template did not survive William's screenshot (0.62) while this did."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    lo, hi = prof.color("marker_white_hsv")
    white = cv2.inRange(hsv, np.array(lo), np.array(hi))
    white[: MARKER_HUD[1], : MARKER_HUD[0]] = 0
    white[frame.shape[0] - BOTTOM_PX :, :] = 0
    ylo, yhi = prof.color("marker_yellow_hsv")
    yellow = cv2.inRange(hsv, np.array(ylo), np.array(yhi))
    a_min, a_max = (int(v) for v in prof.items("marker_area"))
    need = int(prof.number("marker_yellow_px"))
    n, _lab, st, cen = cv2.connectedComponentsWithStats(white)
    cands: list[Blob] = []
    for i in range(1, n):
        x, y, w, h, a = (int(v) for v in st[i])
        if not (a_min <= a <= a_max) or w < 30 or w > 120 or h < 40 or h > 160:
            continue
        paw = int(np.count_nonzero(yellow[y : y + h, x : x + w]))
        if paw >= need:
            cands.append(Blob("marker", int(cen[i][0]), int(cen[i][1]), a))
    if not cands:
        return None
    return min(cands, key=lambda b: b.dist(near)) if near is not None else cands[0]


GLOW_V = 235  # a lit crystal's glow
DARK_V = 160  # ... where it was dark before


def new_glow(before: BgrImage, after: BgrImage, at: tuple[int, int], radius: int) -> int:
    """Pixels within ``radius`` of ``at`` that are bright now and were dark before (the
    camera still): a crystal lighting up. Measured on the recordings: a few thousand."""
    x, y = at
    sl = (slice(max(0, y - radius), y + radius), slice(max(0, x - radius), x + radius))
    vb = cv2.cvtColor(before[sl], cv2.COLOR_BGR2HSV)[:, :, 2]
    va = cv2.cvtColor(after[sl], cv2.COLOR_BGR2HSV)[:, :, 2]
    if vb.shape != va.shape:
        return 0
    return int(np.count_nonzero((va >= GLOW_V) & (vb < DARK_V)))


class CourageText:
    """The "Courage" of the "COURAGE +25" that pops over a crystal as it lights: a masked
    template match (only the blue letters count). 0.9+ on 12 of 39 recorded uses and on
    none of 100 other clicks; live polling catches the two seconds it shows far better than
    the recordings' sparse keyframes did."""

    def __init__(self, tmpl: BgrImage, prof: Profile) -> None:
        self.tmpl = tmpl
        lo, hi = prof.color("courage_mask_hsv")
        mask = cv2.inRange(cv2.cvtColor(tmpl, cv2.COLOR_BGR2HSV), np.array(lo), np.array(hi))
        self.mask = cv2.dilate(mask, np.ones((3, 3), np.uint8))
        self.min = prof.number("courage_min")

    def near(self, frame: BgrImage, at: tuple[int, int], radius: int) -> float:
        """The best score within ``radius`` of ``at``, 0 when none."""
        res = cv2.matchTemplate(frame, self.tmpl, cv2.TM_CCORR_NORMED, mask=self.mask)
        res = np.nan_to_num(res)
        _mn, score, _mnl, loc = cv2.minMaxLoc(res)
        th, tw = self.tmpl.shape[:2]
        cx, cy = loc[0] + tw // 2, loc[1] + th // 2
        return float(score) if float(np.hypot(cx - at[0], cy - at[1])) <= radius else 0.0
