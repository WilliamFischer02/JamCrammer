"""Perceptual hashing and simple matching helpers shared by the recorder and detectors."""

from __future__ import annotations

import cv2
import imagehash
import numpy as np
import numpy.typing as npt
from PIL import Image

BgrImage = npt.NDArray[np.uint8]

PHASH_SIZE = 8  # 64-bit hash


def phash_of(image_bgr: BgrImage, width: int) -> imagehash.ImageHash:
    """pHash of a BGR frame, computed on a ``width``-px downscale for speed."""
    h, w = image_bgr.shape[:2]
    if w > width:
        scale = width / float(w)
        small = cv2.resize(image_bgr, (width, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    else:
        small = image_bgr
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return imagehash.phash(Image.fromarray(gray), hash_size=PHASH_SIZE)


def hamming(a: imagehash.ImageHash, b: imagehash.ImageHash) -> int:
    return int(a - b)


def mean_abs_diff(a: BgrImage, b: BgrImage) -> float:
    """Mean absolute pixel difference between two same-sized BGR frames (0..255)."""
    if a.shape != b.shape:
        msg = f"shape mismatch {a.shape} vs {b.shape}"
        raise ValueError(msg)
    return float(cv2.absdiff(a, b).mean())
