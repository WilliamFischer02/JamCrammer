"""A level model of an adventure, in minimap pixels (docs/MYSTERY_BELOW.md).

The minimap is a fixed, north-up map of the current area that is revealed as the player
explores it; by the end of a run it shows the whole floor plan, and it is the same map pixel
for pixel in every run. So the recordings are turned into:

- **areas**: one per minimap (the moth chamber, the torch hall, the city), each with a floor
  mask made from the union of every recorded minimap frame plus the recorded dot tracks;
- **phases**: the objective banners in order, each belonging to an area;
- **spots**: things William acted on, placed on the minimap from his dot and where he clicked
  on screen (``minimap_scale`` scene px per minimap px around ``avatar_xy``): crystals (an
  item counter went down), fungi and boomseed piles (a counter went up), the door (where the
  "open the door" banner came), repeated clicks in the city (mirrors, rocks);
- a **plan**: the reference run's acts in order, each at a spot.

The counters come from the recorder's OCR of the keyframes (the "N/3" of the item slots), so
every pickup and use is exact in time; the nearest preceding world click is what did it.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt
from rapidfuzz import fuzz

from jamcrammer.core.config import LevelConfig, RouteConfig
from jamcrammer.core.profiles import Profile
from jamcrammer.learn.recorder import read_meta
from jamcrammer.modes.adventures.route import Route, load_or_build

BgrImage = npt.NDArray[np.uint8]
Mask = npt.NDArray[np.bool_]
LEVEL_VERSION = 1
COUNTER_RE = re.compile(r"(\d)\s*/\s*(\d)")
PHASE_FUZZY = 80  # banner text vs a phase phrase (token set ratio)
DOOR_MERGE_PX = 20.0  # the door spot is where the banner came, not a click: looser

# --- The Mystery Below --------------------------------------------------------------------
# (phrase in the banner, phase key, area, what the bot does there)
MYSTERY_BELOW_PHASES: list[tuple[str, str, str, str]] = [
    ("explore the darkness", "start", "moths", "plan"),
    ("pick some light blossoms from the fungi", "pick", "moths", "plan"),
    ("drag a light blossom to drop it on the crystal", "first_crystal", "moths", "plan"),
    ("make a path for the glow moth by illuminating crystals", "first_moth", "moths", "plan"),
    ("find 4 glow moths and lead them to the door", "moths", "moths", "plan"),
    ("open the door to travel deeper underground", "door", "moths", "replay"),
    ("light the 3 torches to illuminate the room", "torches", "torches", "replay"),
    ("light the last torch to illuminate the area", "last_torch", "torches", "replay"),
    ("travel to the lost city", "travel", "torches", "replay"),
    ("solve the puzzles throughout the ancient city", "puzzles", "city", "replay"),
    ("use the boomseeds to find a way deeper into the city", "boomseeds", "city", "replay"),
    ("press all 3 switches within 60 seconds", "switches", "city", "replay"),
    ("find the light source and reflect it through the room", "light_source", "city", "replay"),
    ("redirect the light to the obelisk", "obelisk", "city", "replay"),
    ("speak with the alphas at the obelisk", "alphas", "city", "replay"),
    ("you did it adventure complete", "done", "city", "done"),
]
PHASES_BY_TARGET = {"mystery_below": MYSTERY_BELOW_PHASES}
# the moth-count banners are one phase: "find 4 / 3 / 2 glow moths", "find the last glow moth"
MOTH_WORDS = ("glow moth", "lead")
ITEM_SLOTS = ("blossoms", "boomseeds")  # item_counter_rois order
# spot kinds by what changed: slot 0 down = a crystal lit, slot 0 up = a fungus picked ...
SPOT_KINDS = {
    (0, "use"): "crystal",
    (0, "pickup"): "fungus",
    (1, "use"): "boom_target",
    (1, "pickup"): "boom_pile",
}
ACTIONS = {"crystal": "light", "fungus": "pick", "boom_target": "boom", "boom_pile": "pick"}
SPOT_COLOURS = {
    "crystal": (0, 0, 255),
    "fungus": (0, 220, 0),
    "door": (0, 220, 255),
    "repeat": (255, 200, 0),
    "boom_target": (0, 120, 255),
    "boom_pile": (0, 255, 160),
}


@dataclass
class Phase:
    key: str
    area: str
    mode: str  # plan | replay | done
    phrase: str


@dataclass
class Spot:
    id: int
    kind: str  # crystal | fungus | boom_target | boom_pile | door | repeat
    area: str
    x: float  # minimap px (stage coordinates)
    y: float
    seen: int = 1  # how many events merged into it
    runs: list[str] = field(default_factory=list)  # sessions that saw it


@dataclass
class Act:
    spot: int
    action: str  # light | pick | boom | door | click
    t: float  # route time in the reference run
    kind: str = ""
    phase: str = ""  # the banner phase it happened in: never run before that banner shows


@dataclass
class Level:
    target: str
    scale: float  # scene px per minimap px
    avatar: tuple[int, int]
    minimap_roi: tuple[int, int, int, int]
    phases: list[Phase] = field(default_factory=list)
    spots: list[Spot] = field(default_factory=list)
    plan: dict[str, list[Act]] = field(default_factory=dict)  # per area, in order
    sessions: list[str] = field(default_factory=list)
    reference: str = ""
    floors: dict[str, Mask] = field(default_factory=dict, repr=False)

    # --- lookups -------------------------------------------------------------------------

    def phase_of(self, banner: str) -> Phase | None:
        """The phase a banner text belongs to, or None when it is no known banner."""
        text = banner.lower()
        if not text:
            return None
        best: tuple[float, Phase] | None = None
        for p in self.phases:
            score = fuzz.token_set_ratio(text, p.phrase)
            if best is None or score > best[0]:
                best = (score, p)
        if best is not None and best[0] >= PHASE_FUZZY:
            return best[1]
        if all(w in text for w in MOTH_WORDS):
            return next((p for p in self.phases if p.key == "moths"), None)
        return None

    def phase(self, key: str) -> Phase | None:
        return next((p for p in self.phases if p.key == key), None)

    def phase_index(self, key: str) -> int:
        return next((i for i, p in enumerate(self.phases) if p.key == key), -1)

    def phase_at(self, route: Route, t: float) -> str:
        """The phase key of the banner showing at route time ``t`` in a recorded run."""
        k = route.objective_index_at(t)
        if k < 0:
            return ""
        p = self.phase_of(route.objectives[k].text)
        return p.key if p is not None else ""

    def spot(self, sid: int) -> Spot:
        return self.spots[sid]

    def spots_of(self, area: str, kind: str) -> list[Spot]:
        return [s for s in self.spots if s.area == area and s.kind == kind]

    def nearest(self, area: str, kind: str, pt: tuple[float, float]) -> Spot | None:
        cands = self.spots_of(area, kind)
        if not cands:
            return None
        return min(cands, key=lambda s: float(np.hypot(s.x - pt[0], s.y - pt[1])))

    def to_minimap(
        self, dot: tuple[float, float], screen: tuple[float, float]
    ) -> tuple[float, float]:
        """A screen point seen while our dot was at ``dot``, on the minimap."""
        return (
            dot[0] + (screen[0] - self.avatar[0]) / self.scale,
            dot[1] + (screen[1] - self.avatar[1]) / self.scale,
        )

    def to_screen(self, dot: tuple[float, float], mm: tuple[float, float]) -> tuple[float, float]:
        return (
            self.avatar[0] + (mm[0] - dot[0]) * self.scale,
            self.avatar[1] + (mm[1] - dot[1]) * self.scale,
        )

    # --- files ---------------------------------------------------------------------------

    def save(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        doc = {
            "version": LEVEL_VERSION,
            "target": self.target,
            "scale": self.scale,
            "avatar": list(self.avatar),
            "minimap_roi": list(self.minimap_roi),
            "phases": [asdict(p) for p in self.phases],
            "spots": [asdict(s) for s in self.spots],
            "plan": {a: [asdict(x) for x in acts] for a, acts in self.plan.items()},
            "sessions": self.sessions,
            "reference": self.reference,
        }
        (folder / "level.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
        for area, floor in self.floors.items():
            cv2.imwrite(str(folder / f"{area}_floor.png"), floor.astype(np.uint8) * 255)
            cv2.imwrite(str(folder / f"{area}_map.png"), self.render(area))

    def render(self, area: str, zoom: int = 3) -> BgrImage:
        """The area's floor with its spots drawn, for people: crystals red, fungi green, the
        door yellow, repeated clicks (mirrors, rocks) cyan, numbered in plan order."""
        floor = self.floors.get(area)
        if floor is None:
            return np.zeros((10, 10, 3), np.uint8)
        img = cv2.cvtColor(floor.astype(np.uint8) * 90, cv2.COLOR_GRAY2BGR)
        img = cv2.resize(img, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_NEAREST)
        mx, my = self.minimap_roi[0], self.minimap_roi[1]
        order = {a.spot: n for n, a in enumerate(self.plan.get(area, []))}
        for s in self.spots:
            if s.area != area:
                continue
            c = SPOT_COLOURS.get(s.kind, (200, 200, 200))
            x, y = int((s.x - mx) * zoom), int((s.y - my) * zoom)
            cv2.circle(img, (x, y), 4 if s.seen > 1 else 3, c, -1 if s.seen > 1 else 1)
            if s.id in order:
                cv2.putText(img, str(order[s.id]), (x + 4, y - 3), 0, 0.35, c, 1)
        return np.asarray(img, dtype=np.uint8)

    @classmethod
    def load(cls, folder: Path) -> Level | None:
        path = folder / "level.json"
        if not path.exists():
            return None
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("version") != LEVEL_VERSION:
            return None
        lvl = cls(
            target=doc["target"],
            scale=float(doc["scale"]),
            avatar=(int(doc["avatar"][0]), int(doc["avatar"][1])),
            minimap_roi=tuple(int(v) for v in doc["minimap_roi"]),  # type: ignore[arg-type]
            phases=[Phase(**p) for p in doc["phases"]],
            spots=[Spot(**s) for s in doc["spots"]],
            plan={a: [Act(**x) for x in acts] for a, acts in doc["plan"].items()},
            sessions=list(doc.get("sessions", [])),
            reference=str(doc.get("reference", "")),
        )
        for area in {p.area for p in lvl.phases}:
            img = cv2.imread(str(folder / f"{area}_floor.png"), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                lvl.floors[area] = np.asarray(img > 127, dtype=np.bool_)
        return lvl


def level_dir(profiles_dir: Path, target: str) -> Path:
    return profiles_dir / "levels" / target


# --- building from recordings -----------------------------------------------------------


@dataclass
class ItemEvent:
    t: float
    slot: int
    kind: str  # use | pickup
    before: int
    after: int
    click: tuple[int, int] | None = None  # screen point of the click that did it
    click_t: float = 0.0
    dot: tuple[float, float] | None = None
    mm: tuple[float, float] | None = None


def counters(
    meta: list[dict[str, Any]], t0: float, prof: Profile
) -> list[tuple[float, list[tuple[int, int] | None]]]:
    """(route time, [(n, cap) per slot]) from the recorder's OCR snapshots."""
    rois = [tuple(int(v) for v in r) for r in prof.items("item_counter_rois")]
    out: list[tuple[float, list[tuple[int, int] | None]]] = []
    for r in meta:
        if r.get("t") != "ocr" or not r.get("lines"):
            continue
        slots: list[tuple[int, int] | None] = [None] * len(rois)
        for line in r["lines"]:
            for w in line.get("words", []):
                m = COUNTER_RE.fullmatch(str(w["text"]))
                if m is None:
                    continue
                bx, by, bw, bh = w["box"]
                cx, cy = bx + bw / 2, by + bh / 2
                for k, (x, y, rw, rh) in enumerate(rois):
                    if x <= cx < x + rw and y <= cy < y + rh:
                        slots[k] = (int(m.group(1)), int(m.group(2)))
        out.append((float(r["ts"]) - t0, slots))
    return out


def item_events(
    route: Route, meta: list[dict[str, Any]], prof: Profile, lvl: Level, cfg: LevelConfig
) -> list[ItemEvent]:
    """Every counter change, with the world click that caused it placed on the minimap."""
    t0 = float(next(r["ts"] for r in meta if r.get("t") == "keyframe"))
    clicks = [s for s in route.steps if s.kind == "click" and s.zone == "world"]
    prev: list[tuple[int, int] | None] = [None, None]
    out: list[ItemEvent] = []
    for t, slots in counters(meta, t0, prof):
        for slot, c in enumerate(slots):
            if c is None:
                continue
            p = prev[slot]
            first = p is None and c[0] > 0  # the slot only appears with the first pickup
            if first or (p is not None and c[0] != p[0] and c[1] == p[1]):
                before = 0 if p is None else p[0]
                ev = ItemEvent(t, slot, "use" if c[0] < before else "pickup", before, c[0])
                near = [s for s in clicks if -0.3 <= t - s.t <= cfg.use_click_window_s]
                if near:
                    s = near[-1]
                    ev.click, ev.click_t = (s.ax, s.ay), s.t
                    ev.dot = route.dot_at(s.t)
                    if ev.dot is not None:
                        ev.mm = lvl.to_minimap(ev.dot, ev.click)
                out.append(ev)
            prev[slot] = c
    return out


def area_spans(route: Route, lvl: Level) -> list[tuple[str, float, float]]:
    """(area, from, to) in route time, from the objective banners."""
    spans: list[tuple[str, float, float]] = []
    for k, o in enumerate(route.objectives):
        p = lvl.phase_of(o.text)
        if p is None:
            continue
        end = route.objectives[k + 1].t if k + 1 < len(route.objectives) else route.duration
        if spans and spans[-1][0] == p.area:
            spans[-1] = (p.area, spans[-1][1], end)
        else:
            spans.append((p.area, o.t, end))
    return spans


def area_at(spans: list[tuple[str, float, float]], t: float) -> str | None:
    return next((a for a, s, e in spans if s <= t < e), None)


def floor_mask(
    route: Route, span: tuple[float, float], prof: Profile, cfg: LevelConfig, every: int = 3
) -> Mask:
    """The revealed minimap over the span, plus the dot track. A pixel is floor when it is
    bright in at least ``floor_min_frames`` sampled frames (the dot blanked): a revealed
    floor stays revealed, while a zoomed minimap or a flash is bright everywhere for a
    moment and must not count."""
    mx, my, mw, mh = prof.roi("minimap_roi")
    lo, hi = prof.color("dot_hsv")
    ex, ey, ew, eh = prof.roi("minimap_exclude_roi")
    cnt = np.zeros((mh, mw), np.int32)
    track = np.zeros((mh, mw), np.uint8)
    for i, f in enumerate(route.frames):
        if not (span[0] <= f.t < span[1]):
            continue
        if f.dot is None:
            continue  # a loading screen, a dialog over the minimap, the world map
        dx, dy = round(f.dot[0] - mx), round(f.dot[1] - my)
        if 0 <= dx < mw and 0 <= dy < mh:
            track[dy, dx] = 255
        if i % every:
            continue
        mm = route.image(i)[my : my + mh, mx : mx + mw]
        if mm.shape[0] != mh or mm.shape[1] != mw:
            continue
        hsv = cv2.cvtColor(mm, cv2.COLOR_BGR2HSV)
        v = hsv[:, :, 2].copy()
        dot = cv2.dilate(cv2.inRange(hsv, np.array(lo), np.array(hi)), np.ones((9, 9), np.uint8))
        v[dot > 0] = 0
        cnt += v >= cfg.floor_min_v
    cnt[max(0, ey - my) : ey - my + eh, max(0, ex - mx) : ex - mx + ew] = 0
    floor = cnt >= cfg.floor_min_frames
    if cfg.floor_dilate_px > 0:
        k = 2 * cfg.floor_dilate_px + 1
        track = np.asarray(cv2.dilate(track, np.ones((k, k), np.uint8)), dtype=np.uint8)
    return np.asarray(floor | (track > 0), dtype=np.bool_)


def _merge(
    lvl: Level, kind: str, area: str, mm: tuple[float, float], *, run: str, r: float
) -> Spot:
    for s in lvl.spots:
        if s.kind == kind and s.area == area and float(np.hypot(s.x - mm[0], s.y - mm[1])) <= r:
            n = s.seen
            s.x, s.y = (s.x * n + mm[0]) / (n + 1), (s.y * n + mm[1]) / (n + 1)
            s.seen += 1
            if run not in s.runs:
                s.runs.append(run)
            return s
    s = Spot(len(lvl.spots), kind, area, mm[0], mm[1], 1, [run])
    lvl.spots.append(s)
    return s


def build_level(
    sessions: list[Path],
    target: str,
    prof: Profile,
    *,
    cfg: LevelConfig,
    route_cfg: RouteConfig,
    x_tmpl: BgrImage,
) -> Level:
    """The level from the recorded runs; the last session is the reference plan."""
    if not sessions:
        msg = "no sessions to build the level from"
        raise ValueError(msg)
    lvl = Level(
        target=target,
        scale=prof.number("minimap_scale"),
        avatar=prof.point("avatar_xy"),
        minimap_roi=prof.roi("minimap_roi"),
        phases=[Phase(k, a, m, phrase) for phrase, k, a, m in PHASES_BY_TARGET[target]],
        sessions=[s.name for s in sessions],
        reference=sessions[-1].name,
    )
    for session in sessions:
        route = load_or_build(session, target, prof, route_cfg, x_tmpl)
        meta = read_meta(session)
        spans = area_spans(route, lvl)
        for name, s, e in spans:
            m = floor_mask(route, (s, e), prof, cfg)
            lvl.floors[name] = lvl.floors[name] | m if name in lvl.floors else m
        acts: dict[str, list[Act]] = {}
        for ev in item_events(route, meta, prof, lvl, cfg):
            area = area_at(spans, ev.t)
            kind = SPOT_KINDS.get((ev.slot, ev.kind))
            if area is None or ev.mm is None or kind is None:
                continue
            spot = _merge(lvl, kind, area, ev.mm, run=session.name, r=cfg.spot_merge_px)
            acts.setdefault(area, []).append(
                Act(spot.id, ACTIONS[kind], ev.click_t, kind, lvl.phase_at(route, ev.click_t))
            )
        # the door: where the "open the door" banner came
        door = next(
            (o for o in route.objectives if (p := lvl.phase_of(o.text)) and p.key == "door"), None
        )
        if door is not None and (dot := route.dot_at(door.t)) is not None:
            spot = _merge(lvl, "door", "moths", dot, run=session.name, r=DOOR_MERGE_PX)
            acts.setdefault("moths", []).append(Act(spot.id, "door", door.t, "door", "door"))
        # repeated clicks (mirrors, rocks) as spots, for the map
        seen: set[int] = set()
        for st in route.steps:
            if st.kind != "click" or st.zone != "world" or st.group < 0 or st.group in seen:
                continue
            seen.add(st.group)
            area = area_at(spans, st.t)
            dot = route.dot_at(st.t)
            if area is None or dot is None:
                continue  # pragma: no cover - a click with no banner / dot yet
            _merge(
                lvl,
                "repeat",
                area,
                lvl.to_minimap(dot, (st.ax, st.ay)),
                run=session.name,
                r=cfg.spot_merge_px,
            )
        if session is sessions[-1]:
            lvl.plan = {a: sorted(v, key=lambda x: x.t) for a, v in acts.items()}
    return lvl


def finished_sessions(sessions_dir: Path, target: str) -> list[Path]:
    """Finished Learn sessions of ``target`` that reached the completion banner, oldest first."""
    out: list[Path] = []
    for d in sorted(sessions_dir.glob(f"*_{target}*")):
        if not (d / "meta.jsonl").exists():
            continue
        meta = read_meta(d)
        if not any(r.get("t") == "end" for r in meta):
            continue
        texts = " ".join(
            w["text"].lower()
            for r in meta
            if r.get("t") == "ocr"
            for line in r.get("lines", [])
            for w in line.get("words", [])
            if w["box"][1] < 40
        )
        if "complete" in texts:
            out.append(d)
    return out
