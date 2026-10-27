"""Perpetual learning for Jamaa Derby: what the farmer records about every band it meets, how
a stumble is recognised, and the small adjustments it keeps between races.

A band passes the hooves; if the scroll stalls right after (the passing band's edges stop
moving left), the horse hit it. The record of that band (kind, width class, where our press
landed, the band's colour) drives two kinds of adjustment, both persisted in
``data/derby_learned.json`` and logged as ``derby.learned`` events:

- a press that was late / early for that (kind, width) class moves the class lead in pixels;
- a band that was never pressed for (not detected) widens that kind's colour range toward
  the band's measured colour, within the kind's hue family and never into the grass.
"""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from jamcrammer.core.config import DerbyConfig

HsvRange = tuple[tuple[int, int, int], tuple[int, int, int]]
HUE_FAMILY = {"hay": (12, 40), "mud": (0, 26), "water": (68, 112), "hedge": (34, 72)}
HEDGE_V_MAX = 172  # light grass starts around V 150-180; never widen a hedge into it
MIN_SAT = 40


def width_class(width: int, column_px: int) -> str:
    cols = max(1, min(3, round(width / column_px)))
    return f"{cols}col"


@dataclass
class BandRecord:
    ts_pass: float
    kind: str
    width: int
    wclass: str
    press_edge: int | None  # where the band's edge was when we pressed (stage x), None = no press
    press_ts: float | None
    speed: float
    boosted: bool
    colour: list[list[int]] | None = None  # HSV p5 / p50 / p95 of the band's columns
    outcome: str = "pending"  # cleared | hit

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LearnedDerby:
    path: Path | None = None
    lead_px: dict[str, float] = field(default_factory=dict)  # "kind:wclass" -> extra lead
    ranges: dict[str, list[list[int]]] = field(default_factory=dict)  # kind -> [lo, hi]
    hits: int = 0
    cleared: int = 0
    history: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=300))

    @classmethod
    def load(cls, path: Path | None) -> LearnedDerby:
        obj = cls(path=path)
        if path is None or not path.exists():
            return obj
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return obj
        obj.lead_px = {str(k): float(v) for k, v in doc.get("lead_px", {}).items()}
        obj.ranges = {str(k): v for k, v in doc.get("ranges", {}).items()}
        obj.hits = int(doc.get("hits", 0))
        obj.cleared = int(doc.get("cleared", 0))
        obj.history.extend(doc.get("history", [])[-300:])
        return obj

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        doc = {
            "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "lead_px": self.lead_px,
            "ranges": self.ranges,
            "hits": self.hits,
            "cleared": self.cleared,
            "history": list(self.history),
        }
        self.path.write_text(json.dumps(doc, indent=1), encoding="utf-8")

    def lead_for(self, kind: str, wclass: str) -> float:
        return self.lead_px.get(f"{kind}:{wclass}", 0.0)

    def range_for(self, kind: str, default: HsvRange) -> HsvRange:
        r = self.ranges.get(kind)
        if not r:
            return default
        lo, hi = r
        return ((int(lo[0]), int(lo[1]), int(lo[2])), (int(hi[0]), int(hi[1]), int(hi[2])))

    # --- updates ----------------------------------------------------------------------------------

    def note(self, rec: BandRecord, cfg: DerbyConfig, trigger_x: int, default: HsvRange) -> str:
        """Fold one judged band into the adjustments. Returns a short note of what changed."""
        self.history.append(rec.as_dict())
        if rec.outcome != "hit":
            self.cleared += 1
            self.save()
            return ""
        self.hits += 1
        note = ""
        key = f"{rec.kind}:{rec.wclass}"
        if rec.press_edge is None:
            if rec.colour is not None:
                note = self._widen(rec.kind, rec.colour, default)
        else:
            offset = rec.press_edge - trigger_x  # >0: pressed before the edge got there
            step = cfg.learn_lead_step_px
            if offset < 0:
                delta = step  # late: press earlier next time
            elif offset > cfg.learn_early_px:
                delta = -step  # early: landed before the band arrived
            else:
                delta = step / 2  # on the edge and still hit: lean earlier
            cur = self.lead_for(rec.kind, rec.wclass) + delta
            new = min(cfg.learn_lead_max_px, max(-cfg.learn_lead_max_px, cur))
            self.lead_px[key] = round(new, 1)
            note = f"lead {key} {delta:+.0f}px -> {new:.0f}px"
        self.save()
        return note

    def _widen(self, kind: str, colour: list[list[int]], default: HsvRange) -> str:
        p5, _p50, p95 = colour
        lo, hi = self.range_for(kind, default)
        hmin, hmax = HUE_FAMILY.get(kind, (0, 180))
        step = 15
        new_lo = [
            max(hmin, min(lo[0], lo[0] - min(step, lo[0] - p5[0]) if p5[0] < lo[0] else lo[0])),
            max(MIN_SAT, min(lo[1], lo[1] - min(step, lo[1] - p5[1]) if p5[1] < lo[1] else lo[1])),
            max(0, min(lo[2], lo[2] - min(step, lo[2] - p5[2]) if p5[2] < lo[2] else lo[2])),
        ]
        new_hi = [
            min(hmax, max(hi[0], hi[0] + min(step, p95[0] - hi[0]) if p95[0] > hi[0] else hi[0])),
            min(255, max(hi[1], hi[1] + min(step, p95[1] - hi[1]) if p95[1] > hi[1] else hi[1])),
            min(255, max(hi[2], hi[2] + min(step, p95[2] - hi[2]) if p95[2] > hi[2] else hi[2])),
        ]
        if kind == "hedge":
            new_hi[2] = min(new_hi[2], HEDGE_V_MAX)
        if [list(lo), list(hi)] == [new_lo, new_hi]:
            return f"{kind} colour already covers the band"
        self.ranges[kind] = [new_lo, new_hi]
        return f"{kind} colour range -> {new_lo}..{new_hi}"


class StallMonitor:
    """Watches the band that is passing the hooves: its edges should keep moving left at the
    scroll speed. Two consecutive observations well below it mean the horse stumbled."""

    def __init__(self, cfg: DerbyConfig) -> None:
        self.cfg = cfg
        self._last: tuple[float, int] | None = None
        self._slow = 0

    def reset(self) -> None:
        self._last = None
        self._slow = 0

    def update(self, now: float, edge_x: int | None, nominal: float) -> bool:
        """Feed the passing band's right edge (None when not visible). True on a stall."""
        if edge_x is None:
            self._last = None
            return False
        if self._last is not None:
            ts, x = self._last
            dt = now - ts
            if 0 < dt <= self.cfg.speed_max_dt_s * 2:
                v = (x - edge_x) / dt
                if v < self.cfg.hit_speed_ratio * nominal:
                    self._slow += 1
                else:
                    self._slow = 0
        self._last = (now, edge_x)
        if self._slow >= self.cfg.hit_min_obs:
            self._slow = 0
            return True
        return False
