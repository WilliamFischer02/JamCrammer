from __future__ import annotations

import numpy as np

from jamcrammer.core.config import InputConfig
from jamcrammer.platform.input import InputController
from jamcrammer.platform.window import Rect, WindowInfo
from jamcrammer.vision.stage import StageMap, stage_map_for


def test_fullscreen_is_identity() -> None:
    m = StageMap(1920, 1080)
    assert m.is_identity and m.scale == 1.0 and m.origin == (0.0, 0.0)
    assert m.to_client(690, 50) == (690, 50)
    img = np.zeros((1080, 1920, 3), dtype=np.uint8)
    assert m.normalize(img) is img


def test_maximized_window_measured_values() -> None:
    m = StageMap(1904, 1013)
    assert round(m.scale, 3) == 0.938
    assert m.content_size == (1801, 1013)
    assert round(m.origin[0], 1) == 51.5 and m.origin[1] == 0.0
    # fullscreen HUD at (100, 5) was found at (145, 4) in the maximized client
    assert m.to_client(100, 5) == (145, 5)
    # the bottom-right HUD at (1450, 940) was found at (1413, 881)
    cx, cy = m.to_client(1450, 940)
    assert abs(cx - 1413) <= 3 and abs(cy - 881) <= 3


def test_roundtrip_and_boxes() -> None:
    m = StageMap(1280, 720)
    for sx, sy in ((0, 0), (1919, 1079), (690, 50), (1213, 312)):
        cx, cy = m.to_client(sx, sy)
        bx, by = m.to_stage(cx, cy)
        assert abs(bx - sx) <= 2 and abs(by - sy) <= 2
    assert m.box_to_client((0, 0, 1920, 1080)) == (0, 0, 1280, 720)


def test_normalize_resizes_content_to_stage() -> None:
    client = np.zeros((1013, 1904, 3), dtype=np.uint8)
    client[:, 52:1853] = 200  # the content area is bright, the letterbox dark
    m = stage_map_for(client)
    out = m.normalize(client)
    assert out.shape == (1080, 1920, 3)
    assert out[540, 960, 0] == 200 and out[540, 5, 0] >= 190 and out[540, 1914, 0] >= 190


def test_input_controller_maps_stage_to_client() -> None:
    win = WindowInfo(
        1,
        "Animal Jam Classic",
        "Chrome_WidgetWin_1",
        1,
        "AJ Classic.exe",
        Rect(0, 0, 1920, 1052),
        Rect(8, 31, 1912, 1044),
        96,
    )
    ctl = InputController(lambda: win, InputConfig(), dry_run=True, stage=True)
    # stage (0,0) → client (52,0) → screen (60,31)
    assert ctl.to_screen(0, 0) == (60, 31)
    sx, sy = ctl.to_screen(1919, 1079)
    assert abs(sx - (8 + 52 + 1800)) <= 1 and sy == 31 + 1012
