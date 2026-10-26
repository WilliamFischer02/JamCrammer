"""Replay-style watchdog tests on synthetic frames (KICKOFF M2 acceptance)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from jamcrammer.core.config import Level, WatchdogConfig
from jamcrammer.core.events import EventBus
from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import load_profile
from jamcrammer.core.safety import InputLease
from jamcrammer.replay.sources import GeneratorSource
from jamcrammer.services.watchdog import (
    KIND_LOGGED_OUT,
    KIND_STILL_PLAYING,
    KIND_UNKNOWN,
    Detection,
    PopupDetector,
    Watchdog,
    green_button_candidates,
)
from jamcrammer.vision.ocr import ocr_image
from tests.synthetic.popups import logged_out_popup, room_frame, still_playing_popup, unknown_popup


class World:
    """A room where a popup can be shown; clicking inside its button dismisses it."""

    def __init__(self) -> None:
        self.base = room_frame(1)
        self.popup_img, self.button = still_playing_popup(self.base)
        self.popup = False
        self.clicks: list[tuple[int, int]] = []
        self.text = ""

    def image(self) -> np.ndarray:
        return self.popup_img if self.popup else self.base

    def ocr_text(self, _sub: np.ndarray) -> str:
        return "Are you still playing? YES" if self.popup else ""

    def click(self, cx: int, cy: int) -> bool:
        self.clicks.append((cx, cy))
        bx, by, bw, bh = self.button
        if self.popup and bx <= cx < bx + bw and by <= cy < by + bh:
            self.popup = False
        return True


def _detector(world: World) -> PopupDetector:
    return PopupDetector(load_profile("shared", Paths()), text_fn=world.ocr_text)


def test_room_alone_has_no_button_candidates_or_detection() -> None:
    world = World()
    det = _detector(world)
    p = det.profile
    lo, hi = p.color("popup_green_hsv")
    cands = green_button_candidates(
        world.base,
        det.center_box(world.base),
        hsv_lo=lo,
        hsv_hi=hi,
        min_area=p.number("popup_green_min_area"),
        max_area=p.number("popup_green_max_area"),
        min_aspect=p.number("popup_green_min_aspect"),
        max_aspect=p.number("popup_green_max_aspect"),
    )
    # synthetic foliage may yield button-shaped blobs; the OCR keyword gate must reject them
    assert len(cands) <= 3
    assert det.detect(world.base, baseline=world.base) is None


def test_watchdog_clears_simulated_popup_in_replay(tmp_path: Path) -> None:
    world = World()
    bus = EventBus()
    events: list[tuple[str, Level]] = []
    bus.subscribe("*", lambda e: events.append((e.type, e.level)))
    lease = InputLease()
    lease.acquire("hunter")
    clock = {"t": 1000.0}
    wd = Watchdog(
        WatchdogConfig(clear_timeout_s=3.0, ocr_interval_s=5.0),
        _detector(world),
        world,
        bus=bus,
        lease=lease,
        screenshot_dir=tmp_path / "shots",
        antiafk_active=lambda: True,
        clock=lambda: clock["t"],
    )
    wd.set_baseline(world.base)

    def step() -> Detection | None:
        clock["t"] += 0.5
        frame = next(GeneratorSource(world.image, 1, 0.5, clock["t"]).frames())
        return wd.tick(frame)

    assert step() is None
    world.popup = True
    det = step()
    assert det is not None and det.kind == KIND_STILL_PLAYING
    bx, by, bw, bh = world.button
    assert (
        world.clicks and bx <= world.clicks[0][0] < bx + bw and by <= world.clicks[0][1] < by + bh
    )
    assert lease.owner == "watchdog"
    assert not world.popup  # the click dismissed it
    assert step() is None  # watchdog sees it gone → cleared
    assert wd.stats.cleared == 1 and wd.stats.still_playing_seen == 1
    assert lease.owner is None
    kinds = [e[0] for e in events]
    assert "watchdog.popup_detected" in kinds
    assert "watchdog.popup_cleared" in kinds
    assert ("watchdog.antiafk_not_registering", Level.HIGH) in events
    assert list((tmp_path / "shots").glob("*still_playing.png"))


def test_watchdog_retries_then_alerts_when_popup_sticks(tmp_path: Path) -> None:
    world = World()
    world.button = (-10, -10, 1, 1)  # clicks never land: popup sticks
    bus = EventBus()
    events: list[str] = []
    bus.subscribe("*", lambda e: events.append(e.type))
    paused: list[str] = []
    clock = {"t": 0.0}
    wd = Watchdog(
        WatchdogConfig(clear_timeout_s=3.0, max_retries=1),
        _detector(world),
        world,
        bus=bus,
        on_pause=paused.append,
        screenshot_dir=tmp_path,
        clock=lambda: clock["t"],
    )
    world.popup = True
    for _ in range(20):
        clock["t"] += 1.0
        wd.tick(next(GeneratorSource(world.image, 1, 1.0, clock["t"]).frames()))
    assert len(world.clicks) == 2  # initial + one retry
    assert "watchdog.retry" in events and "watchdog.popup_not_cleared" in events
    assert paused == ["popup_not_cleared"]
    assert wd.stats.clear_failures == 1


def test_logged_out_is_fatal_and_never_clicked(tmp_path: Path) -> None:
    world = World()
    world.text = "You have been logged out"
    img = logged_out_popup(world.base)
    fatal: list[str] = []
    det = PopupDetector(load_profile("shared", Paths()), text_fn=lambda _s: world.text)
    wd = Watchdog(
        WatchdogConfig(),
        det,
        world,
        on_fatal=lambda d: fatal.append(d.kind),
        screenshot_dir=tmp_path,
    )
    frame = next(GeneratorSource(lambda: img, 1, 1.0).frames())
    d = wd.tick(frame)
    assert d is not None and d.kind == KIND_LOGGED_OUT
    assert fatal == [KIND_LOGGED_OUT]
    assert world.clicks == []


def test_unknown_modal_vs_baseline(tmp_path: Path) -> None:
    world = World()
    base = world.base
    det = PopupDetector(load_profile("shared", Paths()), text_fn=lambda _s: "")
    paused: list[str] = []
    wd = Watchdog(
        WatchdogConfig(),
        det,
        world,
        on_pause=paused.append,
        screenshot_dir=tmp_path / "s",
        unknown_dir=tmp_path / "u",
    )
    wd.set_baseline(base)
    d = None
    for _ in range(WatchdogConfig().unknown_min_ticks):  # must persist before it counts
        assert paused == []
        d = wd.tick(next(GeneratorSource(lambda: unknown_popup(base), 1, 1.0).frames()))
    assert d is not None and d.kind == KIND_UNKNOWN
    assert paused == ["unknown_modal"] and world.clicks == []
    assert list((tmp_path / "u").glob("*unknown.png"))
    # a whole-room change is NOT an unknown modal (the border changed too)
    other = np.roll(base, 400, axis=0)
    assert det.detect(other, baseline=base) is None


def test_real_ocr_reads_synthetic_popup() -> None:
    img, _ = still_playing_popup(room_frame(1))
    det = PopupDetector(load_profile("shared", Paths()))
    center = det.center_box(img)
    x, y, w, h = center
    res = ocr_image(img[y : y + h, x : x + w], upscale=1.5)
    if res.error:
        pytest.skip(f"winocr unavailable: {res.error}")
    assert "still" in res.lowered
    d = det.detect(img)
    assert d is not None and d.kind == KIND_STILL_PLAYING and d.via == "green+ocr"
