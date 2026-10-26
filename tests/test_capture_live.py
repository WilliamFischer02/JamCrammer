"""Live capture tests: run only when the AJC window is on screen."""

from __future__ import annotations

import pytest

from jamcrammer.core.config import AppConfig
from jamcrammer.platform.capture import BLACK_FRAME_MAX_MEAN, open_backend
from jamcrammer.platform.window import WindowTracker, ensure_dpi_awareness

pytestmark = pytest.mark.live


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
    assert frame.size == info.client_size
    assert frame.image.shape[2] == 3
    assert frame.mean_brightness() > BLACK_FRAME_MAX_MEAN
