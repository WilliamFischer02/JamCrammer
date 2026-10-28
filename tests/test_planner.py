"""Den planner: time parsing and the screen-reading primitives on William's 2026-10-27 shots."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from jamcrammer.core.config import HuntConfig, PlannerConfig
from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import load_profile
from jamcrammer.modes.party_watcher import BannerLibrary, PanelAnalyzer, PartyWatcher
from jamcrammer.modes.planner import Navigator, format_eta, parse_eta
from jamcrammer.vision.ocr import ocr_image

FIX = Path(__file__).parent / "fixtures" / "navigation"


def _fx(name: str) -> np.ndarray:
    img = cv2.imread(str(FIX / name))
    assert img is not None, name
    return np.asarray(img, dtype=np.uint8)


def _ocr_ok() -> bool:
    return not ocr_image(np.full((40, 120, 3), 255, dtype=np.uint8)).error


def test_parse_and_format_eta() -> None:
    assert parse_eta("43 minutes") == 43 * 60
    assert parse_eta("1 hr 13 mins") == 3600 + 13 * 60
    assert parse_eta("2 hrs 14 mins") == 2 * 3600 + 14 * 60
    assert parse_eta("GO NOW!") == 0
    assert parse_eta("Dinner Party") is None
    assert format_eta(4380) == "1h 13m 00s" and format_eta(None) == "unknown"


class FakeInputs:
    def __init__(self) -> None:
        self.clicks: list[tuple[int, int]] = []
        self.drags: list[tuple[int, int, int, int]] = []

    def click(self, cx: int, cy: int) -> bool:
        self.clicks.append((cx, cy))
        return True

    def drag(self, x0: int, y0: int, x1: int, y1: int) -> bool:
        self.drags.append((x0, y0, x1, y1))
        return True


def _navigator(frames: list[np.ndarray], inputs: FakeInputs) -> Navigator:
    paths = Paths()
    analyzer = PanelAnalyzer(
        load_profile("parties", paths), BannerLibrary(paths.assets / "banners"), paths=paths
    )
    state = {"i": 0}

    def grab() -> np.ndarray:
        img = frames[min(state["i"], len(frames) - 1)]
        state["i"] += 1
        return img

    watcher = PartyWatcher(
        analyzer, HuntConfig(), inputs, grab, clock=lambda: 0.0, sleep=lambda s: None
    )
    clock = {"t": 0.0}

    def tick(s: float) -> None:
        clock["t"] += s

    return Navigator(
        load_profile("navigation", paths),
        PlannerConfig(),
        inputs,
        grab,
        watcher,
        paths,
        clock=lambda: clock["t"],
        sleep=tick,
    )


def test_room_name_and_den_detection() -> None:
    if not _ocr_ok():
        pytest.skip("winocr unavailable")
    nav = _navigator([_fx("den.jpg")], FakeInputs())
    assert nav.in_den(_fx("den.jpg"))
    assert not nav.in_den(_fx("township.jpg"))
    assert "Township" in nav.room_name(_fx("township.jpg"))


def test_go_to_den_clicks_the_hut_until_home() -> None:
    if not _ocr_ok():
        pytest.skip("winocr unavailable")
    inputs = FakeInputs()
    nav = _navigator([_fx("township.jpg"), _fx("township.jpg"), _fx("den.jpg")], inputs)
    res = nav.go_to_den()
    assert res.ok and inputs.clicks == [tuple(nav.profile.point("den_button_point"))]


def test_tray_gives_best_guess_eta() -> None:
    if not _ocr_ok():
        pytest.skip("winocr unavailable")
    nav = _navigator([_fx("tray_open.jpg")], FakeInputs())
    eta, note = nav.eta_from_tray(_fx("tray_open.jpg"))
    assert eta == 43 * 60, note


def test_derby_tile_and_pro_button() -> None:
    nav = _navigator([_fx("games_scrolled.jpg")], FakeInputs())
    tile = nav.find_derby_tile(_fx("games_scrolled.jpg"))
    assert tile is not None and abs(tile[0] - 555) < 30 and abs(tile[1] - 825) < 30, tile
    assert nav.find_derby_tile(_fx("den.jpg")) is None
    if not _ocr_ok():
        pytest.skip("winocr unavailable")
    pro = nav.find_pro_button(_fx("derby_dialog.jpg"))
    assert pro is not None and abs(pro[0] - 562) < 25 and abs(pro[1] - 707) < 25, pro
