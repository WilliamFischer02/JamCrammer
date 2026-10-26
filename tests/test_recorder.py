from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from jamcrammer.core.config import LearnConfig
from jamcrammer.core.events import EventBus
from jamcrammer.learn.recorder import Recorder, read_meta
from jamcrammer.platform.capture import Frame
from jamcrammer.platform.window import Rect, WindowInfo

W, H = 320, 180
WINDOW = WindowInfo(
    hwnd=1,
    title="Animal Jam Classic",
    class_name="Chrome_WidgetWin_1",
    pid=1,
    process_name="AJ Classic.exe",
    window_rect=Rect(0, 0, W, H),
    client_rect=Rect(0, 0, W, H),
    dpi=96,
)


class SyntheticSource:
    """Scene changes every ``change_every`` grabs; otherwise the frame repeats exactly."""

    def __init__(self, change_every: int) -> None:
        self.n = 0
        self.change_every = change_every
        self.scene = 0

    def grab(self) -> Frame:
        self.n += 1
        if self.n % self.change_every == 0:
            self.scene += 1
        rng = np.random.default_rng(self.scene)
        img = rng.integers(0, 255, size=(H, W, 3), dtype=np.uint8)
        return Frame(
            image=img,
            ts=time.time(),
            seq=self.n,
            client_rect=WINDOW.client_rect,
            backend="synthetic",
        )


def _cfg() -> LearnConfig:
    return LearnConfig(
        ring_fps=25, ring_window_s=0.4, heartbeat_s=1000, keyframe_threshold=6, phash_width=W
    )


def test_recorder_writes_keyframes_markers_and_meta(tmp_path: Path) -> None:
    session = tmp_path / "s1"
    src = SyntheticSource(change_every=10)
    bus = EventBus()
    seen: list[str] = []
    bus.subscribe("*", lambda e: seen.append(e.type))
    rec = Recorder(
        session_dir=session,
        target="bestguess",
        cfg=_cfg(),
        grab=src.grab,
        window=lambda: WINDOW,
        bus=bus,
        capture_input=False,
        ocr=False,
    )
    rec.start()
    time.sleep(1.0)
    marker_dir = rec.mark("correct", note="unit")
    time.sleep(0.6)
    rec.stop()

    meta = read_meta(session)
    kinds = [m["t"] for m in meta]
    assert kinds[0] == "session"
    assert kinds[-1] == "end"
    assert meta[0]["window"]["client_rect"] == [0, 0, W, H]

    pngs = sorted((session / "frames").glob("kf_*.png"))
    keyframe_lines = [m for m in meta if m["t"] == "keyframe"]
    assert len(pngs) == len(keyframe_lines) == rec.stats.keyframes
    # ~25 fps for ~1.6 s with a scene change every 10 frames: at least 3 distinct scenes.
    assert rec.stats.keyframes >= 3
    assert rec.stats.keyframes < rec.stats.frames  # unchanged frames are not keyframes

    assert rec.stats.markers == 1
    jpgs = list(marker_dir.glob("*.jpg"))
    # ring_window 0.4 s * 25 fps = 10 frames before, plus ~10 after
    assert len(jpgs) >= 10
    assert any(m["t"] == "marker" and m["kind"] == "correct" for m in meta)
    assert "learn.started" in seen
    assert "learn.marker" in seen
    assert "learn.stopped" in seen


def test_recorder_survives_grab_errors(tmp_path: Path) -> None:
    calls = {"n": 0}

    def flaky() -> Frame:
        calls["n"] += 1
        if calls["n"] % 2:
            msg = "transient"
            raise RuntimeError(msg)
        img = np.zeros((H, W, 3), dtype=np.uint8)
        return Frame(
            image=img,
            ts=time.time(),
            seq=calls["n"],
            client_rect=WINDOW.client_rect,
            backend="synthetic",
        )

    rec = Recorder(
        session_dir=tmp_path / "s2",
        target="custom",
        cfg=_cfg(),
        grab=flaky,
        window=lambda: WINDOW,
        capture_input=False,
        ocr=False,
    )
    rec.start()
    time.sleep(0.5)
    rec.stop()
    assert rec.stats.grab_errors > 0
    assert rec.stats.frames > 0
    assert rec.stats.keyframes >= 1
