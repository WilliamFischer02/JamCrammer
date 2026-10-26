"""Live Learn recorder (KICKOFF §11.2).

Streams:
- change-triggered keyframes (pHash Hamming distance ≥ threshold, plus a heartbeat keyframe);
- a ring buffer at ``ring_fps`` dumped ±``ring_window_s`` around every marker;
- William's own clicks/keys (pynput, local only; nothing is ever sent to the game);
- optional continuous low-fps video.

Everything lands in one session folder with a ``meta.jsonl`` log. The frame source is injected
(``grab``) so the recorder runs identically against the live window or a synthetic source.
"""

from __future__ import annotations

import collections
import contextlib
import json
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from jamcrammer.core.config import LearnConfig
from jamcrammer.core.events import EventBus
from jamcrammer.core.logging import get_logger
from jamcrammer.platform.capture import Frame
from jamcrammer.platform.window import WindowInfo, classify_change
from jamcrammer.vision.match import hamming, phash_of

log = get_logger("learn.recorder")

MARKER_KEYS: dict[str, str] = {"f6": "mark", "f7": "correct", "f8": "wrong"}
STOP_KEY = "f12"
WINDOW_POLL_S = 1.0
WRITER_IDLE_S = 0.2
VIDEO_FOURCC = "mp4v"
WARMUP_SIZE = 16

GrabFn = Callable[[], Frame]
WindowFn = Callable[[], WindowInfo | None]


@dataclass
class RecorderStats:
    frames: int = 0
    keyframes: int = 0
    markers: int = 0
    inputs: int = 0
    ring_dumps: int = 0
    ocr_snapshots: int = 0
    grab_errors: int = 0
    started_ts: float = 0.0
    ended_ts: float = 0.0
    last_keyframe_ts: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class Recorder:
    def __init__(
        self,
        *,
        session_dir: Path,
        target: str,
        cfg: LearnConfig,
        grab: GrabFn,
        window: WindowFn,
        bus: EventBus | None = None,
        capture_input: bool = True,
        video: bool = False,
        ocr: bool = True,
        extra_meta: dict[str, Any] | None = None,
    ) -> None:
        self.session_dir = session_dir
        self.frames_dir = session_dir / "frames"
        self.markers_dir = session_dir / "markers"
        self.meta_path = session_dir / "meta.jsonl"
        self.target = target
        self.cfg = cfg
        self._grab = grab
        self._window = window
        self._bus = bus
        self._capture_input = capture_input
        self._video = video
        self._ocr = ocr
        self._extra_meta = extra_meta or {}

        self.stats = RecorderStats()
        self._stop = threading.Event()
        self._meta_lock = threading.Lock()
        self._meta_fh: Any = None
        self._jobs: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._listeners: list[Any] = []
        self._video_writer: Any = None

        ring_len = max(1, round(cfg.ring_fps * cfg.ring_window_s))
        self._ring: collections.deque[tuple[float, int, bytes]] = collections.deque(maxlen=ring_len)
        self._ring_lock = threading.Lock()
        self._pending_marker: tuple[Path, float] | None = None  # (dir, until_ts)
        self._last_hash: Any = None
        self._last_window: WindowInfo | None = None

    # --- lifecycle -----------------------------------------------------------------------

    def start(self) -> None:
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self.markers_dir.mkdir(parents=True, exist_ok=True)
        self._meta_fh = self.meta_path.open("a", encoding="utf-8")
        self.stats.started_ts = time.time()
        # Warm the hash path (first call imports scipy) so the first live frame isn't delayed.
        phash_of(np.zeros((WARMUP_SIZE, WARMUP_SIZE, 3), dtype=np.uint8), self.cfg.phash_width)
        info = self._window()
        self._last_window = info
        self._write(
            {
                "t": "session",
                "target": self.target,
                "started_ts": self.stats.started_ts,
                "window": info.as_dict() if info else None,
                "learn_cfg": self.cfg.model_dump(),
                "capture_input": self._capture_input,
                "video": self._video,
                "ocr": self._ocr,
                **self._extra_meta,
            }
        )
        self._threads = [
            threading.Thread(target=self._writer_loop, name="rec-writer", daemon=True),
            threading.Thread(target=self._capture_loop, name="rec-capture", daemon=True),
            threading.Thread(target=self._window_loop, name="rec-window", daemon=True),
        ]
        for t in self._threads:
            t.start()
        if self._capture_input:
            self._start_input_listeners()
        self._emit("learn.started", path=str(self.session_dir), target=self.target)

    def stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        for listener in self._listeners:
            with contextlib.suppress(Exception):
                listener.stop()
        for t in self._threads:
            t.join(timeout=5.0)
        self._drain_jobs()
        if self._video_writer is not None:
            self._video_writer.release()
            self._video_writer = None
        self.stats.ended_ts = time.time()
        self._write({"t": "end", "ended_ts": self.stats.ended_ts, "stats": self.stats.as_dict()})
        if self._meta_fh is not None:
            self._meta_fh.close()
            self._meta_fh = None
        self._emit("learn.stopped", path=str(self.session_dir), **self.stats.as_dict())

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def run_until_stopped(
        self, poll_s: float, on_tick: Callable[[RecorderStats], None] | None
    ) -> None:
        try:
            while not self._stop.wait(poll_s):
                if on_tick is not None:
                    on_tick(self.stats)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    # --- markers -------------------------------------------------------------------------

    def mark(self, kind: str, note: str = "", ts: float | None = None) -> Path:
        """Record a marker and dump the ring ±window around it."""
        ts = time.time() if ts is None else ts
        self.stats.markers += 1
        name = f"m{self.stats.markers:03d}_{kind}_{int(ts * 1000)}"
        marker_dir = self.markers_dir / name
        marker_dir.mkdir(parents=True, exist_ok=True)
        with self._ring_lock:
            past = list(self._ring)
            self._pending_marker = (marker_dir, ts + self.cfg.ring_window_s)
        for fts, seq, jpg in past:
            self._jobs.put(("bytes", (marker_dir / f"{int(fts * 1000)}_{seq:06d}.jpg", jpg)))
        self.stats.ring_dumps += len(past)
        self._write(
            {
                "t": "marker",
                "ts": ts,
                "kind": kind,
                "note": note,
                "dir": name,
                "past_frames": len(past),
            }
        )
        self._emit("learn.marker", kind=kind, note=note, dir=name)
        return marker_dir

    # --- loops ---------------------------------------------------------------------------

    def _capture_loop(self) -> None:
        period = 1.0 / self.cfg.ring_fps
        next_t = time.monotonic()
        while not self._stop.is_set():
            try:
                frame = self._grab()
            except Exception as exc:
                self.stats.grab_errors += 1
                log.warning("grab failed: %s", exc)
                time.sleep(period)
                continue
            self._on_frame(frame)
            next_t += period
            delay = next_t - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_t = time.monotonic()

    def _on_frame(self, frame: Frame) -> None:
        self.stats.frames += 1
        ok, jpg_arr = cv2.imencode(
            ".jpg", frame.image, [int(cv2.IMWRITE_JPEG_QUALITY), self.cfg.jpeg_quality]
        )
        jpg = jpg_arr.tobytes() if ok else b""
        with self._ring_lock:
            self._ring.append((frame.ts, frame.seq, jpg))
            pending = self._pending_marker
            if pending is not None and frame.ts > pending[1]:
                self._pending_marker = None
                pending = None
        if pending is not None and jpg:
            self._jobs.put(
                ("bytes", (pending[0] / f"{int(frame.ts * 1000)}_{frame.seq:06d}.jpg", jpg))
            )
            self.stats.ring_dumps += 1

        h = phash_of(frame.image, self.cfg.phash_width)
        distance = hamming(self._last_hash, h) if self._last_hash is not None else None
        heartbeat_due = frame.ts - self.stats.last_keyframe_ts >= self.cfg.heartbeat_s
        if distance is None or distance >= self.cfg.keyframe_threshold or heartbeat_due:
            self._last_hash = h
            self.stats.keyframes += 1
            self.stats.last_keyframe_ts = frame.ts
            name = f"kf_{self.stats.keyframes:06d}_{int(frame.ts * 1000)}.png"
            image = frame.image.copy()
            self._jobs.put(("png", (self.frames_dir / name, image)))
            self._write(
                {
                    "t": "keyframe",
                    "ts": frame.ts,
                    "seq": frame.seq,
                    "file": f"frames/{name}",
                    "phash": str(h),
                    "distance": distance,
                    "heartbeat": bool(
                        heartbeat_due
                        and distance is not None
                        and distance < self.cfg.keyframe_threshold
                    ),
                    "size": frame.size,
                    "backend": frame.backend,
                }
            )
            if self._ocr:
                self._jobs.put(("ocr", (f"frames/{name}", frame.ts, image)))
        if self._video:
            self._jobs.put(("video", (frame.image.copy(), frame.size)))

    def _writer_loop(self) -> None:
        while not (self._stop.is_set() and self._jobs.empty()):
            try:
                kind, payload = self._jobs.get(timeout=WRITER_IDLE_S)
            except queue.Empty:
                continue
            self._run_job(kind, payload)

    def _drain_jobs(self) -> None:
        while True:
            try:
                kind, payload = self._jobs.get_nowait()
            except queue.Empty:
                return
            self._run_job(kind, payload)

    def _run_job(self, kind: str, payload: Any) -> None:
        try:
            if kind == "png":
                path, image = payload
                cv2.imwrite(str(path), image)
            elif kind == "bytes":
                path, data = payload
                Path(path).write_bytes(data)
            elif kind == "ocr":
                rel, ts, image = payload
                self._ocr_snapshot(rel, ts, image)
            elif kind == "video":
                image, size = payload
                if self._video_writer is None:
                    self._video_writer = cv2.VideoWriter(
                        str(self.session_dir / "video.mp4"),
                        cv2.VideoWriter.fourcc(*VIDEO_FOURCC),
                        self.cfg.ring_fps,
                        size,
                    )
                self._video_writer.write(image)
        except Exception as exc:
            log.warning("writer job %s failed: %s", kind, exc)

    def _ocr_snapshot(self, rel: str, ts: float, image: np.ndarray) -> None:
        try:
            import winocr

            result = winocr.recognize_cv2_sync(image, "en")
        except Exception as exc:
            self._write(
                {"t": "ocr", "ts": ts, "file": rel, "error": f"{type(exc).__name__}: {exc}"}
            )
            return
        lines = []
        for line in result.get("lines", []):
            words = []
            for word in line.get("words", []):
                rect = word.get("bounding_rect", {})
                words.append(
                    {
                        "text": word.get("text", ""),
                        "box": [
                            int(rect.get("x", 0)),
                            int(rect.get("y", 0)),
                            int(rect.get("width", 0)),
                            int(rect.get("height", 0)),
                        ],
                    }
                )
            lines.append({"text": line.get("text", ""), "words": words})
        self.stats.ocr_snapshots += 1
        self._write(
            {"t": "ocr", "ts": ts, "file": rel, "text": result.get("text", ""), "lines": lines}
        )

    def _window_loop(self) -> None:
        while not self._stop.wait(WINDOW_POLL_S):
            info = self._window()
            if self._last_window is not None:
                change = classify_change(self._last_window, info)
                if change is not None:
                    self._write(
                        {
                            "t": "window",
                            "ts": time.time(),
                            "change": change,
                            "window": info.as_dict() if info else None,
                        }
                    )
                    self._emit("learn.window_changed", change=change)
            self._last_window = info

    # --- input log (local only) ------------------------------------------------------------

    def _start_input_listeners(self) -> None:
        from pynput import keyboard, mouse

        def on_click(x: int, y: int, button: Any, pressed: bool) -> None:
            info = self._window()
            cx, cy = info.to_client(int(x), int(y)) if info else (None, None)
            inside = bool(info and info.contains_screen_point(int(x), int(y)))
            self.stats.inputs += pressed
            self._write(
                {
                    "t": "input",
                    "ts": time.time(),
                    "kind": "mouse",
                    "event": "press" if pressed else "release",
                    "button": str(button).rsplit(".", 1)[-1],
                    "sx": int(x),
                    "sy": int(y),
                    "cx": cx,
                    "cy": cy,
                    "inside": inside,
                }
            )

        def on_key(key: Any, pressed: bool) -> None:
            name = _key_name(key)
            self._write(
                {
                    "t": "input",
                    "ts": time.time(),
                    "kind": "key",
                    "event": "press" if pressed else "release",
                    "key": name,
                }
            )
            if pressed:
                self.stats.inputs += 1
                if name in MARKER_KEYS:
                    self.mark(MARKER_KEYS[name])
                elif name == STOP_KEY:
                    self._write({"t": "stop_requested", "ts": time.time(), "via": name})
                    self._stop.set()

        m = mouse.Listener(on_click=on_click)
        k = keyboard.Listener(
            on_press=lambda key: on_key(key, True), on_release=lambda key: on_key(key, False)
        )
        m.start()
        k.start()
        self._listeners = [m, k]

    # --- plumbing --------------------------------------------------------------------------

    def _write(self, record: dict[str, Any]) -> None:
        with self._meta_lock:
            if self._meta_fh is None:
                return
            self._meta_fh.write(json.dumps(record, default=str) + "\n")
            self._meta_fh.flush()

    def _emit(self, event_type: str, **payload: Any) -> None:
        if self._bus is not None:
            self._bus.emit(event_type, source="recorder", **payload)


def _key_name(key: Any) -> str:
    char = getattr(key, "char", None)
    if char:
        return str(char)
    name = getattr(key, "name", None)
    return str(name) if name else str(key)


def read_meta(session_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    path = session_dir / "meta.jsonl"
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if line:
                out.append(json.loads(line))
    return out
