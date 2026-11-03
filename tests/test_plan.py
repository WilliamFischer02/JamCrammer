"""The shared "Hunt Best Guess" plan (modes/planner.py BestGuessPlan) against a fake game:
tray -> farm until due at a round boundary -> leave to the den -> hunt -> joined."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import numpy as np

from jamcrammer.core.config import Level, PlannerConfig
from jamcrammer.core.events import EventBus
from jamcrammer.modes.party_watcher import Banner, ScanResult
from jamcrammer.modes.planner import HUNT_NOW, BestGuessPlan, FarmPlugin, NavResult

FRAME = np.zeros((4, 4, 3), dtype=np.uint8)


class Clock:
    def __init__(self) -> None:
        self.t = 0.0


class FakeNav:
    """The navigator the plan talks to: the tray, My Parties, the den, waits."""

    def __init__(self, clock: Clock, *, tray_eta: int | None, banner_showing: bool = False) -> None:
        self.clock = clock
        self.tray_eta = tray_eta
        self.banner_showing = banner_showing
        self.calls: list[str] = []
        self.in_den_now = True
        self.watcher = self

    # tray / panel
    def read_best_guess_eta(self) -> tuple[int | None, str]:
        self.calls.append("read_tray")
        return (self.tray_eta, "tray says 'x'" if self.tray_eta else "no banner")

    def scan(self) -> ScanResult:
        self.calls.append("scan")
        res = ScanResult(ts=self.clock.t)
        if self.banner_showing:
            res.target = Banner(index=2, box=(0, 0, 1, 1), is_slot=True, go_now_box=(0, 0, 1, 1))
        return res

    # den
    def go_to_den(self) -> NavResult:
        self.calls.append("go_to_den")
        self.in_den_now = True
        return NavResult(True, "den reached")

    def in_den(self, _image: Any = None) -> bool:
        return self.in_den_now

    # frames and waits
    def _grab(self) -> np.ndarray:
        return FRAME

    def _wait(self, predicate: Callable[[np.ndarray], bool], timeout_s: float) -> np.ndarray | None:
        self.calls.append(f"wait({timeout_s:.0f})")
        self.clock.t += timeout_s
        return FRAME if predicate(FRAME) else None


class FakeGame:
    """A game with rounds: farm() runs rounds of ``round_s`` and honours the stop predicate
    only at a boundary; ``kick_after`` rounds makes it vanish once (an advert)."""

    def __init__(
        self, clock: Clock, nav: FakeNav, *, round_s: float = 60.0, kick_after: int | None = None
    ) -> None:
        self.clock = clock
        self.nav = nav
        self.round_s = round_s
        self.kick_after = kick_after
        self.open = False
        self.rounds = 0
        self.mid_round = False
        self.reason = ""
        self.stopped_mid_round = False
        self.log: list[str] = []

    def in_game(self, _image: np.ndarray) -> bool:
        return self.open

    def enter(self) -> NavResult:
        self.log.append("enter")
        self.open = True
        self.nav.in_den_now = False
        return NavResult(True, "opened")

    def farm(self, stop: Callable[[], bool]) -> None:
        self.log.append("farm")
        self.reason = ""
        while True:
            if stop():  # only ever asked at a boundary (mid_round is False here)
                return
            self.mid_round = True
            self.clock.t += self.round_s
            self.mid_round = False
            self.rounds += 1
            if self.kick_after is not None and self.rounds == self.kick_after:
                self.open = False
                self.reason = "unknown_screen"
                self.kick_after = None
                return

    def can_pause_now(self) -> bool:
        return not self.mid_round

    def interrupted(self) -> str:
        return self.reason

    def leave(self) -> NavResult:
        self.log.append("leave")
        assert not self.mid_round, "left mid-round"
        self.open = False
        return self.nav.go_to_den()

    def plugin(self) -> FarmPlugin:
        return FarmPlugin(
            name="fake game",
            in_game=self.in_game,
            enter=self.enter,
            farm=self.farm,
            can_pause_now=self.can_pause_now,
            interrupted=self.interrupted,
            leave=self.leave,
        )


def _plan(
    clock: Clock,
    nav: FakeNav,
    *,
    hunt_joins: bool = True,
    stop_at: float | None = None,
    cfg: PlannerConfig | None = None,
) -> tuple[BestGuessPlan, dict[str, Any], list[str]]:
    state: dict[str, Any] = {"eta_ts": None, "hunts": 0, "status": []}
    bus = EventBus()
    events: list[str] = []
    bus.subscribe("*", lambda e: events.append(e.type))

    def eta_s() -> float | None:
        return None if state["eta_ts"] is None else state["eta_ts"] - clock.t

    def set_eta(seconds: float | None, note: str) -> None:
        state["eta_ts"] = None if seconds is None else clock.t + seconds
        state["note"] = note

    def hunt() -> bool:
        state["hunts"] += 1
        return hunt_joins

    plan = BestGuessPlan(
        cfg or PlannerConfig(leave_lead_s=90, ad_wait_s=120, reentries_max=3),
        cast(Any, nav),
        eta_s=eta_s,
        set_eta=set_eta,
        should_stop=lambda: stop_at is not None and clock.t >= stop_at,
        hunt=hunt,
        status=state["status"].append,
        bus=bus,
    )
    return plan, state, events


def test_plan_reads_the_tray_farms_until_due_leaves_at_a_boundary_and_joins() -> None:
    clock = Clock()
    nav = FakeNav(clock, tray_eta=10 * 60)  # "10 minutes"
    game = FakeGame(clock, nav, round_s=60.0)
    plan, state, events = _plan(clock, nav)
    out = plan.run(game.plugin())
    assert out == "joined"
    assert nav.calls[:2] == ["read_tray", "go_to_den"]
    assert game.log == ["enter", "farm", "leave"]
    # left at the first boundary inside the 90 s lead: rounds of 60 s, due at 600 s
    assert game.rounds == 9 and 600 - clock.t <= 90
    assert state["hunts"] == 1 and nav.in_den_now
    assert "planner.eta_read" in events and "planner.left_game" in events


def test_plan_joins_at_once_when_the_banner_is_already_in_my_parties() -> None:
    clock = Clock()
    nav = FakeNav(clock, tray_eta=None, banner_showing=True)
    game = FakeGame(clock, nav)
    plan, state, events = _plan(clock, nav)
    assert plan.read_eta() == HUNT_NOW
    assert plan.run(game.plugin()) == "joined"
    assert game.log == [] and state["hunts"] == 1
    assert "planner.best_guess_showing" in events


def test_plan_falls_back_to_the_default_when_the_tray_and_the_panel_show_nothing() -> None:
    clock = Clock()
    nav = FakeNav(clock, tray_eta=None, banner_showing=False)
    game = FakeGame(clock, nav, round_s=60.0)
    cfg = PlannerConfig(leave_lead_s=90, eta_default_min=5, reentries_max=3)
    plan, state, events = _plan(clock, nav, cfg=cfg)
    assert plan.run(game.plugin()) == "joined"
    assert state["note"] == "default" and "planner.eta_default" in events
    assert game.rounds == 4  # 5 min default, 90 s lead


def test_plan_stops_cleanly_when_the_user_stops_it_mid_farm() -> None:
    clock = Clock()
    nav = FakeNav(clock, tray_eta=60 * 60)
    game = FakeGame(clock, nav, round_s=60.0)
    plan, state, _events = _plan(clock, nav, stop_at=200.0)
    assert plan.run(game.plugin()) == "stopped"
    assert "leave" not in game.log and state["hunts"] == 0


def test_plan_recovers_when_the_game_vanishes_and_re_enters_from_the_den() -> None:
    clock = Clock()
    nav = FakeNav(clock, tray_eta=20 * 60)
    game = FakeGame(clock, nav, round_s=60.0, kick_after=3)
    plan, _state, events = _plan(clock, nav)
    assert plan.run(game.plugin()) == "joined"
    assert "planner.farm_interrupted" in events
    assert game.log == ["enter", "farm", "enter", "farm", "leave"]
    assert nav.calls.count("go_to_den") == 3  # start, after the kick, after leaving


def test_plan_fails_safely_when_the_den_or_the_game_cannot_be_reached() -> None:
    clock = Clock()
    nav = FakeNav(clock, tray_eta=10 * 60)
    game = FakeGame(clock, nav)
    plugin = game.plugin()
    plugin.enter = lambda: NavResult(False, "the tile did not match")
    plan, state, events = _plan(clock, nav)
    assert plan.run(plugin) == "failed"
    assert plan.last_error == "the tile did not match" and state["hunts"] == 0
    assert "planner.enter" in set(events)


def test_plan_reports_high_when_the_hunt_never_joins() -> None:
    clock = Clock()
    nav = FakeNav(clock, tray_eta=2 * 60)
    game = FakeGame(clock, nav, round_s=30.0)
    plan, _state, _events = _plan(clock, nav, hunt_joins=False)
    assert plan.run(game.plugin()) == "failed"


def test_plan_events_carry_levels() -> None:
    bus = EventBus()
    seen: list[tuple[str, Level]] = []
    bus.subscribe("*", lambda e: seen.append((e.type, e.level)))
    clock = Clock()
    nav = FakeNav(clock, tray_eta=None, banner_showing=True)
    plan = BestGuessPlan(
        PlannerConfig(),
        cast(Any, nav),
        eta_s=lambda: None,
        set_eta=lambda _s, _n: None,
        should_stop=lambda: False,
        hunt=lambda: True,
        bus=bus,
    )
    plan.read_eta()
    assert ("planner.eta_unread", Level.NOTICE) in seen
    assert ("planner.best_guess_showing", Level.HIGH) in seen
