"""The Animal Jam Classic economy as numbers the bots can be measured against
(docs/ECONOMY.md): what a run of each activity yields, what a Courage Point or a gem is
worth, and the rates a bot achieves from its own timings.

Facts tagged [wiki] are from the AJ Classic wiki (read 2026-10-30); [measured] from
William's recordings; [est] are ours. Keep the three apart when numbers change."""

from __future__ import annotations

from dataclasses import dataclass, field

# --- Courage Points: the adventure level ladder [wiki] -------------------------------------
# (level, total CP to reach it); the wiki marks 12-21 as approximate
CP_LEVELS: list[tuple[int, int]] = [
    (1, 50),
    (2, 250),
    (3, 700),
    (4, 1_550),
    (5, 2_750),
    (6, 4_450),
    (7, 6_500),
    (8, 9_500),
    (9, 13_000),
    (10, 17_000),
    (11, 22_000),
    (12, 27_500),
    (13, 34_000),
    (14, 41_000),
    (15, 49_000),
    (16, 58_000),
    (17, 68_000),
    (18, 79_000),
    (19, 91_000),
    (20, 104_000),
    (21, 118_000),
]
GEMS_PER_DIAMOND_EST = 15_000  # [est] the community's Diamond-equivalent for item worth


def level_for(cp_total: int) -> int:
    """The adventure level a CP total has reached."""
    lvl = 0
    for level, need in CP_LEVELS:
        if cp_total >= need:
            lvl = level
    return lvl


def cp_to_next(cp_total: int) -> int | None:
    """CP still needed for the next level on the table, None past it."""
    for _level, need in CP_LEVELS:
        if cp_total < need:
            return need - cp_total
    return None


# --- what a run yields ---------------------------------------------------------------------


@dataclass(frozen=True)
class Yield:
    """One completed run of an activity."""

    name: str
    cp_min: int = 0
    cp_max: int = 0
    gems: int = 0  # a typical completion, not the maximum
    minutes: float = 0.0  # a typical run, as measured or estimated
    source: str = ""  # wiki | measured | est
    notes: str = ""

    @property
    def cp_typical(self) -> int:
        return (self.cp_min + self.cp_max) // 2 if self.cp_max else self.cp_min


YIELDS: dict[str, Yield] = {
    "mystery_below_hard": Yield(
        "The Mystery Below (Hard)",
        cp_min=1_290,
        cp_max=2_253,
        gems=300,
        minutes=22.0,  # William's second recording (31 on the first)
        source="wiki+measured",
        notes="43 crystals at 25 CP, 3 decoys, 23 rune stones; chests give 5 fixed den items",
    ),
    "mystery_below_normal": Yield(
        "The Mystery Below (Normal)",
        cp_min=1_000,
        cp_max=1_868,
        gems=150,
        minutes=15.0,
        source="wiki+est",
        notes="William plays Hard only: the second half of the level needs it",
    ),
    "jamaa_derby_normal": Yield(
        "Jamaa Derby (Normal) win",
        gems=100,
        minutes=1.5,
        source="wiki",
        notes="2nd 75, 3rd 50, 4th 40, 5th 30, 6th 25; Pro mode 150/125/100/75/50/25",
    ),
    "double_up": Yield(
        "Double Up (all 8 pairs)",
        gems=44,
        minutes=0.75,
        source="wiki",
        notes="3 gems a match, 20 more for all eight; 60 s timer",
    ),
    "best_guess": Yield(
        "Best Guess (a round)",
        gems=0,
        minutes=0.5,
        source="est",
        notes="payout unknown on the wiki; the ledger measures it live",
    ),
}


# --- rates ---------------------------------------------------------------------------------


@dataclass
class Rate:
    activity: str
    runs: int
    hours: float
    cp: int = 0
    gems: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def cp_per_hour(self) -> float:
        return self.cp / self.hours if self.hours > 0 else 0.0

    @property
    def gems_per_hour(self) -> float:
        return self.gems / self.hours if self.hours > 0 else 0.0

    @property
    def diamonds_per_hour_est(self) -> float:
        return self.gems_per_hour / GEMS_PER_DIAMOND_EST


def rate_from_runs(key: str, runs: int, minutes_total: float, gems_seen: int | None = None) -> Rate:
    """What ``runs`` completed runs in ``minutes_total`` earned: CP from the yield table
    (typical), gems from the ledger when it saw them, else the table."""
    y = YIELDS[key]
    hours = minutes_total / 60.0
    r = Rate(y.name, runs, hours, cp=y.cp_typical * runs)
    r.gems = gems_seen if gems_seen is not None else y.gems * runs
    if gems_seen is None:
        r.notes.append("gems from the table, not the ledger")
    return r


def hours_to_level(cp_now: int, level: int, cp_per_hour: float) -> float | None:
    need = next((n for lv, n in CP_LEVELS if lv == level), None)
    if need is None or cp_per_hour <= 0:
        return None
    return max(0.0, (need - cp_now) / cp_per_hour)
