"""The offline rebuild of an area's picture from the kept frames (docs/MAPPING.md §2b).

The live pass places each frame by the fused position of the moment, which is good to a few
tens of px: the minimap dot is quantised to 70 px and a second behind, the odometry drifts,
and the camera lags the animal. Pasted at those poses the tiles come out offset by a few px
to a few tens of px, textures repeat, and the animal, its tag and the moths get smeared in.

This module takes every kept frame (``ObsStore``: the frame, the animal's world position,
the animal's screen point) and

1. **registers** overlapping frames pairwise: phase correlation of the high-passed, masked
   overlap at ``build_scale`` gives the shift to a px or two, then a textured full-resolution
   patch is matched within ``build_search_px`` for the exact integer shift (normalised
   correlation must reach ``build_min_ncc``);
2. **solves** the frame origins as a pose graph: every accepted pair is an equation
   ``o_i - o_j = s_ij`` (weight 1), every kept pose a weak prior (``build_prior_weight``)
   that pins the map to the minimap; least squares, then the pairs that disagree by more than
   ``build_residual_px`` are dropped and it is solved again;
3. **composites** per 512 px tile: for each pixel the ``build_stack`` samples nearest the
   animal's light are kept and the one with the median brightness of the ``build_k`` nearest
   is the pixel, so a moth, another animal or our own animal (masked anyway) never wins,
   and no two samples are blended: every pixel is a real pixel of one frame.

The tiles, the low-resolution mosaic and the weight layer of the area are replaced; the
world's things, collisions and grid are untouched. ``build.json`` keeps the solved origins.
"""

from __future__ import annotations

import json
import math
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import lsqr

from jamcrammer.core.config import WorldConfig
from jamcrammer.modes.adventures import sight
from jamcrammer.modes.adventures.world import TILE_PX, Obs, ObsStore, TileStore, World

BgrImage = npt.NDArray[np.uint8]
Gray = npt.NDArray[np.float32]
Mask = npt.NDArray[np.bool_]
Log = Callable[[str], None]
Rect = tuple[int, int, int, int]  # y0, y1, x0, x1
FRAME_W, FRAME_H = 1920, 1080
FRAME_CACHE = 60  # decoded frames kept in memory (6 MB each)
FEATURE_CACHE = 240  # registration features kept (2.5 MB each)
LOOP_GAP = 40  # a loop-closure pair is at least this many frames apart
DARK_BLUR = 5  # the dark mask is taken on a blurred V so single dark pixels do not pit it
PATCH_MIN_VALID = 0.85  # share of a refinement patch that must be lit and unmasked
MIN_OVERLAP_PX = 64  # a thinner overlap is not registered
MIN_BOTH_SHARE = 0.05  # of the overlap, lit and unmasked in both frames
PROGRESS_EVERY = 100


@dataclass
class Frame:
    """One kept frame with its working pose."""

    idx: int
    obs: Obs
    path: Path
    origin: tuple[float, float]  # the frame's top-left in world px (pos - anchor)

    @property
    def player(self) -> tuple[float, float]:
        return (self.origin[0] + self.obs.anchor[0], self.origin[1] + self.obs.anchor[1])


@dataclass
class Pair:
    i: int
    j: int
    shift: tuple[float, float]  # o_i - o_j, world px (integer after refinement)
    response: float
    ncc: float
    weight: float = 1.0


@dataclass
class Dialog:
    """How a frame with an open game dialog is recognised (its blue X): such frames show
    the dialog over the scene and are left out."""

    template: BgrImage
    min_score: float
    roi: tuple[int, int, int, int] | None = None


def gain_table(cfg: WorldConfig, max_r: int = 1400) -> npt.NDArray[np.float32]:
    """The light's gain for every integer radius, from ``light_gain`` (piecewise linear,
    flat before the first point, held past the last)."""
    pts = sorted((float(r), float(g)) for r, g in cfg.light_gain) or [(0.0, 1.0)]
    rs = np.arange(max_r + 1, dtype=np.float32)
    xs = np.array([p[0] for p in pts], np.float32)
    ys = np.array([p[1] for p in pts], np.float32)
    return np.asarray(np.interp(rs, xs, ys, left=ys[0], right=ys[-1]), np.float32)


class _Cache:
    """A small LRU of decoded frames and their registration features; frames with an open
    dialog are remembered as such and never used."""

    def __init__(self, cfg: WorldConfig, hud_rois: list[Any], dialog: Dialog | None) -> None:
        self.cfg, self.hud, self.dialog = cfg, hud_rois, dialog
        self._frames: OrderedDict[int, BgrImage] = OrderedDict()
        self._feats: OrderedDict[int, tuple[Gray, Mask]] = OrderedDict()
        self._dialogs: dict[int, bool] = {}
        self.reads = 0
        self.gain = gain_table(cfg)

    def frame(self, f: Frame) -> BgrImage | None:
        img = self._frames.get(f.idx)
        if img is not None:
            self._frames.move_to_end(f.idx)
            return img
        raw = cv2.imread(str(f.path))
        if raw is None:
            return None
        self.reads += 1
        img = np.asarray(raw, np.uint8)
        if f.idx not in self._dialogs:
            self._dialogs[f.idx] = self.dialog is not None and (
                sight.find_dialog_x(
                    img, self.dialog.template, self.dialog.min_score, self.dialog.roi
                )
                is not None
            )
        self._frames[f.idx] = img
        while len(self._frames) > FRAME_CACHE:
            self._frames.popitem(last=False)
        return img

    def has_dialog(self, f: Frame) -> bool:
        if f.idx not in self._dialogs:
            self.frame(f)
        return self._dialogs.get(f.idx, False)

    def dialogs(self) -> int:
        return sum(1 for v in self._dialogs.values() if v)

    def valid(self, f: Frame, img: BgrImage) -> Mask:
        """Lit, unmasked pixels: not the HUD, not the animal and its tag, not the dark, not
        a frame with a dialog open."""
        if self.has_dialog(f):
            return np.zeros(img.shape[:2], np.bool_)
        keep = np.ones(img.shape[:2], np.uint8)
        for x, y, w, h in [*self.hud, f.obs.box]:
            keep[max(0, int(y)) : max(0, int(y + h)), max(0, int(x)) : max(0, int(x + w))] = 0
        v = cv2.blur(img.max(axis=2), (DARK_BLUR, DARK_BLUR))
        return np.asarray((keep > 0) & (v >= self.cfg.build_min_v), np.bool_)

    def radius(self, f: Frame, rect: tuple[int, int, int, int]) -> Gray:
        """Distance from the animal's light for the frame px of ``rect`` (y0, y1, x0, x1)."""
        y0, y1, x0, x1 = rect
        ax, ay = f.obs.anchor
        yy, xx = np.mgrid[y0:y1, x0:x1]
        return np.asarray(np.hypot(xx - ax, yy - ay), np.float32)

    def corrected(self, src: BgrImage, radius: Gray) -> tuple[BgrImage, Mask]:
        """The pixels divided by the light's gain at their radius (one exposure for the
        whole map), and which of them had enough light to be trusted."""
        g = self.gain[np.minimum(radius, len(self.gain) - 1).astype(np.int32)]
        usable = g >= self.cfg.build_min_gain
        scaled = src.astype(np.float32) / np.maximum(g, self.cfg.build_min_gain)[..., None]
        return np.asarray(np.clip(scaled, 0, 255), np.uint8), np.asarray(usable, np.bool_)

    def features(self, f: Frame) -> tuple[Gray, Mask] | None:
        got = self._feats.get(f.idx)
        if got is not None:
            self._feats.move_to_end(f.idx)
            return got
        img = self.frame(f)
        if img is None or self.has_dialog(f):
            return None
        s = self.cfg.build_scale
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(g, None, fx=s, fy=s, interpolation=cv2.INTER_AREA).astype(np.float32)
        small = small - cv2.GaussianBlur(small, (0, 0), self.cfg.build_sigma)
        valid = self.valid(f, img).astype(np.uint8)
        vs = cv2.resize(valid, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_AREA) > 0
        vs = cv2.erode(vs.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        small[~vs] = 0.0
        out = (np.asarray(small, np.float32), np.asarray(vs, np.bool_))
        self._feats[f.idx] = out
        while len(self._feats) > FEATURE_CACHE:
            self._feats.popitem(last=False)
        return out


# --- registration ------------------------------------------------------------------------


def _overlap(a: Frame, b: Frame, light_px: int) -> float:
    """How much of the two lit discs overlap, by the poses (0-1)."""
    d = math.dist(a.player, b.player)
    return max(0.0, 1.0 - d / (2.0 * max(1, light_px)))


def candidates(frames: list[Frame], cfg: WorldConfig) -> list[tuple[int, int]]:
    """Pairs to register: for each frame its recent overlapping predecessors and a few
    older ones (loop closures), by the poses as kept."""
    out: list[tuple[int, int]] = []
    for j in range(1, len(frames)):
        fj = frames[j]
        cut = max(0, j - LOOP_GAP)
        for lo, hi in ((cut, j), (0, cut)):
            scored = [(i, _overlap(frames[i], fj, cfg.light_px)) for i in range(lo, hi)]
            scored = [p for p in scored if p[1] >= cfg.build_min_overlap]
            scored.sort(key=lambda p: -p[1])
            out.extend((i, j) for i, _ov in scored[: cfg.build_pairs])
    return out


@dataclass
class _Overlap:
    """The two frames' common part at build scale, masked to what both show."""

    a: Gray
    b: Gray
    both: Mask
    at: tuple[int, int]  # the crop's top-left in frame-i px (build scale)


def _crop_overlap(
    feats_i: tuple[Gray, Mask], feats_j: tuple[Gray, Mask], pred: tuple[float, float], s: float
) -> _Overlap | None:
    """A world point at frame-i px ``p`` is at ``p + pred`` in frame j."""
    feat_i, val_i = feats_i
    feat_j, val_j = feats_j
    sx, sy = round(pred[0] * s), round(pred[1] * s)
    h, w = feat_i.shape
    x0, y0 = max(0, -sx), max(0, -sy)
    x1, y1 = min(w, w - sx), min(h, h - sy)
    if x1 - x0 < MIN_OVERLAP_PX or y1 - y0 < MIN_OVERLAP_PX:
        return None
    both = val_i[y0:y1, x0:x1] & val_j[y0 + sy : y1 + sy, x0 + sx : x1 + sx]
    if np.count_nonzero(both) < MIN_BOTH_SHARE * both.size:
        return None
    a = np.where(both, feat_i[y0:y1, x0:x1], 0.0).astype(np.float32)
    b = np.where(both, feat_j[y0 + sy : y1 + sy, x0 + sx : x1 + sx], 0.0).astype(np.float32)
    return _Overlap(a, b, both, (x0, y0))


def register(fi: Frame, fj: Frame, cache: _Cache, cfg: WorldConfig) -> Pair | None:
    """The exact shift ``o_i - o_j`` between two overlapping frames, or None when the
    pair cannot be trusted."""
    got_i, got_j = cache.features(fi), cache.features(fj)
    if got_i is None or got_j is None:
        return None
    s = cfg.build_scale
    pred = (fi.origin[0] - fj.origin[0], fi.origin[1] - fj.origin[1])  # o_i - o_j
    ov = _crop_overlap(got_i, got_j, pred, s)
    if ov is None:
        return None
    win = cv2.createHanningWindow((ov.a.shape[1], ov.a.shape[0]), cv2.CV_32F)
    (rx, ry), resp = cv2.phaseCorrelate(ov.a, ov.b, win)
    coarse = (pred[0] + rx / s, pred[1] + ry / s)
    if resp < cfg.build_min_response or math.dist(coarse, pred) > cfg.build_max_err_px:
        return None
    refined = _refine(fi, fj, cache=cache, cfg=cfg, coarse=coarse, overlap=ov)
    if refined is None:
        return None
    shift, ncc = refined
    return Pair(fi.idx, fj.idx, shift, float(resp), ncc)


def _patch_centre(feat_i: Gray, ov: _Overlap, ps: int) -> tuple[int, int] | None:
    """The most textured lit spot of the overlap (frame-i px at build scale)."""
    x0, y0 = ov.at
    sub = np.abs(feat_i[y0 : y0 + ov.both.shape[0], x0 : x0 + ov.both.shape[1]])
    sub = np.where(ov.both, sub, 0.0).astype(np.float32)
    energy = cv2.boxFilter(sub, -1, (ps, ps), normalize=True)
    share = cv2.boxFilter(ov.both.astype(np.float32), -1, (ps, ps), normalize=True)
    energy[share < PATCH_MIN_VALID] = 0.0
    m = ps // 2 + 1
    energy[:m, :], energy[-m:, :], energy[:, :m], energy[:, -m:] = 0, 0, 0, 0
    if float(np.max(energy)) <= 0:
        return None
    cy, cx = np.unravel_index(int(np.argmax(energy)), energy.shape)
    return (x0 + int(cx), y0 + int(cy))


def _refine(
    fi: Frame,
    fj: Frame,
    *,
    cache: _Cache,
    cfg: WorldConfig,
    coarse: tuple[float, float],
    overlap: _Overlap,
) -> tuple[tuple[float, float], float] | None:
    """The integer shift at full resolution: the most textured lit patch of the overlap in
    frame i, matched in frame j around the coarse shift."""
    img_i, img_j = cache.frame(fi), cache.frame(fj)
    feats = cache.features(fi)
    if img_i is None or img_j is None or feats is None:
        return None
    s = cfg.build_scale
    p = cfg.build_patch_px
    centre = _patch_centre(feats[0], overlap, max(8, int(p * s)))
    if centre is None:
        return None
    gi = cv2.cvtColor(img_i, cv2.COLOR_BGR2GRAY)
    gj = cv2.cvtColor(img_j, cv2.COLOR_BGR2GRAY)
    tx0, ty0 = int(centre[0] / s) - p // 2, int(centre[1] / s) - p // 2
    r = cfg.build_search_px
    jx0, jy0 = round(tx0 + coarse[0]) - r, round(ty0 + coarse[1]) - r
    jx1, jy1 = jx0 + p + 2 * r, jy0 + p + 2 * r
    inside = (
        0 <= tx0 <= gi.shape[1] - p
        and 0 <= ty0 <= gi.shape[0] - p
        and jx0 >= 0
        and jy0 >= 0
        and jx1 <= gj.shape[1]
        and jy1 <= gj.shape[0]
    )
    if not inside:
        return None
    tmpl = gi[ty0 : ty0 + p, tx0 : tx0 + p]
    res = cv2.matchTemplate(gj[jy0:jy1, jx0:jx1], tmpl, cv2.TM_CCOEFF_NORMED)
    _mn, ncc, _mnl, loc = cv2.minMaxLoc(res)
    if ncc < cfg.build_min_ncc:
        return None
    return (float(jx0 + loc[0] - tx0), float(jy0 + loc[1] - ty0)), float(ncc)


# --- the pose graph ----------------------------------------------------------------------


def solve(frames: list[Frame], pairs: list[Pair], prior_weight: float) -> list[tuple[float, float]]:
    """Least-squares origins from the pairs (``o_i - o_j = shift``) and the kept poses as
    weak priors. Returns one origin per frame, in order."""
    n = len(frames)
    if n == 0:
        return []
    rows: list[int] = []
    cols: list[int] = []
    vals: list[float] = []
    bx: list[float] = []
    by: list[float] = []
    for r, p in enumerate(pairs):
        rows += [r, r]
        cols += [p.i, p.j]
        vals += [p.weight, -p.weight]
        bx.append(p.weight * p.shift[0])
        by.append(p.weight * p.shift[1])
    for k, f in enumerate(frames):
        rows.append(len(pairs) + k)
        cols.append(k)
        vals.append(prior_weight)
        bx.append(prior_weight * f.origin[0])
        by.append(prior_weight * f.origin[1])
    a = coo_matrix((vals, (rows, cols)), shape=(len(pairs) + n, n)).tocsr()
    ox = lsqr(a, np.asarray(bx), atol=1e-8, btol=1e-8)[0]
    oy = lsqr(a, np.asarray(by), atol=1e-8, btol=1e-8)[0]
    return [(float(ox[k]), float(oy[k])) for k in range(n)]


def residuals(origins: list[tuple[float, float]], pairs: list[Pair]) -> list[float]:
    out = []
    for p in pairs:
        dx = origins[p.i][0] - origins[p.j][0] - p.shift[0]
        dy = origins[p.i][1] - origins[p.j][1] - p.shift[1]
        out.append(math.hypot(dx, dy))
    return out


# --- the composite -----------------------------------------------------------------------


class _Tile:
    """The best-lit samples of one tile, by pixel."""

    def __init__(self, k: int, size: int) -> None:
        self.k, self.size = k, size
        self.vals = np.zeros((k, size, size, 3), np.uint8)
        self.rad = np.full((k, size, size), np.inf, np.float32)

    def insert(self, rect: Rect, src: BgrImage, radius: Gray, valid: Mask) -> None:
        y0, y1, x0, x1 = rect
        rad = self.rad[:, y0:y1, x0:x1]
        slot = np.argmax(rad, axis=0)  # the worst-lit sample of each pixel
        worst = np.take_along_axis(rad, slot[None], axis=0)[0]
        better = valid & (radius < worst)
        if not better.any():
            return
        vals = self.vals[:, y0:y1, x0:x1]
        for k in range(self.k):
            sel = better & (slot == k)
            if sel.any():
                vals[k][sel] = src[sel]
                rad[k][sel] = radius[sel]

    def finish(self, k_median: int) -> tuple[BgrImage, npt.NDArray[np.uint8]]:
        """The pixel of each cell: of the k nearest-to-the-light samples, the one with the
        median brightness (the nearest when there are fewer than three)."""
        order = np.argsort(self.rad, axis=0)
        rad = np.take_along_axis(self.rad, order, axis=0)
        vals = np.take_along_axis(self.vals, order[..., None], axis=0)
        n = np.count_nonzero(np.isfinite(rad), axis=0).astype(np.uint8)
        k = min(k_median, self.k)
        top = vals[:k]
        v = top.max(axis=3).astype(np.int32)
        v[~np.isfinite(rad[:k])] = -1
        # with n >= 3 the middle of the three brightnesses; with fewer, the nearest sample
        idx = np.zeros(n.shape, np.int64)
        if k >= 3:
            three = np.argsort(v[:3], axis=0)[1]
            idx = np.where(n >= 3, three, 0)
        out = np.take_along_axis(top, idx[None, ..., None], axis=0)[0]
        out[n == 0] = 0
        return np.asarray(out, np.uint8), n


def _index_tiles(frames: list[Frame], size: tuple[int, int]) -> dict[tuple[int, int], list[Frame]]:
    """Which frames touch which tile."""
    g = TILE_PX
    aw, ah = size
    by_tile: dict[tuple[int, int], list[Frame]] = {}
    for f in frames:
        ox, oy = round(f.origin[0]), round(f.origin[1])
        for ty in range(max(0, oy // g), min((ah - 1) // g, (oy + FRAME_H - 1) // g) + 1):
            for tx in range(max(0, ox // g), min((aw - 1) // g, (ox + FRAME_W - 1) // g) + 1):
                by_tile.setdefault((tx, ty), []).append(f)
    return by_tile


def _paste(tile: _Tile, key: tuple[int, int], f: Frame, cache: _Cache, valid_all: Mask) -> None:
    """One frame's lit, unmasked part into the tile's sample stacks, gain-corrected."""
    img = cache.frame(f)
    if img is None:
        return
    g = TILE_PX
    tx, ty = key
    ox, oy = round(f.origin[0]), round(f.origin[1])
    x0, y0 = max(tx * g, ox), max(ty * g, oy)
    x1, y1 = min((tx + 1) * g, ox + FRAME_W), min((ty + 1) * g, oy + FRAME_H)
    if x1 <= x0 or y1 <= y0:
        return
    rect = (y0 - oy, y1 - oy, x0 - ox, x1 - ox)
    valid = valid_all[rect[0] : rect[1], rect[2] : rect[3]]
    if not valid.any():
        return
    radius = cache.radius(f, rect)
    src, usable = cache.corrected(img[rect[0] : rect[1], rect[2] : rect[3]], radius)
    tile.insert((y0 - ty * g, y1 - ty * g, x0 - tx * g, x1 - tx * g), src, radius, valid & usable)


def _lowres(world: World, area: str, key: tuple[int, int], img: BgrImage, n: Any) -> None:
    """The tile into the area's low-resolution mosaic and weight layers."""
    a = world.area(area)
    ms = world.cfg.mosaic_scale
    g = TILE_PX
    tx, ty = key
    mx0, my0 = int(tx * g * ms), int(ty * g * ms)
    mx1 = min(a.mosaic.shape[1], int((tx + 1) * g * ms))
    my1 = min(a.mosaic.shape[0], int((ty + 1) * g * ms))
    if mx1 <= mx0 or my1 <= my0:
        return
    size = (mx1 - mx0, my1 - my0)
    a.mosaic[my0:my1, mx0:mx1] = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
    wt = np.minimum(n, 255).astype(np.uint8)
    a.weight[my0:my1, mx0:mx1] = cv2.resize(wt, size, interpolation=cv2.INTER_NEAREST)


def _row(
    keys: list[tuple[int, int]],
    by_tile: dict[tuple[int, int], list[Frame]],
    cache: _Cache,
    stack: int,
    k_median: int,
) -> list[tuple[tuple[int, int], tuple[BgrImage, Any]]]:
    """One row of tiles at a time, so each frame is decoded once per row it touches."""
    tiles = {k: _Tile(stack, TILE_PX) for k in keys}
    touching: dict[int, Frame] = {}
    for k in keys:
        for f in by_tile[k]:
            touching[f.idx] = f
    for f in sorted(touching.values(), key=lambda fr: fr.idx):
        img = cache.frame(f)
        if img is None:
            continue
        valid_all = cache.valid(f, img)
        if not valid_all.any():
            continue
        for k in keys:
            if f in by_tile[k]:
                _paste(tiles[k], k, f, cache, valid_all)
    out = []
    for k in keys:
        img_t, n = tiles[k].finish(k_median)
        if n.any():
            out.append((k, (img_t, n)))
    return out


def composite(
    frames: list[Frame], cache: _Cache, world: World, *, area: str, log: Log
) -> dict[str, Any]:
    """Every frame into the tiles of ``area`` at its solved origin; then the low-resolution
    mosaic and weight from the tiles. The area's old tiles are removed first."""
    cfg = world.cfg
    folder = world.tiles.folder
    if folder is None:
        msg = "the world has no tile folder"
        raise ValueError(msg)
    area_dir = folder / area
    if area_dir.exists():
        for old in area_dir.glob("*_*.png"):
            old.unlink()
    area_dir.mkdir(parents=True, exist_ok=True)
    world.tiles = TileStore(folder, TILE_PX)
    by_tile = _index_tiles(frames, world.size)
    a = world.area(area)
    a.mosaic[:] = 0
    a.weight[:] = 0
    written = 0
    t0 = time.monotonic()
    rows = sorted({ty for _tx, ty in by_tile})
    for n_row, ty in enumerate(rows):
        keys = sorted(k for k in by_tile if k[1] == ty)
        for k, (img_t, n) in _row(keys, by_tile, cache, cfg.build_stack, cfg.build_k):
            cv2.imwrite(str(area_dir / f"{k[0]}_{k[1]}.png"), img_t)
            written += 1
            _lowres(world, area, k, img_t, n)
        log(f"  tile row {n_row + 1}/{len(rows)}: {written} written, {time.monotonic() - t0:.0f} s")
    world.dirty = True
    took = round(time.monotonic() - t0, 1)
    return {"tiles": written, "tiles_touched": len(by_tile), "composite_s": took}


# --- the whole thing ---------------------------------------------------------------------


def load_frames(obs_dirs: list[Path], area: str) -> list[Frame]:
    frames: list[Frame] = []
    for d in obs_dirs:
        for o in ObsStore.load(d):
            path = d / o.file
            if o.area != area or not path.exists():
                continue
            origin = (o.pos[0] - o.anchor[0], o.pos[1] - o.anchor[1])
            frames.append(Frame(len(frames), o, path, origin))
    frames.sort(key=lambda f: f.obs.ts)
    for k, f in enumerate(frames):
        f.idx = k
    return frames


def _register_all(frames: list[Frame], cache: _Cache, cfg: WorldConfig, log: Log) -> dict[str, Any]:
    """Pairs, the solve, the outlier pass; the frames' origins are updated in place."""
    cands = candidates(frames, cfg)
    log(f"  {len(cands)} candidate pairs")
    t0 = time.monotonic()
    pairs: list[Pair] = []
    for tried, (i, j) in enumerate(cands, 1):
        p = register(frames[i], frames[j], cache, cfg)
        if p is not None:
            pairs.append(p)
        if tried % PROGRESS_EVERY == 0:
            log(
                f"  registered {tried}/{len(cands)}: {len(pairs)} ok, {time.monotonic() - t0:.0f} s"
            )
    origins = solve(frames, pairs, cfg.build_prior_weight)
    res = residuals(origins, pairs)
    kept = [p for p, r in zip(pairs, res, strict=True) if r <= cfg.build_residual_px]
    if len(kept) < len(pairs):
        origins = solve(frames, kept, cfg.build_prior_weight)
        res = residuals(origins, kept)
    linked = {p.i for p in kept} | {p.j for p in kept}
    moved = [math.dist(o, f.origin) for o, f in zip(origins, frames, strict=True)]
    for f, o in zip(frames, origins, strict=True):
        f.origin = (round(o[0]), round(o[1]))
    stats = {
        "pairs_candidates": len(cands),
        "pairs_accepted": len(pairs),
        "pairs_dropped": len(pairs) - len(kept),
        "frames_linked": len(linked),
        "residual_px_median": round(float(np.median(res)), 2) if res else None,
        "residual_px_max": round(float(max(res)), 2) if res else None,
        "moved_px_median": round(float(np.median(moved)), 1),
        "moved_px_max": round(float(max(moved)), 1),
        "register_s": round(time.monotonic() - t0, 1),
    }
    log(f"  solved: {stats}")
    return stats


def rebuild(
    world_folder: Path,
    obs_dirs: list[Path],
    cfg: WorldConfig,
    *,
    area: str,
    hud_rois: list[Any],
    dialog: Dialog | None = None,
    register_pairs: bool = True,
    log: Log = lambda _m: None,
) -> dict[str, Any]:
    """Register, solve and composite every kept frame of ``area`` from ``obs_dirs`` into the
    world under ``world_folder``; save it. Returns the statistics (also ``build.json``)."""
    world = World.load(world_folder, cfg)
    if world is None:
        msg = f"no world at {world_folder}"
        raise ValueError(msg)
    world.attach_tiles(world_folder / "tiles")
    frames = load_frames(obs_dirs, area)
    log(f"rebuild {area}: {len(frames)} frames from {len(obs_dirs)} session(s)")
    if not frames:
        return {"frames": 0}
    cache = _Cache(cfg, hud_rois, dialog)
    stats: dict[str, Any] = {"frames": len(frames), "area": area}
    if register_pairs:
        stats.update(_register_all(frames, cache, cfg, log))
    else:
        for f in frames:
            f.origin = (round(f.origin[0]), round(f.origin[1]))
    stats.update(composite(frames, cache, world, area=area, log=log))
    world.save(world_folder)
    stats["frame_reads"] = cache.reads
    stats["frames_with_dialog"] = cache.dialogs()
    doc = {
        "saved": time.strftime("%Y-%m-%d %H:%M:%S"),
        "stats": stats,
        "origins": [
            {"file": str(f.path), "origin": list(f.origin), "anchor": list(f.obs.anchor)}
            for f in frames
        ],
    }
    (world_folder / "build.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    log(f"  done: {stats}")
    return stats
