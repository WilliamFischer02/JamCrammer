"""Steps 1-2: frame loading, stability/dedupe, scene clustering into candidate states."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import imagehash
import numpy as np
import numpy.typing as npt

from jamcrammer.vision.match import hamming, phash_of

BgrImage = npt.NDArray[np.uint8]

HIST_BINS = (8, 8, 4)
THUMB_W = 320
PHASH_WIDTH = 256
DHASH_SIZE = 16
DHASH_WEIGHT = 0.5
HIST_WEIGHT = 2.0


@dataclass
class FrameRec:
    index: int
    ts: float
    path: Path
    tags: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    phash: imagehash.ImageHash | None = None
    dhash: imagehash.ImageHash | None = None
    hist: npt.NDArray[np.float32] | None = None
    stable: bool = True
    duplicate_of: int | None = None
    state: int = -1

    def load(self) -> BgrImage:
        img = cv2.imread(str(self.path), cv2.IMREAD_COLOR)
        if img is None:
            msg = f"cannot read {self.path}"
            raise FileNotFoundError(msg)
        return np.asarray(img, dtype=np.uint8)


def load_frames(session_dir: Path, meta: list[dict[str, Any]]) -> list[FrameRec]:
    out: list[FrameRec] = []
    for rec in meta:
        if rec.get("t") != "keyframe" or "file" not in rec:
            continue
        path = session_dir / str(rec["file"])
        if path.exists():
            out.append(
                FrameRec(
                    index=len(out),
                    ts=float(rec.get("ts", 0.0)),
                    path=path,
                    tags=list(rec.get("tags", [])),
                    meta=rec,
                )
            )
    return out


def dhash_of(image: BgrImage) -> imagehash.ImageHash:
    from PIL import Image

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return imagehash.dhash(Image.fromarray(gray), hash_size=DHASH_SIZE)


def color_hist(image: BgrImage) -> npt.NDArray[np.float32]:
    small = cv2.resize(
        image,
        (THUMB_W, int(THUMB_W * image.shape[0] / image.shape[1])),
        interpolation=cv2.INTER_AREA,
    )
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, list(HIST_BINS), [0, 180, 0, 256, 0, 256])
    hist = hist.flatten().astype(np.float32)
    s = float(hist.sum()) or 1.0
    return np.asarray(hist / s, dtype=np.float32)


def compute_features(frames: list[FrameRec]) -> None:
    for f in frames:
        img = f.load()
        f.phash = phash_of(img, PHASH_WIDTH)
        f.dhash = dhash_of(img)
        f.hist = color_hist(img)


def mark_stability(frames: list[FrameRec], dup_distance: int, transition_distance: int) -> None:
    """Step 1: near-identical consecutive frames are duplicates; big jumps mark transitions."""
    for i, f in enumerate(frames):
        if i == 0 or f.phash is None or frames[i - 1].phash is None:
            continue
        d = hamming(frames[i - 1].phash, f.phash)  # type: ignore[arg-type]
        if d <= dup_distance:
            f.duplicate_of = (
                frames[i - 1].duplicate_of if frames[i - 1].duplicate_of is not None else i - 1
            )
        f.stable = d < transition_distance


def feature_vector(f: FrameRec) -> npt.NDArray[np.float32]:
    """256 dhash bits (x DHASH_WEIGHT) + HSV histogram (x HIST_WEIGHT). Calibrated on the
    synthetic session: dhash16 alone recovers the states (ARI 1.0, threshold 1.6) where pHash
    merges waiting and question frames (ARI 0.6)."""
    assert f.dhash is not None and f.hist is not None
    bits = np.asarray(f.dhash.hash, dtype=np.float32).flatten()
    return np.concatenate([bits * DHASH_WEIGHT, f.hist * HIST_WEIGHT]).astype(np.float32)


def cluster_states(
    frames: list[FrameRec], distance_threshold: float, min_cluster: int
) -> list[list[int]]:
    """Step 2: agglomerative clustering on pHash bits + HSV histogram. Returns member indices
    per state, largest first; tiny clusters are merged into the nearest big one."""
    from sklearn.cluster import AgglomerativeClustering

    if not frames:
        return []
    x = np.stack([feature_vector(f) for f in frames])
    if len(frames) == 1:
        labels = np.zeros(1, dtype=int)
    else:
        model = AgglomerativeClustering(
            n_clusters=None, distance_threshold=distance_threshold, linkage="average"
        )
        labels = model.fit_predict(x)
    groups: dict[int, list[int]] = {}
    for i, lab in enumerate(labels):
        groups.setdefault(int(lab), []).append(i)
    big = [g for g in groups.values() if len(g) >= min_cluster]
    small = [g for g in groups.values() if len(g) < min_cluster]
    if not big:
        big = sorted(groups.values(), key=len, reverse=True)[:1]
        small = [g for g in groups.values() if g is not big[0]]
    centroids = [x[g].mean(axis=0) for g in big]
    for g in small:
        for i in g:
            d = [float(np.linalg.norm(x[i] - c)) for c in centroids]
            big[int(np.argmin(d))].append(i)
    big.sort(key=len, reverse=True)
    for state, members in enumerate(big):
        members.sort()
        for i in members:
            frames[i].state = state
    return big


def medoid(frames: list[FrameRec], members: list[int]) -> int:
    x = np.stack([feature_vector(frames[i]) for i in members])
    c = x.mean(axis=0)
    return members[int(np.argmin(np.linalg.norm(x - c, axis=1)))]


def thumbnail(image: BgrImage, width: int = THUMB_W) -> BgrImage:
    h = int(width * image.shape[0] / image.shape[1])
    return np.asarray(cv2.resize(image, (width, h), interpolation=cv2.INTER_AREA), dtype=np.uint8)
