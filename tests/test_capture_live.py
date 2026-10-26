"""Live capture tests: run only when the AJC window is on screen."""

from __future__ import annotations

import pytest

from jamcrammer.core.config import AppConfig
from jamcrammer.platform.capture import BLACK_FRAME_MAX_MEAN, open_backend
from jamcrammer.platform.window import WindowTracker, ensure_dpi_awareness

pytestmark = pytest.mark.live
WGC_SIZE_SLACK = 8


@pytest.fixture(scope="module")
def tracker() -> WindowTracker:
    ensure_dpi_awareness()
    cfg = AppConfig()
    t = WindowTracker(cfg.window_title_match, cfg.window_process_name)
    if t.refresh() is None:
        pytest.skip("AJC window not running")
    return t


@pytest.mark.parametrize("kind", ["mss", "wgc"])
def test_backend_grabs_client_sized_nonblack_frame(tracker: WindowTracker, kind: str) -> None:
    backend = open_backend(kind, tracker.provider())
    try:
        frame = backend.grab()
    finally:
        backend.close()
    info = tracker.require()
    cw, ch = info.client_size
    if kind == "wgc":
        # maximized/windowed Electron: WGC frames can be a few px smaller than the client
        assert (
            abs(frame.size[0] - cw) <= WGC_SIZE_SLACK and abs(frame.size[1] - ch) <= WGC_SIZE_SLACK
        )
    else:
        assert frame.size == info.client_size
    assert frame.image.shape[2] == 3
    assert frame.mean_brightness() > BLACK_FRAME_MAX_MEAN


def test_auto_backend_frame_matches_client_exactly(tracker: WindowTracker) -> None:
    backend = open_backend("auto", tracker.provider())
    try:
        frame = backend.grab()
    finally:
        backend.close()
    assert frame.size == tracker.require().client_size
