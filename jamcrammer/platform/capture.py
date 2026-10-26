"""Frame capture of the AJC client area.

Backends:
- ``wgc``: Windows Graphics Capture via the ``windows-capture`` package, bound to the hwnd.
  Captures the window even when other windows overlap it.
- ``mss``: screen-region grab of the client rect. Simple, no callbacks, but needs the window
  unobstructed.

``auto`` tries WGC and falls back to mss. ``benchmark`` times both.
"""

from __future__ import annotations

import contextlib
import statistics
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from jamcrammer.platform.window import Rect, WindowInfo

Image = npt.NDArray[np.uint8]
WindowProvider = Callable[[], WindowInfo | None]

BGR_CHANNELS = 3
BLACK_FRAME_MAX_MEAN = 2.0
WGC_FIRST_FRAME_TIMEOUT_S = 3.0
WGC_GRAB_TIMEOUT_S = 1.0
WGC_CLOSE_TIMEOUT_S = 2.0
BENCH_WARMUP = 3


class CaptureError(RuntimeError):
    pass


@dataclass(frozen=True)
class Frame:
    image: Image  # HxWx3 BGR, client area only
    ts: float  # time.time()
    seq: int
    client_rect: Rect
    backend: str

    @property
    def size(self) -> tuple[int, int]:
        return (int(self.image.shape[1]), int(self.image.shape[0]))

    def mean_brightness(self) -> float:
        return float(self.image.mean()) if self.image.size else 0.0


class CaptureBackend(Protocol):
    name: str

    def grab(self) -> Frame: ...

    def close(self) -> None: ...


def _require_window(provider: WindowProvider) -> WindowInfo:
    info = provider()
    if info is None:
        msg = "window not available for capture"
        raise CaptureError(msg)
    return info


class MssBackend:
    name = "mss"

    def __init__(self, window: WindowProvider) -> None:
        self._window = window
        self._local = threading.local()
        self._seq = 0

    def _sct(self) -> Any:
        sct = getattr(self._local, "sct", None)
        if sct is None:
            import mss

            sct = mss.MSS() if hasattr(mss, "MSS") else mss.mss()
            self._local.sct = sct
        return sct

    def grab(self) -> Frame:
        info = _require_window(self._window)
        rect = info.client_rect
        region = {
            "left": rect.left,
            "top": rect.top,
            "width": rect.width,
            "height": rect.height,
        }
        shot = self._sct().grab(region)
        image: Image = np.ascontiguousarray(np.asarray(shot)[:, :, :BGR_CHANNELS])
        self._seq += 1
        return Frame(
            image=image, ts=time.time(), seq=self._seq, client_rect=rect, backend=self.name
        )

    def close(self) -> None:
        sct = getattr(self._local, "sct", None)
        if sct is not None:
            sct.close()
            self._local.sct = None


class WgcBackend:
    name = "wgc"

    def __init__(
        self, window: WindowProvider, first_frame_timeout_s: float = WGC_FIRST_FRAME_TIMEOUT_S
    ) -> None:
        from windows_capture import WindowsCapture

        self._window = window
        info = _require_window(window)
        self._hwnd = info.hwnd
        self._cond = threading.Condition()
        self._latest: Image | None = None
        self._latest_ts = 0.0
        self._count = 0
        self._served = 0
        self._closed = False
        self._closed_by_us = False
        self._seq = 0

        capture = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=info.hwnd)
        outer = self

        def on_frame_arrived(frame: Any, _control: Any) -> None:
            # The native buffer is released after this callback: copy now.
            image: Image = np.ascontiguousarray(frame.frame_buffer[:, :, :BGR_CHANNELS])
            with outer._cond:
                outer._latest = image
                outer._latest_ts = time.time()
                outer._count += 1
                outer._cond.notify_all()

        def on_closed() -> None:
            with outer._cond:
                outer._closed = True
                outer._cond.notify_all()

        capture.frame_handler = on_frame_arrived
        capture.closed_handler = on_closed
        self._capture = capture  # keep alive: the native thread calls back into this object
        self._control = capture.start_free_threaded()
        with self._cond:
            if not self._cond.wait_for(
                lambda: self._latest is not None or self._closed, timeout=first_frame_timeout_s
            ):
                self.close()
                msg = f"WGC produced no frame within {first_frame_timeout_s}s"
                raise CaptureError(msg)
            if self._closed:
                msg = "WGC session closed before the first frame"
                raise CaptureError(msg)

    def grab(self, timeout_s: float = WGC_GRAB_TIMEOUT_S) -> Frame:
        info = _require_window(self._window)
        with self._cond:
            self._cond.wait_for(
                lambda: self._count > self._served or self._closed, timeout=timeout_s
            )
            if self._latest is None:
                msg = "WGC has no frame"
                raise CaptureError(msg)
            # If nothing new arrived (static screen), WGC simply doesn't deliver; reuse latest.
            image = self._latest
            ts = self._latest_ts
            self._served = self._count
        self._seq += 1
        return Frame(
            image=_crop_to_client(image, info),
            ts=ts,
            seq=self._seq,
            client_rect=info.client_rect,
            backend=self.name,
        )

    def close(self) -> None:
        if self._closed_by_us:
            return
        self._closed_by_us = True
        with contextlib.suppress(Exception):
            self._control.stop()
        # Let the native capture thread finish before anything else touches WGC; a second
        # session opened while the first is still tearing down segfaulted the interpreter.
        with contextlib.suppress(Exception):
            self._control.wait()
        with self._cond:
            self._cond.wait_for(lambda: self._closed, timeout=WGC_CLOSE_TIMEOUT_S)


def _crop_to_client(image: Image, info: WindowInfo) -> Image:
    """WGC frames cover the window rect; return the client-area part (no-op when frameless)."""
    ox = info.client_rect.left - info.window_rect.left
    oy = info.client_rect.top - info.window_rect.top
    h, w = image.shape[0], image.shape[1]
    x0 = max(0, min(ox, w))
    y0 = max(0, min(oy, h))
    x1 = max(x0, min(x0 + info.client_rect.width, w))
    y1 = max(y0, min(y0 + info.client_rect.height, h))
    if (x0, y0, x1, y1) == (0, 0, w, h):
        return image
    return np.ascontiguousarray(image[y0:y1, x0:x1])


def open_backend(kind: str, window: WindowProvider) -> CaptureBackend:
    """``wgc`` | ``mss`` | ``auto`` (WGC with mss fallback)."""
    if kind == "mss":
        return MssBackend(window)
    if kind == "wgc":
        return WgcBackend(window)
    if kind == "auto":
        try:
            wgc = WgcBackend(window)
            probe = wgc.grab()
            if probe.mean_brightness() > BLACK_FRAME_MAX_MEAN:
                return wgc
            wgc.close()
        except Exception:
            pass
        return MssBackend(window)
    msg = f"unknown capture backend {kind!r}"
    raise CaptureError(msg)


@dataclass(frozen=True)
class BenchResult:
    backend: str
    ok: bool
    frames: int
    mean_ms: float
    p95_ms: float
    fps: float
    mean_brightness: float
    size: tuple[int, int]
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def benchmark(
    window: WindowProvider, frames: int, kinds: tuple[str, ...] = ("wgc", "mss")
) -> list[BenchResult]:
    results: list[BenchResult] = []
    for kind in kinds:
        backend: CaptureBackend | None = None
        try:
            backend = open_backend(kind, window)
            for _ in range(BENCH_WARMUP):
                backend.grab()
            durations: list[float] = []
            last: Frame | None = None
            t0 = time.perf_counter()
            for _ in range(frames):
                t = time.perf_counter()
                last = backend.grab()
                durations.append((time.perf_counter() - t) * 1000.0)
            elapsed = time.perf_counter() - t0
            assert last is not None
            durations.sort()
            p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))]
            results.append(
                BenchResult(
                    backend=kind,
                    ok=True,
                    frames=frames,
                    mean_ms=statistics.fmean(durations),
                    p95_ms=p95,
                    fps=frames / elapsed if elapsed else 0.0,
                    mean_brightness=last.mean_brightness(),
                    size=last.size,
                )
            )
        except Exception as exc:
            results.append(
                BenchResult(
                    backend=kind,
                    ok=False,
                    frames=0,
                    mean_ms=0.0,
                    p95_ms=0.0,
                    fps=0.0,
                    mean_brightness=0.0,
                    size=(0, 0),
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
        finally:
            if backend is not None:
                backend.close()
    return results
