from __future__ import annotations

import numpy as np

from jamcrammer.core.config import AntiAfkConfig, DriftConfig, InputConfig, Level
from jamcrammer.core.events import EventBus
from jamcrammer.platform.input import InputController
from jamcrammer.platform.window import Rect, WindowInfo
from jamcrammer.services.antiafk import AntiAfk, Heartbeat, RoomDriftGuard
from tests.synthetic.popups import H, W, room_frame, still_playing_popup

WINDOW = WindowInfo(
    1,
    "Animal Jam Classic",
    "Chrome_WidgetWin_1",
    1,
    "AJ Classic.exe",
    Rect(0, 0, W, H),
    Rect(0, 0, W, H),
    96,
)
MASKS = [(0, 0, W, 110), (0, 880, W, 200), (760, 380, 400, 320)]


def test_drift_guard_ignores_masked_hud_and_small_noise() -> None:
    guard = RoomDriftGuard(DriftConfig(threshold=18.0), MASKS)
    base = room_frame(1)
    guard.set_baseline(base)
    same = base.copy()
    same[0:100, :, :] = 255  # HUD band changes are masked out
    assert guard.check(same).score < 1.0
    noisy = np.clip(base.astype(np.int16) + 3, 0, 255).astype(np.uint8)
    assert not guard.check(noisy).drifted


def test_drift_guard_flags_room_change_and_modal() -> None:
    guard = RoomDriftGuard(DriftConfig(threshold=18.0), MASKS)
    guard.set_baseline(room_frame(1))
    other = np.roll(room_frame(1), 400, axis=0)  # the whole room shifted: a room change
    assert guard.check(other).drifted
    popup, _ = still_playing_popup(room_frame(1))
    assert guard.score(popup) > 0.0


def test_antiafk_rotates_pairs_and_stops_on_drift() -> None:
    bus = EventBus()
    events: list[tuple[str, Level]] = []
    bus.subscribe("*", lambda e: events.append((e.type, e.level)))
    inp = InputController(lambda: WINDOW, InputConfig(), dry_run=True, bus=bus)
    guard = RoomDriftGuard(DriftConfig(threshold=18.0), MASKS)
    cfg = AntiAfkConfig(tap_ms=20, gap_ms=1, settle_ms=0)
    afk = AntiAfk(cfg, inp, guard, bus=bus, ensure_focus=lambda: True)
    base = room_frame(1)
    guard.set_baseline(base)

    r1 = afk.cycle(lambda: base)
    r2 = afk.cycle(lambda: base)
    assert r1.pair == ("left", "right") and r2.pair == ("up", "down")
    assert r1.sent and not r1.drift.drifted
    assert afk.cycles == 2

    moved = np.roll(room_frame(1), 400, axis=0)
    r3 = afk.cycle(lambda: moved)
    assert r3.drift.drifted
    assert not afk.enabled
    assert ("antiafk.room_drift", Level.HIGH) in events
    r4 = afk.cycle(lambda: moved)
    assert not r4.sent  # disabled after drift


def test_heartbeat_due() -> None:
    last = {"ts": 100.0}
    hb = Heartbeat(180.0, lambda: last["ts"])
    assert not hb.due(now=200.0)
    assert hb.due(now=280.0)
