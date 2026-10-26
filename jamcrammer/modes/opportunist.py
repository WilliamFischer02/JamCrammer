"""Opportunist orchestrator (KICKOFF §10): farm Double Up, peek at My Parties every
``opportunist_check_s`` at a round boundary, hop to Best Guess the moment it appears.

Rules: never check mid-round; measure the overhead of every check; if re-entry into the
arcade game fails twice in a row, fall back to plain Hunt mode and alert.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from jamcrammer.core.config import Level, OpportunistConfig
from jamcrammer.core.events import Event, EventBus
from jamcrammer.core.logging import get_logger
from jamcrammer.core.profiles import Profile
from jamcrammer.core.store import Store
from jamcrammer.modes.arcade.double_up import DoubleUpFarmer
from jamcrammer.modes.bestguess.hunter import Hunter

log = get_logger("opportunist")

BgrImage = npt.NDArray[np.uint8]
GrabFn = Callable[[], BgrImage]
ENTRY_FIELDS = ("exit_point", "games_menu_point", "game_tile_point")


class Clicker(Protocol):
    def click(self, cx: int, cy: int) -> bool: ...


class ArcadeEntry:
    """Leave and re-enter the arcade game through the calibrated entry route."""

    def __init__(
        self,
        profile: Profile,
        cfg: OpportunistConfig,
        clicker: Clicker,
        grab: GrabFn,
        in_game: Callable[[BgrImage], bool],
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        missing = profile.missing(list(ENTRY_FIELDS))
        if missing:
            msg = f"double_up entry route missing {missing}"
            raise ValueError(msg)
        self.profile = profile
        self.cfg = cfg
        self._click = clicker
        self._grab = grab
        self._in_game = in_game
        self._clock = clock
        self._sleep = sleep

    def _wait(self, predicate: Callable[[BgrImage], bool], timeout_s: float) -> bool:
        deadline = self._clock() + timeout_s
        while True:
            if predicate(self._grab()):
                return True
            if self._clock() >= deadline:
                return False
            self._sleep(self.cfg.poll_s)

    def leave(self) -> bool:
        x, y = self.profile.point("exit_point")
        for _ in range(2):
            self._click.click(x, y)
            if self._wait(lambda im: not self._in_game(im), self.cfg.exit_timeout_s):
                return True
        return False

    def enter(self) -> bool:
        mx, my = self.profile.point("games_menu_point")
        tx, ty = self.profile.point("game_tile_point")
        self._click.click(mx, my)
        self._sleep(self.cfg.settle_s)
        self._click.click(tx, ty)
        return self._wait(self._in_game, self.cfg.entry_timeout_s)


@dataclass
class OpportunistStats:
    rounds: int = 0
    checks: int = 0
    overhead_s: list[float] = field(default_factory=list)
    entry_failures: int = 0
    hopped: bool = False
    fell_back: bool = False

    @property
    def mean_overhead_s(self) -> float:
        return sum(self.overhead_s) / len(self.overhead_s) if self.overhead_s else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "rounds": self.rounds,
            "checks": self.checks,
            "mean_overhead_s": round(self.mean_overhead_s, 2),
            "total_overhead_s": round(sum(self.overhead_s), 1),
            "entry_failures": self.entry_failures,
            "hopped": self.hopped,
            "fell_back": self.fell_back,
        }


class Opportunist:
    def __init__(
        self,
        cfg: OpportunistConfig,
        check_interval_s: float,
        farmer: DoubleUpFarmer,
        hunter: Hunter,
        entry: ArcadeEntry,
        *,
        store: Store | None = None,
        bus: EventBus | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.cfg = cfg
        self.check_interval_s = check_interval_s
        self.farmer = farmer
        self.hunter = hunter
        self.entry = entry
        self._store = store
        self._bus = bus
        self._clock = clock
        self.stats = OpportunistStats()
        self.state = "FARMING"

    def run(
        self, should_stop: Callable[[], bool], status: Callable[[str], None] | None = None
    ) -> None:
        next_check = self._clock() + self.check_interval_s
        while not should_stop():
            self.state = "FARMING"
            res = self.farmer.play_round()
            self.stats.rounds += 1
            if status is not None:
                wait = max(0, int(next_check - self._clock()))
                status(
                    f"FARMING · round {self.stats.rounds} · {res.pairs_found}/8 · "
                    f"next check in {wait}s"
                )
            if self._clock() < next_check or should_stop():
                continue
            if self.check(should_stop):
                return  # hopped to Best Guess or fell back to Hunt
            next_check = self._clock() + self.check_interval_s

    def check(self, should_stop: Callable[[], bool]) -> bool:
        """One party-slot check at a round boundary. Returns True when the orchestrator is done
        (joined Best Guess, or handed over to plain Hunt)."""
        t0 = self._clock()
        self.state = "CHECKING"
        self.stats.checks += 1
        if not self.entry.leave():
            self._emit("opportunist.exit_failed", Level.NOTICE)
        scan = self.hunter.cycle()
        if self.hunter.stats.joined:
            self.stats.hopped = True
            self._record(t0, "hopped")
            self._emit("opportunist.hopped", Level.HIGH, message="Hopped to Best Guess.")
            return True
        self.state = "REENTERING"
        if self.entry.enter() or self.entry.enter():
            self.stats.entry_failures = 0
            self._record(t0, "ok", labels=scan.labels)
            return False
        self.stats.entry_failures += 1
        self._record(t0, "entry_failed", labels=scan.labels)
        self._emit("opportunist.entry_failed", Level.NOTICE, failures=self.stats.entry_failures)
        if self.stats.entry_failures >= self.cfg.max_entry_failures:
            self.stats.fell_back = True
            self.state = "HUNT_FALLBACK"
            self._emit(
                "opportunist.fallback",
                Level.HIGH,
                message="Could not re-enter the arcade twice; hunting only.",
            )
            self.hunter.run(should_stop)
            return True
        return False

    def _record(self, t0: float, outcome: str, labels: list[str] | None = None) -> None:
        overhead = self._clock() - t0
        self.stats.overhead_s.append(overhead)
        self._emit(
            "opportunist.check", overhead_s=round(overhead, 2), outcome=outcome, labels=labels or []
        )

    def _emit(self, event_type: str, level: Level = Level.INFO, **p: object) -> None:
        if self._bus is not None:
            self._bus.publish(
                Event(type=event_type, source="opportunist", level=level, payload=dict(p))
            )


def overhead_summary(store: Store, since_ts: float = 0.0) -> dict[str, float | int]:
    """Profit Lab: seconds lost to party checks, from the events table."""
    rows = store.query(
        "SELECT payload_json FROM events WHERE type='opportunist.check' AND ts>=?", (since_ts,)
    )
    import json

    values = [float(json.loads(r["payload_json"]).get("overhead_s", 0.0)) for r in rows]
    return {
        "checks": len(values),
        "mean_s": round(sum(values) / len(values), 2) if values else 0.0,
        "total_s": round(sum(values), 1),
    }
