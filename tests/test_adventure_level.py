"""The level framework on the synthetic adventure: walking by the minimap around a wall the
map does not show, the menu states from words, the item counters, the level model, and the
level runner lighting the crystals from a plan with William's fungi as the stock."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from jamcrammer.core.config import LevelConfig
from jamcrammer.core.events import EventBus
from jamcrammer.core.paths import Paths
from jamcrammer.modes.adventures import objects, sight
from jamcrammer.modes.adventures.inventory import read_counts
from jamcrammer.modes.adventures.level import (
    MYSTERY_BELOW_PHASES,
    Act,
    Level,
    Phase,
    Spot,
    level_dir,
)
from jamcrammer.modes.adventures.level_runner import LevelRunner
from jamcrammer.modes.adventures.nav import Floor, Navigator, keys_toward, plan_path
from jamcrammer.modes.adventures.opening import Opening, classify
from jamcrammer.vision.ocr import OcrLine, OcrResult, OcrWord
from tests.synthetic import adventure as synth
from tests.test_adventure_route import SimInput, _x_template

# the synthetic minimap px is 14 scene px (the game: 70): stop further from a spot, else the
# animal stands on the crystal and hides it
CFG = LevelConfig(approach_px=6.0)
PROF = synth.profile()
MX, MY, MW, MH = PROF.roi("minimap_roi")
SCALE_X, SCALE_Y = synth.WORLD_W / MW, synth.WORLD_H / MH


def mm_of(wx: float, wy: float) -> tuple[float, float]:
    """World point -> minimap stage point, as the synthetic minimap draws the dot."""
    return (MX + wx / SCALE_X, MY + wy / SCALE_Y)


def _level(world: synth.World) -> Level:
    lvl = Level(
        target="mystery_below",
        scale=(SCALE_X + SCALE_Y) / 2,
        avatar=(synth.W // 2, synth.H // 2),
        minimap_roi=(MX, MY, MW, MH),
        phases=[Phase(k, a, m, phrase) for phrase, k, a, m in MYSTERY_BELOW_PHASES],
    )
    lvl.floors["moths"] = np.ones((MH, MW), np.bool_)
    for th in world.things:
        x, y = mm_of(th.x, th.y)
        lvl.spots.append(
            Spot(len(lvl.spots), "crystal" if th.kind == "crystal" else "fungus", "moths", x, y)
        )
    return lvl


# --- navigation -------------------------------------------------------------------------


def test_keys_follow_the_minimap_axes() -> None:
    assert keys_toward(5, 0) == {"right"} and keys_toward(0, -5) == {"up"}
    assert keys_toward(4, 4) == {"right", "down"} and keys_toward(-4, 1) == {"left"}


def test_a_path_goes_around_a_known_wall() -> None:
    mask = np.ones((20, 20), np.bool_)
    mask[2:18, 10] = False
    floor = Floor(mask, (0, 0))
    path = plan_path(floor, (2, 10), (17, 10))
    assert path is not None and path[0] == (2, 10) and path[-1] == (17, 10)
    assert all(floor.walkable(c) for c in path) and len(path) > 16
    assert plan_path(floor, (2, 10), (10, 10)) is None


def test_it_walks_to_a_minimap_point_and_learns_an_unmapped_wall() -> None:
    world = synth.World(PROF)
    gx, gy = world.px + 900, world.py  # 900 scene px right: about 63 minimap px
    world.walls = [(int(world.px + 400), int(world.py - 40), 30, 80)]  # a short wall on the way
    nav = Navigator(
        Floor(np.ones((MH, MW), np.bool_), (MX, MY)),
        PROF,
        CFG,
        key_down=world.keys.add,
        key_up=world.keys.discard,
        grab=world.image,
        clock=lambda: world.t,
        sleep=world.tick,
    )
    res = nav.go_to(mm_of(gx, gy), tol=1.5)
    assert res.ok, (res, world.log)
    assert res.blocks >= 1 and nav.floor.learned, "the wall was never learned"
    assert abs(world.px - gx) < 1.5 * SCALE_X and abs(world.py - gy) < 1.5 * SCALE_Y
    assert not world.keys  # released


# --- what is on screen -----------------------------------------------------------------


def test_crystals_and_fungi_are_found_by_colour() -> None:
    world = synth.World(PROF)
    synth.layout(world)
    world.things[1].done = True  # the first crystal lit
    world.px, world.py = 1950.0, 1200.0  # fungus 1, crystal 1 (lit) and crystal 2 in view
    img = world.image()
    kinds = {(b.kind) for b in objects.crystals(img, PROF)}
    assert {"unlit", "lit"} <= kinds, kinds  # the random texture also has purple patches
    f = objects.fungi(img, PROF)
    assert len(f) == 2 and all(b.area > 1500 for b in f)
    near = objects.nearest(objects.crystals(img, PROF), (synth.W / 2, synth.H / 2), "unlit")
    assert near is not None and abs(near.x - (1800 - 1950 + synth.W / 2)) < 8  # crystal 2


def test_the_item_counters_read_both_slots() -> None:
    img = np.zeros((1080, 1920, 3), np.uint8)
    seen: list[tuple[int, int]] = []

    def ocr(crop: np.ndarray) -> str:
        seen.append(crop.shape[:2])
        return "2/3" if len(seen) == 1 else "garbage"

    c = read_counts(img, PROF, ocr, retry=lambda crop: "0 / 3")
    assert (c.blossoms, c.boomseeds, c.blossom_cap) == (2, 0, 3)
    assert len(seen) == 2


# --- the menus --------------------------------------------------------------------------


def _ocr(words: list[tuple[str, int, int]]) -> OcrResult:
    ws = tuple(OcrWord(t, (x - 20, y - 10, 40, 20)) for t, x, y in words)
    return OcrResult(
        text=" ".join(w.text for w in ws), lines=(OcrLine(" ".join(w.text for w in ws), ws),)
    )


def test_menu_states_are_read_from_the_words() -> None:
    lvl = _level(synth.World(PROF))
    frame = np.full((1080, 1920, 3), 90, np.uint8)
    assert classify(np.zeros((1080, 1920, 3), np.uint8), _ocr([]), lvl, PROF).state == "loading"
    assert (
        classify(frame, _ocr([("WORLD", 900, 70), ("MAP", 1030, 70)]), lvl, PROF).state
        == "world_map"
    )
    seen = classify(
        frame,
        _ocr([("Adventures", 460, 125), ("IWJSterY", 369, 820), ("guow", 470, 820)]),
        lvl,
        PROF,
    )
    assert seen.state == "adventures" and seen.entry == (369, 820)
    assert classify(frame, _ocr([("Adventures", 460, 125)]), lvl, PROF).entry is None
    assert (
        classify(frame, _ocr([("ADVerqtUre", 955, 403), ("NOSt", 1100, 537)]), lvl, PROF).state
        == "adventure_dialog"
    )
    assert (
        classify(frame, _ocr([("Normal", 840, 630), ("Hard", 1100, 630)]), lvl, PROF).state
        == "difficulty"
    )
    assert (
        classify(
            frame, _ocr([("rye", 820, 79), ("MYSterY", 936, 79), ("nay", 973, 1007)]), lvl, PROF
        ).state
        == "lobby"
    )
    assert (
        classify(
            frame,
            _ocr([("Explore", 900, 18), ("the", 960, 18), ("darkness.", 1020, 18)]),
            lvl,
            PROF,
        ).state
        == "in_adventure"
    )
    assert (
        classify(frame, _ocr([("Bingusthewizard's", 1400, 50), ("Den", 1570, 50)]), lvl, PROF).state
        == "room"
    )


class FakeMenus:
    """A menu tree that moves on only when the right point is clicked."""

    FLOW = {
        "room": ("menu_globe", "world_map"),
        "world_map": ("menu_adventures", "adventures"),
        "adventures": ("entry", "adventure_dialog"),
        "adventure_dialog": ("menu_join", "difficulty"),
        "difficulty": ("menu_hard", "lobby"),
        "lobby": ("menu_play", "loading"),
    }
    WORDS = {
        "room": [("Den", 1570, 50)],
        "world_map": [("WORLD", 900, 70), ("MAP", 1030, 70)],
        "adventures": [("Adventures", 460, 125)],
        "adventure_dialog": [("ADVerqtUre", 955, 403), ("NOSt", 1100, 537)],
        "difficulty": [("Normal", 840, 630), ("Hard", 1100, 630)],
        "lobby": [("MYSterY", 936, 79)],
        "in_adventure": [("Explore", 900, 18), ("the", 960, 18), ("darkness.", 1020, 18)],
    }

    def __init__(self) -> None:
        self.state = "room"
        self.t = 0.0
        self.scrolled = False
        self.clicks: list[tuple[int, int]] = []
        self.loading_until = 0.0

    def tick(self, s: float) -> None:
        self.t += s
        if self.state == "loading" and self.t >= self.loading_until:
            self.state = "in_adventure"

    def grab(self) -> np.ndarray:
        v = 0 if self.state == "loading" else 90
        return np.full((1080, 1920, 3), v, np.uint8)

    def ocr(self, _frame: np.ndarray) -> OcrResult:
        words = list(self.WORDS.get(self.state, []))
        if self.state == "adventures" and self.scrolled:
            words += [("IWJSterY", 369, 820), ("guow", 470, 820)]
        return _ocr(words)

    def click(self, x: int, y: int) -> bool:
        self.clicks.append((x, y))
        key, nxt = self.FLOW.get(self.state, ("", ""))
        want = (369, 820) if key == "entry" else PROF.point(key) if key else None
        if want is not None and abs(x - want[0]) < 30 and abs(y - want[1]) < 30:
            self.state = nxt
            if nxt == "loading":
                self.loading_until = self.t + 3.0
        return True

    def drag(self, x0: int, y0: int, x1: int, y1: int) -> bool:
        if self.state == "adventures" and y1 > y0 + 300:
            self.scrolled = True
        return True


def test_the_opening_walks_the_menus_into_the_adventure() -> None:
    menus = FakeMenus()
    events: list[Any] = []
    bus = EventBus()
    bus.subscribe("*", events.append)
    op = Opening(
        _level(synth.World(PROF)),
        PROF,
        CFG,
        click=menus.click,
        drag=menus.drag,
        grab=menus.grab,
        ocr=menus.ocr,
        bus=bus,
        clock=lambda: menus.t,
        sleep=menus.tick,
    )
    assert op.run(lambda: menus.t > 120), (op.steps, menus.clicks)
    assert op.steps == [
        "room",
        "world_map",
        "adventures",
        "adventure_dialog",
        "difficulty",
        "lobby",
        "loading",
        "in_adventure",
    ], op.steps
    assert menus.scrolled and len(menus.clicks) == 6, menus.clicks
    assert "adventure.menu_stuck" not in [e.type for e in events]


def test_the_opening_asks_for_help_when_a_menu_does_not_move() -> None:
    menus = FakeMenus()
    menus.FLOW = dict(menus.FLOW, world_map=("menu_globe", "x"))  # Adventures does nothing
    events: list[Any] = []
    bus = EventBus()
    bus.subscribe("*", events.append)
    op = Opening(
        _level(synth.World(PROF)),
        PROF,
        CFG,
        click=menus.click,
        drag=menus.drag,
        grab=menus.grab,
        ocr=menus.ocr,
        bus=bus,
        clock=lambda: menus.t,
        sleep=menus.tick,
    )
    assert not op.run(lambda: menus.t > 200)
    assert [e.type for e in events][-1] == "adventure.menu_stuck"


# --- the level model --------------------------------------------------------------------


def test_the_level_round_trips_and_knows_its_banners(tmp_path: Path) -> None:
    world = synth.World(PROF)
    synth.layout(world)
    lvl = _level(world)
    lvl.plan["moths"] = [Act(1, "light", 10.0, "crystal"), Act(0, "pick", 5.0, "fungus")]
    lvl.save(tmp_path)
    back = Level.load(tmp_path)
    assert back is not None and len(back.spots) == 4 and back.plan["moths"][0].action == "light"
    assert back.floors["moths"].shape == (MH, MW) and back.floors["moths"].all()
    assert back.phase_of("Find 2 GLOW MOTHS and LEAD THEM to the door.") is not None
    assert back.phase_of("find the last glow moth and lead it to the door").key == "moths"  # type: ignore[union-attr]
    assert back.phase_of("li ht the last torch to illuminate the area").mode == "replay"  # type: ignore[union-attr]
    assert back.phase_of("Bingusthewizard's Den") is None and back.phase_of("") is None
    dot = (1600.0, 800.0)
    mm = back.to_minimap(dot, (synth.W / 2 + 142, synth.H / 2 - 71))
    sx, sy = back.to_screen(dot, mm)
    assert abs(sx - (synth.W / 2 + 142)) < 1e-6 and abs(sy - (synth.H / 2 - 71)) < 1e-6
    assert level_dir(Paths().profiles, "mystery_below").name == "mystery_below"


# --- the level runner -------------------------------------------------------------------


def _runner(
    world: synth.World, lvl: Level, events: list[Any], banner: Callable[[], str], **kw: Any
) -> LevelRunner:
    bus = EventBus()
    bus.subscribe("*", events.append)
    return LevelRunner(
        lvl,
        PROF,
        CFG,
        SimInput(world),
        world.image,
        banner_ocr=lambda _img: banner(),
        counter_ocr=lambda _crop: f"{world.items}/3",
        dialog_x=_x_template(),
        bus=bus,
        clock=lambda: world.t,
        sleep=world.tick,
        **kw,
    )


def test_the_runner_lights_the_crystals_from_the_plan_and_restocks() -> None:
    world = synth.World(PROF)
    synth.layout(world)
    lvl = _level(world)
    # the plan: light both crystals; no pick act, so the stock comes from the restock
    lvl.plan["moths"] = [Act(1, "light", 10.0, "crystal"), Act(3, "light", 20.0, "crystal")]
    events: list[Any] = []
    banner = {"text": "Find 4 GLOW MOTHS and LEAD THEM to the door."}
    runner = _runner(world, lvl, events, lambda: banner["text"])

    def stop() -> bool:
        if all(th.done for th in world.things if th.kind == "crystal"):
            banner["text"] = "Open the door to travel deeper underground."
        return world.t > 240

    out = runner.run(stop)
    types = [e.type for e in events]
    assert out == "handover", (out, world.log, runner.stats.as_dict(), types)
    assert runner.stats.lit == 2 and runner.stats.restocks >= 1, runner.stats.as_dict()
    assert "adventure.dialog_closed" in types  # Cosmo's dialog after the first pickup
    assert runner.stats.helps == 0 and not world.keys


def test_an_unseen_crystal_is_skipped_and_three_failures_ask_for_help() -> None:
    world = synth.World(PROF)
    synth.layout(world)
    world.items = 3
    lvl = _level(world)
    far = Spot(len(lvl.spots), "crystal", "moths", *mm_of(3900.0, 2400.0))  # nothing there
    lvl.spots.append(far)
    lvl.plan["moths"] = [Act(far.id, "light", 1.0, "crystal")] * 3 + [
        Act(1, "light", 2.0, "crystal")
    ]

    class Presence:
        def __init__(self) -> None:
            self.active_from: float | None = None

        def user_active(self) -> bool:
            return self.active_from is not None and world.t >= self.active_from

        def seconds_since_user_input(self) -> float:
            if self.active_from is None or world.t < self.active_from:
                return 1e9
            return max(0.0, world.t - self.active_from - 1.0)  # one second of his input

    presence = Presence()
    events: list[Any] = []
    runner = _runner(
        world,
        lvl,
        events,
        lambda: "Find 4 GLOW MOTHS and LEAD THEM to the door.",
        presence=presence,
    )

    def stop() -> bool:
        if runner.state == "HELP" and presence.active_from is None:
            presence.active_from = world.t + 0.5  # William comes
        return world.t > 400 or runner.k >= 4

    runner.run(stop)
    types = [e.type for e in events]
    assert "adventure.help" in types and runner.stats.helps == 1
    assert types.count("adventure.not_seen") >= 3 and runner.stats.user_turns == 1
    assert runner.k == 4 and runner.stats.lit == 1, runner.stats.as_dict()


# --- the real recordings, when they are on this machine --------------------------------

REAL = Paths().sessions / "20261030_100745_mystery_below"


@pytest.mark.skipif(not (REAL / "analysis" / "route.json").exists(), reason="recording not here")
def test_the_real_level_has_the_moth_chamber_mapped() -> None:
    lvl = Level.load(level_dir(Paths().profiles, "mystery_below"))
    assert lvl is not None
    assert (
        len(lvl.spots_of("moths", "crystal")) >= 30 and len(lvl.spots_of("moths", "fungus")) >= 10
    )
    assert lvl.plan["moths"][0].action == "light" and 15000 < lvl.floors["moths"].sum() < 30000
    img = np.asarray(cv2.imread(str(sorted((REAL / "frames").iterdir())[0])), np.uint8)
    assert img.size and sight.minimap_dot(img, PROF) is None  # the den: no dot
