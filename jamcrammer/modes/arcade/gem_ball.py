"""Gem Ball auto-tuner (KICKOFF §9.4).

1. Sweep the click-and-hold duration across the usable range in fixed steps.
2. Detect the outcome of every ball (slot landed or gem delta) and build a histogram.
3. If one duration hits the top slot / jackpot at least ``exploit_rate`` of the time, exploit it;
   otherwise mark the game non-deterministic and stop. The report is written to data/.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from jamcrammer.core.config import GemBallConfig, Level
from jamcrammer.core.events import Event, EventBus
from jamcrammer.core.logging import get_logger
from jamcrammer.core.profiles import Profile
from jamcrammer.core.store import Store

log = get_logger("gem_ball")

BgrImage = npt.NDArray[np.uint8]
GrabFn = Callable[[], BgrImage]
OutcomeFn = Callable[[BgrImage, BgrImage], str | None]  # (before, after) -> slot label or None
REQUIRED = ("launch_point",)
TOP_SLOTS = ("top", "jackpot")


class Holder(Protocol):
    def press_hold(self, cx: int, cy: int, hold_ms: int) -> bool: ...


@dataclass
class TuningReport:
    durations_ms: list[int]
    outcomes: dict[int, Counter[str]] = field(default_factory=dict)
    balls: int = 0
    best_duration_ms: int | None = None
    best_rate: float = 0.0
    deterministic: bool = False
    exploit_rate: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "durations_ms": self.durations_ms,
            "outcomes": {str(d): dict(c) for d, c in self.outcomes.items()},
            "balls": self.balls,
            "best_duration_ms": self.best_duration_ms,
            "best_rate": round(self.best_rate, 3),
            "deterministic": self.deterministic,
            "exploit_rate": self.exploit_rate,
        }


def evaluate(
    outcomes: dict[int, Counter[str]],
    exploit_rate: float,
    top: tuple[str, ...] = TOP_SLOTS,
    min_balls: int = 1,
) -> tuple[int | None, float, bool]:
    """Best duration by top-slot hit rate; deterministic if the best rate ≥ exploit_rate with
    at least ``min_balls`` balls behind it (two lucky balls are not a law)."""
    best: tuple[int | None, float] = (None, 0.0)
    for duration, counter in outcomes.items():
        total = sum(counter.values())
        if total < min_balls:
            continue
        rate = sum(counter[s] for s in top) / total
        if rate > best[1] or (rate == best[1] and best[0] is None):
            best = (duration, rate)
    return (best[0], best[1], best[1] >= exploit_rate and best[0] is not None)


class GemBallTuner:
    def __init__(
        self,
        profile: Profile,
        cfg: GemBallConfig,
        holder: Holder,
        grab: GrabFn,
        outcome: OutcomeFn,
        *,
        store: Store | None = None,
        bus: EventBus | None = None,
        report_dir: Path | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        missing = profile.missing(list(REQUIRED))
        if missing:
            msg = f"gem_ball profile missing {missing}"
            raise ValueError(msg)
        self.profile = profile
        self.cfg = cfg
        self._hold = holder
        self._grab = grab
        self._outcome = outcome
        self._store = store
        self._bus = bus
        self._report_dir = report_dir
        self._clock = clock
        self._sleep = sleep
        self.report = TuningReport(
            durations_ms=list(range(cfg.hold_min_ms, cfg.hold_max_ms + 1, cfg.hold_step_ms))
        )

    def launch(self, hold_ms: int) -> str | None:
        x, y = self.profile.point("launch_point")
        before = self._grab()
        self._hold.press_hold(x, y, hold_ms)
        self._sleep(self.cfg.settle_s)
        after = self._grab()
        return self._outcome(before, after)

    def sweep(self, should_stop: Callable[[], bool] | None = None) -> TuningReport:
        rep = self.report
        for rep_round in range(self.cfg.repeats):
            for duration in rep.durations_ms:
                if should_stop is not None and should_stop():
                    return self._finish()
                slot = self.launch(duration)
                rep.balls += 1
                rep.outcomes.setdefault(duration, Counter())[slot or "miss"] += 1
                self._emit("gem_ball.ball", duration_ms=duration, slot=slot, round=rep_round)
                if self._store is not None:
                    self._store.add_arcade_round(
                        ts=self._clock(),
                        game="gem_ball",
                        duration_s=duration / 1000.0,
                        gems_reported=None,
                        mismatches=0,
                        notes=f"tune slot={slot}",
                    )
            _best, _rate, deterministic = evaluate(
                rep.outcomes, self.cfg.exploit_rate, min_balls=self.cfg.min_balls
            )
            if deterministic and rep_round + 1 >= self.cfg.min_repeats:
                break
        return self._finish()

    def exploit(self, balls: int, should_stop: Callable[[], bool] | None = None) -> Counter[str]:
        assert self.report.best_duration_ms is not None
        hits: Counter[str] = Counter()
        for _ in range(balls):
            if should_stop is not None and should_stop():
                break
            hits[self.launch(self.report.best_duration_ms) or "miss"] += 1
        self._emit("gem_ball.exploit", duration_ms=self.report.best_duration_ms, hits=dict(hits))
        return hits

    def _finish(self) -> TuningReport:
        rep = self.report
        rep.best_duration_ms, rep.best_rate, rep.deterministic = evaluate(
            rep.outcomes, self.cfg.exploit_rate, min_balls=self.cfg.min_balls
        )
        rep.exploit_rate = self.cfg.exploit_rate
        verdict = "deterministic" if rep.deterministic else "non-deterministic"
        self._emit(
            "gem_ball.verdict",
            Level.NOTICE,
            verdict=verdict,
            best_duration_ms=rep.best_duration_ms,
            best_rate=round(rep.best_rate, 2),
            balls=rep.balls,
            message=(
                f"Gem Ball is {verdict}: best hold {rep.best_duration_ms} ms hits the top slot "
                f"{rep.best_rate:.0%} of the time ({rep.balls} balls)"
            ),
        )
        if self._report_dir is not None:
            self._report_dir.mkdir(parents=True, exist_ok=True)
            out = self._report_dir / f"gemball_tuning_{time.strftime('%Y%m%d_%H%M%S')}.json"
            out.write_text(json.dumps(rep.as_dict(), indent=2), encoding="utf-8")
        return rep

    def _emit(self, event_type: str, level: Level = Level.INFO, **p: object) -> None:
        if self._bus is not None:
            self._bus.publish(
                Event(type=event_type, source="gem_ball", level=level, payload=dict(p))
            )


def gem_delta_outcome(
    read_gems: Callable[[BgrImage], int | None], top_delta: int, jackpot_delta: int
) -> OutcomeFn:
    """Outcome from the gem counter: delta ≥ jackpot → 'jackpot', ≥ top slot → 'top', >0 → 'low'."""

    def outcome(before: BgrImage, after: BgrImage) -> str | None:
        a, b = read_gems(before), read_gems(after)
        if a is None or b is None:
            return None
        delta = b - a
        if delta >= jackpot_delta:
            return "jackpot"
        if delta >= top_delta:
            return "top"
        return "low" if delta > 0 else "miss"

    return outcome
