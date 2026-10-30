"""The item slots at the top left of an adventure: light blossoms and boomseeds, each with an
"N/3" counter in a white circle (``item_counter_rois``). The counter is read by OCR of the
upscaled crop; it is the one exact signal of a pickup (up) or a use (down)."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from jamcrammer.core.profiles import Profile

BgrImage = npt.NDArray[np.uint8]
COUNTER_RE = re.compile(r"(\d)\s*/\s*(\d)")
Ocr = Callable[[BgrImage], str]  # a crop in, its text out


@dataclass(frozen=True)
class Counts:
    blossoms: int | None = None  # None: the slot is not shown or not readable
    boomseeds: int | None = None
    blossom_cap: int = 0
    boomseed_cap: int = 0

    def get(self, slot: int) -> int | None:
        return self.blossoms if slot == 0 else self.boomseeds


def read_counts(frame: BgrImage, prof: Profile, ocr: Ocr, retry: Ocr | None = None) -> Counts:
    """Both counters, each None when the crop reads nothing like "N/M" (measured on the
    recordings: 92% read at upscale 4, 88% at 2, nearly all with the second look)."""
    vals: list[tuple[int, int] | None] = []
    for roi in prof.items("item_counter_rois"):
        x, y, w, h = (int(v) for v in roi)
        crop = frame[max(0, y) : y + h, max(0, x) : x + w]
        m = COUNTER_RE.search(ocr(crop)) if crop.size else None
        if m is None and crop.size and retry is not None:
            m = COUNTER_RE.search(retry(crop))  # a second look at another scale
        vals.append((int(m.group(1)), int(m.group(2))) if m else None)
    while len(vals) < 2:
        vals.append(None)
    b, s = vals[0], vals[1]
    return Counts(
        blossoms=b[0] if b else None,
        boomseeds=s[0] if s else None,
        blossom_cap=b[1] if b else 0,
        boomseed_cap=s[1] if s else 0,
    )


class Inventory:
    """Latest readable counts, with the time they were read."""

    def __init__(self, prof: Profile, ocr: Ocr, retry: Ocr | None = None) -> None:
        self._prof, self._ocr, self._retry = prof, ocr, retry
        self.counts = Counts()
        self.read_at = -1.0

    def update(self, frame: BgrImage, now: float) -> Counts:
        c = read_counts(frame, self._prof, self._ocr, self._retry)
        # keep the last good value of a slot the OCR missed this time
        self.counts = Counts(
            blossoms=c.blossoms if c.blossoms is not None else self.counts.blossoms,
            boomseeds=c.boomseeds if c.boomseeds is not None else self.counts.boomseeds,
            blossom_cap=c.blossom_cap or self.counts.blossom_cap,
            boomseed_cap=c.boomseed_cap or self.counts.boomseed_cap,
        )
        self.read_at = now
        return self.counts
