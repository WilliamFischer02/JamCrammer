"""The world map, the localiser and the pilot's mapping commands on the synthetic adventure
(docs/MAPPING.md): coordinates, the level seed, save / load, planning around a block,
odometry fused with the dot, a probe that finds a wall, a scan that places a crystal, a mark,
a survey, the rendered map, the time budget, and the command server's round trip."""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from jamcrammer.cli import build_parser
from jamcrammer.core.config import AppConfig, WorldConfig
from jamcrammer.modes.adventures.localize import Localizer
from jamcrammer.modes.adventures.world import BLOCKED, FLOOR, FREE, World, world_dir
from jamcrammer.pilot import (
    Executor,
    PilotRefusedError,
    PilotSession,
    dispatch,
    send,
    serve,
    server_alive,
    server_paths,
)
from jamcrammer.vision.ocr import OcrResult
from tests.synthetic import adventure as synth
from tests.test_adventure_level import SCALE_X, SCALE_Y, _level
from tests.test_adventure_route import SimInput

PROF = synth.profile()
WCFG = WorldConfig(grid_px=14, mosaic_scale=0.2, merge_px=80.0, vignette_px=600)
BANNER = "explore the darkness"


def _world(world: synth.World) -> World:
    return World.from_level(_level(world), WCFG)


def truth(world: synth.World, w: World) -> tuple[float, float]:
    """The animal's true position in the world model's px (the synthetic minimap is a
    little anisotropic; the model uses one scale)."""
    return (world.px * w.scale / SCALE_X, world.py * w.scale / SCALE_Y)


def _cfg(enabled: bool = True) -> AppConfig:
    return AppConfig.model_validate(
        {"pilot": {"enabled": enabled, "area": "moths"}, "adventures": {"world": WCFG.model_dump()}}
    )


def _executor(
    world: synth.World, tmp_path: Path, *, enabled: bool = True, session: bool = True
) -> tuple[Executor, World]:
    cfg = _cfg(enabled)
    w = _world(world)
    sess = (
        PilotSession(tmp_path / "sess", tmp_path / "sess.stop", "mystery_below", 0.0, 45.0, "test")
        if session
        else None
    )
    ex = Executor(
        cfg=cfg,
        prof=PROF,
        lvl=_level(world),
        world=w,
        grab=world.image,
        inp=SimInput(world),
        ocr=lambda _f: OcrResult(text="", lines=()),
        banner_ocr=lambda _f: BANNER,
        user_input_age=lambda: 99.0,
        out_dir=tmp_path / "pilot",
        world_dir=tmp_path / "world",
        session=sess,
        clock=lambda: world.t,
        wall=lambda: world.t,
        sleep=world.tick,
    )
    return ex, w


# --- the world model ---------------------------------------------------------------------


def test_world_coordinates_round_trip_and_the_level_seeds_it() -> None:
    world = synth.World(PROF)
    synth.layout(world)
    w = _world(world)
    mx, my = w.minimap_roi[0], w.minimap_roi[1]
    assert w.dot_to_world((mx, my)) == (0.0, 0.0)
    back = w.world_to_dot(w.dot_to_world((mx + 10.5, my + 7.25)))
    assert abs(back[0] - (mx + 10.5)) < 1e-6 and abs(back[1] - (my + 7.25)) < 1e-6
    pos = (1000.0, 800.0)
    screen = w.world_to_screen(pos, w.screen_to_world(pos, (1300.0, 400.0)))
    assert screen == (1300.0, 400.0)
    # the level's floor (all floor in the synthetic level) and its spots arrived
    a = w.area("moths")
    assert a.counts()["floor"] > 0.9 * a.grid.size
    kinds = sorted(o.kind for o in w.objects)
    assert kinds == ["crystal", "crystal", "fungus", "fungus"]
    th = world.things[1]
    o = w.nearest("moths", truth_at(th.x, th.y, w), "crystal")
    assert o is not None and o.dist(truth_at(th.x, th.y, w)) < 40 and o.source == "level"


def truth_at(x: float, y: float, w: World) -> tuple[float, float]:
    return (x * w.scale / SCALE_X, y * w.scale / SCALE_Y)


def test_the_world_saves_and_loads_its_layers(tmp_path: Path) -> None:
    world = synth.World(PROF)
    synth.layout(world)
    w = _world(world)
    w.mark_track("moths", (500.0, 500.0))
    col = w.add_collision("moths", (700.0, 500.0), (1, 0), ts=3.0, note="a wall")
    o = w.add_object("rock", "moths", (900.0, 900.0), source="claude", confidence=0.9, note="big")
    w.observe("moths", (1200.0, 1100.0), world.image(), PROF.items("hud_rois"))
    w.note("the start room", "moths", (1200.0, 1100.0), 1.0)
    w.save(tmp_path)
    assert not w.dirty
    back = World.load(tmp_path, WCFG)
    assert back is not None
    a, b = w.area("moths"), back.area("moths")
    assert np.array_equal(a.grid, b.grid)
    assert b.state((500.0, 500.0)) == FREE
    assert b.state((700.0 + WCFG.grid_px, 500.0)) == BLOCKED
    assert np.array_equal(a.mosaic, b.mosaic) and np.array_equal(a.weight, b.weight)
    assert b.observed() > 0.01
    assert [c.note for c in back.collisions] == [col.note]
    assert back.objects[o.id].kind == "rock" and back.objects[o.id].note == "big"
    assert back.notes[0]["text"] == "the start room"
    assert World.load(tmp_path / "nothing", WCFG) is None


def test_objects_merge_by_kind_and_distance() -> None:
    world = synth.World(PROF)
    w = _world(world)
    a = w.add_object("rock", "moths", (100.0, 100.0), source="scan", confidence=0.5)
    b = w.add_object(
        "rock", "moths", (130.0, 100.0), source="claude", confidence=0.9, status="cracked"
    )
    assert a is b and a.seen == 2 and a.status == "cracked" and a.source == "claude"
    assert 100.0 < a.x < 130.0
    c = w.add_object("rock", "moths", (400.0, 100.0), source="scan", confidence=0.5)
    assert c is not a
    d = w.add_object("fungus", "moths", (100.0, 100.0), source="scan", confidence=0.5)
    assert d is not a  # another kind at the same place is another thing
    w.forget(c.id)
    assert w.near("moths", (400.0, 100.0), 50) == []


def test_a_plan_goes_around_blocked_cells() -> None:
    world = synth.World(PROF)
    w = _world(world)
    a = w.area("moths")
    g = WCFG.grid_px
    # a vertical wall of blocked cells between start and goal, with a gap far below
    for cy in range(0, 40):
        a.set_state((20, cy), BLOCKED)
    path = w.plan("moths", (10 * g, 10 * g), (30 * g, 10 * g))
    assert path is not None
    cells = [a.cell(p) for p in path]
    assert all(a.grid[c[1], c[0]] != BLOCKED for c in cells)
    assert max(c[1] for c in cells) >= 40  # it went round the end of the wall
    for cy in range(40, a.grid.shape[0]):
        a.set_state((20, cy), BLOCKED)
    assert w.plan("moths", (10 * g, 10 * g), (30 * g, 10 * g)) is None
    assert w.unblock("moths", (20 * g, 10 * g)) >= 1  # Claude: no wall here after all
    assert w.plan("moths", (10 * g, 10 * g), (30 * g, 10 * g)) is not None


def test_the_rendered_map_has_the_things_and_the_crop(tmp_path: Path) -> None:
    world = synth.World(PROF)
    synth.layout(world)
    w = _world(world)
    w.observe("moths", truth(world, w), world.image(), PROF.items("hud_rois"))
    img = w.render("moths", zoom=0.1, pos=truth(world, w))
    assert img.shape[0] == int(w.size[1] * 0.1) and img.shape[1] == int(w.size[0] * 0.1)
    assert int(np.max(img)) > 0
    crop = w.render("moths", zoom=0.5, crop=(1000, 900, 600, 400))
    assert crop.shape == (200, 300, 3)
    # the mosaic landed where the animal is: the centre of the crop is textured, not flat
    centre = crop[80:120, 130:170]
    assert centre.std() > 5


# --- the localiser ------------------------------------------------------------------------


def test_odometry_tracks_a_walk_and_the_dot_keeps_it_honest() -> None:
    world = synth.World(PROF)
    w = _world(world)
    loc = Localizer(w, PROF, WCFG)
    loc.rejoin(world.image(), world.t)
    assert loc.pos is not None and math.dist(loc.pos, truth(world, w)) < 30
    world.keys.add("right")
    errors = []
    for _ in range(20):
        world.tick(0.1)
        fix = loc.update(world.image(), world.t, moving=True)
        assert fix.pos is not None
        errors.append(math.dist(fix.pos, truth(world, w)))
    world.keys.clear()
    assert fix.moved[0] > 15 and abs(fix.moved[1]) < 6, fix  # 26 px a tick to the right
    assert max(errors) < 45, errors
    # the odometry lost (teleport): the dot wins after dot_reset_n updates
    world.px += 900
    for _ in range(WCFG.dot_reset_n + 1):
        world.tick(0.1)
        fix = loc.update(world.image(), world.t, moving=True)
    assert fix.pos is not None
    assert fix.source == "dot" or math.dist(fix.pos, truth(world, w)) < 60, fix
    # between commands a far dot means someone moved the animal
    world.px -= 400
    fix = loc.rejoin(world.image(), world.t)
    assert fix.pos is not None and math.dist(fix.pos, truth(world, w)) < 30


# --- the pilot's commands ----------------------------------------------------------------


def test_a_probe_walks_into_a_wall_and_maps_it(tmp_path: Path) -> None:
    world = synth.World(PROF)
    ex, w = _executor(world, tmp_path)
    world.walls = [(int(world.px + 300), int(world.py - 120), 40, 240)]
    out = ex.probe("right", 2.5)
    assert out["reason"] == "collision", out
    assert 230 < out["travelled"] < 360, out
    assert "collision" in out and not world.keys
    cx, cy = out["collision"]["at"]
    tx, ty = truth(world, w)
    assert abs(cx - tx) < 40 and abs(cy - ty) < 40
    a = w.area("moths")
    assert a.state((cx + WCFG.grid_px, cy)) == BLOCKED
    assert a.state((cx - 100, cy)) == FREE  # the walk itself is free floor now
    assert (tmp_path / "world" / "world.json").exists()  # saved after the command
    # open floor: the probe runs its time out
    out = ex.probe("down", 1.0)
    assert out["reason"] == "done" and out["travelled"] > 200
    # a survey probes every direction and comes back
    out = ex.survey(0.6, 4)
    assert len(out["legs"]) == 4 and all(leg["back"]["residual"] < 80 for leg in out["legs"])


def test_a_scan_places_what_it_sees_and_a_mark_places_what_claude_says(tmp_path: Path) -> None:
    world = synth.World(PROF)
    synth.layout(world)
    world.px = 1800.0  # crystal 1 (2200, 1100) is 400 px to the right: on screen
    ex, w = _executor(world, tmp_path)
    out = ex.scan()
    found = [f for f in out["found"] if f["kind"] == "crystal"]
    assert found and found[0]["status"] == "unlit", out
    tx, ty = truth_at(2200, 1100, w)
    assert math.dist(found[0]["world"], (tx, ty)) < 60, (found, (tx, ty))
    assert found[0]["merged"]  # the level already knew this crystal: one thing, seen twice
    o = w.objects[found[0]["id"]]
    assert o.seen >= 2 and o.status == "unlit"
    marked = ex.mark("rock", 1200.0, 500.0, status="cracked", note="by the first crystal")
    assert marked["kind"] == "rock" and marked["status"] == "cracked"
    mx, my = w.screen_to_world(truth(world, w), (1200.0, 500.0))
    assert math.dist(marked["world"], (mx, my)) < 30
    with pytest.raises(PilotRefusedError):
        ex.mark("dragon", None, None)
    here = ex.where()
    ids = {t["id"] for t in here["things"]}
    assert marked["id"] in ids and found[0]["id"] in ids
    assert here["cell"] in ("free", "floor")
    mp = ex.map(None, None, 0.1, True)
    assert Path(mp["path"]).exists() and any(t["kind"] == "rock" for t in mp["things"])
    gone = ex.forget(marked["id"])
    assert gone["gone"] and marked["id"] not in {t["id"] for t in ex.where()["things"]}


def test_look_and_key_report_the_position_and_the_switch_and_budget_hold(tmp_path: Path) -> None:
    world = synth.World(PROF)
    synth.layout(world)
    ex, w = _executor(world, tmp_path)
    seen = ex.look()
    assert seen["area"] == "moths" and seen["pos"]["pos"] is not None and seen["mm"] is not None
    assert seen["phase"] == "start"
    out = ex.key("right", 1.0)
    assert out["travelled"] > 200 and out["reason"] == "done" and not world.keys
    off, _ = _executor(world, tmp_path, enabled=False)
    with pytest.raises(PilotRefusedError):
        off.key("right", 0.5)
    assert off.look()["area"] == "moths"  # looking is always allowed
    no_session, _ = _executor(world, tmp_path, session=False)
    with pytest.raises(PilotRefusedError):
        no_session.probe("right", 0.5)
    assert ex.session is not None
    ex.session.budget_min = 0.001
    world.t += 60.0
    with pytest.raises(PilotRefusedError):
        ex.probe("right", 0.5)
    note = ex.note("a torch by the door")
    assert Path(note["log"]).exists() and w.notes[-1]["text"] == "a torch by the door"


def test_dispatch_runs_parsed_commands(tmp_path: Path) -> None:
    world = synth.World(PROF)
    synth.layout(world)
    ex, _w = _executor(world, tmp_path)
    parser = build_parser()
    out = dispatch(ex, parser.parse_args(["pilot", "where"]))
    assert out["area"] == "moths"
    out = dispatch(ex, parser.parse_args(["pilot", "probe", "left", "--max", "0.5"]))
    assert out["cmd"] == "probe" and out["direction"] == "left"
    out = dispatch(
        ex, parser.parse_args(["pilot", "map", "--crop", "0", "0", "800", "600", "--zoom", "0.25"])
    )
    assert out["size"] == [200, 150]
    args = parser.parse_args(
        ["pilot", "mark", "torch", "900", "400", "--status", "unlit", "--note", "left", "wall"]
    )
    out = dispatch(ex, args)
    assert out["kind"] == "torch" and out["status"] == "unlit"
    args = parser.parse_args(["pilot", "start", "--budget", "20", "--goal", "map", "the", "hall"])
    assert args.budget == 20 and args.goal == ["map", "the", "hall"]
    args = parser.parse_args(["pilot", "goto", "100", "200", "--world", "--tol", "3"])
    assert args.world and args.tol == 3


# --- the server ---------------------------------------------------------------------------


def test_the_server_answers_command_files(tmp_path: Path) -> None:
    data = tmp_path / "data"
    assert send(data, ["look"], 1.0) is None  # no server: the caller runs it itself
    served: list[list[str]] = []

    def run(argv: list[str]) -> dict[str, Any]:
        served.append(argv)
        if argv[0] == "boom":
            raise PilotRefusedError("no")
        return {"echo": argv}

    t = threading.Thread(target=serve, args=(data, run), kwargs={"poll_s": 0.02}, daemon=True)
    t.start()
    for _ in range(100):
        if server_alive(data):
            break
        time.sleep(0.02)
    assert server_alive(data)
    out = send(data, ["probe", "right", "--max", "1"], 5.0)
    assert out == {"echo": ["probe", "right", "--max", "1"]}
    assert send(data, ["boom"], 5.0) == {"refused": "no"}
    server_paths(data)["stop"].write_text("stop", encoding="utf-8")
    t.join(timeout=5.0)
    assert not t.is_alive() and served == [["probe", "right", "--max", "1"], ["boom"]]
    assert not server_alive(data)
    pointer = tmp_path / "current.json"
    PilotSession(tmp_path / "s", tmp_path / "s.stop", "mystery_below", 1.0, 30.0, "a goal").save(
        pointer
    )
    back = PilotSession.load(pointer)
    assert (
        back is not None and back.budget_min == 30.0 and back.goal == "a goal" and not back.stopped
    )
    assert json.loads(pointer.read_text(encoding="utf-8"))["goal"] == "a goal"


def test_the_real_level_seeds_a_world_with_the_moth_chamber() -> None:
    from jamcrammer.core.paths import Paths
    from jamcrammer.modes.adventures.level import Level, level_dir

    lvl = Level.load(level_dir(Paths().profiles, "mystery_below"))
    assert lvl is not None
    w = World.from_level(lvl)
    a = w.area("moths")
    assert a.counts()["floor"] > 1000 and a.counts()["blocked"] == 0
    kinds = {o.kind for o in w.objects}
    assert {"crystal", "fungus", "clickable"} <= kinds
    assert len(w.near("moths", w.dot_to_world((1548.7, 860.2)), 200, "crystal")) >= 1
    assert w.area("moths").state(w.dot_to_world((1548.7, 860.2))) in (FLOOR, FREE)
    assert world_dir(Paths().profiles, "mystery_below").name == "world"
