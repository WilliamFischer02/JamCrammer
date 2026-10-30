"""Walking by the minimap: A* on an area's floor mask (minimap pixels), the live dot as the
position, arrow keys as the actuator (up = dot -y, right = dot +x, both = the diagonal,
measured on the recordings: 5.6 px/s straight, 5.3/3.4 on the diagonal). A held key with a
still dot means a wall the mask does not show: the cells ahead are marked blocked and the
path is planned again."""

from __future__ import annotations

import heapq
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from jamcrammer.core.config import LevelConfig
from jamcrammer.core.profiles import Profile
from jamcrammer.modes.adventures import sight

BgrImage = npt.NDArray[np.uint8]
Mask = npt.NDArray[np.bool_]
Point = tuple[float, float]
Cell = tuple[int, int]
MOVE_KEYS = ("up", "down", "left", "right")
_NEIGHBOURS = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0)] + [
    (dx, dy, 1.4142) for dx in (-1, 1) for dy in (-1, 1)
]


class Floor:
    """A walkable mask over the minimap ROI, with blocked cells learned live."""

    def __init__(self, mask: Mask, origin: tuple[int, int]) -> None:
        self.mask = mask.copy()
        self.ox, self.oy = origin
        self.learned: list[Cell] = []

    def cell(self, p: Point) -> Cell:
        return (round(p[0]) - self.ox, round(p[1]) - self.oy)

    def point(self, c: Cell) -> Point:
        return (c[0] + self.ox, c[1] + self.oy)

    def inside(self, c: Cell) -> bool:
        return bool(0 <= c[1] < self.mask.shape[0] and 0 <= c[0] < self.mask.shape[1])

    def walkable(self, c: Cell) -> bool:
        return bool(self.inside(c) and self.mask[c[1], c[0]])

    def block(self, c: Cell) -> None:
        if self.inside(c):
            self.mask[c[1], c[0]] = False
            self.learned.append(c)

    def snap(self, c: Cell, radius: int) -> Cell | None:
        """The nearest walkable cell within ``radius``."""
        if self.walkable(c):
            return c
        best: tuple[float, Cell] | None = None
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                q = (c[0] + dx, c[1] + dy)
                d = float(np.hypot(dx, dy))
                if d <= radius and self.walkable(q) and (best is None or d < best[0]):
                    best = (d, q)
        return best[1] if best else None


def _dist(a: Cell, b: Cell) -> float:
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


def plan_path(floor: Floor, a: Cell, b: Cell) -> list[Cell] | None:
    """A* over 8-connected walkable cells, ``a`` to ``b`` inclusive; None when unreachable."""
    if not floor.walkable(a) or not floor.walkable(b):
        return None
    best: dict[Cell, float] = {a: 0.0}
    prev: dict[Cell, Cell] = {}
    heap: list[tuple[float, float, Cell]] = [(_dist(a, b), 0.0, a)]
    while heap:
        _f, g, c = heapq.heappop(heap)
        if c == b:
            path = [c]
            while c in prev:
                c = prev[c]
                path.append(c)
            return path[::-1]
        if g > best.get(c, float("inf")):
            continue
        for dx, dy, cost in _NEIGHBOURS:
            n = (c[0] + dx, c[1] + dy)
            if not floor.walkable(n):
                continue
            corner = floor.walkable((c[0] + dx, c[1])) or floor.walkable((c[0], c[1] + dy))
            if dx and dy and not corner:
                continue  # no squeezing between two diagonal corners
            ng = g + cost
            if ng < best.get(n, float("inf")):
                best[n], prev[n] = ng, c
                heapq.heappush(heap, (ng + _dist(n, b), ng, n))
    return None


def keys_toward(dx: float, dy: float, dead: float = 0.35) -> set[str]:
    """Arrow keys for a minimap direction: one axis, or both when the other is at least
    ``dead`` of it (the diagonal)."""
    keys: set[str] = set()
    ax, ay = abs(dx), abs(dy)
    if ax >= dead * ay and ax > 0:
        keys.add("right" if dx > 0 else "left")
    if ay >= dead * ax and ay > 0:
        keys.add("down" if dy > 0 else "up")
    return keys


@dataclass
class NavResult:
    ok: bool
    reason: str  # arrived | timeout | no_path | no_dot | blocked | stopped
    dist: float = 0.0
    blocks: int = 0
    path_len: int = 0
    log: list[str] = field(default_factory=list)


class Navigator:
    def __init__(
        self,
        floor: Floor,
        prof: Profile,
        cfg: LevelConfig,
        *,
        key_down: Callable[[str], None],
        key_up: Callable[[str], None],
        grab: Callable[[], BgrImage],
        clock: Callable[[], float],
        sleep: Callable[[float], None],
        tick_s: float = 0.05,
        hook: Callable[[BgrImage], bool] | None = None,
    ) -> None:
        """``hook`` sees every frame while walking; True means it acted (closed a dialog
        that was holding the animal), so the stuck timer starts over."""
        self.floor, self.prof, self.cfg = floor, prof, cfg
        self._down, self._up, self._grab = key_down, key_up, grab
        self._clock, self._sleep, self._tick = clock, sleep, tick_s
        self._hook = hook
        self.held: set[str] = set()
        self.dot: Point | None = None
        self.frame: BgrImage | None = None
        # one go_to's working state
        self._path: list[Cell] | None = None
        self._goal: Cell | None = None
        self._still_since: float | None = None
        self._last_dot: Point | None = None
        self._no_dot_since: float | None = None

    def release(self) -> None:
        for k in sorted(self.held):
            self._up(k)
        self.held.clear()

    def _hold(self, keys: set[str]) -> None:
        for k in sorted(self.held - keys):
            self._up(k)
        for k in sorted(keys - self.held):
            self._down(k)
        self.held = set(keys)

    def see(self) -> Point | None:
        self.frame = self._grab()
        self.dot = sight.minimap_dot(self.frame, self.prof)
        return self.dot

    def go_to(
        self,
        target: Point,
        *,
        stop: Callable[[], bool] = lambda: False,
        tol: float | None = None,
        timeout: float | None = None,
    ) -> NavResult:
        """Walk until the dot is within ``tol`` of ``target``."""
        c = self.cfg
        tol = c.nav_tol_px if tol is None else tol
        deadline = self._clock() + (c.nav_timeout_s if timeout is None else timeout)
        res = NavResult(False, "timeout")
        self._goal = self.floor.snap(self.floor.cell(target), c.nav_snap_px)
        self._path = self._still_since = self._last_dot = self._no_dot_since = None
        if self._goal is None:
            res.reason = "no_path"
            return res
        try:
            while not stop() and self._clock() < deadline:
                now = self._clock()
                dot = self.see()
                if self._hook is not None and self.frame is not None and self._hook(self.frame):
                    self._still_since = self._last_dot = None  # a dialog held us: not a wall
                    continue
                if dot is None:
                    if self._wait_dot(now):
                        continue
                    res.reason = "no_dot"
                    return res
                res.dist = float(np.hypot(dot[0] - target[0], dot[1] - target[1]))
                if res.dist <= tol:
                    res.ok, res.reason = True, "arrived"
                    return res
                if self._stuck(dot, now):
                    res.blocks += 1
                    res.log.append(f"blocked at {dot[0]:.0f},{dot[1]:.0f}")
                    if res.blocks > c.nav_max_blocks:
                        res.reason = "blocked"
                        return res
                    continue
                if self._path is None and not self._plan(dot, res):
                    res.reason = "no_path"
                    return res
                aim = self._aim(dot)
                self._hold(keys_toward(aim[0] - dot[0], aim[1] - dot[1]))
                self._sleep(self._tick)
            res.reason = "stopped" if stop() else "timeout"
            return res
        finally:
            self.release()

    def _wait_dot(self, now: float) -> bool:
        """No dot (a dialog, loading): keys off; True while still worth waiting."""
        self._no_dot_since = self._no_dot_since or now
        self._hold(set())
        if now - self._no_dot_since >= self.cfg.nav_dot_wait_s:
            return False
        self._sleep(self._tick)
        return True

    def _stuck(self, dot: Point, now: float) -> bool:
        """Keys held and the dot within ``nav_stuck_px`` of where it was when the still
        period began, for ``nav_stuck_s``: learn the wall, drop the path. (The dot moves a
        quarter pixel per tick, so it is measured against the anchor, never the last tick.)"""
        self._no_dot_since = None
        if not self.held:
            self._still_since = self._last_dot = None
            return False
        if self._last_dot is None or self._still_since is None:
            self._still_since, self._last_dot = now, dot
            return False
        if (
            float(np.hypot(dot[0] - self._last_dot[0], dot[1] - self._last_dot[1]))
            >= self.cfg.nav_stuck_px
        ):
            self._still_since, self._last_dot = now, dot  # moving: a new anchor
            return False
        if now - self._still_since < self.cfg.nav_stuck_s:
            return False
        self._learn_block(dot)
        self._hold(set())
        self._path = self._still_since = self._last_dot = None
        return True

    def _plan(self, dot: Point, res: NavResult) -> bool:
        """A* on the floor; with no path (the mask has gaps at doors, the dot is off the
        floor) a straight line to the goal, so the walls get learned instead of giving up."""
        if self._goal is None:
            return False
        start = self.floor.snap(self.floor.cell(dot), self.cfg.nav_snap_px)
        path = plan_path(self.floor, start, self._goal) if start is not None else None
        if path is None:
            res.log.append("straight")
            path = [self.floor.cell(dot), self._goal]
        self._path = path
        res.path_len = len(path)
        return True

    def _aim(self, dot: Point) -> Point:
        """The path point ``nav_lookahead_px`` beyond the one nearest the dot."""
        pts = [self.floor.point(c) for c in self._path or []]
        k = int(np.argmin([np.hypot(p[0] - dot[0], p[1] - dot[1]) for p in pts]))
        for j in range(k, len(pts)):
            if np.hypot(pts[j][0] - dot[0], pts[j][1] - dot[1]) >= self.cfg.nav_lookahead_px:
                return pts[j]
        return pts[-1]

    def _learn_block(self, dot: Point) -> None:
        """Mark the cells just ahead in the held direction as walls."""
        dx = (1 if "right" in self.held else 0) - (1 if "left" in self.held else 0)
        dy = (1 if "down" in self.held else 0) - (1 if "up" in self.held else 0)
        here = self.floor.cell(dot)
        for step in (1, 2):
            ahead = (here[0] + dx * step, here[1] + dy * step)
            self.floor.block(ahead)
            if step == 1:  # a wall is wider than one cell: its neighbours across the heading
                self.floor.block((ahead[0] + dy, ahead[1] + dx))
                self.floor.block((ahead[0] - dy, ahead[1] - dx))
