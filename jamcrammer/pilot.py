"""The pilot: Claude at the controls, one command at a time (docs/PILOT.md, docs/MAPPING.md).

``jamcrammer pilot <command>`` finds the AJC window, takes a frame, acts through the same
InputController the bots use (inside the client only), takes another frame and reports what
changed, as JSON for the one reading it. With ``pilot serve`` running, the same commands go
to that process through a command file and come back in a fraction of a second; without it,
each command is its own short process.

Beyond look / key / click / drag / goto / note / crop, the pilot **maps**: ``probe`` walks a
direction until the scene stops moving and records the collision in the world map
(``modes/adventures/world.py``); ``scan`` puts what the detectors see on the map; ``mark``
puts what Claude says is there on the map; ``where`` gives the fused position
(``localize.py``); ``map`` renders the map to a PNG to read. A pilot session is an ordinary
Learn recording (``learn --stop-file``) started by ``pilot start`` and ended by ``pilot
stop``, so everything done at the controls can be analysed by the same route and level
builders as William's own play, plus a ``pilot_log.jsonl`` of the commands and notes.

Guardrails, none of them optional:
- input only with ``pilot.enabled: true`` in config.yaml (William's switch); ``look``,
  ``where``, ``map``, ``note``, ``crop``, ``mark`` always;
- input only inside a started session and within its time budget (``pilot start --budget``);
- clicks only inside the client, never on the chat field or the log-out button
  (``forbidden_rois``), never a drag that starts or ends there;
- keys from a short list (arrows, space, escape), held at most ``max_hold_s``;
- a refusal when William's own input was seen within ``yield_s`` (he has the controls), and
  any walk stops the moment he touches anything;
- every command and its outcome written down.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt

from jamcrammer.core.config import AppConfig, PilotConfig
from jamcrammer.core.profiles import Profile
from jamcrammer.modes.adventures import objects, sight
from jamcrammer.modes.adventures.inventory import DigitBank, read_counts
from jamcrammer.modes.adventures.level import Level, level_dir
from jamcrammer.modes.adventures.localize import Fix, Localizer
from jamcrammer.modes.adventures.nav import Floor, Navigator
from jamcrammer.modes.adventures.opening import classify
from jamcrammer.modes.adventures.replay import Input
from jamcrammer.modes.adventures.world import (
    DIRECTION_KEYS,
    DIRECTIONS,
    STATE_NAMES,
    World,
    world_dir,
)
from jamcrammer.vision.ocr import OcrResult

BgrImage = npt.NDArray[np.uint8]
Point = tuple[float, float]
ALLOWED_KEYS = frozenset({"up", "down", "left", "right", "space", "escape"})
CONTACT_WIDTH = 960  # the frame I read, halved
MAX_OCR_LINES = 40
SETTLE_S = 0.3  # after an act, before the second look
DIALOG_GONE_S = 0.6  # a closed dialog takes this long to go
PROBE_WARMUP_TICKS = 2  # the game takes a moment to start moving: no stuck count yet
WHERE_RADIUS_PX = 1200  # things this close are listed by `where`
COLLISION_RADIUS_PX = 300
SURVEY_DIRS = ("right", "dr", "down", "dl", "left", "ul", "up", "ur")
CARDINAL_DIRS = ("right", "down", "left", "up")
SURVEY_BACK_TOL_PX = 40.0
SERVER_HEARTBEAT_S = 1.0
WRITE_RETRIES = 8  # a reader may hold the file for an instant (Windows)
WRITE_RETRY_S = 0.03
SERVER_STALE_S = 5.0
WORLD_OBJECT_KINDS = (
    "crystal",
    "fungus",
    "rock",
    "decoy",
    "bridge",
    "mushroom",
    "torch",
    "basin",
    "mirror",
    "switch",
    "boom_pile",
    "boom_target",
    "clickable",
    "marker",
    "door",
    "npc",
    "enemy",
    "spawner",
    "flame",
    "rune",
    "chest",
    "ledge",
    "note",
)


class PilotRefusedError(RuntimeError):
    """A command the guardrails do not allow."""


@dataclass
class Seen:
    """What one frame shows, for the pilot's eyes."""

    ts: float
    frame_path: str
    contact_path: str
    window: dict[str, Any]
    menu: str
    banner: str
    phase: str
    dot: list[float] | None
    counts: list[int | None]
    dialog_x: list[int] | None
    crystals: list[dict[str, Any]]
    fungi: list[dict[str, Any]]
    pin: list[int] | None
    ocr: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def contact(frame: BgrImage) -> BgrImage:
    h, w = frame.shape[:2]
    scale = CONTACT_WIDTH / w
    return np.asarray(
        cv2.resize(frame, (CONTACT_WIDTH, int(h * scale)), interpolation=cv2.INTER_AREA),
        dtype=np.uint8,
    )


def summarize(
    frame: BgrImage,
    prof: Profile,
    lvl: Level | None,
    ocr: OcrResult,
    banner: str,
    *,
    digits: DigitBank | None,
    dialog_x: BgrImage | None,
    window: dict[str, Any],
    frame_path: str,
    contact_path: str,
) -> Seen:
    """Everything the detectors say about a frame."""
    menu = classify(frame, ocr, lvl, prof).state if lvl is not None else ""
    phase = ""
    if lvl is not None:
        p = lvl.phase_of(banner)
        phase = p.key if p is not None else ""
    counts = read_counts(frame, prof, lambda _c: "", digits=digits)
    dot = sight.minimap_dot(frame, prof)
    x = None
    if dialog_x is not None:
        pt = sight.find_dialog_x(frame, dialog_x, prof.number("dialog_x_min"))
        x = [pt[0], pt[1]] if pt is not None else None
    pin = objects.find_marker(frame, prof)
    lines = [{"text": ln.text, "box": list(ln.box)} for ln in ocr.lines[:MAX_OCR_LINES] if ln.text]
    return Seen(
        ts=time.time(),
        frame_path=frame_path,
        contact_path=contact_path,
        window=window,
        menu=menu,
        banner=banner,
        phase=phase,
        dot=[round(dot[0], 1), round(dot[1], 1)] if dot else None,
        counts=[counts.blossoms, counts.boomseeds],
        dialog_x=x,
        crystals=[asdict(b) for b in objects.crystals(frame, prof)[:12]],
        fungi=[asdict(b) for b in objects.fungi(frame, prof)[:8]],
        pin=[pin.x, pin.y] if pin else None,
        ocr=lines,
    )


# --- guardrails -----------------------------------------------------------------------


def check_point(cfg: PilotConfig, x: int, y: int) -> None:
    for roi in cfg.forbidden_rois:
        rx, ry, rw, rh = (int(v) for v in roi)
        if rx <= x < rx + rw and ry <= y < ry + rh:
            msg = f"({x},{y}) is in a forbidden area {list(roi)} (chat, log out)"
            raise PilotRefusedError(msg)


def check_key(cfg: PilotConfig, key: str, hold_s: float) -> None:
    if key.lower() not in ALLOWED_KEYS:
        msg = f"key {key!r} is not on the pilot's list {sorted(ALLOWED_KEYS)}"
        raise PilotRefusedError(msg)
    if hold_s > cfg.max_hold_s or hold_s <= 0:
        msg = f"hold {hold_s} s is outside (0, {cfg.max_hold_s}]"
        raise PilotRefusedError(msg)


def check_enabled(cfg: PilotConfig) -> None:
    if not cfg.enabled:
        msg = "pilot input is off: set pilot.enabled: true in config.yaml (William's switch)"
        raise PilotRefusedError(msg)


def check_yield(cfg: PilotConfig, seconds_since_user_input: float) -> None:
    if seconds_since_user_input < cfg.yield_s:
        msg = (
            f"William used the keyboard or mouse {seconds_since_user_input:.1f} s ago: his controls"
        )
        raise PilotRefusedError(msg)


def check_budget(session: PilotSession | None, now: float) -> None:
    """Input needs a started session, and stops when its time budget is spent."""
    if session is None:
        msg = "no pilot session: run `jamcrammer pilot start` first (input is time-boxed)"
        raise PilotRefusedError(msg)
    spent = (now - session.started_ts) / 60.0
    if spent > session.budget_min:
        msg = (
            f"the session's budget of {session.budget_min:.0f} min is spent ({spent:.0f} min):"
            " `pilot stop`, then a new `pilot start` with a stated goal"
        )
        raise PilotRefusedError(msg)


def check_direction(cfg: PilotConfig, direction: str, max_s: float) -> set[str]:
    keys = DIRECTION_KEYS.get(direction.lower())
    if keys is None:
        msg = f"direction {direction!r} is not one of {sorted(DIRECTIONS)}"
        raise PilotRefusedError(msg)
    for k in keys:
        check_key(cfg, k, max_s)
    return keys


# --- the session ----------------------------------------------------------------------


@dataclass
class PilotSession:
    """The active pilot session: where the recording and the command log live."""

    session_dir: Path
    stop_file: Path
    target: str
    started_ts: float
    budget_min: float = 45.0
    goal: str = ""

    @property
    def log_path(self) -> Path:
        return self.session_dir / "pilot_log.jsonl"

    def log(self, record: dict[str, Any]) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), **record}) + "\n")

    def save(self, pointer: Path) -> None:
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text(
            json.dumps(
                {
                    "session_dir": str(self.session_dir),
                    "stop_file": str(self.stop_file),
                    "target": self.target,
                    "started_ts": self.started_ts,
                    "budget_min": self.budget_min,
                    "goal": self.goal,
                }
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, pointer: Path) -> PilotSession | None:
        if not pointer.exists():
            return None
        doc = json.loads(pointer.read_text(encoding="utf-8"))
        return cls(
            Path(doc["session_dir"]),
            Path(doc["stop_file"]),
            doc["target"],
            doc["started_ts"],
            float(doc.get("budget_min", 45.0)),
            str(doc.get("goal", "")),
        )

    @property
    def stopped(self) -> bool:
        return self.stop_file.exists()


def pointer_path(data_dir: Path) -> Path:
    return data_dir / "pilot" / "current.json"


def load_level(cfg: AppConfig, profiles_dir: Path) -> Level | None:
    return Level.load(level_dir(profiles_dir, cfg.adventures.route.target))


def load_world(cfg: AppConfig, prof: Profile, lvl: Level | None, profiles_dir: Path) -> World:
    """The saved world, else one seeded from the level, else an empty one from the profile."""
    target = cfg.adventures.route.target
    w = World.load(world_dir(profiles_dir, target), cfg.adventures.world)
    if w is not None:
        return w
    if lvl is not None:
        return World.from_level(lvl, cfg.adventures.world)
    return World(
        target,
        prof.number("minimap_scale"),
        prof.point("avatar_xy"),
        prof.roi("minimap_roi"),
        cfg.adventures.world,
    )


# --- the executor ---------------------------------------------------------------------


@dataclass
class Hold:
    """What a tracked hold of keys did."""

    reason: str  # done | collision | dialog | user
    travelled: float
    pos_before: Point | None
    pos_after: Point | None
    ticks: int
    fix: Fix


class Executor:
    """One pilot's eyes, hands and memory; the window, the capture and the input are
    injected so the synthetic adventure can drive it in tests."""

    def __init__(
        self,
        *,
        cfg: AppConfig,
        prof: Profile,
        lvl: Level | None,
        world: World,
        grab: Callable[[], BgrImage],
        inp: Input,
        ocr: Callable[[BgrImage], OcrResult],
        banner_ocr: Callable[[BgrImage], str],
        user_input_age: Callable[[], float],
        foreground: Callable[[], None] = lambda: None,
        window: Callable[[], dict[str, Any]] = dict,
        digits: DigitBank | None = None,
        dialog_x: BgrImage | None = None,
        out_dir: Path,
        world_dir: Path | None,
        session: PilotSession | None,
        position_path: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cfg, self.prof, self.lvl, self.world = cfg, prof, lvl, world
        self._grab, self._in, self._ocr, self._banner = grab, inp, ocr, banner_ocr
        self._user_age, self._foreground, self._window = user_input_age, foreground, window
        self.digits, self.dialog_x = digits, dialog_x
        self.out_dir, self.world_dir, self.session = out_dir, world_dir, session
        self.position_path = position_path
        self._clock, self._wall, self._sleep = clock, wall, sleep
        self.loc = Localizer(world, prof, cfg.adventures.world)
        self.area = cfg.pilot.area
        self.last_frame_path = ""
        self.dialogs_closed = 0
        self.align_log: list[tuple[int, int, float]] = []  # corrections (dx, dy, resp)
        self.last_align: tuple[float, float] = (0.0, 0.0)

    # --- eyes ----------------------------------------------------------------------------

    def grab(self) -> BgrImage:
        return self._grab()

    def _save_frame(self, frame: BgrImage, tag: str) -> tuple[str, str]:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        frame_path = self.out_dir / f"{stamp}_{tag}.png"
        contact_path = self.out_dir / f"{stamp}_{tag}_half.png"
        cv2.imwrite(str(frame_path), frame)
        cv2.imwrite(str(contact_path), contact(frame))
        self.last_frame_path = str(frame_path)
        return str(frame_path), str(contact_path)

    def _fix(self, frame: BgrImage) -> Fix:
        """A fresh fix between commands; a known banner also says which area this is."""
        banner = self._banner(frame)
        if self.lvl is not None:
            p = self.lvl.phase_of(banner)
            if p is not None:
                self.area = p.area
        fix = self.loc.rejoin(frame, self._wall())
        if fix.pos is not None:
            self.world.mark_track(self.area, fix.pos)
            self._observe(frame, fix.pos)
        self._publish(fix)
        return fix

    def _observe(self, frame: BgrImage, pos: Point) -> int:
        """The frame into the mosaic, unless a dialog covers the scene; the HUD and the
        animal's own box (``avatar_roi``) are left out."""
        if self.dialog_x is not None:
            roi = (
                self.prof.roi("dialog_x_search_roi")
                if self.prof.has("dialog_x_search_roi")
                else None
            )
            if sight.find_dialog_x(frame, self.dialog_x, self.prof.number("dialog_x_min"), roi):
                return 0
        masks = [*self.prof.items("hud_rois")]
        if self.prof.has("avatar_roi"):
            masks.append(list(self.prof.roi("avatar_roi")))
        fixed, overlap, resp = self.world.align(self.area, pos, frame, masks)
        if resp > 0 and fixed != pos:
            self.loc.pos = fixed  # the mosaic knows better than the dot
            self.align_log.append(
                (round(pos[0] - fixed[0]), round(pos[1] - fixed[1]), round(resp, 2))
            )
            pos = fixed
        self.last_align = (round(overlap, 2), round(resp, 2))
        return self.world.observe(self.area, pos, frame, masks)

    def _publish(self, fix: Fix) -> None:
        """The position for the Mapping tab (``data/pilot/position.json``)."""
        if self.position_path is None:
            return
        with contextlib.suppress(OSError):
            _write_json(
                self.position_path,
                {"ts": self._wall(), "area": self.area, "pos": fix.as_dict()["pos"]},
            )

    def _position(self, fix: Fix) -> dict[str, Any]:
        mm = self.world.world_to_dot(fix.pos) if fix.pos else None
        return {
            "area": self.area,
            "pos": fix.as_dict(),
            "mm": [round(mm[0], 1), round(mm[1], 1)] if mm else None,
            "align": {
                "overlap": self.last_align[0],
                "response": self.last_align[1],
                "corrections": self.align_log[-3:],
            },
        }

    def look(self, tag: str = "look", frame: BgrImage | None = None) -> dict[str, Any]:
        frame = self.grab() if frame is None else frame
        frame_path, contact_path = self._save_frame(frame, tag)
        res = self._ocr(frame)
        banner = self._banner(frame)
        seen = summarize(
            frame,
            self.prof,
            self.lvl,
            res,
            banner,
            digits=self.digits,
            dialog_x=self.dialog_x,
            window=self._window(),
            frame_path=frame_path,
            contact_path=contact_path,
        )
        doc = seen.as_dict()
        doc.update(self._position(self._fix(frame)))
        self._log({"cmd": tag, "seen": {k: v for k, v in doc.items() if k != "ocr"}})
        return doc

    def where(self) -> dict[str, Any]:
        """The fused position and what the map knows around it."""
        frame = self.grab()
        fix = self._fix(frame)
        out = self._position(fix)
        if fix.pos is None:
            out["note"] = "no minimap dot: not in an adventure, or a dialog / the map covers it"
            return out
        pos = fix.pos
        a = self.world.area(self.area)
        out["cell"] = STATE_NAMES[a.state(pos)]
        near = self.world.near(self.area, pos, WHERE_RADIUS_PX)
        out["things"] = [self._thing(o, pos) for o in near]
        out["collisions"] = [
            {"at": [round(c.x), round(c.y)], "heading": [c.dx, c.dy], "note": c.note}
            for c in self.world.collisions
            if c.area == self.area and math.dist((c.x, c.y), pos) <= COLLISION_RADIUS_PX
        ]
        out["world"] = self.world.summary()
        self._log({"cmd": "where", **{k: v for k, v in out.items() if k != "world"}})
        return out

    def _thing(self, o: Any, pos: Point) -> dict[str, Any]:
        sx, sy = self.world.world_to_screen(pos, (o.x, o.y))
        on_screen = 0 <= sx < 1920 and 0 <= sy < 1080
        return {
            "id": o.id,
            "kind": o.kind,
            "status": o.status,
            "conf": o.confidence,
            "src": o.source,
            "world": [round(o.x), round(o.y)],
            "dist": round(o.dist(pos)),
            "screen": [round(sx), round(sy)] if on_screen else None,
            "note": o.note,
        }

    def scan(self) -> dict[str, Any]:
        """Everything the detectors see, placed on the map (source scan, confidence 0.5)."""
        frame = self.grab()
        fix = self._fix(frame)
        frame_path, _contact = self._save_frame(frame, "scan")
        out = self._position(fix)
        if fix.pos is None:
            out["note"] = "no position: nothing placed"
            return out
        found: list[dict[str, Any]] = []
        fungi = objects.fungi(frame, self.prof)
        base_px = (
            self.prof.number("scan_fungus_base_px") if self.prof.has("scan_fungus_base_px") else 0
        )
        glow_px = self.prof.number("scan_glow_px") if self.prof.has("scan_glow_px") else 0
        for b in objects.crystals(frame, self.prof):
            near_fungus = objects.nearest(fungi, (b.x, b.y))
            at_fungus = near_fungus is not None and near_fungus.dist((b.x, b.y)) <= (
                base_px if b.kind == "unlit" else glow_px if b.kind == "lit" else 0
            )
            if at_fungus:
                continue  # the fungus's navy stem or its glow, not a crystal
            kind, status = ("decoy", "red") if b.kind == "decoy" else ("crystal", b.kind)
            found.append(self._place(kind, fix.pos, (b.x, b.y), status=status, shot=frame_path))
        for b in fungi:
            found.append(self._place("fungus", fix.pos, (b.x, b.y), shot=frame_path))
        for inner in ("paw", "flame", "bang"):
            m = objects.find_marker(frame, self.prof, inner=inner)
            if m is not None:
                found.append(
                    self._place("marker", fix.pos, (m.x, m.y), status=inner, shot=frame_path)
                )
        out["found"] = found
        out["frame"] = frame_path
        self._log({"cmd": "scan", "found": found, "pos": out["pos"]})
        self._save_world()
        return out

    def _place(
        self,
        kind: str,
        pos: Point,
        screen: Point,
        *,
        status: str = "",
        shot: str = "",
        source: str = "scan",
        confidence: float = 0.5,
        note: str = "",
    ) -> dict[str, Any]:
        w = self.world.screen_to_world(pos, screen)
        before = len(self.world.objects)
        o = self.world.add_object(
            kind,
            self.area,
            w,
            source=source,
            confidence=confidence,
            status=status,
            note=note,
            ts=self._wall(),
            shot=shot,
        )
        return {
            "id": o.id,
            "kind": o.kind,
            "status": o.status,
            "screen": [round(screen[0]), round(screen[1])],
            "world": [round(o.x), round(o.y)],
            "merged": len(self.world.objects) == before,
            "seen": o.seen,
        }

    def mark(
        self,
        kind: str,
        x: float | None,
        y: float | None,
        *,
        status: str = "",
        note: str = "",
        in_world: bool = False,
    ) -> dict[str, Any]:
        """Claude's word: a thing of ``kind`` at screen (x, y) (default: at the animal), or at
        world (x, y) with ``in_world``. Confidence 0.9."""
        if kind not in WORLD_OBJECT_KINDS:
            msg = f"kind {kind!r} is not one of {WORLD_OBJECT_KINDS}"
            raise PilotRefusedError(msg)
        frame = self.grab()
        fix = self._fix(frame)
        if fix.pos is None:
            msg = "no position (no minimap dot): nothing marked"
            raise PilotRefusedError(msg)
        if in_world and x is not None and y is not None:
            screen = self.world.world_to_screen(fix.pos, (x, y))
        else:
            screen = (
                float(self.world.avatar[0]) if x is None else x,
                float(self.world.avatar[1]) if y is None else y,
            )
        placed = self._place(
            kind,
            fix.pos,
            screen,
            status=status,
            note=note,
            source="claude",
            confidence=0.9,
            shot=self.last_frame_path,
        )
        self._log({"cmd": "mark", **placed, "note": note})
        self._save_world()
        return {**self._position(fix), **placed}

    def forget(self, oid: int) -> dict[str, Any]:
        o = self.world.forget(oid)
        if o is None:
            msg = f"no thing with id {oid}"
            raise PilotRefusedError(msg)
        self._log({"cmd": "forget", "id": oid, "kind": o.kind})
        self._save_world()
        return {"cmd": "forget", "id": oid, "kind": o.kind, "gone": True}

    def unblock(self, x: float | None, y: float | None) -> dict[str, Any]:
        """No wall here after all (an NPC, a moth, a misread): the blocked cells around the
        world point (default: the animal) go back to unknown."""
        frame = self.grab()
        fix = self._fix(frame)
        pos = (x, y) if (x is not None and y is not None) else fix.pos
        if pos is None:
            msg = "no position: nothing unblocked"
            raise PilotRefusedError(msg)
        n = self.world.unblock(self.area, pos)
        self._log({"cmd": "unblock", "at": [round(pos[0]), round(pos[1])], "cells": n})
        self._save_world()
        return {"cmd": "unblock", "at": [round(pos[0]), round(pos[1])], "cells": n}

    def map(
        self,
        area: str | None,
        crop: tuple[int, int, int, int] | None,
        zoom: float,
        labels: bool,
    ) -> dict[str, Any]:
        """The world rendered to a PNG, for reading; the things in it listed."""
        name = area or self.area
        pos = self.loc.pos
        img = self.world.render(name, zoom=zoom, crop=crop, pos=pos, labels=labels)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / f"{time.strftime('%Y%m%d_%H%M%S')}_map_{name}.png"
        cv2.imwrite(str(path), img)
        x0, y0, w, h = crop if crop else (0, 0, *self.world.size)
        things = [
            {
                "id": o.id,
                "kind": o.kind,
                "status": o.status,
                "conf": o.confidence,
                "world": [round(o.x), round(o.y)],
                "mm": [round(v, 1) for v in self.world.world_to_dot((o.x, o.y))],
                "note": o.note,
            }
            for o in self.world.objects
            if o.area == name and not o.gone and x0 <= o.x < x0 + w and y0 <= o.y < y0 + h
        ]
        return {
            "cmd": "map",
            "path": str(path),
            "size": [int(img.shape[1]), int(img.shape[0])],
            "area": name,
            "crop": [x0, y0, w, h],
            "zoom": zoom,
            "pos": [round(pos[0]), round(pos[1])] if pos else None,
            "legend": "black unknown, grey floor (minimap), green-grey walked, red blocked;"
            " yellow ring = the animal; dots = things (filled when sure) with id:status;"
            " red ticks = collisions; gridlines every 10 minimap px, labelled in minimap px",
            "things": things,
            "world": self.world.summary(),
        }

    # --- hands ---------------------------------------------------------------------------

    def _arm(self) -> None:
        check_enabled(self.cfg.pilot)
        check_budget(self.session, self._wall())
        check_yield(self.cfg.pilot, self._user_age())
        self._foreground()
        self.close_dialog()

    def close_dialog(self, frame: BgrImage | None = None) -> bool:
        """An open game dialog (Cosmo's, the blue X) holds the animal: close it first, as
        the level runner does. True when one was closed."""
        if self.dialog_x is None:
            return False
        frame = self.grab() if frame is None else frame
        roi = self.prof.roi("dialog_x_search_roi") if self.prof.has("dialog_x_search_roi") else None
        pt = sight.find_dialog_x(frame, self.dialog_x, self.prof.number("dialog_x_min"), roi)
        if pt is None:
            return False
        self._in.click(pt[0], pt[1])
        self._sleep(DIALOG_GONE_S)
        self.dialogs_closed += 1
        self._log({"cmd": "dialog_closed", "x": pt[0], "y": pt[1]})
        return True

    def _user_stop(self) -> bool:
        return self._user_age() < self.cfg.pilot.yield_s / 4

    def _after(self, cmd: dict[str, Any], before: BgrImage) -> dict[str, Any]:
        self._sleep(SETTLE_S)
        after = self.grab()
        dot0, dot1 = sight.minimap_dot(before, self.prof), sight.minimap_dot(after, self.prof)
        wc = self.cfg.adventures.world
        g0 = sight.masked_gray(before, self.prof, wc.odom_scale, wc.odom_sigma)
        g1 = sight.masked_gray(after, self.prof, wc.odom_scale, wc.odom_sigma)
        dx, dy, resp = sight.scene_shift(g0, g1, wc.odom_scale)
        out = {
            **cmd,
            "dot_before": [round(v, 1) for v in dot0] if dot0 else None,
            "dot_after": [round(v, 1) for v in dot1] if dot1 else None,
            "scene_shift": [round(dx), round(dy), round(resp, 3)],
            "seen": self.look("after", after),
        }
        self._log({k: v for k, v in out.items() if k != "seen"})
        self._save_world()
        return out

    def _hold(
        self, keys: set[str], max_s: float, *, stop_on_collision: bool, rejoin: bool = True
    ) -> Hold:
        """Hold ``keys`` for up to ``max_s`` while tracking the position every tick; with
        ``stop_on_collision`` let go as soon as the scene stops moving. ``rejoin`` False
        keeps the fused position from the previous command (a survey's legs follow each
        other without a gap, and the dot lags a second behind a walk)."""
        wc = self.cfg.adventures.world
        frame = self.grab()
        fix = (
            self.loc.rejoin(frame, self._wall())
            if rejoin
            else self.loc.update(frame, self._wall(), moving=False)
        )
        pos0 = fix.pos
        travelled = 0.0
        still = ticks = 0
        reason = "done"
        for k in sorted(keys):
            self._in.key_down(k)
        try:
            end = self._clock() + max_s
            while self._clock() < end:
                self._sleep(wc.probe_tick_s)
                ticks += 1
                frame = self.grab()
                fix = self.loc.update(frame, self._wall(), moving=True)
                if fix.pos is not None:
                    self.world.mark_track(self.area, fix.pos)
                d = math.hypot(*fix.moved)
                travelled += d
                if self._user_stop():
                    reason = "user"
                    break
                if ticks <= PROBE_WARMUP_TICKS:
                    continue
                still = still + 1 if d < wc.stuck_px else 0
                if stop_on_collision and still >= wc.stuck_ticks:
                    reason = "collision"
                    break
        finally:
            for k in sorted(keys):
                self._in.key_up(k)
        if reason == "collision" and self.close_dialog(frame):
            reason = "dialog"  # a dialog held the animal: not a wall
        if fix.pos is not None:
            self._observe(frame, fix.pos)
        return Hold(reason, travelled, pos0, fix.pos, ticks, fix)

    def key(self, name: str, hold_s: float) -> dict[str, Any]:
        check_key(self.cfg.pilot, name, hold_s)
        self._arm()
        before = self.grab()
        if name in ("space", "escape"):
            self._in.key_down(name)
            self._sleep(hold_s)
            self._in.key_up(name)
            return self._after({"cmd": "key", "key": name, "hold_s": hold_s}, before)
        h = self._hold({name}, hold_s, stop_on_collision=False)
        doc = {"cmd": "key", "key": name, "hold_s": hold_s, **self._hold_doc(h)}
        return self._after(doc, before)

    def _hold_doc(self, h: Hold) -> dict[str, Any]:
        return {
            "reason": h.reason,
            "travelled": round(h.travelled),
            "pos_before": [round(v) for v in h.pos_before] if h.pos_before else None,
            "pos_after": [round(v) for v in h.pos_after] if h.pos_after else None,
            "ticks": h.ticks,
            "fix": h.fix.as_dict(),
        }

    def probe(self, direction: str, max_s: float | None, note: str = "") -> dict[str, Any]:
        """Walk ``direction`` until something stops the animal (or ``max_s``); a stop is a
        collision on the map, heading that way."""
        wc = self.cfg.adventures.world
        max_s = min(wc.probe_max_s if max_s is None else max_s, self.cfg.pilot.max_hold_s)
        keys = check_direction(self.cfg.pilot, direction, max_s)
        self._arm()
        before = self.grab()
        h = self._hold(keys, max_s, stop_on_collision=True)
        doc: dict[str, Any] = {"cmd": "probe", "direction": direction, "max_s": max_s}
        doc.update(self._hold_doc(h))
        if h.reason == "collision" and h.pos_after is not None:
            col = self.world.add_collision(
                self.area,
                h.pos_after,
                DIRECTIONS[direction],
                ts=self._wall(),
                note=note,
            )
            doc["collision"] = {"at": [round(col.x), round(col.y)], "heading": [col.dx, col.dy]}
            doc["shot"] = self._save_frame(self.grab(), f"collision_{direction}")[0]
        return self._after(doc, before)

    def survey(self, max_s: float | None, dirs: int) -> dict[str, Any]:
        """Probe each direction from here and walk back by odometry: the obstacle edges
        around the animal in one command."""
        wc = self.cfg.adventures.world
        max_s = min(wc.probe_max_s if max_s is None else max_s, self.cfg.pilot.max_hold_s)
        names = SURVEY_DIRS if dirs == 8 else CARDINAL_DIRS
        self._arm()
        before = self.grab()
        home = self.loc.rejoin(before, self._wall()).pos
        legs: list[dict[str, Any]] = []
        for n, d in enumerate(names):
            keys = check_direction(self.cfg.pilot, d, max_s)
            check_yield(self.cfg.pilot, self._user_age())
            out = self._hold(keys, max_s, stop_on_collision=True, rejoin=n == 0)
            leg: dict[str, Any] = {"direction": d, **self._hold_doc(out)}
            if out.reason == "collision" and out.pos_after is not None:
                col = self.world.add_collision(
                    self.area, out.pos_after, DIRECTIONS[d], ts=self._wall()
                )
                leg["collision"] = [round(col.x), round(col.y)]
            if out.reason == "user":
                legs.append(leg)
                break
            back = self._walk_back(home, max_s * 1.5)
            leg["back"] = back
            legs.append(leg)
        doc = {"cmd": "survey", "home": [round(v) for v in home] if home else None, "legs": legs}
        return self._after(doc, before)

    def _walk_back(self, home: Point | None, max_s: float) -> dict[str, Any]:
        """Toward ``home`` by the fused position until within SURVEY_BACK_TOL_PX."""
        wc = self.cfg.adventures.world
        if home is None or self.loc.pos is None:
            return {"reason": "no_position"}
        end = self._clock() + max_s
        held: set[str] = set()
        reason = "arrived"
        try:
            while True:
                pos = self.loc.pos
                if pos is None:
                    reason = "no_position"
                    break
                dx, dy = home[0] - pos[0], home[1] - pos[1]
                if math.hypot(dx, dy) <= SURVEY_BACK_TOL_PX:
                    break
                if self._clock() >= end:
                    reason = "timeout"
                    break
                if self._user_stop():
                    reason = "user"
                    break
                keys = _keys_toward(dx, dy)
                for k in sorted(held - keys):
                    self._in.key_up(k)
                for k in sorted(keys - held):
                    self._in.key_down(k)
                held = keys
                self._sleep(wc.probe_tick_s)
                fix = self.loc.update(self.grab(), self._wall(), moving=True)
                if fix.pos is not None:
                    self.world.mark_track(self.area, fix.pos)
        finally:
            for k in sorted(held):
                self._in.key_up(k)
        pos = self.loc.pos
        return {
            "reason": reason,
            "residual": round(math.dist(pos, home)) if pos else None,
        }

    def click(self, x: int, y: int) -> dict[str, Any]:
        check_point(self.cfg.pilot, x, y)
        self._arm()
        before = self.grab()
        ok = self._in.click(x, y)
        return self._after({"cmd": "click", "x": x, "y": y, "ok": ok}, before)

    def drag(self, x0: int, y0: int, x1: int, y1: int) -> dict[str, Any]:
        check_point(self.cfg.pilot, x0, y0)
        check_point(self.cfg.pilot, x1, y1)
        self._arm()
        before = self.grab()
        ok = self._in.drag(x0, y0, x1, y1)
        return self._after({"cmd": "drag", "from": [x0, y0], "to": [x1, y1], "ok": ok}, before)

    def goto(
        self,
        x: float,
        y: float,
        timeout_s: float,
        *,
        in_world: bool = False,
        tol: float | None = None,
    ) -> dict[str, Any]:
        """Walk to a minimap point (or a world point with ``in_world``) by the level's floor
        mask (all floor when there is none), tracking the position on the way."""
        self._arm()
        target = self.world.world_to_dot((x, y)) if in_world else (x, y)
        roi = self.prof.roi("minimap_roi")
        mask = (
            self.lvl.floors[self.area]
            if self.lvl is not None and self.area in self.lvl.floors
            else np.ones((roi[3], roi[2]), np.bool_)
        )
        before = self.grab()
        self.loc.rejoin(before, self._wall())
        nav = Navigator(
            Floor(mask, (roi[0], roi[1])),
            self.prof,
            self.cfg.adventures.level,
            key_down=self._in.key_down,
            key_up=self._in.key_up,
            grab=self._tracked_grab(lambda: bool(nav.held)),
            clock=self._clock,
            sleep=self._sleep,
        )
        limit = min(timeout_s, self.cfg.pilot.max_goto_s)
        deadline = self._clock() + limit

        def stop() -> bool:
            return self._clock() > deadline or self._user_stop()

        res = nav.go_to(target, stop=stop, tol=tol, timeout=limit)
        cmd = {
            "cmd": "goto",
            "target_mm": [round(target[0], 1), round(target[1], 1)],
            "ok": res.ok,
            "reason": res.reason,
            "dist_mm": round(res.dist, 1),
            "blocks": res.blocks,
            "trace": nav.trace[-20:],
        }
        return self._after(cmd, before)

    def _tracked_grab(self, moving: Callable[[], bool]) -> Callable[[], BgrImage]:
        def grab() -> BgrImage:
            frame = self.grab()
            fix = self.loc.update(frame, self._wall(), moving=moving())
            if fix.pos is not None:
                self.world.mark_track(self.area, fix.pos)
            return frame

        return grab

    # --- the record ------------------------------------------------------------------------

    def note(self, text: str) -> dict[str, Any]:
        if self.session is None:
            msg = "no pilot session: run `jamcrammer pilot start` first"
            raise PilotRefusedError(msg)
        self.session.log({"cmd": "note", "text": text})
        self.world.note(text, self.area, self.loc.pos, self._wall())
        self._save_world()
        return {"cmd": "note", "text": text, "log": str(self.session.log_path)}

    def crop(self, x: int, y: int, w: int, h: int, name: str) -> dict[str, Any]:
        frame = self.grab()
        patch = frame[max(0, y) : y + h, max(0, x) : x + w]
        folder = self.out_dir / "templates"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{name}.png"
        cv2.imwrite(str(path), patch)
        self._log({"cmd": "crop", "roi": [x, y, w, h], "name": name, "path": str(path)})
        return {
            "cmd": "crop",
            "path": str(path),
            "size": [int(patch.shape[1]), int(patch.shape[0])],
        }

    def save(self) -> dict[str, Any]:
        self._save_world(force=True)
        return {"cmd": "save", "world_dir": str(self.world_dir), "world": self.world.summary()}

    def _save_world(self, force: bool = False) -> None:
        if self.world_dir is not None and (self.world.dirty or force):
            self.world.save(self.world_dir)

    def _log(self, record: dict[str, Any]) -> None:
        if self.session is not None:
            self.session.log(record)


def _keys_toward(dx: float, dy: float, dead: float = 0.35) -> set[str]:
    keys: set[str] = set()
    ax, ay = abs(dx), abs(dy)
    if ax >= dead * ay and ax > 0:
        keys.add("right" if dx > 0 else "left")
    if ay >= dead * ax and ay > 0:
        keys.add("down" if dy > 0 else "up")
    return keys


# --- commands to the executor ----------------------------------------------------------


def _crop_arg(crop: list[int] | None) -> tuple[int, int, int, int] | None:
    if not crop:
        return None
    return (int(crop[0]), int(crop[1]), int(crop[2]), int(crop[3]))


def dispatch(ex: Executor, args: argparse.Namespace) -> dict[str, Any]:
    """Run one parsed ``pilot`` command on an executor."""
    a = args
    table: dict[str, Callable[[], dict[str, Any]]] = {
        "look": ex.look,
        "where": ex.where,
        "scan": ex.scan,
        "save": ex.save,
        "key": lambda: ex.key(a.key, a.seconds),
        "probe": lambda: ex.probe(a.direction, a.max, " ".join(a.note or [])),
        "survey": lambda: ex.survey(a.max, a.dirs),
        "click": lambda: ex.click(a.x, a.y),
        "drag": lambda: ex.drag(a.x0, a.y0, a.x1, a.y1),
        "goto": lambda: ex.goto(a.x, a.y, a.timeout, in_world=a.world, tol=a.tol),
        "mark": lambda: ex.mark(
            a.kind, a.x, a.y, status=a.status or "", note=" ".join(a.note or []), in_world=a.world
        ),
        "forget": lambda: ex.forget(a.id),
        "unblock": lambda: ex.unblock(a.x, a.y),
        "map": lambda: ex.map(a.area, _crop_arg(a.crop), a.zoom, not a.no_labels),
        "note": lambda: ex.note(" ".join(a.text)),
        "crop": lambda: ex.crop(a.x, a.y, a.w, a.h, a.name),
    }
    fn = table.get(a.pilot_command)
    if fn is None:
        msg = f"unknown pilot command {a.pilot_command!r}"
        raise PilotRefusedError(msg)
    return fn()


# --- the server: one process, many commands ---------------------------------------------


def server_paths(data_dir: Path) -> dict[str, Path]:
    root = data_dir / "pilot"
    return {
        "server": root / "server.json",
        "cmd": root / "cmd",
        "out": root / "out",
        "stop": root / "serve.stop",
    }


def server_alive(data_dir: Path, now: float | None = None) -> bool:
    p = server_paths(data_dir)["server"]
    if not p.exists():
        return False
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (time.time() if now is None else now) - float(doc.get("heartbeat", 0)) < SERVER_STALE_S


def _write_json(path: Path, doc: dict[str, Any]) -> None:
    """Atomic enough for Windows: a reader holding the file for an instant makes the
    replace fail with a permission error, so it is retried a few times."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc), encoding="utf-8")
    for _attempt in range(WRITE_RETRIES):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            time.sleep(WRITE_RETRY_S)
    path.write_text(json.dumps(doc), encoding="utf-8")
    tmp.unlink(missing_ok=True)


def send(
    data_dir: Path, argv: list[str], timeout_s: float, poll_s: float = 0.05
) -> dict[str, Any] | None:
    """Hand a command to a running server and wait for its answer; None without a server."""
    if not server_alive(data_dir):
        return None
    p = server_paths(data_dir)
    p["cmd"].mkdir(parents=True, exist_ok=True)
    p["out"].mkdir(parents=True, exist_ok=True)
    cid = f"{time.time():.3f}_{uuid.uuid4().hex[:6]}"
    _write_json(p["cmd"] / f"{cid}.json", {"argv": argv, "sent": time.time()})
    result = p["out"] / f"{cid}.json"
    end = time.time() + timeout_s
    while time.time() < end:
        if result.exists():
            try:
                doc = json.loads(result.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                time.sleep(poll_s)
                continue
            result.unlink(missing_ok=True)
            return dict(doc)
        time.sleep(poll_s)
    return {"refused": f"no answer from the pilot server within {timeout_s:.0f} s"}


def serve(
    data_dir: Path,
    run: Callable[[list[str]], dict[str, Any]],
    *,
    poll_s: float,
    stop: Callable[[], bool] = lambda: False,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Answer command files until the stop file appears. ``run`` takes a pilot argv and
    returns the answer (or a refusal). Returns the number of commands served."""
    p = server_paths(data_dir)
    for d in (p["cmd"], p["out"]):
        d.mkdir(parents=True, exist_ok=True)
    p["stop"].unlink(missing_ok=True)
    served = 0
    done = threading.Event()

    def heartbeat() -> None:
        while not done.is_set():
            with contextlib.suppress(OSError):
                _write_json(
                    p["server"], {"pid": os.getpid(), "heartbeat": clock(), "served": served}
                )
            done.wait(SERVER_HEARTBEAT_S)

    threading.Thread(target=heartbeat, name="pilot-heartbeat", daemon=True).start()
    try:
        while not stop() and not p["stop"].exists():
            files = sorted(p["cmd"].glob("*.json"))
            if not files:
                sleep(poll_s)
                continue
            for f in files:
                try:
                    doc = json.loads(f.read_text(encoding="utf-8"))
                    f.unlink(missing_ok=True)
                except (OSError, ValueError):
                    sleep(poll_s)
                    continue
                try:
                    answer = run([str(a) for a in doc.get("argv", [])])
                except PilotRefusedError as exc:
                    answer = {"refused": str(exc)}
                except Exception as exc:
                    answer = {"error": f"{type(exc).__name__}: {exc}"}
                with contextlib.suppress(OSError):
                    _write_json(p["out"] / f.name, answer)
                served += 1
    finally:
        done.set()
        with contextlib.suppress(OSError):
            p["server"].unlink(missing_ok=True)
            p["stop"].unlink(missing_ok=True)
    return served
