"""Stage space: the game renders a fixed 16:9 stage (1920x1080 at fullscreen) scaled uniformly
to the client **height** (``h/1080``) and centered horizontally: a wider client shows bars at
the sides, a narrower one crops the stage's left and right edges. Every profile coordinate is a
*stage* coordinate; frames are normalized to stage size before vision runs and clicks are mapped
back to client pixels. Measured on the PC's maximized window 2026-10-26 (client 1904x1013 →
scale 0.938, origin (52, 0)) and the laptop's 2026-10-29 (client 2560x1459, 16:10 screen minus
the taskbar → scale 1.351, origin (-17, 0): the Expand/Log Out tabs are cut off). A
``min(w/1920, h/1080)`` letterbox rule agrees with the first and is 12-16 px off on the second.
Fullscreen 1920x1080 is the identity.
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
        return self.client_h / self.stage_h

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
        if x >= 0:
            return np.asarray(
                cv2.resize(content, (self.stage_w, self.stage_h), interpolation=interp),
                dtype=np.uint8,
            )
        # Client narrower than 16:9: the stage's sides are off-screen. Resize what is visible
        # and leave the cropped stage columns black so every stage coordinate stays put.
        sx0, _ = self.to_stage(x0, y0)
        sx1, _ = self.to_stage(x1, y1)
        sx0, sx1 = max(0, sx0), min(self.stage_w, sx1)
        out = np.zeros((self.stage_h, self.stage_w, *client_image.shape[2:]), dtype=np.uint8)
        out[:, sx0:sx1] = cv2.resize(content, (sx1 - sx0, self.stage_h), interpolation=interp)
        return out


def stage_map_for(image: BgrImage) -> StageMap:
    return StageMap(int(image.shape[1]), int(image.shape[0]))
