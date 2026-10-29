"""Route farmer on the synthetic adventure: a recorded run replayed in a world where the animal
starts off its recorded spot and walks slower, with William taking over mid-way."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from jamcrammer.core.config import RouteConfig
from jamcrammer.core.events import EventBus
from jamcrammer.core.paths import Paths
from jamcrammer.modes.adventures import sight
from jamcrammer.modes.adventures.replay import FixLog, RouteRunner
from jamcrammer.modes.adventures.route import Route, build_route, validate_route
from tests.synthetic import adventure as synth

CFG = RouteConfig()


class SimInput:
    def __init__(self, world: synth.World) -> None:
        self.w = world

    def key_down(self, key: str) -> None:
        self.w.keys.add(key)

    def key_up(self, key: str) -> None:
        self.w.keys.discard(key)

    def click(
        self, cx: int, cy: int, *, button: Any = "left", verify: Any = None, retries: Any = None
    ) -> bool:
        for _ in range((retries or 0) + 1):
            self.w.click(cx, cy)
            self.w.tick(0.1)
            if verify is None or verify():
                return True
        return False

    def drag(self, x0: int, y0: int, x1: int, y1: int, steps: int = 12, hold_ms: int = 60) -> bool:
        self.w.tick(0.3)
        self.w.drag(x0, y0, x1, y1)
        return True

    def move(self, cx: int, cy: int) -> bool:
        return True


def _x_template() -> np.ndarray:
    img = cv2.imread(str(Paths().assets / "templates" / "adventures" / "dialog_x.png"))
    assert img is not None
    return np.asarray(img, np.uint8)


@pytest.fixture(scope="module")
def route(tmp_path_factory: pytest.TempPathFactory) -> Route:
    session = tmp_path_factory.mktemp("sess") / "20990101_000000_synthetic"
    world = synth.World(synth.profile())
    synth.layout(world)
    synth.record(session, world)
    assert all(th.done for th in world.things), world.log  # the scripted run itself works
    return build_route(session, "synthetic", synth.profile(), CFG, _x_template())


def _runner(world: synth.World, route: Route, events: list[Any], **kw: Any) -> RouteRunner:
    bus = EventBus()
    bus.subscribe("*", events.append)
    return RouteRunner(
        route,
        synth.profile(),
        CFG,
        SimInput(world),
        world.image,
        dialog_x=_x_template(),
        bus=bus,
        clock=lambda: world.t,
        sleep=world.tick,
        cursor=lambda: int(world.held_item),
        **kw,
    )


def test_the_route_holds_clicks_items_groups_and_the_dot(route: Route) -> None:
    kinds = [s.kind for s in route.steps]
    assert kinds.count("click") >= 5 and kinds.count("drag") == 1
    zones = {s.zone for s in route.steps if s.kind != "key"}
    assert {"world", "items", "dialog"} <= zones
    assert sum(s.items_change for s in route.steps) >= 3  # two pickups, two uses
    assert sum(f.dot is not None for f in route.frames) > 0.9 * len(route.frames)
    assert 200 < route.walk_px_s < 320, route.walk_px_s  # scene speed ~260 px/s
    acc, n = validate_route(route, CFG)
    assert n > 10 and acc >= 0.95, (acc, n)


def test_dot_and_scene_shift_point_the_right_way() -> None:
    """Content moved right in the live view means the animal is left of its spot: walk right."""
    prof = synth.profile()
    a = synth.World(prof)
    b = synth.World(prof, px=a.px - 120, py=a.py + 60)  # animal left and below
    ga = sight.masked_gray(a.image(), prof, CFG.align_scale, CFG.highpass_sigma)
    gb = sight.masked_gray(b.image(), prof, CFG.align_scale, CFG.highpass_sigma)
    dx, dy, resp = sight.scene_shift(ga, gb, CFG.align_scale)
    assert resp >= CFG.scene_min_response
    assert abs(dx - 120) < 12 and abs(dy + 60) < 12, (dx, dy)
    da, db = sight.minimap_dot(a.image(), prof), sight.minimap_dot(b.image(), prof)
    assert da is not None and db is not None and db[0] < da[0] and db[1] > da[1]


def test_replay_recovers_an_offset_start_and_a_slower_animal(route: Route) -> None:
    world = synth.World(synth.profile(), px=1200 + 140, py=1100 + 70, speed=240.0)
    synth.layout(world)
    events: list[Any] = []
    runner = _runner(world, route, events)
    runner.run(lambda: world.t > 120)
    types = [e.type for e in events]
    assert runner.state == "DONE", (runner.state, world.log, runner.stats.as_dict())
    assert all(th.done for th in world.things), (world.log, runner.stats.as_dict())
    assert runner.stats.course_fixes + runner.stats.warps >= 1
    assert "adventure.dialog_closed" in types and runner.stats.helps == 0


class FakePresence:
    """William grabs the keyboard between t0 and t1 and walks the animal 300 px right."""

    def __init__(self, world: synth.World, t0: float, t1: float) -> None:
        self.w, self.t0, self.t1 = world, t0, t1
        self.done = False

    def user_active(self) -> bool:
        active = self.t0 <= self.w.t < self.t1
        if active:
            self.w.px += 300 * 0.02 / (self.t1 - self.t0) * 10
        return active

    def seconds_since_user_input(self) -> float:
        return max(0.0, self.w.t - self.t1) if self.w.t >= self.t0 else 99.0


def test_william_takes_over_and_it_continues_where_he_left_it(route: Route, tmp_path: Path) -> None:
    world = synth.World(synth.profile(), speed=260.0)
    synth.layout(world)
    events: list[Any] = []
    fixes = FixLog(tmp_path / "fixes.jsonl")
    runner = _runner(world, route, events, presence=FakePresence(world, 6.0, 7.0), fixes=fixes)
    runner.run(lambda: world.t > 140)
    types = [e.type for e in events]
    assert "adventure.user_took_over" in types and "adventure.user_resumed" in types
    assert runner.state == "DONE" and all(th.done for th in world.things), world.log
    assert any(e["reason"] == "user" for e in fixes.entries)
    assert (tmp_path / "fixes.jsonl").exists()


def test_it_waits_while_the_level_is_still_loading(route: Route) -> None:
    """The recording starts in the level; live it is still a black loading screen for 3 s."""
    world = synth.World(synth.profile(), loading_until=3.0)
    synth.layout(world)
    events: list[Any] = []
    runner = _runner(world, route, events)
    runner.run(lambda: world.t > 120)
    types = [e.type for e in events]
    assert "adventure.scene_wait" in types and "adventure.scene_ready" in types
    assert runner.state == "DONE" and all(th.done for th in world.things), world.log
