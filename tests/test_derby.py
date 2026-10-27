"""Jamaa Derby farmer against the synthetic track (real layout) and real-frame fixtures."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from jamcrammer.core.config import DerbyConfig
from jamcrammer.core.events import EventBus
from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import Profile, load_profile
from jamcrammer.core.store import Store
from jamcrammer.modes.arcade.derby import (
    DerbyDetector,
    DerbyFarmer,
    Screen,
    detect_bands,
    fill,
    highlighted_row,
    horse_top,
)
from jamcrammer.vision.ocr import crop
from tests.synthetic import derby as synth

FIX = Path(__file__).parent / "fixtures" / "derby"
CFG = DerbyConfig()


def _profile() -> Profile:
    prof = load_profile("jamaa_derby", Paths())
    values = {
        "strip_roi": list(synth.STRIP_ROI),
        "horse_front_x": synth.HORSE_FRONT_X,
        "jump_trigger_x": synth.JUMP_TRIGGER_X,
        "horse_box_roi": list(synth.HORSE_BOX),
        "boost_button_point": list(synth.BOOST_POINT),
        "boost_box_roi": list(synth.BOOST_BOX),
        "results_box_roi": list(synth.RESULTS_BOX),
        "results_rows_roi": list(synth.RESULTS_ROWS),
        "results_first_row_cy": synth.FIRST_ROW_CY,
        "results_row_pitch": synth.ROW_PITCH,
        "startline_roi": list(synth.STARTLINE_ROI),
        "progress_bar_roi": list(synth.PROGRESS_ROI),
        "scroll_px_s": 830,
        "jump_lead_s": 0.08,
        "jump_lead_px": 0,
        "jump_cooldown_s": 0.5,
        "boost_clear_px": 500,
    }
    for key, value in values.items():
        prof.set_value(key, value, status="calibrated")
    return prof


class Sim:
    def __init__(self, world: synth.DerbyWorld) -> None:
        self.world = world

    def grab(self) -> np.ndarray:
        return self.world.image()

    def key_tap(self, key: str, hold_ms: int) -> None:
        self.world.key_tap(key, hold_ms)

    def click(self, x: int, y: int) -> bool:
        return self.world.click(x, y)

    def sleep(self, s: float) -> None:
        self.world.tick(s)

    def clock(self) -> float:
        return self.world.t

    def ocr(self, img: np.ndarray) -> str:
        w = self.world
        if w.phase != "RESULTS" or not w.places:
            return ""
        place = w.places[-1]
        gems = [100, 75, 50, 40, 30, 25][place - 1]
        if img.shape[1] < 300:
            return f"{gems} Gems"
        return f"{place}{['st', 'nd', 'rd', 'th', 'th', 'th'][place - 1]} rider"


def test_synthetic_screens_and_bands() -> None:
    world = synth.DerbyWorld(seed=5)
    det = DerbyDetector(_profile(), CFG)
    assert det.screen(world.image()) == Screen.WAITING
    world.tick(3.2)
    for _ in range(200):  # scroll until a whole band sits inside the strip
        if any(b.x >= 900 and b.x + b.width <= 1700 for b in world.bands):
            break
        world.tick(0.05)
    obs = det.observe(world.image())
    assert obs.screen == Screen.RACING and obs.carrots_left
    kinds = {b.kind for b in obs.bands}
    assert kinds and kinds <= {"hay", "mud", "water", "hedge"}
    inside = [b for b in obs.bands if b.left > 110 and b.right < 1810]  # not clipped by the strip
    assert inside
    for b in inside:  # edges within a few px of the world's bands
        match = min(world.bands, key=lambda wb: abs(wb.x - b.left))
        assert abs(match.x - b.left) <= 6 and abs(match.width - b.width) <= 12, (b, match)
    world.dist = world.race_len_px + 1
    world.tick(0.02)
    assert det.screen(world.image()) == Screen.RESULTS


def test_farmer_wins_at_least_90_percent(tmp_path: Path) -> None:
    world = synth.DerbyWorld(seed=3)
    sim = Sim(world)
    store = Store(tmp_path / "t.db")
    bus = EventBus()
    events: list[str] = []
    bus.subscribe("*", lambda e: events.append(e.type))
    farmer = DerbyFarmer(
        _profile(),
        DerbyConfig(fps=20.0),
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
    assert farmer.stats.jumps > 0 and farmer.stats.pro
    assert "derby.pro_unlocked" in events and events.count("derby.race") == races
    rows = store.query("SELECT gems_reported, notes FROM arcade_rounds WHERE game='jamaa_derby'")
    assert len(rows) == races and all(r["gems_reported"] in (100, 75, 50, 40, 30, 25) for r in rows)
    assert farmer.stats.last is not None and farmer.stats.last.boosts == 3
    assert not farmer.paused_reason


def test_uncalibrated_profile_refused() -> None:
    prof = _profile()
    prof.set_value("strip_roi", None, status="uncalibrated")
    with pytest.raises(ValueError, match="missing"):
        DerbyFarmer(
            prof, DerbyConfig(), Sim(synth.DerbyWorld()), lambda: synth.DerbyWorld().image()
        )


# --- real frames (2026-10-26 recording, stage-space crops) ------------------------------------


def _fx(name: str) -> np.ndarray:
    img = cv2.imread(str(FIX / name))
    assert img is not None, name
    return np.asarray(img, dtype=np.uint8)


def _kinds(name: str) -> list[tuple[str, int, int]]:
    return [(b.kind, b.left, b.right) for b in detect_bands(_fx(name), 100, CFG)]


def test_real_bands_by_colour() -> None:
    hay = _kinds("strip_36s.png")
    assert [k for k, *_ in hay] == ["hay"] and 1180 <= hay[0][1] <= 1250, hay
    water = _kinds("strip_40s.png")
    assert [k for k, *_ in water] == ["water"] and 600 <= water[0][1] <= 660, water
    hedge = _kinds("strip_44s.png")
    assert "hedge" in [k for k, *_ in hedge] and all(
        k != "water" or b - a >= 40 for k, a, b in hedge
    ), hedge
    tall = _kinds("strip_52s.png")
    assert [k for k, *_ in tall if k == "hedge"] == ["hedge"], tall
    hedge_band = next(b for b in detect_bands(_fx("strip_52s.png"), 100, CFG) if b.kind == "hedge")
    assert hedge_band.width >= 180, hedge_band  # stripes closed by hysteresis
    mud = _kinds("strip_56s.png")
    assert [k for k, *_ in mud] == ["mud", "hay"], mud


def test_checkered_finish_is_not_a_hedge() -> None:
    assert _kinds("strip_76s.png") == []


def test_real_screen_anchors() -> None:
    assert fill(_fx("progress_8s.png"), CFG.progress_hsv) >= CFG.progress_min_fill
    assert fill(_fx("progress_results_96s.png"), CFG.progress_hsv) >= CFG.progress_min_fill
    assert fill(_fx("results_box_96s.png"), CFG.beige_hsv) >= CFG.results_min_fill
    line = cv2.cvtColor(_fx("startline_8s.png"), cv2.COLOR_BGR2HSV)
    lo, hi = CFG.white_hsv
    cols = (cv2.inRange(line, np.array(lo, np.uint8), np.array(hi, np.uint8)) > 0).mean(axis=0)
    assert (cols >= CFG.startline_col_fill).mean() >= CFG.startline_min_cols
    none = cv2.cvtColor(_fx("startline_none_36s.png"), cv2.COLOR_BGR2HSV)
    cols = (cv2.inRange(none, np.array(lo, np.uint8), np.array(hi, np.uint8)) > 0).mean(axis=0)
    assert (cols >= CFG.startline_col_fill).mean() < CFG.startline_min_cols
    assert fill(_fx("boost_box_36s.png"), CFG.carrot_hsv) >= CFG.carrot_min_fill
    assert fill(_fx("boost_box_8s.png"), CFG.carrot_hsv) < CFG.carrot_min_fill  # grey before GO


def test_real_results_row_and_horse() -> None:
    box = _fx("results_box_96s.png")
    rows = crop(box, (100, 40, 560, 540))  # results_rows_roi relative to results_box_roi
    cy = highlighted_row(rows, CFG)
    assert cy is not None and 40 <= cy <= 64, cy  # first row: 1st place
    ground = horse_top(_fx("horse_box_36s.png"), CFG)
    assert ground is not None and 100 <= ground <= 160, ground
    # at 64 s a hay band fills the box: nothing of the horse can be told apart -> unknown
    assert horse_top(_fx("horse_box_64s.png"), CFG) is None
