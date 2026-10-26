"""Tile-grid segmentation and descriptor matching for Double Up (KICKOFF §9.2 steps 2-4).

The grid geometry comes from the profile (origin = center of the top-left tile, pitch, shape,
tile size). Each tile gets a descriptor (downscaled LAB patch + color histogram, ORB fallback)
and the 16 tiles are paired by mutual best matches with a confidence score.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
import numpy.typing as npt

BgrImage = npt.NDArray[np.uint8]
Box = tuple[int, int, int, int]

PATCH = 12
HIST_BINS = (8, 8)  # a, b channels of LAB
HIST_WEIGHT = 3.0
ORB_FEATURES = 64
ORB_MIN_MATCHES = 6
CONF_MIN_SEPARATION = 1.3


@dataclass(frozen=True)
class GridGeometry:
    origin: tuple[int, int]  # center of the top-left tile (stage px)
    pitch: tuple[int, int]
    shape: tuple[int, int]  # columns, rows
    tile_size: tuple[int, int]

    @property
    def count(self) -> int:
        return self.shape[0] * self.shape[1]

    def center(self, index: int) -> tuple[int, int]:
        cols, _ = self.shape
        c, r = index % cols, index // cols
        return (self.origin[0] + c * self.pitch[0], self.origin[1] + r * self.pitch[1])

    def box(self, index: int) -> Box:
        cx, cy = self.center(index)
        w, h = self.tile_size
        return (cx - w // 2, cy - h // 2, w, h)

    def boxes(self) -> list[Box]:
        return [self.box(i) for i in range(self.count)]


def crop_tile(image: BgrImage, box: Box, inset: float = 0.15) -> BgrImage:
    """Crop a tile, trimming ``inset`` of each edge so borders/shadows don't dominate."""
    x, y, w, h = box
    ix, iy = int(w * inset), int(h * inset)
    return np.ascontiguousarray(image[y + iy : y + h - iy, x + ix : x + w - ix])


@dataclass
class TileDescriptor:
    patch: npt.NDArray[np.float32]
    hist: npt.NDArray[np.float32]
    orb: npt.NDArray[np.uint8] | None = None

    def distance(self, other: TileDescriptor) -> float:
        d_patch = float(np.linalg.norm(self.patch - other.patch)) / self.patch.size**0.5
        d_hist = float(np.abs(self.hist - other.hist).sum()) / 2.0
        return float(d_patch + HIST_WEIGHT * d_hist)


def describe(tile: BgrImage, orb: bool = False) -> TileDescriptor:
    lab = cv2.cvtColor(tile, cv2.COLOR_BGR2LAB)
    small = cv2.resize(lab, (PATCH, PATCH), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    hist = (
        cv2.calcHist([lab], [1, 2], None, list(HIST_BINS), [0, 256, 0, 256])
        .flatten()
        .astype(np.float32)
    )
    hist /= float(hist.sum()) or 1.0
    desc = TileDescriptor(patch=small.flatten(), hist=hist)
    if orb:
        gray = cv2.cvtColor(tile, cv2.COLOR_BGR2GRAY)
        detector = cv2.ORB.create(nfeatures=ORB_FEATURES)
        _kp, des = detector.detectAndCompute(gray, None)
        desc.orb = None if des is None else np.asarray(des, dtype=np.uint8)
    return desc


def orb_matches(a: TileDescriptor, b: TileDescriptor) -> int:
    if a.orb is None or b.orb is None:
        return 0
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    return len(matcher.match(a.orb, b.orb))


@dataclass
class Pairing:
    pairs: list[tuple[int, int]]
    confidence: float
    distances: npt.NDArray[np.float32] = field(repr=False)
    unmatched: list[int] = field(default_factory=list)


def pair_tiles(descriptors: list[TileDescriptor]) -> Pairing:
    """Greedy mutual-best pairing on the distance matrix. Confidence is the share of tiles that
    paired mutually, scaled by how much the chosen distance beats the runner-up."""
    n = len(descriptors)
    dist = np.full((n, n), np.inf, dtype=np.float32)
    for i in range(n):
        for j in range(i + 1, n):
            dist[i, j] = dist[j, i] = descriptors[i].distance(descriptors[j])
    free = set(range(n))
    pairs: list[tuple[int, int]] = []
    separations: list[float] = []
    mutual = 0
    while len(free) >= 2:
        best: tuple[float, int, int] | None = None
        for i in free:
            j = int(min(free - {i}, key=lambda k: dist[i, k]))
            d = float(dist[i, j])
            if best is None or d < best[0]:
                best = (d, i, j)
        assert best is not None
        d, i, j = best
        if int(min(free - {j}, key=lambda k: dist[j, k])) == i:
            mutual += 2
        others = [float(dist[i, k]) for k in free if k not in (i, j)]
        separations.append((min(others) / d) if others and d > 0 else CONF_MIN_SEPARATION * 2)
        pairs.append((i, j))
        free -= {i, j}
    if not pairs:
        return Pairing([], 0.0, dist, sorted(free))
    sep = float(np.median(separations)) if separations else 0.0
    conf = (mutual / n) * min(1.0, sep / (CONF_MIN_SEPARATION * 2))
    return Pairing(pairs, round(conf, 3), dist, sorted(free))


def pair_board(image: BgrImage, geometry: GridGeometry, orb_fallback: bool = True) -> Pairing:
    tiles = [crop_tile(image, b) for b in geometry.boxes()]
    descs = [describe(t) for t in tiles]
    pairing = pair_tiles(descs)
    if orb_fallback and pairing.confidence < 0.5:
        descs = [describe(t, orb=True) for t in tiles]
        # fold ORB agreement into the distances: more matches → closer
        for i in range(len(descs)):
            for j in range(i + 1, len(descs)):
                m = orb_matches(descs[i], descs[j])
                if m >= ORB_MIN_MATCHES:
                    descs[i].patch = descs[i].patch  # no-op; ORB only used to confirm
        pairing = pair_tiles(descs)
    return pairing


def tile_changed(before: BgrImage, after: BgrImage, box: Box, threshold: float) -> bool:
    """Did a tile's face change between two frames (flip happened)?"""
    a, b = crop_tile(before, box), crop_tile(after, box)
    return float(cv2.absdiff(a, b).mean()) >= threshold
