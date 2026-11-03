"""A small synthetic adventure for the route farmer: a textured world bigger than the screen,
a camera that follows the player, a fixed minimap with the light-blue dot, walls, fungi that
give an item, crystals the item lights (click or drag from the slot), and a dialog with the
real blue X. Geometry comes from profiles/adventure.example.yaml.

``record`` plays a scripted William through it and writes a Learn-format session
(meta.jsonl + keyframes) so ``build_route`` can turn it into a route."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import Profile

BgrImage = npt.NDArray[np.uint8]
W, H = 1920, 1080
WORLD_W, WORLD_H = 4200, 2600
SPEED = 260.0  # px/s
DOT_BGR = (235, 200, 120)  # HSV ~ (100, 125, 235): inside dot_hsv
DIALOG_X = (1409, 454)
PIN_UP = 110  # the paw pin floats this far above a fungus in reach ...
PIN_REACH = 300  # ... when the animal is this close
_PAW: BgrImage | None = None


def paw_template() -> BgrImage:
    """The real paw pin, drawn over a fungus the animal is next to."""
    global _PAW  # noqa: PLW0603 - a cached asset
    if _PAW is None:
        img = cv2.imread(str(Paths().assets / "templates" / "adventures" / "paw_marker.png"))
        _PAW = np.asarray(img if img is not None else np.zeros((100, 80, 3)), np.uint8)
    return _PAW


def profile() -> Profile:
    return Profile.from_yaml(Paths().profiles / "adventure.example.yaml")


def _texture(seed: int) -> BgrImage:
    rng = np.random.default_rng(seed)
    img = np.full((WORLD_H, WORLD_W, 3), 70, np.uint8)
    # dull rock: luminance varies, saturation stays low, so only the things have colour
    for _ in range(2600):
        x, y = int(rng.integers(0, WORLD_W)), int(rng.integers(0, WORLD_H))
        r = int(rng.integers(6, 40))
        v = int(rng.integers(30, 190))  # never as bright as a lit crystal's glow
        c = tuple(int(np.clip(v + d, 0, 255)) for d in rng.integers(-18, 18, 3))
        cv2.circle(img, (x, y), r, c, -1)
    for _ in range(500):
        p0 = (int(rng.integers(0, WORLD_W)), int(rng.integers(0, WORLD_H)))
        p1 = (p0[0] + int(rng.integers(-120, 120)), p0[1] + int(rng.integers(-120, 120)))
        v = int(rng.integers(0, 190))
        c = tuple(int(np.clip(v + d, 0, 255)) for d in rng.integers(-18, 18, 3))
        cv2.line(img, p0, p1, c, int(rng.integers(2, 6)))
    return img


@dataclass
class Thing:
    kind: str  # fungus | crystal
    x: int
    y: int
    done: bool = False


@dataclass
class World:
    prof: Profile
    seed: int = 1
    px: float = 1200.0
    py: float = 1100.0
    speed: float = SPEED
    t: float = 0.0
    things: list[Thing] = field(default_factory=list)
    walls: list[tuple[int, int, int, int]] = field(default_factory=list)
    items: int = 0
    held_item: bool = False
    dialog_at: float | None = None  # opens at this time
    dialog_open: bool = False
    dialog_shown: bool = False
    loading_until: float = 0.0  # a black loading screen until then (inputs do nothing)
    keys: set[str] = field(default_factory=set)
    walk_target: tuple[float, float] | None = None  # a floor click: the game walks there
    log: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.base = _texture(self.seed)
        _mx, _my, mw, mh = self.prof.roi("minimap_roi")
        self._mm = cv2.resize(self.base, (mw, mh), interpolation=cv2.INTER_AREA) // 3

    # --- physics -------------------------------------------------------------------------

    def tick(self, dt: float) -> None:
        steps = max(1, int(dt / 0.01))
        for _ in range(steps):
            self.t += dt / steps
            if self.dialog_at is not None and self.t >= self.dialog_at:
                self.dialog_open, self.dialog_at = True, None
            if self.dialog_open or self.t < self.loading_until:
                continue
            vx = (("right" in self.keys) - ("left" in self.keys)) * self.speed
            vy = (("down" in self.keys) - ("up" in self.keys)) * self.speed
            if not self.keys and self.walk_target is not None:
                dx, dy = self.walk_target[0] - self.px, self.walk_target[1] - self.py
                d = float(np.hypot(dx, dy))
                if d < 4.0:
                    self.walk_target = None
                else:
                    vx, vy = self.speed * dx / d, self.speed * dy / d
            nx, ny = self.px + vx * dt / steps, self.py + vy * dt / steps
            moved = False
            if not self._blocked(nx, self.py):
                self.px, moved = nx, True
            if not self._blocked(self.px, ny):
                self.py, moved = ny, True
            if not moved and not self.keys:
                self.walk_target = None  # a wall: the walk ends short

    def _blocked(self, x: float, y: float) -> bool:
        return any(wx <= x < wx + ww and wy <= y < wy + wh for wx, wy, ww, wh in self.walls)

    # --- input ---------------------------------------------------------------------------

    def to_world(self, x: int, y: int) -> tuple[float, float]:
        return (self.px + x - W / 2, self.py + y - H / 2)

    def click(self, x: int, y: int) -> None:
        if self.dialog_open:
            if abs(x - DIALOG_X[0]) <= 30 and abs(y - DIALOG_X[1]) <= 30:
                self.dialog_open = False
                self.log.append(f"{self.t:.2f} dialog closed")
            return
        ix, iy, iw, ih = self.prof.roi("items_roi")
        if ix <= x < ix + iw and iy <= y < iy + ih:
            self.held_item = self.items > 0
            return
        wx, wy = self.to_world(x, y)
        for th in self.things:
            if th.done:
                continue
            on_pin = (
                self._pin_shows(th) and abs(wx - th.x) <= 40 and abs(wy - (th.y - PIN_UP)) <= 50
            )
            if not on_pin and (abs(wx - th.x) > 40 or abs(wy - th.y) > 40):
                continue
            if th.kind == "fungus" and not on_pin:
                continue  # the fungus itself gives nothing: its pin does
            if th.kind == "fungus":
                th.done = True
                self.items += 1
                self.log.append(f"{self.t:.2f} picked fungus")
                if not self.dialog_shown:  # the first pickup opens Cosmo's dialog
                    self.dialog_shown, self.dialog_at = True, self.t + 0.4
            elif self.held_item and self.items > 0:
                th.done = True
                self.items -= 1
                self.log.append(f"{self.t:.2f} lit crystal")
            self.held_item = False
            return
        self.held_item = False
        if 0 <= x < W and 0 <= y < H * 0.86:  # the floor: the game walks the animal there
            self.walk_target = (wx, wy)

    def _pin_shows(self, th: Thing) -> bool:
        near = float(np.hypot(th.x - self.px, th.y - self.py)) <= PIN_REACH
        return th.kind == "fungus" and not th.done and near

    def drag(self, x0: int, y0: int, x1: int, y1: int) -> None:
        ix, iy, iw, ih = self.prof.roi("items_roi")
        if ix <= x0 < ix + iw and iy <= y0 < iy + ih and self.items > 0:
            self.held_item = True
            self.click(x1, y1)

    # --- rendering -----------------------------------------------------------------------

    def image(self) -> BgrImage:
        if self.t < self.loading_until:
            return np.full((H, W, 3), 8, np.uint8)
        x0, y0 = int(self.px - W / 2), int(self.py - H / 2)
        img = np.full((H, W, 3), 0, np.uint8)
        sx0, sy0 = max(0, x0), max(0, y0)
        sx1, sy1 = min(WORLD_W, x0 + W), min(WORLD_H, y0 + H)
        if sx1 > sx0 and sy1 > sy0:
            img[sy0 - y0 : sy1 - y0, sx0 - x0 : sx1 - x0] = self.base[sy0:sy1, sx0:sx1]
        for th in self.things:
            c = (
                (250, 240, 200)  # pale cyan: lit
                if th.done
                else ((60, 220, 80) if th.kind == "fungus" else (130, 50, 15))  # green / navy
            )
            cx, cy = int(th.x - x0), int(th.y - y0)
            cv2.rectangle(img, (cx - 30, cy - 30), (cx + 30, cy + 30), c, -1)
            cv2.putText(img, th.kind[0].upper(), (cx - 12, cy + 12), 0, 1.2, (255, 255, 255), 3)
        for th in self.things:
            if self._pin_shows(th):
                paw = paw_template()
                ph, pw = paw.shape[:2]
                cx, cy = int(th.x - x0), int(th.y - y0 - PIN_UP)
                py, px = cy - ph // 2, cx - pw // 2
                if py >= 0 and py + ph <= H and px >= 0 and px + pw <= W:
                    img[py : py + ph, px : px + pw] = paw
        for wx, wy, ww, wh in self.walls:
            cv2.rectangle(img, (wx - x0, wy - y0), (wx - x0 + ww, wy - y0 + wh), (20, 20, 20), -1)
        cv2.circle(img, (W // 2, H // 2), 40, (0, 220, 255), -1)  # the animal
        cv2.rectangle(img, (0, 990), (W, H), (90, 90, 90), -1)  # bottom bar
        mx, my, mw, mh = self.prof.roi("minimap_roi")
        img[my : my + mh, mx : mx + mw] = self._mm
        dx = int(mx + self.px / WORLD_W * mw)
        dy = int(my + self.py / WORLD_H * mh)
        cv2.circle(img, (dx, dy), 7, DOT_BGR, -1)
        ix, iy, iw, ih = self.prof.roi("items_roi")
        cv2.rectangle(img, (ix, iy), (ix + iw, iy + ih), (40, 40, 40), -1)
        for k in range(self.items):
            cv2.rectangle(
                img,
                (ix + 10 + k * 40, iy + 20),
                (ix + 40 + k * 40, iy + ih - 20),
                (255, 255, 255),
                -1,
            )
        if self.dialog_open:
            cv2.rectangle(img, (560, 430), (1430, 700), (230, 230, 210), -1)
            tmpl = cv2.imread(str(Paths().assets / "templates" / "adventures" / "dialog_x.png"))
            if tmpl is not None:
                h, w = tmpl.shape[:2]
                img[
                    DIALOG_X[1] - h // 2 : DIALOG_X[1] - h // 2 + h,
                    DIALOG_X[0] - w // 2 : DIALOG_X[0] - w // 2 + w,
                ] = tmpl
        return img


def layout(world: World) -> None:
    """Two fungi and two crystals along a right-then-down walk."""
    world.things = [
        Thing("fungus", 1700, 1100),
        Thing("crystal", 2200, 1100),
        Thing("fungus", 2200, 1500),
        Thing("crystal", 1800, 1600),
    ]


def record(session: Path, world: World, fps: float = 5.0) -> None:
    """Play the scripted run: walk to each thing, click / drag it, close the dialog."""
    (session / "frames").mkdir(parents=True)
    meta: list[dict[str, object]] = [
        {
            "t": "session",
            "target": "synthetic",
            "started_ts": 0.0,
            "window": {"client_rect": [0, 0, W, H]},
        }
    ]
    t0 = 1_800_000_000.0
    nxt = 0.0
    n = 0

    def frame() -> None:
        nonlocal nxt, n
        while world.t >= nxt:
            n += 1
            name = f"frames/kf_{n:06d}.png"
            cv2.imwrite(str(session / name), world.image())
            meta.append({"t": "keyframe", "ts": t0 + world.t, "file": name})
            nxt += 1.0 / fps

    def wait(s: float) -> None:
        end = world.t + s
        while world.t < end:
            world.tick(0.02)
            frame()

    def hold(key: str, s: float) -> None:
        meta.append({"t": "input", "ts": t0 + world.t, "kind": "key", "event": "press", "key": key})
        world.keys.add(key)
        wait(s)
        world.keys.discard(key)
        meta.append(
            {"t": "input", "ts": t0 + world.t, "kind": "key", "event": "release", "key": key}
        )

    def click(x: int, y: int) -> None:
        for ev in ("press", "release"):
            meta.append(
                {
                    "t": "input",
                    "ts": t0 + world.t,
                    "kind": "mouse",
                    "event": ev,
                    "button": "left",
                    "cx": x,
                    "cy": y,
                    "inside": True,
                }
            )
            if ev == "press":
                wait(0.1)
        world.click(x, y)
        wait(0.6)

    def drag(x0: int, y0: int, x1: int, y1: int) -> None:
        meta.append(
            {
                "t": "input",
                "ts": t0 + world.t,
                "kind": "mouse",
                "event": "press",
                "button": "left",
                "cx": x0,
                "cy": y0,
                "inside": True,
            }
        )
        wait(0.5)
        meta.append(
            {
                "t": "input",
                "ts": t0 + world.t,
                "kind": "mouse",
                "event": "release",
                "button": "left",
                "cx": x1,
                "cy": y1,
                "inside": True,
            }
        )
        world.drag(x0, y0, x1, y1)
        wait(0.6)

    def screen(th: Thing) -> tuple[int, int]:
        return (int(th.x - world.px + W / 2), int(th.y - world.py + H / 2))

    frame()
    wait(1.0)
    hold("right", 1.6)  # fungus 1 in reach
    fx, fy = screen(world.things[0])
    click(fx, fy - PIN_UP)  # its paw pin
    wait(1.0)
    click(*DIALOG_X)
    hold("right", 1.8)
    slot = (world.prof.roi("items_roi")[0] + 30, world.prof.roi("items_roi")[1] + 60)
    sx, sy = screen(world.things[1])
    drag(slot[0], slot[1], sx, sy)
    hold("down", 1.5)
    fx, fy = screen(world.things[2])
    click(fx, fy - PIN_UP)
    hold("left", 1.5)
    hold("down", 0.4)
    click(*slot)
    click(*screen(world.things[3]))
    wait(1.0)
    meta.append(
        {
            "t": "end",
            "ended_ts": t0 + world.t,
            "stats": {"started_ts": t0, "ended_ts": t0 + world.t},
        }
    )
    (session / "meta.jsonl").write_text("".join(json.dumps(m) + "\n" for m in meta))
