"""Jamaa Derby farmer against the synthetic track: ≥90% first places, boosts, results OCR."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from jamcrammer.core.config import DerbyConfig
from jamcrammer.core.events import EventBus
from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import Profile, load_profile
from jamcrammer.core.store import Store
from jamcrammer.modes.arcade.derby import DerbyFarmer, detect_obstacles
from tests.synthetic import derby as synth


def _profile() -> Profile:
    prof = load_profile("jamaa_derby", Paths())
    prof.set_value("lane_roi", list(synth.LANE_ROI))
    prof.set_value("horse_x", synth.HORSE_X)
    prof.set_value("boost_button_point", list(synth.BOOST_POINT))
    prof.set_value("results_place_roi", list(synth.PLACE_ROI))
    prof.set_value("results_gems_roi", list(synth.GEMS_ROI))
    prof.set_value("race_again_point", list(synth.RACE_AGAIN))
    prof.set_value("jump_lead_short_px", 150)
    prof.set_value("jump_lead_long_px", 110)
    return prof


class Sim:
    def __init__(self, world: synth.DerbyWorld) -> None:
        self.world = world
        self.t = 0.0

    def grab(self) -> np.ndarray:
        return self.world.image()

    def key_tap(self, key: str, hold_ms: int) -> None:
        self.world.key_tap(key, hold_ms)

    def click(self, x: int, y: int) -> bool:
        return self.world.click(x, y)

    def sleep(self, s: float) -> None:
        self.t += s
        self.world.tick(s)

    def clock(self) -> float:
        return self.t

    def ocr(self, img: np.ndarray) -> str:
        w = self.world
        if w.racing:
            return ""
        place = w.places[-1]
        if img.shape[0] == synth.GEMS_ROI[3]:
            return f"Gems {dict(enumerate([100, 75, 50, 40, 30, 25], start=1))[place]}"
        return {1: "You finished 1st!", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", 6: "6th"}[place]


def test_obstacle_detection_against_rolling_background() -> None:
    world = synth.DerbyWorld(seed=7)
    from jamcrammer.vision.ocr import crop

    cfg = DerbyConfig()
    lane0 = crop(world.image(), synth.LANE_ROI)
    import cv2

    bg = cv2.cvtColor(lane0, cv2.COLOR_BGR2GRAY).astype(np.float32)
    bg[:, :] = np.median(bg)  # a clean lane background
    obs = detect_obstacles(lane0, bg, synth.HORSE_X, cfg)
    assert obs, "obstacles on the lane should be detected"
    assert all(o.left > synth.HORSE_X for o in obs)
    widths = sorted(o.width for o in obs)
    assert widths[0] >= cfg.obstacle_min_px


def test_farmer_wins_at_least_90_percent(tmp_path: Path) -> None:
    world = synth.DerbyWorld(seed=3)
    sim = Sim(world)
    store = Store(tmp_path / "t.db")
    bus = EventBus()
    events: list[str] = []
    bus.subscribe("*", lambda e: events.append(e.type))
    farmer = DerbyFarmer(
        _profile(),
        DerbyConfig(fps=20.0, race_timeout_s=40.0),
        sim,
        sim.grab,
        store=store,
        bus=bus,
        ocr=sim.ocr,
        clock=sim.clock,
        sleep=sim.sleep,
    )
    races = 10
    farmer.run(lambda: farmer.stats.races >= races)
    assert farmer.stats.races == races
    assert farmer.stats.first_place_rate >= 0.9, (world.places, farmer.stats.as_dict())
    assert farmer.stats.jumps > 0
    assert farmer.stats.pro  # 10 first places unlock Pro
    assert "derby.pro_unlocked" in events and events.count("derby.race") == races
    rows = store.query("SELECT gems_reported, notes FROM arcade_rounds WHERE game='jamaa_derby'")
    assert len(rows) == races and all(r["gems_reported"] in (100, 75, 50, 40, 30, 25) for r in rows)
    assert any(r.boosts > 0 for r in [farmer.stats.last] if r is not None)
    store.close()


def test_uncalibrated_profile_refused() -> None:
    import pytest

    with pytest.raises(ValueError, match="missing"):
        DerbyFarmer(
            load_profile("jamaa_derby", Paths()),
            DerbyConfig(),
            Sim(synth.DerbyWorld()),
            lambda: synth.DerbyWorld().image(),
        )
