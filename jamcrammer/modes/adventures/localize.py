"""Where the animal is, in world px (docs/MAPPING.md): the minimap dot fused with scene
odometry.

- The **dot** is absolute and never drifts, but it is quantised to a minimap pixel (~70 scene
  px on The Mystery Below) and a little behind the animal.
- **Odometry** is the shift of the scene between two consecutive frames (phase correlation
  on the high-passed grey image with the HUD and our animal masked, ``sight.scene_shift``):
  the camera follows the animal, so content that moved by (dx, dy) means the animal moved by
  (-dx, -dy). It is exact to a few px over a tick, and it drifts over a walk.

The estimate moves with the odometry and is pulled toward the dot a little on every update
(``dot_gain``); when it sits more than ``dot_reset_px`` off the dot for ``dot_reset_n``
updates running, the odometry has lost it and the dot wins outright. ``moved`` per update is
also how a probe knows it has hit something: a key held and nothing moving.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from jamcrammer.core.config import WorldConfig
from jamcrammer.core.profiles import Profile
from jamcrammer.modes.adventures import sight
from jamcrammer.modes.adventures.world import World

BgrImage = npt.NDArray[np.uint8]
Point = tuple[float, float]


@dataclass
class Fix:
    pos: Point | None  # world px, None until a dot has been seen
    dot: Point | None  # the dot's world position this frame
    moved: Point  # odometry this update (world px)
    response: float  # phase correlation peak (0 when not measured)
    source: str  # dot | odometry | fused | none
    off_dot: float  # distance between the estimate and the dot (0 without a dot)

    def as_dict(self) -> dict[str, Any]:
        return {
            "pos": [round(self.pos[0]), round(self.pos[1])] if self.pos else None,
            "dot": [round(self.dot[0]), round(self.dot[1])] if self.dot else None,
            "moved": [round(self.moved[0], 1), round(self.moved[1], 1)],
            "response": round(self.response, 3),
            "source": self.source,
            "off_dot": round(self.off_dot),
        }


class Localizer:
    def __init__(self, world: World, prof: Profile, cfg: WorldConfig) -> None:
        self.world, self.prof, self.cfg = world, prof, cfg
        self.pos: Point | None = None
        self._gray: npt.NDArray[np.float32] | None = None
        self._ts: float | None = None
        self._off_n = 0
        self.last: Fix = Fix(None, None, (0.0, 0.0), 0.0, "none", 0.0)

    def reset(self, pos: Point | None = None) -> None:
        """Forget the odometry's history (between commands, after a user turn)."""
        self.pos = pos
        self._gray = self._ts = None
        self._off_n = 0

    def rejoin(self, frame: BgrImage, ts: float) -> Fix:
        """A fresh look between commands: no odometry (the frames are not consecutive).
        A dot far from the estimate means someone moved the animal: start from the dot."""
        dot = sight.minimap_dot(frame, self.prof)
        dotw = self.world.dot_to_world(dot) if dot is not None else None
        if dotw is not None and (
            self.pos is None or math.dist(self.pos, dotw) > self.cfg.dot_rejoin_px
        ):
            self.pos = dotw
        self._gray = sight.masked_gray(frame, self.prof, self.cfg.odom_scale, self.cfg.odom_sigma)
        self._ts = ts
        self._off_n = 0
        off = math.dist(self.pos, dotw) if (self.pos is not None and dotw is not None) else 0.0
        self.last = Fix(self.pos, dotw, (0.0, 0.0), 0.0, "dot" if dotw else "none", off)
        return self.last

    def update(self, frame: BgrImage, ts: float, *, moving: bool = True) -> Fix:
        """The next consecutive frame. ``moving`` False skips the odometry (no key held:
        nothing should have moved, and a moth crossing is not the animal walking)."""
        cfg = self.cfg
        dot = sight.minimap_dot(frame, self.prof)
        dotw = self.world.dot_to_world(dot) if dot is not None else None
        gray = sight.masked_gray(frame, self.prof, cfg.odom_scale, cfg.odom_sigma)
        moved: Point = (0.0, 0.0)
        resp = 0.0
        if moving and self._gray is not None and self._ts is not None and self.pos is not None:
            dx, dy, resp = sight.scene_shift(self._gray, gray, cfg.odom_scale)
            dt = max(ts - self._ts, 1e-3)
            limit = cfg.odom_max_px_s * dt + cfg.grid_px
            if resp >= cfg.odom_min_response and math.hypot(dx, dy) <= limit:
                moved = (-dx, -dy)
        source = "none"
        if self.pos is None:
            self.pos = dotw
            source = "dot" if dotw else "none"
        else:
            self.pos = (self.pos[0] + moved[0], self.pos[1] + moved[1])
            source = "odometry"
            if dotw is not None:
                off = math.dist(self.pos, dotw)
                if off > cfg.dot_reset_px:
                    self._off_n += 1
                    if self._off_n >= cfg.dot_reset_n:
                        self.pos, self._off_n, source = dotw, 0, "dot"
                else:
                    self._off_n = 0
                    g = cfg.dot_gain
                    self.pos = (
                        self.pos[0] + g * (dotw[0] - self.pos[0]),
                        self.pos[1] + g * (dotw[1] - self.pos[1]),
                    )
                    source = "fused"
        self._gray, self._ts = gray, ts
        off = math.dist(self.pos, dotw) if (self.pos is not None and dotw is not None) else 0.0
        self.last = Fix(self.pos, dotw, moved, resp, source, off)
        return self.last
