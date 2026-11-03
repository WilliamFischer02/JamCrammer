"""The engine's start paths and live stats (William, 2026-11-02 evening report):

- stats reach the GUI-facing snapshot while a mode runs (not only when it stops);
- Start classifies a fresh frame and goes straight to farming in the arena;
- ``presence.mark()`` runs on every mode's Start;
- the den gate refuses a user Start outside the den, never an internal hand-off;
- the input lease passes from a farmer to the Best Guess Farmer, logged.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from jamcrammer.app.engine import BESTGUESS_STATS, MODES, Engine
from jamcrammer.core.config import AppConfig, BestGuessConfig
from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import Profile, load_profile
from jamcrammer.core.store import Store
from jamcrammer.modes.bestguess.brain import Brain
from jamcrammer.modes.bestguess.farmer import BestGuessFarmer
from jamcrammer.modes.bestguess.learner import Verdict
from jamcrammer.modes.bestguess.state import BgState, Observation
from jamcrammer.modes.situation import Sighting, Situation

FRAME = np.zeros((1080, 1920, 3), dtype=np.uint8)
ROUND_TICKS = 12  # scripted detector: waiting 2, question 7, reveal 3


@pytest.fixture(scope="module")
def engine(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Engine]:
    repo = Paths()
    root = tmp_path_factory.mktemp("engine")
    shutil.copytree(repo.profiles, root / "profiles")
    shutil.copytree(repo.assets, root / "assets")
    cfg = AppConfig()
    cfg.notify.toast = cfg.notify.sound = False
    eng = Engine(cfg, Paths(root))
    yield eng
    eng.shutdown()


# --- 1. live stats -----------------------------------------------------------------------------


class ScriptedDetector:
    """Mocked detector output: rounds of WAITING -> QUESTION_OPEN -> REVEAL (always True)."""

    def __init__(self) -> None:
        self.tick = 0

    def observe(self, _image: np.ndarray, _now: float | None = None) -> Observation:
        phase = self.tick % ROUND_TICKS
        n = self.tick // ROUND_TICKS
        self.tick += 1
        if phase < 2:
            return Observation(BgState.WAITING_FOR_ROUND)
        if phase < 9:
            return Observation(
                BgState.QUESTION_OPEN,
                fact_text=f"Scripted fact number {n} is true",
                fact_norm=f"scripted fact number {n} is true",
                seconds_left=10 - (phase - 2),
            )
        return Observation(BgState.REVEAL, reveal_side="T", reveal_score=0.9)


class RevealLearner:
    name = "scripted"

    def judge(
        self,
        before: np.ndarray,
        reveal: np.ndarray,
        obs: Observation,
        chosen: Any,
        gems_before: int | None,
    ) -> Verdict:
        return Verdict(self.name, obs.reveal_side, 0.95)  # type: ignore[arg-type]


class Keys:
    def __init__(self) -> None:
        self.log: list[str] = []

    def click(self, cx: int, cy: int) -> bool:
        self.log.append(f"click {cx},{cy}")
        return True

    def key_down(self, key: str) -> None:
        self.log.append(f"down {key}")

    def key_up(self, key: str) -> None:
        self.log.append(f"up {key}")


def _bg_profile(paths: Paths) -> Profile:
    prof = load_profile("bestguess", paths)
    prof.set_value("fact_roi", [360, 40, 1200, 120])
    prof.set_value("true_point", [480, 700])
    prof.set_value("false_point", [1440, 700])
    prof.set_value("reveal_word_roi", [700, 15, 540, 95])
    return prof


def test_best_guess_stats_reach_the_gui_snapshot_while_farming(
    engine: Engine, tmp_path: Path
) -> None:
    store = Store(tmp_path / "t.db")
    brain = Brain(store, fuzzy_threshold=90, unknown_policy="fixed_true", answer_mode="bank_policy")
    cfg = BestGuessConfig(fps=5.0, commit_after_s=0.0, own_name="")
    clock = {"t": 0.0}

    def sleep(s: float) -> None:
        clock["t"] += s

    farmer = BestGuessFarmer(
        _bg_profile(engine.paths),
        cfg,
        brain,
        Keys(),
        lambda: FRAME,
        detector=ScriptedDetector(),  # type: ignore[arg-type]
        learners=[RevealLearner()],
        store=store,
        clock=lambda: clock["t"],
        sleep=sleep,
    )
    engine.state.stats.pop(BESTGUESS_STATS, None)
    engine._attach(farmer, BESTGUESS_STATS)
    seen: list[tuple[int, int, int]] = []

    def should_stop() -> bool:
        s = engine.state.stats.get(BESTGUESS_STATS, {})  # what the page tiles read
        seen.append((int(s.get("rounds", 0)), int(s.get("answered", 0)), int(s.get("correct", 0))))
        return seen[-1][0] >= 3 and seen[-1][2] >= 2

    farmer.run(should_stop, engine._status)
    rounds = [r for r, _a, _c in seen]
    assert rounds[0] == 0 and 1 in rounds and 2 in rounds and rounds[-1] == 3, rounds
    assert seen[-1][1] >= 2 and seen[-1][2] >= 2  # answered and correct moved during the run
    assert engine.state.mode_state == str(farmer.state)
    assert engine.state.stats[BESTGUESS_STATS]["multiplier"] >= 2
    store.close()


def test_stats_are_published_for_plain_dataclasses_too(engine: Engine) -> None:
    class Stats:
        def __init__(self) -> None:
            self.races = 2
            self.gems = 150
            self.last = object()  # not a scalar: left out

    class Mode:
        stats = Stats()

    engine._attach(Mode(), "farm")
    assert engine.state.stats["farm"] == {"races": 2, "gems": 150}
    engine._mode_obj = None
    engine._stats_key = ""


# --- 2 / 3. Start: fresh classification, presence mark, routing ------------------------------


def _fake_start(engine: Engine, monkeypatch: pytest.MonkeyPatch, situation: Situation) -> None:
    monkeypatch.setattr(engine, "start_capture", lambda quiet=False: True)
    monkeypatch.setattr(engine, "focus_game", lambda: True)
    monkeypatch.setattr(engine, "grab", lambda: (_ for _ in ()).throw(AssertionError("no grab")))
    monkeypatch.setattr(
        engine,
        "classify_now",
        lambda announce=True: _set_sighting(engine, Sighting(situation, note="fake")),
    )
    engine.state.situation = str(situation)
    engine.state.situation_ts = time.time()
    engine.state.killed = False


def _set_sighting(engine: Engine, sight: Sighting) -> Sighting:
    engine.state.situation = str(sight.situation)
    engine.state.situation_room = sight.room
    engine.state.situation_ts = time.time()
    return sight


def _wait_idle(engine: Engine) -> None:
    t = engine._mode_thread
    if t is not None:
        t.join(5.0)
    assert engine._mode_thread is None


def test_every_mode_start_marks_presence_and_runs_its_target(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_start(engine, monkeypatch, Situation.DEN)
    marks: list[float] = []
    monkeypatch.setattr(engine.presence, "mark", lambda: marks.append(time.time()))
    ran: list[str] = []
    for mode in MODES:
        monkeypatch.setattr(engine, f"_run_{mode}", lambda m=mode: ran.append(m))
        assert engine.start_mode(mode), mode
        _wait_idle(engine)
    assert ran == list(MODES)
    assert len(marks) == len(MODES)  # the click on Start is never a takeover, for any mode
    assert engine.lease.owner is None


def test_best_guess_farmer_start_in_the_arena_farms_at_once(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_start(engine, monkeypatch, Situation.ARENA)
    calls: list[str] = []
    monkeypatch.setattr(engine, "_phase_c_allowed", lambda: True)
    monkeypatch.setattr(engine, "_run_bestguess_farmer", lambda: calls.append("farmer"))
    monkeypatch.setattr(engine, "_run_plan", lambda *a: calls.append("plan"))
    events: list[str] = []
    unsub = engine.bus.subscribe("*", lambda e: events.append(e.type))
    assert engine.start_mode("hunt")
    _wait_idle(engine)
    unsub()
    assert calls == ["farmer"]  # never the tray, never the den button
    assert "engine.mode_started" in events


def test_best_guess_farmer_start_in_the_den_waits_or_races_under_the_plan(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_start(engine, monkeypatch, Situation.DEN)
    plugins: list[str] = []
    monkeypatch.setattr(engine, "_run_bestguess_farmer", lambda: pytest.fail("farmed"))
    monkeypatch.setattr(engine, "_run_plan", lambda _m, _nav, plugin: plugins.append(plugin.name))
    monkeypatch.setattr(
        engine,
        "_make_navigator",
        lambda: SimpleNamespace(in_den=lambda _im: True, go_to_den=lambda: None),
    )
    engine.cfg.hunt.derby_while_waiting = False
    assert engine.start_mode("hunt")
    _wait_idle(engine)
    assert plugins == ["the den (waiting)"]


def test_arcade_start_runs_the_plan_only_with_the_checkbox(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_start(engine, monkeypatch, Situation.DEN)
    calls: list[str] = []
    monkeypatch.setattr(engine, "_run_plan", lambda _m, _nav, p: calls.append(f"plan:{p.name}"))
    monkeypatch.setattr(engine, "_farm_game", lambda extra_stop=None: calls.append("farm"))
    monkeypatch.setattr(engine, "_make_navigator", object)
    monkeypatch.setattr(engine, "_derby_plugin", lambda _nav: _Plugin("Jamaa Derby"))
    engine.cfg.arcade_game = "jamaa_derby"
    engine.cfg.hunt_best_guess.farm = True
    assert engine.start_mode("farm")
    _wait_idle(engine)
    engine.cfg.hunt_best_guess.farm = False
    assert engine.start_mode("farm")
    _wait_idle(engine)
    assert calls == ["plan:Jamaa Derby", "farm"]


class _Plugin:
    def __init__(self, name: str) -> None:
        self.name = name


# --- 5. the den gate on Start ---------------------------------------------------------------


def test_user_start_is_refused_outside_the_den_and_allowed_internally(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_start(engine, monkeypatch, Situation.ROOM)
    ran: list[str] = []
    monkeypatch.setattr(engine, "_run_farm", lambda: ran.append("farm"))
    monkeypatch.setattr(engine, "_run_hunt", lambda: ran.append("hunt"))
    events: list[str] = []
    unsub = engine.bus.subscribe("*", lambda e: events.append(e.type))
    assert not engine.start_mode("farm")
    assert "Return to Den" in engine.state.last_error and "engine.start_refused" in events
    assert not engine.start_mode("hunt")  # the Best Guess Farmer too: a room is not the arena
    assert engine.start_mode("farm", internal=True)  # the hand-off path is never gated
    _wait_idle(engine)
    unsub()
    assert ran == ["farm"]


def test_a_stale_room_read_is_refreshed_before_a_start_is_refused(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The poll said 'room' 2 s ago; William walked into the den: the fresh look lets him go."""
    _fake_start(engine, monkeypatch, Situation.DEN)
    engine.state.situation = str(Situation.ROOM)  # the stale poll
    ran: list[str] = []
    monkeypatch.setattr(engine, "_run_farm", lambda: ran.append("farm"))
    assert engine.start_mode("farm")
    _wait_idle(engine)
    assert ran == ["farm"] and engine.state.situation == str(Situation.DEN)


def test_best_guess_farmer_may_start_in_the_arena_but_the_arcade_may_not(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_start(engine, monkeypatch, Situation.ARENA)
    assert engine.start_allowed("hunt") == (True, "")
    ok, why = engine.start_allowed("farm")
    assert not ok and "arena" in why


# --- 4. the hand-off -------------------------------------------------------------------------


def test_handoff_moves_the_lease_the_mode_and_is_logged(engine: Engine) -> None:
    events: list[Any] = []
    unsub = engine.bus.subscribe("engine.handoff", events.append)
    engine.state.mode = "farm"
    engine._take_lease("farm")
    assert engine.lease.owner == "farm"
    engine._handoff("farm", "hunt")
    unsub()
    assert engine.lease.owner == "hunt" and engine.state.mode == "hunt"
    assert engine.state.handoff == "Arcade Farmer -> Best Guess Farmer"
    assert (
        events and events[0].payload["previous"] == "farm" and events[0].payload["mode"] == "hunt"
    )
    engine._release_lease()
    assert engine.lease.owner is None
    engine.state.mode = None
    engine.state.handoff = ""
