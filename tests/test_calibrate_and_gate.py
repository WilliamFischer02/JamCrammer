from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np

from jamcrammer.core.gate import gate_status, write_replay_record
from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import load_profile
from jamcrammer.vision.calibrate import (
    apply_drag,
    crop_template,
    hit_test,
    hsv_range,
    normalize_rect,
)

ROI = (100, 100, 200, 80)


def test_hit_test_handles_and_body() -> None:
    assert hit_test(ROI, 100, 100) == "nw"
    assert hit_test(ROI, 300, 100) == "ne"
    assert hit_test(ROI, 100, 180) == "sw"
    assert hit_test(ROI, 300, 180) == "se"
    assert hit_test(ROI, 200, 100) == "n"
    assert hit_test(ROI, 200, 180) == "s"
    assert hit_test(ROI, 100, 140) == "w"
    assert hit_test(ROI, 300, 140) == "e"
    assert hit_test(ROI, 200, 140) == "move"
    assert hit_test(ROI, 50, 50) is None


def test_apply_drag_move_and_resize_with_clamping() -> None:
    assert apply_drag(ROI, "move", 10, -5) == (110, 95, 200, 80)
    assert apply_drag(ROI, "se", 20, 10) == (100, 100, 220, 90)
    assert apply_drag(ROI, "nw", 20, 10) == (120, 110, 180, 70)
    assert apply_drag(ROI, "e", -500, 0)[2] >= 4  # never smaller than min size
    assert apply_drag(ROI, "move", -1000, -1000) == (0, 0, 200, 80)
    assert apply_drag(ROI, "move", 5000, 5000) == (1720, 1000, 200, 80)
    assert normalize_rect(50, 60, 10, 20) == (10, 20, 40, 40)


def test_hsv_range_contains_sampled_color() -> None:
    img = np.zeros((40, 40, 3), dtype=np.uint8)
    img[:, :] = (60, 205, 70)  # the synthetic popup button green (BGR)
    lo, hi = hsv_range(img, 20, 20)
    import cv2

    h, s, v = cv2.cvtColor(np.array([[[60, 205, 70]]], dtype=np.uint8), cv2.COLOR_BGR2HSV)[0, 0]
    assert lo[0] <= h <= hi[0] and lo[1] <= s <= hi[1] and lo[2] <= v <= hi[2]
    assert hi[0] - lo[0] <= 20


def test_crop_template(tmp_path: Path) -> None:
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    img[10:30, 20:60] = 255
    out = crop_template(img, (20, 10, 40, 20), tmp_path / "t" / "x.png")
    import cv2

    saved = cv2.imread(str(out))
    assert saved is not None and saved.shape[:2] == (20, 40) and int(saved.mean()) == 255


def test_gate_locks_until_fields_and_replay(tmp_path: Path) -> None:
    root = tmp_path
    (root / "profiles").mkdir()
    for name in ("bestguess", "parties", "shared"):
        shutil.copy2(
            Paths().root / "profiles" / f"{name}.example.yaml",
            root / "profiles" / f"{name}.example.yaml",
        )
    paths = Paths(root=root)
    st = gate_status("bestguess", paths)
    assert (
        not st.unlocked and "bestguess.fact_roi" in st.missing and st.summary().startswith("LOCKED")
    )
    prof = load_profile("bestguess", paths)
    for key, val in (
        ("fact_roi", [360, 40, 1200, 120]),
        ("true_point", [480, 700]),
        ("false_point", [1440, 700]),
        ("reveal_roi", [760, 300, 400, 160]),
    ):
        prof.set_value(key, val, status="calibrated")
    prof.save(root / "profiles" / "bestguess.yaml")
    st = gate_status("bestguess", paths)
    assert not st.missing and not st.unlocked and "no replay" in st.summary()
    session = paths.sessions / "20260101_000000_bestguess_x"
    write_replay_record(session, 0.9, 100, "nearest")
    assert not gate_status("bestguess", paths).unlocked
    write_replay_record(paths.sessions / "20260102_000000_bestguess_y", 0.97, 120, "nearest")
    st = gate_status("bestguess", paths)
    assert st.unlocked and st.best_replay is not None and st.best_replay.session.endswith("_y")
    assert (
        json.loads(
            (
                paths.sessions / "20260102_000000_bestguess_y" / "analysis" / "replay.json"
            ).read_text()
        )["frames"]
        == 120
    )
    # hunt needs no replay, only its parties fields (calibrated in the example profile)
    assert gate_status("hunt", paths).unlocked
