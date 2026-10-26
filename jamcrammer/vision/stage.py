"""Stage space: the game renders a fixed 16:9 stage (1920x1080 at fullscreen) scaled uniformly
by ``min(w/1920, h/1080)`` and centered in the client area (letterboxed). Every profile
coordinate is a *stage* coordinate; frames are normalized to stage size before vision runs and
clicks are mapped back to client pixels. Measured 2026-10-26 on the maximized window
(client 1904x1013 → scale 0.938, origin (52, 0)); fullscreen is the identity.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
import numpy.typing as npt

BgrImage = npt.NDArray[np.uint8]
Box = tuple[int, int, int, int]

STAGE_W = 1920
STAGE_H = 1080
STAGE_SIZE = (STAGE_W, STAGE_H)


@dataclass(frozen=True)
class StageMap:
    client_w: int
    client_h: int
    stage_w: int = STAGE_W
    stage_h: int = STAGE_H

    @property
    def scale(self) -> float:
        return min(self.client_w / self.stage_w, self.client_h / self.stage_h)

    @property
    def content_size(self) -> tuple[int, int]:
        return (round(self.stage_w * self.scale), round(self.stage_h * self.scale))

    @property
    def origin(self) -> tuple[float, float]:
        cw, ch = self.content_size
        return ((self.client_w - cw) / 2.0, (self.client_h - ch) / 2.0)

    @property
    def is_identity(self) -> bool:
        return (self.client_w, self.client_h) == (self.stage_w, self.stage_h)

    def to_client(self, sx: float, sy: float) -> tuple[int, int]:
        ox, oy = self.origin
        return (round(ox + sx * self.scale), round(oy + sy * self.scale))

    def to_stage(self, cx: float, cy: float) -> tuple[int, int]:
        ox, oy = self.origin
        s = self.scale
        return (round((cx - ox) / s), round((cy - oy) / s))

    def box_to_client(self, box: Box) -> Box:
        x, y, w, h = box
        x0, y0 = self.to_client(x, y)
        x1, y1 = self.to_client(x + w, y + h)
        return (x0, y0, x1 - x0, y1 - y0)

    def content_box(self) -> Box:
        ox, oy = self.origin
        cw, ch = self.content_size
        return (round(ox), round(oy), cw, ch)

    def normalize(self, client_image: BgrImage) -> BgrImage:
        """Crop the letterboxed content and resize it to stage size (no-op when identical)."""
        if (
            self.is_identity
            and client_image.shape[1] == self.stage_w
            and client_image.shape[0] == self.stage_h
        ):
            return client_image
        x, y, w, h = self.content_box()
        h_img, w_img = client_image.shape[:2]
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(w_img, x + w), min(h_img, y + h)
        content = client_image[y0:y1, x0:x1]
        interp = cv2.INTER_AREA if content.shape[1] > self.stage_w else cv2.INTER_CUBIC
        return np.asarray(
            cv2.resize(content, (self.stage_w, self.stage_h), interpolation=interp), dtype=np.uint8
        )


def stage_map_for(image: BgrImage) -> StageMap:
    return StageMap(int(image.shape[1]), int(image.shape[0]))
