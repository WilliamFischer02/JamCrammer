"""M11: Gem Ball auto-tuner verdicts and the Swoopy Eagle controller on a flappy simulation."""

from __future__ import annotations

import random
from pathlib import Path

import cv2
import numpy as np

from jamcrammer.core.config import GemBallConfig, SwoopyConfig
from jamcrammer.core.events import EventBus
from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import Profile, load_profile
from jamcrammer.modes.arcade.gem_ball import GemBallTuner, evaluate, gem_delta_outcome
from jamcrammer.modes.arcade.swoopy import PDController, SwoopyPilot, track

# --- Gem Ball ------------------------------------------------------------------------------------


class GemBallSim:
    """Outcome depends on the hold duration: 180 ms is the sweet spot (±1 step still 'top')."""

    def __init__(self, deterministic: bool, seed: int = 1) -> None:
        self.deterministic = deterministic
        self.rng = random.Random(seed)
        self.gems = 500
        self.last_hold = 0
        self.holds: list[int] = []

    def press_hold(self, cx: int, cy: int, hold_ms: int) -> bool:
        self.last_hold = hold_ms
        self.holds.append(hold_ms)
        if self.deterministic:
            if abs(hold_ms - 180) <= 10:
                self.gems += 75 if hold_ms == 180 else 3
            elif self.rng.random() < 0.1:
                self.gems += 1
        else:
            r = self.rng.random()
            self.gems += 75 if r < 0.05 else (3 if r < 0.3 else (1 if r < 0.6 else 0))
        return True

    def grab(self) -> np.ndarray:
        img = np.zeros((8, 8, 3), dtype=np.uint8)
        img[0, 0, 0] = self.gems % 256
        img[0, 0, 1] = self.gems // 256
        return img

    @staticmethod
    def read_gems(img: np.ndarray) -> int:
        return int(img[0, 0, 0]) + 256 * int(img[0, 0, 1])


def _gem_profile() -> Profile:
    prof = load_profile("gem_ball", Paths())
    prof.set_value("launch_point", [960, 900])
    return prof


def _tuner(sim: GemBallSim, tmp_path: Path, bus: EventBus) -> GemBallTuner:
    cfg = GemBallConfig(
        hold_min_ms=100,
        hold_max_ms=300,
        hold_step_ms=20,
        repeats=8,
        min_repeats=2,
        min_balls=6,
        settle_s=0.0,
    )
    outcome = gem_delta_outcome(sim.read_gems, top_delta=3, jackpot_delta=75)
    return GemBallTuner(
        _gem_profile(),
        cfg,
        sim,
        sim.grab,
        outcome,
        bus=bus,
        report_dir=tmp_path,
        sleep=lambda _s: None,
    )


def test_tuner_finds_the_deterministic_sweet_spot(tmp_path: Path) -> None:
    sim = GemBallSim(deterministic=True)
    bus = EventBus()
    verdicts: list[dict[str, object]] = []
    bus.subscribe("gem_ball.verdict", lambda e: verdicts.append(e.payload))
    rep = _tuner(sim, tmp_path, bus).sweep()
    assert rep.deterministic and rep.best_duration_ms == 180 and rep.best_rate >= 0.7
    assert verdicts and verdicts[0]["verdict"] == "deterministic"
    assert list(tmp_path.glob("gemball_tuning_*.json"))
    assert rep.balls == len(rep.durations_ms) * 6  # stops once min_balls per duration confirm it


def test_tuner_reports_non_deterministic(tmp_path: Path) -> None:
    sim = GemBallSim(deterministic=False, seed=5)
    rep = _tuner(sim, tmp_path, EventBus()).sweep()
    assert not rep.deterministic and rep.best_rate < 0.7
    assert rep.balls == len(rep.durations_ms) * 8  # full sweep when nothing stands out


def test_evaluate_pure() -> None:
    from collections import Counter

    outcomes = {
        100: Counter({"miss": 5}),
        180: Counter({"top": 7, "low": 3}),
        200: Counter({"jackpot": 2, "miss": 8}),
    }
    best, rate, det = evaluate(outcomes, 0.7)
    assert best == 180 and rate == 0.7 and det


# --- Swoopy Eagle --------------------------------------------------------------------------------

W, H = 400, 300
BIRD = (0, 220, 220)  # BGR yellow
PIPE = (60, 180, 60)  # BGR green


class FlappyWorld:
    def __init__(self, seed: int = 1) -> None:
        self.rng = random.Random(seed)
        self.reset()

    def reset(self) -> None:
        self.y = H / 2
        self.vy = 0.0
        self.x = 80.0
        self.pipes = [[260.0, self.rng.uniform(80, 220)]]
        self.t = 0.0
        self.crashed = False
        self.passed = 0

    def flap(self) -> None:
        if not self.crashed:
            self.vy = -230.0

    def tick(self, dt: float) -> None:
        if self.crashed:
            return
        self.vy += 650.0 * dt
        self.y += self.vy * dt
        for p in self.pipes:
            p[0] -= 160.0 * dt
        if self.pipes[-1][0] < W - 200:
            self.pipes.append([self.pipes[-1][0] + 200.0, self.rng.uniform(80, 220)])
        for p in self.pipes:
            if p[0] + 30 < self.x - 10 and p[0] + 30 >= self.x - 10 - 160.0 * dt:
                self.passed += 1
            if p[0] - 10 <= self.x <= p[0] + 40 and abs(self.y - p[1]) > 55:
                self.crashed = True
        if self.y < 0 or self.y > H:
            self.crashed = True
        self.pipes = [p for p in self.pipes if p[0] > -60]
        self.t += dt

    def image(self) -> np.ndarray:
        img = np.full((H, W, 3), (200, 160, 110), dtype=np.uint8)
        for px, gy in self.pipes:
            x0 = int(px)
            cv2.rectangle(img, (x0, 0), (x0 + 30, int(gy - 60)), PIPE, -1)
            cv2.rectangle(img, (x0, int(gy + 60)), (x0 + 30, H), PIPE, -1)
        cv2.circle(img, (int(self.x), int(self.y)), 9, BIRD, -1)
        if self.crashed:
            cv2.putText(img, "CRASH", (120, 150), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 255), 4)
        return img


def _swoopy_profile() -> Profile:
    prof = load_profile("swoopy_eagle", Paths())
    prof.set_value("play_roi", [0, 0, W, H])
    prof.set_value("bird_hsv", [[25, 150, 150], [35, 255, 255]])
    prof.set_value("pipe_hsv", [[50, 100, 100], [70, 255, 255]])
    return prof


def test_tracker_finds_bird_and_gap() -> None:
    world = FlappyWorld(seed=2)
    tr = track(
        world.image(),
        ((25, 150, 150), (35, 255, 255)),
        ((50, 100, 100), (70, 255, 255)),
        SwoopyConfig(),
    )
    assert tr.bird_y is not None and abs(tr.bird_y - world.y) < 3
    assert tr.gap_center_y is not None and abs(tr.gap_center_y - world.pipes[0][1]) < 6
    assert tr.gap_x is not None and abs(tr.gap_x - world.pipes[0][0]) < 4


def test_pilot_passes_gaps_in_the_simulation() -> None:
    world = FlappyWorld(seed=3)
    cfg = SwoopyConfig(fps=60.0)
    clock = {"t": 0.0}

    class Tap:
        def key_tap(self, key: str, hold_ms: int) -> None:
            world.flap()

    def sleep(s: float) -> None:
        clock["t"] += s
        world.tick(s)

    pilot = SwoopyPilot(
        _swoopy_profile(), cfg, Tap(), world.image, clock=lambda: clock["t"], sleep=sleep
    )
    runs = {"n": 0}

    crash_t: dict[str, float | None] = {"t": None}

    def crashed(_img: np.ndarray) -> bool:
        if not world.crashed:
            return False
        if crash_t["t"] is None:
            crash_t["t"] = clock["t"]
            runs["n"] += 1
        elif clock["t"] - crash_t["t"] >= cfg.restart_s:  # the game lets us restart now
            crash_t["t"] = None
            world.reset()
            return False  # live again: control resumes immediately
        return True

    stats = pilot.run(lambda: clock["t"] > 120.0 or runs["n"] >= 6, crashed)
    assert stats.flaps > 20
    best = max(stats.gaps_per_run, default=world.passed)
    assert max(best, world.passed) >= 5, stats.as_dict()


def test_pd_controller_rate_limits() -> None:
    cfg = SwoopyConfig(min_flap_interval_s=0.2)
    pd = PDController(cfg)
    assert pd.should_flap(200.0, 100.0, 0.0)  # far below the target: flap
    assert not pd.should_flap(200.0, 100.0, 0.05)  # too soon
    assert pd.should_flap(200.0, 100.0, 0.3)
    assert not pd.should_flap(50.0, 100.0, 1.0)  # above the target: don't
