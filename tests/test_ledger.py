from __future__ import annotations

from pathlib import Path

import numpy as np

from jamcrammer.core.events import EventBus
from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import load_profile
from jamcrammer.core.store import Store
from jamcrammer.services.ledger import GemLedger
from jamcrammer.vision.ocr import OcrResult


def test_uncalibrated_roi_reads_nothing() -> None:
    ledger = GemLedger(None, load_profile("shared", Paths()))
    assert not ledger.hud_available
    assert ledger.read_hud(np.zeros((10, 10, 3), dtype=np.uint8)) is None


def test_record_deltas_and_glitch_filter(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    bus = EventBus()
    seen: list[str] = []
    bus.subscribe("*", lambda e: seen.append(e.type))
    prof = load_profile("shared", Paths())
    prof.set_value("gem_counter_roi", [0, 0, 10, 10])
    readings = iter(["Gems 1,000", "1,044", "99999", "1,088"])
    ledger = GemLedger(store, prof, bus=bus, ocr=lambda img: OcrResult(text=next(readings)))
    img = np.zeros((20, 20, 3), dtype=np.uint8)
    assert ledger.tick(img, "double_up") is None  # first reading: no delta
    assert ledger.tick(img, "double_up") == 44
    assert ledger.tick(img, "double_up") is None  # glitch filtered
    assert ledger.tick(img, "hunt") == 44
    assert ledger.stats.session_delta == 88
    assert ledger.stats.by_mode == {"double_up": 44, "hunt": 44}
    assert ledger.stats.glitches == 1
    rows = store.query("SELECT hud_gems, delta, mode FROM ledger ORDER BY id")
    assert [(r["hud_gems"], r["delta"], r["mode"]) for r in rows] == [
        (1000, None, "double_up"),
        (1044, 44, "double_up"),
        (1088, 44, "hunt"),
    ]
    assert "ledger.glitch" in seen and "ledger.delta" in seen
    store.close()
