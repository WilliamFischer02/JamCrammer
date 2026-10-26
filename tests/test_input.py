from __future__ import annotations

import pytest

from jamcrammer.core.config import InputConfig
from jamcrammer.core.events import EventBus
from jamcrammer.platform.input import InputController, InputRefusedError
from jamcrammer.platform.window import Rect, WindowInfo

WINDOW = WindowInfo(
    hwnd=1,
    title="Animal Jam Classic",
    class_name="Chrome_WidgetWin_1",
    pid=1,
    process_name="AJ Classic.exe",
    window_rect=Rect(100, 100, 1100, 700),
    client_rect=Rect(100, 100, 1100, 700),
    dpi=96,
)


def _ctl(window: WindowInfo | None = WINDOW) -> tuple[InputController, EventBus]:
    bus = EventBus()
    return InputController(lambda: window, InputConfig(), dry_run=True, bus=bus, stage=False), bus


def test_to_screen_inside() -> None:
    ctl, _ = _ctl()
    assert ctl.to_screen(0, 0) == (100, 100)
    assert ctl.to_screen(999, 599) == (1099, 699)


@pytest.mark.parametrize("pt", [(-1, 0), (0, -1), (1000, 0), (0, 600), (5000, 5000)])
def test_out_of_bounds_refused(pt: tuple[int, int]) -> None:
    ctl, bus = _ctl()
    seen: list[str] = []
    bus.subscribe("input.refused", lambda e: seen.append(e.payload["reason"]))
    with pytest.raises(InputRefusedError):
        ctl.click(*pt)
    assert ctl.stats.refused == 1
    assert seen == ["out_of_bounds"]


def test_no_window_refused() -> None:
    ctl, _ = _ctl(None)
    with pytest.raises(InputRefusedError):
        ctl.click(1, 1)
    with pytest.raises(InputRefusedError):
        ctl.key_tap("left", 10)


def test_dry_run_sends_nothing_but_logs() -> None:
    ctl, bus = _ctl()
    events: list[str] = []
    bus.subscribe("*", lambda e: events.append(e.type))
    assert ctl.click(10, 10) is True
    ctl.movement_pair(("left", "right"), tap_ms=1, gap_ms=1)
    assert ctl.stats.clicks == 0
    assert ctl.stats.keys == 0
    assert ctl.stats.last_injected_ts == 0.0
    assert "input.click" in events
    assert "input.movement_pair" in events


def test_unknown_key_rejected() -> None:
    ctl, _ = _ctl()
    from jamcrammer.platform.input import InputError

    with pytest.raises(InputError):
        ctl.key_down("bogus")
