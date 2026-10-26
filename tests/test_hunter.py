"""Hunter Phases A/B against the simulated panel: HIGH alert on Best Guess, join or no-join."""

from __future__ import annotations

import numpy as np

from jamcrammer.core.config import AppConfig, Level
from jamcrammer.core.events import EventBus
from jamcrammer.modes.bestguess.hunter import Hunter
from jamcrammer.modes.party_watcher import PartyWatcher
from jamcrammer.platform.capture import Frame
from jamcrammer.platform.window import Rect
from jamcrammer.services.antiafk import RoomDriftGuard
from tests.test_party_watcher import FIXTURE_FULL, FakeWorld, _analyzer, _load


def _best_guess_world() -> FakeWorld:
    """Fake OCR reads 'Best Guess Game' on the slot art; the slot art itself is blanked."""
    an = _analyzer(ocr=lambda c: "Best Guess Game" if c.shape[1] < 500 else "")
    world = FakeWorld(an)
    panel = _load(FIXTURE_FULL)
    panel[670:750, 712:1035] = (30, 30, 30)
    world.panel = panel
    return world


def _frame(image: np.ndarray, ts: float) -> Frame:
    return Frame(image=image, ts=ts, seq=1, client_rect=Rect(0, 0, 1920, 1080), backend="fake")


def _hunter(world: FakeWorld, bus: EventBus, no_join: bool, clock: dict[str, float]) -> Hunter:
    cfg = AppConfig()
    cfg.hunt.panel_settle_s = 0
    cfg.hunt.panel_poll_s = 0
    cfg.hunt.arrival_timeout_s = 1.0
    cfg.hunt.antiafk_each_cycle = False
    watcher = PartyWatcher(
        world.an,
        cfg.hunt,
        world,
        world.grab,
        bus=bus,
        clock=lambda: clock["t"],
        sleep=lambda _s: None,
    )
    guard = RoomDriftGuard(cfg.drift, [])
    return Hunter(
        cfg,
        watcher,
        lambda: _frame(world.grab(), clock["t"]),
        guard=guard,
        bus=bus,
        no_join=no_join,
        clock=lambda: clock["t"],
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
    )


def test_high_alert_fires_and_no_join_never_clicks_go_now() -> None:
    world = _best_guess_world()
    bus = EventBus()
    events: list[tuple[str, Level]] = []
    bus.subscribe("*", lambda e: events.append((e.type, e.level)))
    hunter = _hunter(world, bus, no_join=True, clock={"t": 0.0})
    res = hunter.cycle()
    assert res.target is not None and res.target.label == "Best Guess Game"
    assert ("hunter.best_guess_found", Level.HIGH) in events
    assert hunter.stats.target_seen == 1 and not hunter.stats.joined
    assert not world.joined
    assert not world.open  # panel closed again after the scan


def test_auto_join_clicks_go_now_and_starts_learn_callback() -> None:
    world = _best_guess_world()
    bus = EventBus()
    events: list[str] = []
    bus.subscribe("*", lambda e: events.append(e.type))
    joined: list[str] = []
    clock = {"t": 0.0}
    hunter = _hunter(world, bus, no_join=False, clock=clock)
    hunter._on_joined = lambda b: joined.append(b.label or "?")
    hunter.cycle()
    assert world.joined and hunter.stats.joined
    assert "hunter.joined" in events
    assert joined == ["Best Guess Game"]


def test_run_loop_scans_on_interval_and_stops_when_joined() -> None:
    world = _best_guess_world()
    bus = EventBus()
    clock = {"t": 0.0}
    hunter = _hunter(world, bus, no_join=True, clock=clock)
    hunter.cfg.hunt_interval_s = 10.0
    ticks = {"n": 0}

    def should_stop() -> bool:
        ticks["n"] += 1
        return ticks["n"] > 200 or clock["t"] > 35.0

    hunter.run(should_stop)
    assert hunter.stats.cycles >= 3  # t=0, 10, 20, 30
    assert hunter.stats.target_seen == hunter.stats.cycles
