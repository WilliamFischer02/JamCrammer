"""The world map of an adventure (docs/MAPPING.md): what the bot knows about a level,
in **world pixels**, scene pixels anchored to the minimap. One world px is one scene px; the
minimap's top-left corner is the origin and ``scale`` (70 on The Mystery Below) scene px make
one minimap px, so the minimap dot gives an absolute position and a screen point seen from a
known position lands on the map at ``pos + (screen - avatar)``.

Per area (one per minimap: the moth chamber, the torch hall, the city) it keeps:

- an **occupancy grid** of ``grid_px`` cells: unknown, floor (the minimap says so, coarse),
  free (the animal walked there) or blocked (the animal walked into something there);
- a **mosaic**: the lit disc around the animal pasted at every observed position, kept at a
  downscale, so a person (or Claude) can see the whole area at once and the localiser can
  match a frame against it;
- the **things**: crystals, fungi, rocks, bridges, torches, mirrors ... each with a kind, a
  status, where it came from (the recordings, a scan, Claude's word, the bot) and a
  confidence; and the **collisions**: where a probe stopped, and heading which way.

The recordings' level model (``level.py``) seeds it: floors become floor cells, spots become
things of confidence 0.4. The pilot's probes, scans and marks refine it; the bot reads it.
Everything is saved as JSON plus PNG layers under ``profiles/levels/<target>/world/``.
"""

from __future__ import annotations

import heapq
import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt

from jamcrammer.core.config import WorldConfig
from jamcrammer.modes.adventures.level import Level

BgrImage = npt.NDArray[np.uint8]
Point = tuple[float, float]
Cell = tuple[int, int]
WORLD_VERSION = 1
UNKNOWN, FLOOR, FREE, BLOCKED = 0, 1, 2, 3
STATE_NAMES = {UNKNOWN: "unknown", FLOOR: "floor", FREE: "free", BLOCKED: "blocked"}
GRID_PNG_STEP = 80  # grid states are saved as 0 / 80 / 160 / 240
# what the recordings' spot kinds are called in the world
LEVEL_KINDS = {
    "crystal": "crystal",
    "fungus": "fungus",
    "boom_target": "boom_target",
    "boom_pile": "boom_pile",
    "repeat": "clickable",
    "marker": "marker",
    "door": "door",
}
LEVEL_CONFIDENCE = 0.4  # a spot from the recordings: a few hundred scene px of error
KIND_COLOURS: dict[str, tuple[int, int, int]] = {  # BGR, for the rendered map
    "crystal": (0, 0, 255),
    "fungus": (0, 220, 0),
    "rock": (160, 160, 160),
    "decoy": (255, 0, 200),
    "bridge": (0, 200, 255),
    "mushroom": (0, 200, 255),
    "torch": (0, 140, 255),
    "basin": (0, 140, 255),
    "mirror": (255, 200, 0),
    "switch": (255, 255, 0),
    "boom_pile": (0, 255, 160),
    "boom_target": (0, 120, 255),
    "clickable": (200, 200, 0),
    "marker": (0, 220, 255),
    "door": (0, 220, 255),
    "npc": (255, 120, 255),
    "enemy": (0, 0, 180),
    "spawner": (0, 0, 120),
    "flame": (0, 60, 255),
    "rune": (255, 255, 255),
    "chest": (0, 215, 255),
    "ledge": (120, 60, 0),
    "note": (180, 180, 180),
}
STATE_COLOURS: dict[int, tuple[int, int, int]] = {
    UNKNOWN: (0, 0, 0),
    FLOOR: (60, 60, 60),
    FREE: (70, 95, 70),
    BLOCKED: (40, 40, 200),
}
NEIGHBOURS = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0)] + [
    (dx, dy, math.sqrt(2.0)) for dx in (-1, 1) for dy in (-1, 1)
]
DIRECTIONS: dict[str, tuple[int, int]] = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
    "ul": (-1, -1),
    "ur": (1, -1),
    "dl": (-1, 1),
    "dr": (1, 1),
}
DIRECTION_KEYS: dict[str, set[str]] = {
    "up": {"up"},
    "down": {"down"},
    "left": {"left"},
    "right": {"right"},
    "ul": {"up", "left"},
    "ur": {"up", "right"},
    "dl": {"down", "left"},
    "dr": {"down", "right"},
}


TILE_PX = 512  # full-resolution mosaic tiles on disk (the Mapping tab's picture)


class TileStore:
    """The full-resolution mosaic, as PNG tiles under ``<world>/tiles/<area>/<tx>_<ty>.png``,
    written only where something was observed, so the map fills in like an explored chart.
    The low-resolution mosaic in memory is for reading the whole area and for matching;
    these tiles are the picture William looks at and can edit."""

    def __init__(self, folder: Path | None, tile_px: int = TILE_PX) -> None:
        self.folder = folder
        self.tile_px = tile_px
        self._cache: dict[tuple[str, int, int], BgrImage] = {}
        self._dirty: set[tuple[str, int, int]] = set()

    def path(self, area: str, tx: int, ty: int) -> Path | None:
        if self.folder is None:
            return None
        return self.folder / area / f"{tx}_{ty}.png"

    def get(self, area: str, tx: int, ty: int) -> BgrImage:
        key = (area, tx, ty)
        img = self._cache.get(key)
        if img is None:
            path = self.path(area, tx, ty)
            raw = cv2.imread(str(path)) if path is not None and path.exists() else None
            img = (
                np.asarray(raw, np.uint8)
                if raw is not None and raw.shape[:2] == (self.tile_px, self.tile_px)
                else np.zeros((self.tile_px, self.tile_px, 3), np.uint8)
            )
            self._cache[key] = img
        return img

    def mark(self, area: str, tx: int, ty: int) -> None:
        self._dirty.add((area, tx, ty))

    def flush(self) -> int:
        """Write the changed tiles. Returns how many."""
        n = 0
        for key in sorted(self._dirty):
            path = self.path(*key)
            if path is None:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(path), self._cache[key])
            n += 1
        self._dirty.clear()
        return n

    def tiles(self, area: str) -> list[tuple[int, int]]:
        """Every tile of the area on disk or in memory."""
        out = {k[1:] for k in self._cache if k[0] == area}
        if self.folder is not None and (self.folder / area).exists():
            for f in (self.folder / area).glob("*_*.png"):
                tx, ty = f.stem.split("_")
                out.add((int(tx), int(ty)))
        return sorted(out)

    def bounds(self, area: str) -> tuple[int, int, int, int] | None:
        """(x0, y0, x1, y1) world px covered by the area's tiles, None when there are none."""
        ts = self.tiles(area)
        if not ts:
            return None
        g = self.tile_px
        return (
            min(t[0] for t in ts) * g,
            min(t[1] for t in ts) * g,
            (max(t[0] for t in ts) + 1) * g,
            (max(t[1] for t in ts) + 1) * g,
        )

    def export(self, area: str, path: Path) -> tuple[int, int, int, int] | None:
        """Stitch the area's tiles into one full-resolution PNG (the observed bounding box).
        Returns the box in world px, or None when nothing has been observed."""
        box = self.bounds(area)
        if box is None:
            return None
        x0, y0, x1, y1 = box
        img = np.zeros((y1 - y0, x1 - x0, 3), np.uint8)
        g = self.tile_px
        for tx, ty in self.tiles(area):
            img[ty * g - y0 : (ty + 1) * g - y0, tx * g - x0 : (tx + 1) * g - x0] = self.get(
                area, tx, ty
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(path), img)
        return box


@dataclass
class Obj:
    """A thing in the world: where it is, what it is, how sure we are."""

    id: int
    kind: str  # crystal | fungus | rock | decoy | bridge | torch | mirror | switch | ...
    area: str
    x: float  # world px
    y: float
    status: str = ""  # unlit | lit | muddy | broken | ... free text per kind
    source: str = ""  # level | scan | claude | bot
    confidence: float = 0.5
    note: str = ""
    seen: int = 1
    ts: float = 0.0
    shot: str = ""  # a frame that shows it
    gone: bool = False  # Claude or the bot found it is not there (kept for the record)

    def dist(self, pt: Point) -> float:
        return float(math.hypot(self.x - pt[0], self.y - pt[1]))


@dataclass
class Collision:
    """A probe stopped here, heading (dx, dy): a wall, a ledge, a rock, an NPC."""

    area: str
    x: float
    y: float
    dx: int
    dy: int
    ts: float = 0.0
    source: str = "probe"
    note: str = ""


class Area:
    """One minimap's worth of world: the grid and the mosaic."""

    def __init__(self, name: str, w: int, h: int, cfg: WorldConfig) -> None:
        self.name, self.w, self.h, self.cfg = name, w, h, cfg
        g = cfg.grid_px
        self.grid: npt.NDArray[np.uint8] = np.zeros((h // g + 1, w // g + 1), np.uint8)
        mw, mh = int(w * cfg.mosaic_scale) + 1, int(h * cfg.mosaic_scale) + 1
        self.mosaic: BgrImage = np.zeros((mh, mw, 3), np.uint8)
        self.weight: npt.NDArray[np.uint8] = np.zeros((mh, mw), np.uint8)

    def cell(self, p: Point) -> Cell:
        return (int(p[0] // self.cfg.grid_px), int(p[1] // self.cfg.grid_px))

    def centre(self, c: Cell) -> Point:
        g = self.cfg.grid_px
        return ((c[0] + 0.5) * g, (c[1] + 0.5) * g)

    def inside(self, c: Cell) -> bool:
        return bool(0 <= c[1] < self.grid.shape[0] and 0 <= c[0] < self.grid.shape[1])

    def state(self, p: Point) -> int:
        c = self.cell(p)
        return int(self.grid[c[1], c[0]]) if self.inside(c) else BLOCKED

    def set_state(self, c: Cell, state: int) -> None:
        if self.inside(c):
            self.grid[c[1], c[0]] = state

    def counts(self) -> dict[str, int]:
        return {STATE_NAMES[s]: int(np.count_nonzero(self.grid == s)) for s in STATE_NAMES}

    def observed(self) -> float:
        """Share of the mosaic that has been seen at least once."""
        return float(np.count_nonzero(self.weight)) / float(self.weight.size)


class World:
    def __init__(
        self,
        target: str,
        scale: float,
        avatar: tuple[int, int],
        minimap_roi: tuple[int, int, int, int],
        cfg: WorldConfig | None = None,
    ) -> None:
        self.target = target
        self.scale = float(scale)
        self.avatar = (int(avatar[0]), int(avatar[1]))
        self.minimap_roi = tuple(int(v) for v in minimap_roi)
        self.cfg = cfg or WorldConfig()
        self.areas: dict[str, Area] = {}
        self.objects: list[Obj] = []
        self.collisions: list[Collision] = []
        self.notes: list[dict[str, Any]] = []
        self.sessions: list[str] = []
        self.dirty = False
        self.tiles = TileStore(None)

    def attach_tiles(self, folder: Path) -> None:
        """Keep the full-resolution tiles under ``folder`` (``<world>/tiles``)."""
        if self.tiles.folder != folder:
            self.tiles = TileStore(folder, self.tiles.tile_px)

    @classmethod
    def from_level(cls, lvl: Level, cfg: WorldConfig | None = None) -> World:
        w = cls(lvl.target, lvl.scale, lvl.avatar, lvl.minimap_roi, cfg)
        w.import_level(lvl)
        return w

    # --- coordinates ---------------------------------------------------------------------

    @property
    def size(self) -> tuple[int, int]:
        """World px covered by one minimap."""
        _mx, _my, mw, mh = self.minimap_roi
        return (math.ceil(mw * self.scale), math.ceil(mh * self.scale))

    def dot_to_world(self, dot: Point) -> Point:
        mx, my = self.minimap_roi[0], self.minimap_roi[1]
        return ((dot[0] - mx) * self.scale, (dot[1] - my) * self.scale)

    def world_to_dot(self, p: Point) -> Point:
        mx, my = self.minimap_roi[0], self.minimap_roi[1]
        return (mx + p[0] / self.scale, my + p[1] / self.scale)

    def screen_to_world(self, pos: Point, screen: Point) -> Point:
        """A screen point seen while the animal (at ``avatar`` on screen) was at ``pos``."""
        return (pos[0] + screen[0] - self.avatar[0], pos[1] + screen[1] - self.avatar[1])

    def world_to_screen(self, pos: Point, p: Point) -> Point:
        return (self.avatar[0] + p[0] - pos[0], self.avatar[1] + p[1] - pos[1])

    # --- areas and the grid --------------------------------------------------------------

    def area(self, name: str) -> Area:
        a = self.areas.get(name)
        if a is None:
            w, h = self.size
            a = Area(name, w, h, self.cfg)
            self.areas[name] = a
            self.dirty = True
        return a

    def mark_track(self, area: str, pos: Point) -> None:
        """The animal stood here: a free cell, whatever the map said."""
        a = self.area(area)
        c = a.cell(pos)
        if a.inside(c) and a.grid[c[1], c[0]] != FREE:
            a.set_state(c, FREE)
            self.dirty = True

    def add_collision(
        self,
        area: str,
        pos: Point,
        heading: tuple[int, int],
        *,
        ts: float = 0.0,
        source: str = "probe",
        note: str = "",
    ) -> Collision:
        """A probe stopped at ``pos`` heading ``heading``: the cell ahead is blocked, the
        cell under the animal is free."""
        a = self.area(area)
        col = Collision(
            area, float(pos[0]), float(pos[1]), heading[0], heading[1], ts, source, note
        )
        self.collisions.append(col)
        here = a.cell(pos)
        g = self.cfg.grid_px
        ahead = a.cell((pos[0] + heading[0] * g, pos[1] + heading[1] * g))
        # across the heading: (-dy, dx); a diagonal heading blocks along both axes
        side = (-heading[1], heading[0]) if heading[0] == 0 or heading[1] == 0 else (1, 0)
        for k in range(-self.cfg.collision_span, self.cfg.collision_span + 1):
            cell = (ahead[0] + side[0] * k, ahead[1] + side[1] * k)
            if cell != here and a.inside(cell) and a.grid[cell[1], cell[0]] != FREE:
                a.set_state(cell, BLOCKED)
        self.mark_track(area, pos)
        self.dirty = True
        return col

    def unblock(self, area: str, pos: Point) -> int:
        """Claude says there is no wall here (an NPC, a moth, a misread): the cell and its
        neighbours go back to unknown. Returns how many cells changed."""
        a = self.area(area)
        c = a.cell(pos)
        n = 0
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                q = (c[0] + dx, c[1] + dy)
                if a.inside(q) and a.grid[q[1], q[0]] == BLOCKED:
                    a.set_state(q, UNKNOWN)
                    n += 1
        if n:
            self.dirty = True
        return n

    def import_level(self, lvl: Level) -> int:
        """Seed from the recordings: floors become floor cells, spots become things."""
        n = 0
        for name, floor in lvl.floors.items():
            a = self.area(name)
            gh, gw = a.grid.shape
            # a minimap px covers scale/grid_px cells: resize the mask onto the grid
            up = cv2.resize(floor.astype(np.uint8), (gw, gh), interpolation=cv2.INTER_NEAREST)
            a.grid[(a.grid == UNKNOWN) & (up > 0)] = FLOOR
        for s in lvl.spots:
            kind = LEVEL_KINDS.get(s.kind, s.kind)
            x, y = self.dot_to_world((s.x, s.y))
            self.add_object(
                kind,
                s.area,
                (x, y),
                source="level",
                confidence=LEVEL_CONFIDENCE,
                note=f"level spot {s.id}, seen in {s.seen} event(s)",
                seen=max(1, s.seen),
            )
            n += 1
        for sess in lvl.sessions:
            if sess not in self.sessions:
                self.sessions.append(sess)
        self.dirty = True
        return n

    # --- the mosaic ----------------------------------------------------------------------

    def _prep(
        self, frame: BgrImage, hud_rois: list[Any], anchor: Point | None = None
    ) -> tuple[BgrImage, npt.NDArray[np.uint8]]:
        """The frame at mosaic scale and the mask of what goes in: the lit disc around the
        animal (at ``anchor``, default ``avatar``) without the HUD (and the animal's own box,
        when passed)."""
        s = self.cfg.mosaic_scale
        small = np.asarray(
            cv2.resize(frame, None, fx=s, fy=s, interpolation=cv2.INTER_AREA), np.uint8
        )
        keep = np.ones(small.shape[:2], np.uint8)
        for x, y, w, h in hud_rois:
            y0, x0 = max(0, int(y * s)), max(0, int(x * s))
            keep[y0 : int((y + h) * s) + 1, x0 : int((x + w) * s) + 1] = 0
        px, py = anchor if anchor is not None else self.avatar
        ax, ay = int(px * s), int(py * s)
        disc = np.zeros_like(keep)
        cv2.circle(disc, (ax, ay), max(1, int(self.cfg.vignette_px * s)), 1, -1)
        keep &= disc
        return small, keep

    def align(
        self,
        area: str,
        pos: Point,
        frame: BgrImage,
        hud_rois: list[Any],
        anchor: Point | None = None,
    ) -> tuple[Point, float, float]:
        """Where this frame really belongs, by phase correlation against the part of the
        mosaic it overlaps: (corrected pos, overlap share, response). The estimate from the
        dot and the odometry is good to a few tens of px; the mosaic says the rest. Without
        enough overlap, or with a weak or implausible peak, ``pos`` comes back unchanged
        with response 0. ``anchor`` is where the animal is on screen (default ``avatar``)."""
        a = self.area(area)
        s = self.cfg.mosaic_scale
        ax, ay = anchor if anchor is not None else self.avatar
        small, keep = self._prep(frame, hud_rois, (ax, ay))
        ox, oy = round((pos[0] - ax) * s), round((pos[1] - ay) * s)
        mh, mw = a.mosaic.shape[:2]
        fh, fw = small.shape[:2]
        x0, y0 = max(0, ox), max(0, oy)
        x1, y1 = min(mw, ox + fw), min(mh, oy + fh)
        if x1 <= x0 or y1 <= y0:
            return pos, 0.0, 0.0
        kv = keep[y0 - oy : y1 - oy, x0 - ox : x1 - ox] > 0
        seen = a.weight[y0:y1, x0:x1] > 0
        both = kv & seen
        disc_px = int(np.count_nonzero(keep))
        overlap = float(np.count_nonzero(both)) / disc_px if disc_px else 0.0
        if overlap < self.cfg.align_min_overlap:
            return pos, overlap, 0.0
        live = cv2.cvtColor(small[y0 - oy : y1 - oy, x0 - ox : x1 - ox], cv2.COLOR_BGR2GRAY)
        ref = cv2.cvtColor(a.mosaic[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
        sig = self.cfg.align_sigma
        live_f = live.astype(np.float32)
        ref_f = ref.astype(np.float32)
        live_f = live_f - cv2.GaussianBlur(live_f, (0, 0), sig)
        ref_f = ref_f - cv2.GaussianBlur(ref_f, (0, 0), sig)
        live_f[~both] = 0.0
        ref_f[~both] = 0.0
        win = cv2.createHanningWindow((live_f.shape[1], live_f.shape[0]), cv2.CV_32F)
        (dx, dy), resp = cv2.phaseCorrelate(ref_f, live_f, win)
        shift = (dx / s, dy / s)  # the live content is the mosaic's moved by this (scene px)
        if resp < self.cfg.align_min_response or math.hypot(*shift) > self.cfg.align_max_px:
            return pos, overlap, float(resp)
        # content that appears moved right means the frame was placed too far left
        return (pos[0] - shift[0], pos[1] - shift[1]), overlap, float(resp)

    def observe(
        self,
        area: str,
        pos: Point,
        frame: BgrImage,
        hud_rois: list[Any],
        anchor: Point | None = None,
    ) -> int:
        """Paste the lit disc around the animal into the mosaic at ``pos``. Bright pixels win
        over what was there; dark ones (the vignette's edge) never overwrite. ``hud_rois``
        are left out: pass the HUD and the animal's own box (it is in every frame and would
        be smeared over the map). ``anchor`` is the animal's screen point in this frame
        (default ``avatar``): the frame's origin is ``pos - anchor``. Returns the number of
        mosaic pixels written."""
        a = self.area(area)
        s = self.cfg.mosaic_scale
        ax, ay = anchor if anchor is not None else self.avatar
        small, keep = self._prep(frame, hud_rois, (ax, ay))
        v = np.asarray(cv2.cvtColor(small, cv2.COLOR_BGR2HSV)[:, :, 2], np.int32)
        ox, oy = round((pos[0] - ax) * s), round((pos[1] - ay) * s)
        mh, mw = a.mosaic.shape[:2]
        fh, fw = small.shape[:2]
        x0, y0 = max(0, ox), max(0, oy)
        x1, y1 = min(mw, ox + fw), min(mh, oy + fh)
        if x1 <= x0 or y1 <= y0:
            return 0
        sub = small[y0 - oy : y1 - oy, x0 - ox : x1 - ox]
        kv = keep[y0 - oy : y1 - oy, x0 - ox : x1 - ox] > 0
        vv = v[y0 - oy : y1 - oy, x0 - ox : x1 - ox]
        dst = a.mosaic[y0:y1, x0:x1]
        wt = a.weight[y0:y1, x0:x1]
        old_v = np.asarray(cv2.cvtColor(dst, cv2.COLOR_BGR2HSV)[:, :, 2], np.int32)
        write = kv & (vv >= self.cfg.mosaic_min_v) & ((wt == 0) | (vv >= old_v * 0.8))
        dst[write] = sub[write]
        wt[write] = np.minimum(255, wt[write].astype(np.int32) + 1).astype(np.uint8)
        n = int(np.count_nonzero(write))
        if n:
            self.dirty = True
            self._observe_tiles(area, pos, frame, hud_rois, (ax, ay))
        return n

    def _observe_tiles(
        self, area: str, pos: Point, frame: BgrImage, hud_rois: list[Any], anchor: Point
    ) -> None:
        """The same disc at full resolution into the on-disk tiles (when attached)."""
        if self.tiles.folder is None:
            return
        fh, fw = frame.shape[:2]
        keep = np.ones((fh, fw), np.uint8)
        for x, y, w, h in hud_rois:
            keep[max(0, int(y)) : int(y + h) + 1, max(0, int(x)) : int(x + w) + 1] = 0
        disc = np.zeros_like(keep)
        cv2.circle(disc, (int(anchor[0]), int(anchor[1])), max(1, self.cfg.vignette_px), 1, -1)
        keep &= disc
        v = np.asarray(cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[:, :, 2], np.int32)
        ox, oy = round(pos[0] - anchor[0]), round(pos[1] - anchor[1])  # frame origin
        g = self.tiles.tile_px
        aw, ah = self.size
        for ty in range(max(0, oy // g), min((ah - 1) // g, (oy + fh - 1) // g) + 1):
            for tx in range(max(0, ox // g), min((aw - 1) // g, (ox + fw - 1) // g) + 1):
                x0, y0 = max(tx * g, ox), max(ty * g, oy)
                x1, y1 = min((tx + 1) * g, ox + fw), min((ty + 1) * g, oy + fh)
                if x1 <= x0 or y1 <= y0:
                    continue
                kv = keep[y0 - oy : y1 - oy, x0 - ox : x1 - ox] > 0
                if not kv.any():
                    continue
                tile = self.tiles.get(area, tx, ty)
                dst = tile[y0 - ty * g : y1 - ty * g, x0 - tx * g : x1 - tx * g]
                src = frame[y0 - oy : y1 - oy, x0 - ox : x1 - ox]
                vv = v[y0 - oy : y1 - oy, x0 - ox : x1 - ox]
                old_v = np.asarray(cv2.cvtColor(dst, cv2.COLOR_BGR2HSV)[:, :, 2], np.int32)
                write = kv & (vv >= self.cfg.mosaic_min_v) & (vv >= old_v * 0.8)
                if write.any():
                    dst[write] = src[write]
                    self.tiles.mark(area, tx, ty)

    # --- things --------------------------------------------------------------------------

    def add_object(
        self,
        kind: str,
        area: str,
        pos: Point,
        *,
        source: str,
        confidence: float,
        status: str = "",
        note: str = "",
        ts: float = 0.0,
        shot: str = "",
        seen: int = 1,
        merge_px: float | None = None,
    ) -> Obj:
        """A thing seen at ``pos``; one of the same kind within ``merge_px`` is the same
        thing (its position moves toward the more confident sighting; status and note
        follow a sighting at least as confident)."""
        r = self.cfg.merge_px if merge_px is None else merge_px
        near = [o for o in self.objects if o.kind == kind and o.area == area and not o.gone]
        best = min(near, key=lambda o: o.dist(pos)) if near else None
        if best is not None and best.dist(pos) <= r:
            wa, wb = best.confidence * best.seen, confidence
            tot = wa + wb or 1.0
            best.x = (best.x * wa + pos[0] * wb) / tot
            best.y = (best.y * wa + pos[1] * wb) / tot
            best.seen += seen
            if confidence >= best.confidence:
                best.confidence = confidence
                best.source = source
                if status:
                    best.status = status
                if note:
                    best.note = note
                if shot:
                    best.shot = shot
            best.ts = max(best.ts, ts)
            self.dirty = True
            return best
        o = Obj(
            len(self.objects),
            kind,
            area,
            float(pos[0]),
            float(pos[1]),
            status,
            source,
            confidence,
            note,
            seen,
            ts,
            shot,
        )
        self.objects.append(o)
        self.dirty = True
        return o

    def forget(self, oid: int) -> Obj | None:
        for o in self.objects:
            if o.id == oid:
                o.gone = True
                self.dirty = True
                return o
        return None

    def near(self, area: str, pos: Point, radius: float, kind: str | None = None) -> list[Obj]:
        out = [
            o
            for o in self.objects
            if o.area == area
            and not o.gone
            and (kind is None or o.kind == kind)
            and o.dist(pos) <= radius
        ]
        return sorted(out, key=lambda o: o.dist(pos))

    def nearest(self, area: str, pos: Point, kind: str, status: str | None = None) -> Obj | None:
        cands = [
            o
            for o in self.objects
            if o.area == area
            and not o.gone
            and o.kind == kind
            and (status is None or o.status == status)
        ]
        return min(cands, key=lambda o: o.dist(pos)) if cands else None

    def note(self, text: str, area: str = "", pos: Point | None = None, ts: float = 0.0) -> None:
        self.notes.append(
            {
                "ts": ts,
                "area": area,
                "pos": [round(pos[0]), round(pos[1])] if pos else None,
                "text": text,
            }
        )
        self.dirty = True

    # --- planning ------------------------------------------------------------------------

    def cost(self, area: str, c: Cell) -> float:
        a = self.area(area)
        if not a.inside(c):
            return math.inf
        s = int(a.grid[c[1], c[0]])
        if s == BLOCKED:
            return math.inf
        if s == FREE:
            return 0.8
        if s == FLOOR:
            return 1.0
        return self.cfg.unknown_cost

    def plan(
        self, area: str, start: Point, goal: Point, *, max_cells: int = 200_000
    ) -> list[Point] | None:
        """A* on the grid, world px in and out; unknown cells cost more, blocked never.
        None when the goal cannot be reached."""
        a = self.area(area)
        s, g = a.cell(start), a.cell(goal)
        if not a.inside(s) or not a.inside(g) or self.cost(area, g) == math.inf:
            return None
        if self.cost(area, s) == math.inf:  # standing in a blocked cell: start out of it
            a.set_state(s, FREE)
        best: dict[Cell, float] = {s: 0.0}
        prev: dict[Cell, Cell] = {}
        heap: list[tuple[float, float, Cell]] = [(math.dist(s, g), 0.0, s)]
        popped = 0
        while heap:
            _f, cost, c = heapq.heappop(heap)
            popped += 1
            if c == g:
                path = [c]
                while c in prev:
                    c = prev[c]
                    path.append(c)
                return [a.centre(p) for p in path[::-1]]
            if cost > best.get(c, math.inf) or popped > max_cells:
                continue
            for dx, dy, step in NEIGHBOURS:
                n = (c[0] + dx, c[1] + dy)
                k = self.cost(area, n)
                if k == math.inf:
                    continue
                if (
                    dx
                    and dy
                    and (
                        self.cost(area, (c[0] + dx, c[1])) == math.inf
                        or self.cost(area, (c[0], c[1] + dy)) == math.inf
                    )
                ):
                    continue  # no cutting a blocked corner
                nc = cost + step * k
                if nc < best.get(n, math.inf):
                    best[n], prev[n] = nc, c
                    heapq.heappush(heap, (nc + math.dist(n, g), nc, n))
        return None

    # --- for people ----------------------------------------------------------------------

    def coverage(self, area: str) -> dict[str, Any]:
        """How much of the area is mapped: of the floor the recordings know (floor + walked
        cells), the share seen in the mosaic and the share walked; plus the counts."""
        a = self.area(area)
        floor = (a.grid == FLOOR) | (a.grid == FREE)
        gh, gw = a.grid.shape
        seen_small = (a.weight > 0).astype(np.uint8)
        seen = cv2.resize(seen_small, (gw, gh), interpolation=cv2.INTER_AREA) > 0
        n_floor = int(np.count_nonzero(floor))
        n_seen = int(np.count_nonzero(floor & seen))
        n_walked = int(np.count_nonzero(a.grid == FREE))
        things = sum(1 for o in self.objects if o.area == area and not o.gone)
        sure = sum(1 for o in self.objects if o.area == area and not o.gone and o.confidence >= 0.8)
        cols = sum(1 for c in self.collisions if c.area == area)
        return {
            "area": area,
            "floor_cells": n_floor,
            "seen_cells": n_seen,
            "walked_cells": n_walked,
            "blocked_cells": int(np.count_nonzero(a.grid == BLOCKED)),
            "unknown_cells": int(np.count_nonzero(a.grid == UNKNOWN)),
            "percent_seen": round(100.0 * n_seen / n_floor, 1) if n_floor else 0.0,
            "percent_walked": round(100.0 * n_walked / n_floor, 1) if n_floor else 0.0,
            "things": things,
            "things_sure": sure,
            "collisions": cols,
            "tiles": len(self.tiles.tiles(area)),
        }

    def summary(self) -> dict[str, Any]:
        kinds: dict[str, int] = {}
        for o in self.objects:
            if not o.gone:
                kinds[o.kind] = kinds.get(o.kind, 0) + 1
        return {
            "target": self.target,
            "scale": self.scale,
            "size": list(self.size),
            "grid_px": self.cfg.grid_px,
            "areas": {
                n: {"cells": a.counts(), "mosaic_seen": round(a.observed(), 4)}
                for n, a in self.areas.items()
            },
            "objects": kinds,
            "collisions": len(self.collisions),
            "notes": len(self.notes),
            "sessions": self.sessions,
        }

    def render(
        self,
        area: str,
        *,
        zoom: float = 0.1,
        crop: tuple[int, int, int, int] | None = None,
        pos: Point | None = None,
        path: list[Point] | None = None,
        labels: bool = True,
    ) -> BgrImage:
        """The area as a picture: the grid's colours (black unknown, grey floor, green-grey
        walked, red blocked) under the mosaic where it has been seen, the things with their
        ids, the collisions as red ticks, the animal as a yellow ring, minimap gridlines every
        10 minimap px with their minimap coordinates (what ``goto`` takes). ``crop`` is
        (x, y, w, h) in world px; ``zoom`` is image px per world px."""
        a = self.area(area)
        x0, y0, w, h = crop if crop is not None else (0, 0, a.w, a.h)
        iw, ih = max(1, int(w * zoom)), max(1, int(h * zoom))
        img = self._render_grid(a, (x0, y0, w, h), zoom, (iw, ih))
        self._render_mosaic(a, img, (x0, y0, w, h), zoom)
        self._render_gridlines(img, (x0, y0, w, h), zoom, labels)

        def to_img(p: Point) -> tuple[int, int]:
            return (int((p[0] - x0) * zoom), int((p[1] - y0) * zoom))

        for col in self.collisions:
            if col.area == area:
                p = to_img((col.x, col.y))
                cv2.line(img, p, (p[0] + col.dx * 4, p[1] + col.dy * 4), (0, 0, 255), 1)
        if path:
            pts = np.array([to_img(p) for p in path], np.int32)
            cv2.polylines(img, [pts], False, (255, 255, 0), 1)
        for o in self.objects:
            if o.area != area or o.gone or not (x0 <= o.x < x0 + w and y0 <= o.y < y0 + h):
                continue
            p = to_img((o.x, o.y))
            c = KIND_COLOURS.get(o.kind, (200, 200, 200))
            sure = o.confidence >= 0.8
            cv2.circle(img, p, 4 if sure else 3, c, -1 if sure else 1)
            if labels:
                text = f"{o.id}" + (f":{o.status[:3]}" if o.status else "")
                cv2.putText(img, text, (p[0] + 4, p[1] - 3), 0, 0.32, c, 1)
        if pos is not None:
            cv2.circle(img, to_img(pos), 7, (0, 255, 255), 2)
        return np.asarray(img, dtype=np.uint8)

    def _render_grid(
        self, a: Area, crop: tuple[int, int, int, int], zoom: float, size: tuple[int, int]
    ) -> BgrImage:
        """The grid's state colours over the crop, as an image of ``size``."""
        x0, y0, w, h = crop
        iw, ih = size
        g = self.cfg.grid_px
        cx0, cy0 = x0 // g, y0 // g
        cx1 = min(a.grid.shape[1], (x0 + w) // g + 1)
        cy1 = min(a.grid.shape[0], (y0 + h) // g + 1)
        cells = a.grid[cy0:cy1, cx0:cx1]
        colour = np.zeros((*cells.shape, 3), np.uint8)
        for s, c in STATE_COLOURS.items():
            colour[cells == s] = c
        bw = int(max(1, cells.shape[1]) * g * zoom) + 1
        bh = int(max(1, cells.shape[0]) * g * zoom) + 1
        base = cv2.resize(colour, (bw, bh), interpolation=cv2.INTER_NEAREST)
        ox, oy = int((x0 - cx0 * g) * zoom), int((y0 - cy0 * g) * zoom)
        img = np.zeros((ih, iw, 3), np.uint8)
        sub = base[oy : oy + ih, ox : ox + iw]
        img[: sub.shape[0], : sub.shape[1]] = sub
        return np.asarray(img, dtype=np.uint8)

    def _render_mosaic(
        self, a: Area, img: BgrImage, crop: tuple[int, int, int, int], zoom: float
    ) -> None:
        """The mosaic, where it has been seen, over ``img``."""
        x0, y0, w, h = crop
        ih, iw = img.shape[:2]
        ms = self.cfg.mosaic_scale
        mx0, my0 = int(x0 * ms), int(y0 * ms)
        mx1 = min(a.mosaic.shape[1], int((x0 + w) * ms) + 1)
        my1 = min(a.mosaic.shape[0], int((y0 + h) * ms) + 1)
        if mx1 <= mx0 or my1 <= my0:
            return
        piece = a.mosaic[my0:my1, mx0:mx1]
        wt = a.weight[my0:my1, mx0:mx1]
        pw = max(1, int(piece.shape[1] / ms * zoom))
        ph = max(1, int(piece.shape[0] / ms * zoom))
        inter = cv2.INTER_AREA if zoom < ms else cv2.INTER_NEAREST
        big = cv2.resize(piece, (pw, ph), interpolation=inter)
        seen = cv2.resize(wt, (pw, ph), interpolation=cv2.INTER_NEAREST) > 0
        bh, bw = min(ih, ph), min(iw, pw)
        region = img[:bh, :bw]
        m = seen[:bh, :bw]
        region[m] = big[:bh, :bw][m]

    def _render_gridlines(
        self, img: BgrImage, crop: tuple[int, int, int, int], zoom: float, labels: bool
    ) -> None:
        """Minimap gridlines every 10 minimap px, labelled in minimap (stage) px."""
        x0, y0, w, h = crop
        ih, iw = img.shape[:2]
        step = 10 * self.scale
        mx, my = self.minimap_roi[0], self.minimap_roi[1]
        k = int(x0 // step)
        while k * step < x0 + w:
            xx = int((k * step - x0) * zoom)
            cv2.line(img, (xx, 0), (xx, ih), (90, 60, 30), 1)
            if labels:
                cv2.putText(img, str(int(mx + k * 10)), (xx + 2, 10), 0, 0.3, (200, 170, 120), 1)
            k += 1
        k = int(y0 // step)
        while k * step < y0 + h:
            yy = int((k * step - y0) * zoom)
            cv2.line(img, (0, yy), (iw, yy), (90, 60, 30), 1)
            if labels:
                cv2.putText(img, str(int(my + k * 10)), (2, yy - 2), 0, 0.3, (200, 170, 120), 1)
            k += 1

    # --- files ---------------------------------------------------------------------------

    def save(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        doc = {
            "version": WORLD_VERSION,
            "target": self.target,
            "scale": self.scale,
            "avatar": list(self.avatar),
            "minimap_roi": list(self.minimap_roi),
            "grid_px": self.cfg.grid_px,
            "mosaic_scale": self.cfg.mosaic_scale,
            "areas": sorted(self.areas),
            "objects": [asdict(o) for o in self.objects],
            "collisions": [asdict(c) for c in self.collisions],
            "notes": self.notes,
            "sessions": self.sessions,
            "saved": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.attach_tiles(folder / "tiles")
        self.tiles.flush()
        tmp = folder / "world.json.tmp"
        tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
        tmp.replace(folder / "world.json")
        for name, a in self.areas.items():
            cv2.imwrite(str(folder / f"{name}_grid.png"), a.grid * GRID_PNG_STEP)
            cv2.imwrite(str(folder / f"{name}_mosaic.png"), a.mosaic)
            cv2.imwrite(str(folder / f"{name}_weight.png"), a.weight)
        self.dirty = False

    @classmethod
    def load(cls, folder: Path, cfg: WorldConfig | None = None) -> World | None:
        path = folder / "world.json"
        if not path.exists():
            return None
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("version") != WORLD_VERSION:
            return None
        cfg = cfg or WorldConfig()
        if doc.get("grid_px") != cfg.grid_px or doc.get("mosaic_scale") != cfg.mosaic_scale:
            cfg = cfg.model_copy(
                update={"grid_px": int(doc["grid_px"]), "mosaic_scale": float(doc["mosaic_scale"])}
            )
        w = cls(
            doc["target"],
            float(doc["scale"]),
            (int(doc["avatar"][0]), int(doc["avatar"][1])),
            (
                int(doc["minimap_roi"][0]),
                int(doc["minimap_roi"][1]),
                int(doc["minimap_roi"][2]),
                int(doc["minimap_roi"][3]),
            ),
            cfg,
        )
        w.objects = [Obj(**o) for o in doc.get("objects", [])]
        w.collisions = [Collision(**c) for c in doc.get("collisions", [])]
        w.notes = list(doc.get("notes", []))
        w.sessions = list(doc.get("sessions", []))
        for name in doc.get("areas", []):
            a = w.area(name)
            grid = cv2.imread(str(folder / f"{name}_grid.png"), cv2.IMREAD_GRAYSCALE)
            if grid is not None and grid.shape == a.grid.shape:
                states = (grid.astype(np.int32) + GRID_PNG_STEP // 2) // GRID_PNG_STEP
                a.grid = np.asarray(states, np.uint8)
            mosaic = cv2.imread(str(folder / f"{name}_mosaic.png"))
            if mosaic is not None and mosaic.shape == a.mosaic.shape:
                a.mosaic = np.asarray(mosaic, np.uint8)
            weight = cv2.imread(str(folder / f"{name}_weight.png"), cv2.IMREAD_GRAYSCALE)
            if weight is not None and weight.shape == a.weight.shape:
                a.weight = np.asarray(weight, np.uint8)
        w.attach_tiles(folder / "tiles")
        w.dirty = False
        return w


@dataclass
class Obs:
    """One kept frame: where the animal was (world px) and where it was on screen."""

    area: str
    ts: float
    file: str  # relative to the store's folder
    pos: Point  # the animal, world px (the fused estimate at the time)
    anchor: Point  # the animal on screen (the frame's origin in the world is pos - anchor)
    box: tuple[int, int, int, int]  # the animal and its tag on screen (masked)
    tag: str = ""  # which command kept it
    dot: Point | None = None  # the minimap dot, world px, when seen


class ObsStore:
    """Every observed frame on disk with its pose (``obs/<area>/<n>.png`` + ``obs.jsonl``
    under the session), so the map can be rebuilt offline with better alignment than the
    live pass had (``world_build.py``). Frames are written by a thread; ``flush`` waits."""

    def __init__(self, folder: Path | None) -> None:
        import queue
        import threading

        self.folder = folder
        self.records: list[Obs] = []
        self._n = 0
        self._q: queue.Queue[tuple[Path, BgrImage] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        if folder is not None:
            folder.mkdir(parents=True, exist_ok=True)
            self.records = self.load(folder)
            self._n = len(self.records)
            self._thread = threading.Thread(target=self._writer, name="obs-writer", daemon=True)
            self._thread.start()

    @staticmethod
    def load(folder: Path) -> list[Obs]:
        path = folder / "obs.jsonl"
        out: list[Obs] = []
        if not path.exists():
            return out
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            out.append(
                Obs(
                    d["area"],
                    float(d["ts"]),
                    d["file"],
                    (float(d["pos"][0]), float(d["pos"][1])),
                    (float(d["anchor"][0]), float(d["anchor"][1])),
                    tuple(int(v) for v in d["box"]),  # type: ignore[arg-type]
                    d.get("tag", ""),
                    (float(d["dot"][0]), float(d["dot"][1])) if d.get("dot") else None,
                )
            )
        return out

    def _writer(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                self._q.task_done()
                return
            path, frame = item
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(path), frame, [cv2.IMWRITE_PNG_COMPRESSION, 1])
            except (OSError, cv2.error):
                pass
            self._q.task_done()

    def add(
        self,
        area: str,
        ts: float,
        frame: BgrImage,
        *,
        pos: Point,
        anchor: Point,
        box: tuple[int, int, int, int],
        tag: str = "",
        dot: Point | None = None,
    ) -> Obs | None:
        if self.folder is None:
            return None
        self._n += 1
        rel = f"{area}/{self._n:06d}.png"
        o = Obs(area, ts, rel, pos, anchor, box, tag, dot)
        self.records.append(o)
        self._q.put((self.folder / rel, np.ascontiguousarray(frame)))
        with (self.folder / "obs.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "area": area,
                        "ts": ts,
                        "file": rel,
                        "pos": [round(pos[0], 1), round(pos[1], 1)],
                        "anchor": [round(anchor[0], 1), round(anchor[1], 1)],
                        "box": list(box),
                        "tag": tag,
                        "dot": [round(dot[0], 1), round(dot[1], 1)] if dot else None,
                    }
                )
                + "\n"
            )
        return o

    def pending(self) -> int:
        return self._q.qsize()

    def flush(self) -> None:
        if self._thread is not None:
            self._q.join()


def world_dir(profiles_dir: Path, target: str) -> Path:
    return profiles_dir / "levels" / target / "world"
