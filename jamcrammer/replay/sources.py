"""Frame sources for the offline replay harness.

Every mode consumes ``Frame`` objects, so anything that yields them can drive a mode without
the live game: a recorded Learn session, a list of images, or a synthetic generator.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
import numpy.typing as npt

from jamcrammer.learn.recorder import read_meta
from jamcrammer.platform.capture import Frame
from jamcrammer.platform.window import Rect

BgrImage = npt.NDArray[np.uint8]


class FrameSource(Protocol):
    def frames(self) -> Iterator[Frame]: ...


def _rect_for(image: BgrImage) -> Rect:
    h, w = image.shape[:2]
    return Rect(0, 0, w, h)


class ImageListSource:
    def __init__(self, items: Iterable[tuple[float, BgrImage]], backend: str = "replay") -> None:
        self._items = list(items)
        self._backend = backend

    def frames(self) -> Iterator[Frame]:
        for seq, (ts, image) in enumerate(self._items, start=1):
            yield Frame(
                image=image, ts=ts, seq=seq, client_rect=_rect_for(image), backend=self._backend
            )


class SessionSource:
    """Keyframes of a recorded session in time order (``meta.jsonl`` + ``frames/``)."""

    def __init__(self, session_dir: Path, kinds: tuple[str, ...] = ("keyframe",)) -> None:
        self.session_dir = session_dir
        self.meta = read_meta(session_dir)
        self._records = [m for m in self.meta if m.get("t") in kinds and "file" in m]

    def __len__(self) -> int:
        return len(self._records)

    def frames(self) -> Iterator[Frame]:
        for seq, rec in enumerate(self._records, start=1):
            path = self.session_dir / str(rec["file"])
            raw = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if raw is None:
                continue
            image = np.asarray(raw, dtype=np.uint8)
            yield Frame(
                image=image,
                ts=float(rec.get("ts", 0.0)),
                seq=seq,
                client_rect=_rect_for(image),
                backend="session",
            )


class VideoSource:
    """Frames of a recording (an inbox MP4) at ~``fps``, stage-normalised to 1920x1080."""

    def __init__(self, path: Path, fps: float = 2.0) -> None:
        self.path = path
        self.fps = fps

    def frames(self) -> Iterator[Frame]:
        from jamcrammer.learn.analyze.bestguess_banner import video_frames

        for seq, (ts, image) in enumerate(video_frames(self.path, self.fps), start=1):
            yield Frame(image=image, ts=ts, seq=seq, client_rect=_rect_for(image), backend="video")


class GeneratorSource:
    """Wraps a callable that returns the *current* image of a simulated world."""

    def __init__(
        self, world: Callable[[], BgrImage], ticks: int, dt: float, t0: float = 0.0
    ) -> None:
        self._world = world
        self._ticks = ticks
        self._dt = dt
        self._t0 = t0

    def frames(self) -> Iterator[Frame]:
        for seq in range(1, self._ticks + 1):
            image = self._world()
            yield Frame(
                image=image,
                ts=self._t0 + seq * self._dt,
                seq=seq,
                client_rect=_rect_for(image),
                backend="synthetic",
            )
