"""Gem Ledger (KICKOFF §7.6): the real earnings numbers, read off the screen.

Sources: the HUD gem total (when the ROI is calibrated and visible) and mini-game end-screen
payouts reported by the modes. Every reading becomes a ``ledger`` row with a delta.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from jamcrammer.core.events import EventBus
from jamcrammer.core.profiles import Profile
from jamcrammer.core.store import Store
from jamcrammer.vision.ocr import OcrResult, crop, ocr_image, parse_int

BgrImage = npt.NDArray[np.uint8]
OcrFn = Callable[[BgrImage], OcrResult]

GEM_ROI_FIELD = "gem_counter_roi"
HUD_UPSCALE = 2.0
MAX_PLAUSIBLE_DELTA = 5000  # a single reading jumping more than this is treated as an OCR glitch


@dataclass
class LedgerStats:
    readings: int = 0
    glitches: int = 0
    first_gems: int | None = None
    last_gems: int | None = None
    session_delta: int = 0
    by_mode: dict[str, int] = field(default_factory=dict)


class GemLedger:
    def __init__(
        self,
        store: Store | None,
        profile: Profile,
        bus: EventBus | None = None,
        ocr: OcrFn | None = None,
    ) -> None:
        self._store = store
        self._profile = profile
        self._bus = bus
        self._ocr: OcrFn = ocr or (lambda img: ocr_image(img, upscale=HUD_UPSCALE))
        self.stats = LedgerStats()

    @property
    def hud_available(self) -> bool:
        return self._profile.has(GEM_ROI_FIELD)

    def read_hud(self, image: BgrImage) -> int | None:
        """OCR the HUD gem total; None if the ROI is uncalibrated or nothing parses."""
        if not self.hud_available:
            return None
        result = self._ocr(crop(image, self._profile.roi(GEM_ROI_FIELD)))
        return parse_int(result.text)

    def record(self, hud_gems: int, mode: str, ts: float | None = None) -> int | None:
        """Store a reading. Returns the delta vs the previous reading (None for the first)."""
        ts = time.time() if ts is None else ts
        prev = self.stats.last_gems
        delta = None if prev is None else hud_gems - prev
        if delta is not None and abs(delta) > MAX_PLAUSIBLE_DELTA:
            self.stats.glitches += 1
            if self._bus is not None:
                self._bus.emit("ledger.glitch", source="ledger", prev=prev, read=hud_gems)
            return None
        self.stats.readings += 1
        if self.stats.first_gems is None:
            self.stats.first_gems = hud_gems
        self.stats.last_gems = hud_gems
        if delta:
            self.stats.session_delta += delta
            self.stats.by_mode[mode] = self.stats.by_mode.get(mode, 0) + delta
        if self._store is not None:
            self._store.add_ledger(ts, hud_gems, delta, mode)
        if self._bus is not None and delta:
            self._bus.emit("ledger.delta", source="ledger", gems=hud_gems, delta=delta, mode=mode)
        return delta

    def tick(self, image: BgrImage, mode: str, ts: float | None = None) -> int | None:
        """Read the HUD and record if the value changed. Returns the delta if any."""
        gems = self.read_hud(image)
        if gems is None or gems == self.stats.last_gems:
            return None
        return self.record(gems, mode, ts)

    def record_payout(self, gems: int, mode: str, ts: float | None = None) -> None:
        """A mini-game end screen reported ``gems``; attribute it without a HUD reading."""
        ts = time.time() if ts is None else ts
        self.stats.session_delta += gems
        self.stats.by_mode[mode] = self.stats.by_mode.get(mode, 0) + gems
        if self._store is not None:
            hud = self.stats.last_gems if self.stats.last_gems is not None else 0
            self._store.add_ledger(ts, hud, gems, mode)
