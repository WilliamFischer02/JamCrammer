"""A recorded adventure run as a replayable route (docs/ADVENTURES.md §3).

Built from a Learn session (``meta.jsonl`` + keyframes) and cached under its ``analysis/``:

- steps in time order: key downs/ups (key repeat collapsed), clicks and drags in stage
  pixels, each with the zone it hit (world, HUD, item slots, dialog X), the keyframe just
  before it and an anchor patch cut from that frame;
- repeat groups: clicks William repeated on one spot (mirror rotations, rock smashing), with
  the frame that shows their result;
- whether a click changed the item slots (a pickup or a use), so the replay can check that
  it "took";
- per keyframe: the minimap dot, how far the scene moved since the previous one, and a
  localisation thumbnail;
- the objective banner over time (from the recorder's OCR of every keyframe).

Times are seconds since the session's first keyframe.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt
from rapidfuzz import fuzz

from jamcrammer.core.config import RouteConfig
from jamcrammer.core.profiles import Profile
from jamcrammer.learn.recorder import MARKER_KEYS, STOP_KEY, read_meta
from jamcrammer.modes.adventures import sight
from jamcrammer.vision.ocr import normalize_text
from jamcrammer.vision.stage import StageMap

BgrImage = npt.NDArray[np.uint8]
ROUTE_VERSION = 4
DRAG_MIN_PX = 25  # a press and release further apart than this is a drag
DIALOG_X_RADIUS = 40  # a click this close to a dialog's X closes the dialog


@dataclass
class Step:
    t: float
    kind: str  # key | click | drag
    key: str = ""
    down: bool = False
    button: str = "left"
    x: int = 0  # stage point of the press
    y: int = 0
    x2: int = 0  # stage point of the release (drags)
    y2: int = 0
    ax: int = 0  # where the clicked thing was in the ref frame (the view may have moved since)
    ay: int = 0
    dur: float = 0.0  # press to release
    zone: str = "world"  # world | hud | items | dialog
    ref: int = -1  # keyframe index just before the press
    patch: str = ""  # anchor patch file under analysis/route_patches
    group: int = -1  # repeat group id
    group_last: bool = False
    post: int = -1  # keyframe that shows a group's result
    items_change: bool = False  # the recorded click changed the item slots


@dataclass
class Frame:
    t: float
    file: str
    dot: tuple[float, float] | None = None
    motion: float = 0.0  # scene px moved since the previous keyframe
    shift: tuple[float, float] = (0.0, 0.0)  # that motion as (dx, dy) of the content


@dataclass
class Objective:
    t: float
    text: str


@dataclass
class Route:
    session: Path
    target: str
    steps: list[Step] = field(default_factory=list)
    frames: list[Frame] = field(default_factory=list)
    objectives: list[Objective] = field(default_factory=list)
    walk_px_s: float = 0.0
    dot_px_s: float = 0.0
    thumbs: npt.NDArray[np.float32] = field(default_factory=lambda: np.zeros((0, 0), np.float32))
    _images: dict[int, BgrImage] = field(default_factory=dict, repr=False)

    @property
    def duration(self) -> float:
        return self.frames[-1].t if self.frames else 0.0

    @property
    def opening_end(self) -> float:
        """Route time the adventure itself starts: the first objective banner. Before it the
        recording is menus (globe, Adventures, Join, PLAY) in whatever room William was in, so
        nothing there is worth aligning to and the HUD clicks are the same from any room."""
        return self.objectives[0].t if self.objectives else 0.0

    @property
    def completion_t(self) -> float:
        """Route time of the completion banner (the last objective change). The frames after
        it are the prize and the exit, ending in the room the next run starts from."""
        return self.objectives[-1].t if len(self.objectives) > 1 else self.duration

    def inside(self, t: float) -> bool:
        return self.opening_end <= t < self.completion_t

    # --- lookups -------------------------------------------------------------------------

    def frame_index_at(self, t: float) -> int:
        lo, hi = 0, len(self.frames) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.frames[mid].t <= t:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def image(self, i: int) -> BgrImage:
        img = self._images.get(i)
        if img is None:
            loaded = cv2.imread(str(self.session / self.frames[i].file))
            img = np.asarray(loaded if loaded is not None else np.zeros((1080, 1920, 3)), np.uint8)
            if len(self._images) > IMAGE_CACHE:
                self._images.clear()
            self._images[i] = img
        return img

    def patch(self, step: Step) -> BgrImage | None:
        if not step.patch:
            return None
        img = cv2.imread(str(self.session / "analysis" / "route_patches" / step.patch))
        return None if img is None else np.asarray(img, np.uint8)

    def dot_at(self, t: float) -> tuple[float, float] | None:
        """The recorded dot at ``t`` (nearest keyframe that has one, within a second)."""
        i = self.frame_index_at(t)
        for j in (i, i + 1, i - 1, i + 2, i - 2):
            if 0 <= j < len(self.frames):
                f = self.frames[j]
                if f.dot is not None and abs(f.t - t) <= 1.0:
                    return f.dot
        return None

    def objective_index_at(self, t: float) -> int:
        idx = -1
        for k, o in enumerate(self.objectives):
            if o.t <= t:
                idx = k
        return idx

    def keys_down_at(self, t: float) -> set[str]:
        held: set[str] = set()
        for s in self.steps:
            if s.t > t:
                break
            if s.kind == "key":
                (held.add if s.down else held.discard)(s.key)
        return held

    def next_step_index(self, t: float) -> int:
        return next((k for k, s in enumerate(self.steps) if s.t > t), len(self.steps))

    # --- cache ---------------------------------------------------------------------------

    def save(self) -> None:
        out = self.session / "analysis"
        out.mkdir(parents=True, exist_ok=True)
        doc = {
            "version": ROUTE_VERSION,
            "target": self.target,
            "walk_px_s": self.walk_px_s,
            "dot_px_s": self.dot_px_s,
            "steps": [asdict(s) for s in self.steps],
            "frames": [asdict(f) for f in self.frames],
            "objectives": [asdict(o) for o in self.objectives],
        }
        (out / "route.json").write_text(json.dumps(doc), encoding="utf-8")
        np.save(out / "route_thumbs.npy", self.thumbs)

    @classmethod
    def load(cls, session: Path) -> Route | None:
        path = session / "analysis" / "route.json"
        thumbs = session / "analysis" / "route_thumbs.npy"
        if not path.exists() or not thumbs.exists():
            return None
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("version") != ROUTE_VERSION:
            return None
        frames = []
        for f in doc["frames"]:
            dot = tuple(f["dot"]) if f.get("dot") else None
            shift = tuple(f.get("shift", (0.0, 0.0)))
            frames.append(Frame(f["t"], f["file"], dot, f.get("motion", 0.0), shift))
        return cls(
            session=session,
            target=doc["target"],
            steps=[Step(**s) for s in doc["steps"]],
            frames=frames,
            objectives=[Objective(**o) for o in doc["objectives"]],
            walk_px_s=doc["walk_px_s"],
            dot_px_s=doc["dot_px_s"],
            thumbs=np.load(thumbs),
        )


IMAGE_CACHE = 64  # recorded frames kept decoded


def _in(roi: tuple[int, int, int, int] | list[int], x: int, y: int) -> bool:
    rx, ry, rw, rh = roi
    return rx <= x < rx + rw and ry <= y < ry + rh


def _zone(prof: Profile, x: int, y: int, ref_img: BgrImage | None, x_tmpl: BgrImage) -> str:
    if _in(prof.roi("items_roi"), x, y):
        return "items"
    if any(_in(r, x, y) for r in prof.items("hud_rois")):
        return "hud"
    if ref_img is not None:
        # only where in-adventure dialogs open: the world map has its own X (top right)
        pt = sight.find_dialog_x(
            ref_img, x_tmpl, prof.number("dialog_x_min"), prof.roi("dialog_x_search_roi")
        )
        if (
            pt is not None
            and abs(pt[0] - x) <= DIALOG_X_RADIUS
            and abs(pt[1] - y) <= DIALOG_X_RADIUS
        ):
            return "dialog"
    return "world"


def _input_steps(meta: list[dict[str, Any]], t0: float, stage: StageMap) -> list[Step]:
    """Key holds (repeat collapsed) and clicks/drags inside the client, in stage pixels."""
    skip = {*MARKER_KEYS, STOP_KEY}
    steps: list[Step] = []
    held: set[str] = set()
    pressed: dict[str, dict[str, Any]] = {}
    for r in meta:
        if r.get("t") != "input":
            continue
        t = float(r["ts"]) - t0
        if r["kind"] == "key":
            k = str(r["key"])
            if k in skip:
                continue
            if r["event"] == "press" and k not in held:
                held.add(k)
                steps.append(Step(t, "key", key=k, down=True))
            elif r["event"] == "release" and k in held:
                held.discard(k)
                steps.append(Step(t, "key", key=k, down=False))
            continue
        b = str(r.get("button", "left"))
        if r["event"] == "press":
            pressed[b] = r
            continue
        p = pressed.pop(b, None)
        if p is None or not p.get("inside") or p.get("cx") is None or r.get("cx") is None:
            continue
        x, y = stage.to_stage(int(p["cx"]), int(p["cy"]))
        x2, y2 = stage.to_stage(int(r["cx"]), int(r["cy"]))
        kind = "drag" if np.hypot(x2 - x, y2 - y) > DRAG_MIN_PX else "click"
        steps.append(
            Step(
                float(p["ts"]) - t0,
                kind,
                button=b,
                x=x,
                y=y,
                x2=x2,
                y2=y2,
                dur=t - (float(p["ts"]) - t0),
            )
        )
    for k in sorted(held):  # the session ended with a key down
        steps.append(Step(steps[-1].t if steps else 0.0, "key", key=k, down=False))
    return sorted(steps, key=lambda s: s.t)


def _objectives(
    meta: list[dict[str, Any]], t0: float, prof: Profile, cfg: RouteConfig
) -> list[Objective]:
    """Banner text over time from the recorder's OCR: a new text counts once it is read on
    ``objective_min_hits`` snapshots in a row."""
    bx, by, bw, bh = prof.roi("banner_roi")
    out: list[Objective] = []
    cand: tuple[float, str, int] | None = None
    for r in meta:
        if r.get("t") != "ocr" or not r.get("lines"):
            continue
        words = [
            w["text"]
            for line in r["lines"]
            for w in line.get("words", [])
            if _in((bx, by, bw, bh), w["box"][0] + w["box"][2] // 2, w["box"][1] + w["box"][3] // 2)
        ]
        text = normalize_text(" ".join(words))
        if len(text) < OBJECTIVE_MIN_CHARS:
            continue
        if out and same_objective(text, out[-1].text, cfg.objective_fuzzy):
            cand = None
            continue
        if cand is not None and same_objective(text, cand[1], cfg.objective_fuzzy):
            cand = (cand[0], cand[1], cand[2] + 1)
        else:
            cand = (float(r["ts"]) - t0, text, 1)
        if cand[2] >= cfg.objective_min_hits:
            out.append(Objective(cand[0], cand[1]))
            cand = None
    return out


OBJECTIVE_MIN_CHARS = 12  # shorter banner reads are partial


def same_objective(a: str, b: str, threshold: int) -> bool:
    """Banner texts are the same objective: word-set match, because OCR of the banner sometimes
    starts mid-sentence ("deeper into the cit use the boomseeds to find a wa")."""
    return fuzz.token_set_ratio(a, b) >= threshold


def build_route(
    session: Path, target: str, prof: Profile, cfg: RouteConfig, x_tmpl: BgrImage
) -> Route:
    meta = read_meta(session)
    head = next(r for r in meta if r.get("t") == "session")
    cr = head["window"]["client_rect"]
    stage = StageMap(cr[2] - cr[0], cr[3] - cr[1])
    kfs = sorted((r for r in meta if r.get("t") == "keyframe"), key=lambda r: r["ts"])
    t0 = float(kfs[0]["ts"])
    route = Route(session=session, target=target)
    route.frames = [Frame(float(r["ts"]) - t0, str(r["file"])) for r in kfs]
    route.steps = _input_steps(meta, t0, stage)
    route.objectives = _objectives(meta, t0, prof, cfg)

    thumbs: list[npt.NDArray[np.float32]] = []
    prev_gray: sight.Gray | None = None
    for i, f in enumerate(route.frames):
        img = route.image(i)
        f.dot = sight.minimap_dot(img, prof)
        thumbs.append(sight.thumb(img, prof, cfg.thumb_w, cfg.thumb_h))
        g = sight.masked_gray(img, prof, cfg.align_scale, cfg.highpass_sigma)
        if prev_gray is not None:
            dx, dy, resp = sight.scene_shift(prev_gray, g, cfg.align_scale)
            if resp >= cfg.scene_min_response:
                f.motion, f.shift = float(np.hypot(dx, dy)), (dx, dy)
        prev_gray = g
    route.thumbs = np.stack(thumbs) if thumbs else np.zeros((0, 0), np.float32)
    _annotate_clicks(route, prof, cfg, x_tmpl)
    route.walk_px_s, route.dot_px_s = _speeds(route, cfg)
    route.save()
    return route


def _annotate_clicks(route: Route, prof: Profile, cfg: RouteConfig, x_tmpl: BgrImage) -> None:
    pdir = route.session / "analysis" / "route_patches"
    pdir.mkdir(parents=True, exist_ok=True)
    half = cfg.patch_px // 2
    mice = [s for s in route.steps if s.kind in ("click", "drag")]
    group, last = -1, None
    for n, s in enumerate(mice):
        s.ref = route.frame_index_at(s.t)
        img = route.image(s.ref)
        s.zone = _zone(prof, s.x, s.y, img, x_tmpl)
        s.ax, s.ay = _anchor(route, s)
        y0, x0 = max(0, s.ay - half), max(0, s.ax - half)
        patch = img[y0 : s.ay + half, x0 : s.ax + half]
        if patch.shape[0] >= half and patch.shape[1] >= half:
            s.patch = f"{n:05d}.png"
            cv2.imwrite(str(pdir / s.patch), patch)
        after = route.frame_index_at(s.t + s.dur + cfg.item_check_s)
        moved = sum(f.motion for f in route.frames[s.ref + 1 : after + 1])
        s.items_change = (
            moved <= cfg.items_still_px  # walking changes the slots' area when they are hidden
            and sight.region_change(img, route.image(after), prof.roi("items_roi"))
            >= cfg.item_change_min
        )
        same = (
            last is not None
            and s.kind == "click"
            and last.kind == "click"
            and np.hypot(s.x - last.x, s.y - last.y) <= cfg.repeat_px
            and s.t - last.t <= cfg.repeat_window_s
        )
        if same and last is not None:
            if last.group < 0:
                group += 1
                last.group = group
            s.group = last.group
        last = s
    by_group: dict[int, list[Step]] = {}
    for s in mice:
        if s.group >= 0:
            by_group.setdefault(s.group, []).append(s)
    for members in by_group.values():
        end = members[-1]
        end.group_last = True
        end.post = route.frame_index_at(end.t + end.dur + cfg.post_frame_s)


def _anchor(route: Route, s: Step) -> tuple[int, int]:
    """Where the clicked thing was in the ref frame. That keyframe can be up to a fifth of a
    second older than the press, and if the view was scrolling the thing has moved since:
    undo the share of the next keyframe's scene shift that had happened by the press."""
    if s.zone != "world" or s.ref + 1 >= len(route.frames):
        return (s.x, s.y)
    a, b = route.frames[s.ref], route.frames[s.ref + 1]
    if b.t <= a.t:
        return (s.x, s.y)
    f = min(1.0, max(0.0, (s.t - a.t) / (b.t - a.t)))
    return (round(s.x - f * b.shift[0]), round(s.y - f * b.shift[1]))


def _speeds(route: Route, cfg: RouteConfig) -> tuple[float, float]:
    """Median scene and dot speed while a movement key was held."""
    walk: list[float] = []
    dots: list[float] = []
    held: set[str] = set()
    k = 0
    for a, b in zip(route.frames, route.frames[1:], strict=False):
        while k < len(route.steps) and route.steps[k].t <= a.t:
            s = route.steps[k]
            if s.kind == "key":
                (held.add if s.down else held.discard)(s.key)
            k += 1
        dt = b.t - a.t
        if dt <= 0 or not held & MOVE_KEYS:
            continue
        if b.motion > 0:
            walk.append(b.motion / dt)
        if a.dot is not None and b.dot is not None:
            d = float(np.hypot(b.dot[0] - a.dot[0], b.dot[1] - a.dot[1]))
            if d > 0:
                dots.append(d / dt)
    return (
        statistics.median(walk) if walk else cfg.walk_px_s,
        statistics.median(dots) if dots else cfg.dot_px_s,
    )


MOVE_KEYS = {"left", "right", "up", "down"}


def latest_session(sessions_dir: Path, target: str) -> Path | None:
    """The longest finished Learn session of ``target``."""
    best: tuple[float, Path] | None = None
    for d in sessions_dir.glob(f"*_{target}*"):
        meta = read_meta(d)
        end = next((r for r in meta if r.get("t") == "end"), None)
        if end is None:
            continue
        st = end.get("stats", {})
        span = float(st.get("ended_ts", 0)) - float(st.get("started_ts", 0))
        if best is None or span > best[0]:
            best = (span, d)
    return best[1] if best else None


def locate(
    route: Route,
    th: npt.NDArray[np.float32],
    dot: tuple[float, float] | None,
    idx: npt.NDArray[np.intp],
    dot_weight: float,
) -> tuple[float, float]:
    """(route time, score) of the frame among ``idx`` most like a live thumbnail: thumbnail
    correlation, less ``dot_weight`` per minimap px between the dots."""
    if idx.size == 0:
        return (0.0, -1.0)
    sims = route.thumbs[idx] @ th
    if dot is not None:
        for n, j in enumerate(idx):
            fd = route.frames[int(j)].dot
            if fd is not None:
                sims[n] -= dot_weight * float(np.hypot(dot[0] - fd[0], dot[1] - fd[1]))
    k = int(np.argmax(sims))
    return (route.frames[int(idx[k])].t, float(sims[k]))


VALIDATE_EVERY = 5  # every 5th keyframe is a query
VALIDATE_EXCLUDE_S = 1.0  # its own moment is left out of the route ...
VALIDATE_TOL_S = 4.0  # ... and a neighbour within this counts as found


def validate_route(route: Route, cfg: RouteConfig) -> tuple[float, int]:
    """Leave-out localisation (the replay gate for route farmers): each sampled keyframe is
    located within ``resync_window_s`` of its time with its own +-1 s removed; it is found when
    the answer is within 4 s. Returns (accuracy, queries)."""
    times = np.array([f.t for f in route.frames])
    hits = n = 0
    for q in range(0, len(route.frames), VALIDATE_EVERY):
        t = times[q]
        near = np.abs(times - t)
        idx = np.nonzero((near <= cfg.resync_window_s) & (near > VALIDATE_EXCLUDE_S))[0]
        if idx.size == 0:
            continue
        found, _score = locate(route, route.thumbs[q], route.frames[q].dot, idx, cfg.dot_weight)
        hits += abs(found - t) <= VALIDATE_TOL_S
        n += 1
    return (hits / n if n else 0.0, n)


def load_or_build(
    session: Path, target: str, prof: Profile, cfg: RouteConfig, x_tmpl: BgrImage
) -> Route:
    return Route.load(session) or build_route(session, target, prof, cfg, x_tmpl)


@dataclass
class SessionInfo:
    """What the Adventures page lists about a recorded run (read cheaply: the first and last
    lines of meta.jsonl, the cached route's header)."""

    path: Path
    started: str
    minutes: float
    finished: bool
    route_steps: int = 0
    route_clicks: int = 0
    route_objectives: int = 0
    route_current: bool = False  # the cached route matches this code's ROUTE_VERSION


TAIL_BYTES = 8192  # the end record is the last line of meta.jsonl


def session_info(session: Path) -> SessionInfo:
    meta = session / "meta.jsonl"
    started, span, finished = session.name[:15], 0.0, False
    try:
        with meta.open("rb") as fh:
            head = json.loads(fh.readline() or b"{}")
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - TAIL_BYTES))
            tail = fh.read().decode("utf-8", "replace").strip().splitlines()
        end = json.loads(tail[-1]) if tail else {}
        if end.get("t") == "end":
            st = end.get("stats", {})
            span = float(st.get("ended_ts", 0)) - float(
                st.get("started_ts", head.get("started_ts", 0))
            )
            finished = True
    except (OSError, ValueError):
        pass
    info = SessionInfo(session, started, span / 60.0, finished)
    rj = session / "analysis" / "route.json"
    if rj.exists():
        try:
            doc = json.loads(rj.read_text(encoding="utf-8"))
            info.route_current = doc.get("version") == ROUTE_VERSION
            info.route_steps = len(doc.get("steps", []))
            info.route_clicks = sum(1 for s in doc.get("steps", []) if s.get("kind") != "key")
            info.route_objectives = len(doc.get("objectives", []))
        except (OSError, ValueError):
            pass
    return info


def list_sessions(sessions_dir: Path, target: str) -> list[SessionInfo]:
    """Recorded runs of ``target``, newest first."""
    if not sessions_dir.exists():
        return []
    dirs = sorted((d for d in sessions_dir.glob(f"*_{target}*") if d.is_dir()), reverse=True)
    return [session_info(d) for d in dirs]
