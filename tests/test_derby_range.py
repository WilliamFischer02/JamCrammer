"""The Jamaa Derby horse box as a *range* over every lane: the detector finds the player's
horse inside it (by the glow under it, else by the nose) instead of assuming one spot."""

from __future__ import annotations

import cv2
import numpy as np

from jamcrammer.core.config import DerbyConfig
from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import Profile
from jamcrammer.modes.arcade.derby import DerbyDetector, Screen, find_player_lane
from tests.synthetic import derby as synth

CFG = DerbyConfig()
LANE_H = 140
LANE_TOPS = tuple(80 + i * 150 for i in range(6))  # six lanes down the start line
RANGE_BOX = (700, 60, 260, 920)  # the horse box stretched over all six lanes
HORSE_X = (720, 940)
HORSE_H = 100
GLOW = synth._bgr(26, 200, 250)  # the yellow horseshoe glow under the player's horse
HORSE = synth._bgr(140, 120, 180)  # a purple horse: neither grass nor any band colour
GLOW_H = 14
LIFT = 60  # how far the horse rises while airborne


def _profile(range_flag: bool, *, marker: bool = True) -> Profile:
    prof = Profile.from_yaml(Paths().profiles / "jamaa_derby.example.yaml")
    values = {
        "strip_roi": list(synth.STRIP_ROI),
        "horse_front_x": HORSE_X[1],
        "jump_trigger_x": synth.JUMP_TRIGGER_X,
        "horse_box_roi": list(RANGE_BOX) if range_flag else [700, 60, 260, 160],
        "boost_button_point": list(synth.BOOST_POINT),
        "boost_box_roi": list(synth.BOOST_BOX),
        "results_box_roi": list(synth.RESULTS_BOX),
        "results_rows_roi": list(synth.RESULTS_ROWS),
        "startline_roi": list(synth.STARTLINE_ROI),
        "progress_bar_roi": list(synth.PROGRESS_ROI),
        "horse_lane_h": LANE_H,
    }
    for key, value in values.items():
        prof.set_value(key, value, status="calibrated")
    prof.set_range("horse_box_roi", range_flag)
    if marker:
        prof.set_value("player_marker_hsv", [[18, 110, 150], [35, 255, 255]], status="calibrated")
    else:
        prof.field("player_marker_hsv").status = "uncalibrated"
    return prof


def _frame(
    player_lane: int,
    *,
    airborne: bool = False,
    waiting: bool = False,
    npc_dx: int = 0,
    glow: bool = True,
) -> np.ndarray:
    """Grass, six horses at the line (NPCs shifted by ``npc_dx``), the player's one with a glow
    under it, plus the HUD bits the screen classifier needs."""
    img = np.full((synth.H, synth.W, 3), synth.GRASS, dtype=np.uint8)
    cv2.rectangle(img, (0, 0), (100, synth.H), synth.FRAME, -1)
    cv2.rectangle(img, (1820, 0), (synth.W, synth.H), synth.FRAME, -1)
    for i, top in enumerate(LANE_TOPS):
        ours = i == player_lane
        dx = 0 if ours else npc_dx
        lift = LIFT if (ours and airborne) else 0
        y0, y1 = top + 20 - lift, top + 20 + HORSE_H - lift
        cv2.rectangle(img, (HORSE_X[0] + dx, y0), (HORSE_X[1] + dx, y1), HORSE, -1)
        if ours and glow:
            cv2.rectangle(img, (HORSE_X[0], y1), (HORSE_X[1], y1 + GLOW_H), GLOW, -1)
    if waiting:
        cv2.rectangle(img, (880, 100), (930, 920), synth.WHITE, -1)
    px, py, pw, ph = synth.PROGRESS_ROI
    cv2.rectangle(img, (px, py), (px + pw, py + ph), synth.PROGRESS, -1)
    return img


def _lane_of(box: tuple[int, int, int, int]) -> int:
    """Which lane a found box sits in (its top within the lane's extent)."""
    return min(range(6), key=lambda i: abs(box[1] - LANE_TOPS[i]))


def test_find_player_lane_by_glow_then_by_nose() -> None:
    rx, ry, rw, rh = RANGE_BOX
    box = _frame(4)[ry : ry + rh, rx : rx + rw]
    top = find_player_lane(box, CFG, LANE_H, ((18, 110, 150), (35, 255, 255)), HORSE_X[1] - rx)
    assert top is not None and abs((ry + top) - LANE_TOPS[4]) <= LANE_H // 2
    # no glow colour known: the NPCs have drifted ahead, ours still ends at the nose
    box = _frame(1, npc_dx=180, glow=False)[ry : ry + rh, rx : rx + rw]
    top = find_player_lane(box, CFG, LANE_H, None, HORSE_X[1] - rx)
    assert top is not None and abs((ry + top) - LANE_TOPS[1]) <= LANE_H // 2
    # an empty field: nothing horse-like
    empty = np.full((rh, rw, 3), synth.GRASS, dtype=np.uint8)
    assert find_player_lane(empty, CFG, LANE_H, None, 100) is None


def test_range_box_locks_the_lane_at_the_line_and_tracks_the_jump() -> None:
    det = DerbyDetector(_profile(True), CFG)
    waiting = det.observe(_frame(3, waiting=True))
    assert waiting.screen == Screen.WAITING
    assert waiting.horse_box is not None and _lane_of(waiting.horse_box) == 3
    assert waiting.horse_box[3] == LANE_H and waiting.horse_box[0] == RANGE_BOX[0]
    ground = det.observe(_frame(3))
    assert ground.screen == Screen.RACING and ground.horse_box == waiting.horse_box
    assert ground.horse_top is not None
    air = det.observe(_frame(3, airborne=True))
    assert air.horse_box == waiting.horse_box, "the lane stays locked for the race"
    assert air.horse_top is not None
    assert ground.horse_top - air.horse_top >= CFG.jump_rise_px


def test_next_race_may_deal_another_lane() -> None:
    det = DerbyDetector(_profile(True), CFG)
    first = det.observe(_frame(0, waiting=True))
    assert first.horse_box is not None and _lane_of(first.horse_box) == 0
    results = synth.DerbyWorld(seed=2)
    results.phase = "RESULTS"
    results.places.append(1)
    assert det.observe(results.image()).screen == Screen.RESULTS
    assert det.lane_box is None, "the lock is dropped on the leaderboard"
    second = det.observe(_frame(5, waiting=True))
    assert second.horse_box is not None and _lane_of(second.horse_box) == 5


def test_plain_box_keeps_the_single_spot_behaviour() -> None:
    prof = _profile(False)
    det = DerbyDetector(prof, CFG)
    obs = det.observe(_frame(0))
    assert obs.screen == Screen.RACING
    assert obs.horse_box == prof.roi("horse_box_roi"), "no search without the range flag"
    assert det.lane_box is None


def test_range_flag_round_trips_through_yaml(tmp_path) -> None:  # type: ignore[no-untyped-def]
    prof = _profile(True)
    prof.save(tmp_path / "jamaa_derby.yaml")
    back = Profile.from_yaml(tmp_path / "jamaa_derby.yaml")
    assert back.is_range("horse_box_roi") and back.roi("horse_box_roi") == RANGE_BOX
    assert not back.is_range("strip_roi")
