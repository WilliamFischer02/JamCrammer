"""Route farmer for adventures (The Mystery Below first; docs/ADVENTURES.md §3).

It replays William's recorded run step by step and keeps it on course:

- **clicks** land on the recorded target found again live: the anchor patch is searched around
  where the scene alignment puts it (world clicks move with the camera; HUD, item-slot and
  dialog clicks do not). A target that does not show within ``click_wait_s`` is clicked at the
  predicted point and logged as unverified;
- **dialogs** are closed with their blue X whenever one is open (a recorded X click with no
  dialog on screen is skipped);
- **items**: a click that changed the item slots in the recording (a pickup or a use) must
  change them again, otherwise it is retried; clicking an item slot must change the cursor
  (the item is held) or the slots, otherwise it is retried;
- **repeat groups** (mirror rotations, rock smashing): after the group the area around it must
  look like the recorded result (the full beam), otherwise one more click, up to
  ``repeat_extra_max``;
- **course**: every ``align_every_s`` the minimap dot is compared with the recorded dot at the
  same route time and the scene with the recorded frame; timing drift moves the route clock,
  position drift walks back (closed loop on the dot or the scene);
- **stuck**: a held movement key that moves neither the dot nor the scene, where the recording
  moved, is stuck. A stored fix for that spot is tried first, then a walk toward where the
  route is a moment later, with sidesteps; then it asks for help;
- **progress**: the objective banner. A later objective than expected moves the route forward;
  an earlier one past ``behind_grace_s`` retries the segment from up to ``retry_back_s``
  before the change, at most ``segment_retries`` times, then asks for help;
- **William steering**: any input of his pauses it (keys released). When his hands have been
  off for ``resume_idle_s`` it finds where it is on the route and continues. What he did and
  where it resumed is stored as a fix that is tried first the next time it is stuck there.

Every correction is an ``adventure.*`` event and a line in ``data/adventures/<target>_fixes.jsonl``.
"""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt
from rapidfuzz import fuzz

from jamcrammer.core.config import Level, RouteConfig
from jamcrammer.core.events import Event, EventBus
from jamcrammer.core.profiles import Profile
from jamcrammer.modes.adventures import sight
from jamcrammer.modes.adventures.route import MOVE_KEYS, Route, Step, locate

BgrImage = npt.NDArray[np.uint8]
PROGRESS_EPS = 0.5  # an error this much smaller counts as progress (dot or scene px)
DIALOG_GONE_S = 0.6  # a dialog's X must disappear this soon after the click


class Input(Protocol):
    def key_down(self, key: str) -> None: ...
    def key_up(self, key: str) -> None: ...
    def click(
        self,
        cx: int,
        cy: int,
        *,
        button: Any = ...,
        verify: Callable[[], bool] | None = ...,
        retries: int | None = ...,
    ) -> bool: ...
    def drag(  # noqa: PLR0917 - InputController.drag's signature
        self, x0: int, y0: int, x1: int, y1: int, steps: int = ..., hold_ms: int = ...
    ) -> bool: ...
    def move(self, cx: int, cy: int) -> bool: ...


class Presence(Protocol):
    def user_active(self) -> bool: ...
    def seconds_since_user_input(self) -> float: ...


class FixLog:
    """Corrections (William's and its own), one JSON line each; the latest one near a route time
    that worked is offered again there."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.entries: list[dict[str, Any]] = []
        if path is not None and path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.entries.append(json.loads(line))

    def add(self, entry: dict[str, Any]) -> None:
        entry = {"logged": time.strftime("%Y-%m-%d %H:%M:%S"), **entry}
        self.entries.append(entry)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")

    def near(self, route: str, rt: float, within: float) -> dict[str, Any] | None:
        hits = [
            e
            for e in self.entries
            if e.get("route") == route
            and e.get("inputs")
            and e.get("ok", True)
            and abs(float(e.get("rt", -1e9)) - rt) <= within
        ]
        return hits[-1] if hits else None


class Tape:
    """William's own keys and clicks (injected ones are the bot's) while he steers."""

    def __init__(self, to_stage: Callable[[int, int], tuple[int, int] | None]) -> None:
        self._to_stage = to_stage
        self._on = False
        self._t0 = 0.0
        self.events: list[dict[str, Any]] = []
        self._listeners: list[Any] = []

    def start(self) -> None:
        from pynput import keyboard, mouse

        def key(k: Any, pressed: bool, injected: bool) -> None:
            if self._on and not injected:
                name = getattr(k, "char", None) or str(getattr(k, "name", k))
                self.events.append(
                    {"dt": time.time() - self._t0, "kind": "key", "key": str(name), "down": pressed}
                )

        def click(x: int, y: int, button: Any, pressed: bool, injected: bool = False) -> None:
            if self._on and not injected and pressed:
                pt = self._to_stage(int(x), int(y))
                if pt is not None:
                    self.events.append(
                        {
                            "dt": time.time() - self._t0,
                            "kind": "click",
                            "button": str(button).rsplit(".", 1)[-1],
                            "x": pt[0],
                            "y": pt[1],
                        }
                    )

        kl = keyboard.Listener(
            on_press=lambda k, injected=False: key(k, True, injected),
            on_release=lambda k, injected=False: key(k, False, injected),
        )
        ml = mouse.Listener(on_click=click)
        for li in (kl, ml):
            li.daemon = True
            li.start()
        self._listeners = [kl, ml]

    def begin(self) -> None:
        self.events, self._t0, self._on = [], time.time(), True

    def take(self) -> list[dict[str, Any]]:
        self._on = False
        return list(self.events)

    def stop(self) -> None:
        for li in self._listeners:
            li.stop()
        self._listeners = []


@dataclass
class RouteStats:
    clicks: int = 0
    unverified: int = 0
    skipped: int = 0
    retries: int = 0
    extra_clicks: int = 0
    dialogs: int = 0
    course_fixes: int = 0
    warps: int = 0
    stuck: int = 0
    user_turns: int = 0
    jumps: int = 0
    helps: int = 0
    runs: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d.pop("notes")
        return d


class RouteRunner:
    def __init__(
        self,
        route: Route,
        prof: Profile,
        cfg: RouteConfig,
        inp: Input,
        grab: Callable[[], BgrImage],
        *,
        dialog_x: BgrImage,
        presence: Presence | None = None,
        bus: EventBus | None = None,
        ocr: Callable[[BgrImage], str] | None = None,
        fixes: FixLog | None = None,
        tape: Tape | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        cursor: Callable[[], int] = sight.cursor_handle,
    ) -> None:
        self.route, self.prof, self.cfg = route, prof, cfg
        self._in, self._grab = inp, grab
        self._x = dialog_x
        self._presence, self._bus, self._ocr = presence, bus, ocr
        self.fixes = fixes or FixLog(None)
        self._tape = tape
        self._clock, self._sleep, self._cursor = clock, sleep, cursor
        self.stats = RouteStats()
        self.state = "IDLE"
        self.paused_reason = ""
        self.rt = 0.0
        self.i = 0
        self.held: set[str] = set()
        self._stop: Callable[[], bool] = lambda: False
        self._ref_gray: dict[int, sight.Gray] = {}
        self._motion: deque[tuple[float, tuple[float, float] | None, sight.Gray]] = deque()
        self._held_since: float | None = None
        self._behind_since: float | None = None
        self._hold_since: float | None = None
        self._retries: dict[int, int] = {}
        self._walk = route.walk_px_s or cfg.walk_px_s
        self._dot_speed = route.dot_px_s or cfg.dot_px_s

    @property
    def screen_known(self) -> bool:
        """The runner handles its own dialogs: the watchdog's unknown-dialog layer stays out."""
        return True

    # --- the loop ------------------------------------------------------------------------

    def run(
        self, should_stop: Callable[[], bool], status: Callable[[str], None] | None = None
    ) -> None:
        self._stop = should_stop
        try:
            self._start()
            self._loop(status)
        finally:
            self._release_all()

    def _start(self) -> None:
        frame = self._grab()
        t, score = self.localize(frame, None, None)
        if score >= self.cfg.start_min_sim and t > 0:
            self._jump(t, "start", score)
        else:
            self._jump(0.0, "start_from_the_top", score)
        self.state = "PLAY"

    def _loop(self, status: Callable[[str], None] | None) -> None:
        c = self.cfg
        last = self._last_obj = self._last_align = self._clock()
        while not self._stop() and not self.paused_reason and self.state != "DONE":
            now = self._clock()
            if self._presence is not None and self._presence.user_active():
                self._user_turn()
                last = self._last_obj = self._last_align = self._clock()
                continue
            if self.state == "HELP":
                self._sleep(c.tick_s)
                last = self._clock()
                continue
            if self._guards(self._grab(), now):
                self._sleep(c.tick_s)  # acted or holding: never spin
                last = self._clock()  # the route clock waited with it
                continue
            if self._behind_since is None:
                self.rt += now - last
            last = self._advance(now)
            if status is not None:
                status(self._status_text())
            self._sleep(max(0.0, c.tick_s - (self._clock() - now)))

    def _guards(self, frame: BgrImage, now: float) -> bool:
        """Dialog, objective, course and stuck checks; True when one of them acted."""
        c = self.cfg
        if self._close_dialog(frame):
            return True
        if self._hold_since is not None and now - self._last_align < c.align_every_s:
            return True  # still waiting for the recorded scene
        if now - self._last_obj >= c.objective_every_s:
            self._last_obj = now
            if self._objective(frame):
                return True
        if now - self._last_align >= c.align_every_s:
            self._last_align = now
            if self._scene_gate(frame, now) or self._course(frame):
                return True
        return self._check_stuck(frame, now)

    def _advance(self, now: float) -> float:
        """Run the steps that are due; returns the clock the route time is counted from."""
        last = now
        while self.i < len(self.route.steps) and self.route.steps[self.i].t <= self.rt:
            step = self.route.steps[self.i]
            self.i += 1
            if self._do(step):
                last = self._clock()  # a blocking step: the route clock waited with it
        if self.i >= len(self.route.steps):
            self._release_all()
            self.stats.runs += 1
            self._emit("adventure.route_done", runs=self.stats.runs)
            if self.cfg.loop:
                self._jump(0.0, "loop")
            else:
                self.state = "DONE"
        return last

    def _status_text(self) -> str:
        k = self.route.objective_index_at(self.rt)
        obj = self.route.objectives[k].text[:48] if k >= 0 else "opening"
        return (
            f"{self.state} · {self.rt:.0f}/{self.route.duration:.0f}s · {obj} · "
            f"{self.stats.clicks} clicks ({self.stats.unverified} unverified) · "
            f"{self.stats.course_fixes} fixes · {self.stats.user_turns} by you"
        )

    # --- steps ---------------------------------------------------------------------------

    def _do(self, s: Step) -> bool:
        """Run one step; True when it blocked (the route clock must not run on)."""
        if s.kind == "key":
            (self._down if s.down else self._up)(s.key)
            return False
        self._mouse(s)
        return True

    def _down(self, key: str) -> None:
        if key not in self.held:
            self._in.key_down(key)
            self.held.add(key)
            if key in MOVE_KEYS and self._held_since is None:
                self._held_since = self._clock()

    def _up(self, key: str) -> None:
        if key in self.held:
            self._in.key_up(key)
            self.held.discard(key)
        if not self.held & MOVE_KEYS:
            self._held_since = None
            self._motion.clear()

    def _release_all(self) -> None:
        for k in sorted(self.held):
            self._in.key_up(k)
        self.held.clear()
        self._held_since = None
        self._motion.clear()

    def _reconcile(self) -> None:
        """Hold exactly the keys the route holds at the current route time."""
        want = self.route.keys_down_at(self.rt)
        for k in sorted(self.held - want):
            self._up(k)
        for k in sorted(want - self.held):
            self._down(k)

    def _jump(
        self, t: float, reason: str, score: float | None = None, keep_steps: bool = False
    ) -> None:
        """Move the route clock. ``keep_steps`` (timing corrections, getting unstuck): no step
        runs twice and none is skipped, pending ones simply come due sooner or later. Without
        it (start, objective, segment retry, after William) the next step follows the time."""
        before = self.rt
        self.rt = max(0.0, min(t, self.route.duration))
        if not keep_steps:
            self.i = self.route.next_step_index(self.rt)
        self._reconcile()
        self._behind_since = None
        self._hold_since = None
        self.stats.jumps += reason not in ("start", "start_from_the_top")
        self._emit(
            "adventure.jump",
            reason=reason,
            rt_from=round(before, 1),
            rt_to=round(self.rt, 1),
            score=None if score is None else round(score, 3),
        )

    # --- clicks --------------------------------------------------------------------------

    def _gray(self, img: BgrImage) -> sight.Gray:
        return sight.masked_gray(img, self.prof, self.cfg.align_scale, self.cfg.highpass_sigma)

    def _ref(self, i: int) -> sight.Gray:
        g = self._ref_gray.get(i)
        if g is None:
            if len(self._ref_gray) > REF_CACHE:
                self._ref_gray.clear()
            g = self._gray(self.route.image(i))
            self._ref_gray[i] = g
        return g

    def _offset(self, live: BgrImage, ref_i: int) -> tuple[float, float] | None:
        dx, dy, resp = sight.scene_shift(self._ref(ref_i), self._gray(live), self.cfg.align_scale)
        return (dx, dy) if resp >= self.cfg.scene_min_response else None

    def _locate(self, s: Step) -> tuple[int, int, bool, float]:
        """Where to click: the recorded target found live, else the predicted point."""
        c = self.cfg
        patch = self.route.patch(s)
        deadline = self._clock() + c.click_wait_s
        base = (s.ax, s.ay) if s.zone == "world" and (s.ax or s.ay) else (s.x, s.y)
        pred = base
        score = -1.0
        released = False
        while True:
            live = self._grab()
            if s.zone == "world":
                off = self._offset(live, s.ref)
                if off is not None:
                    pred = (round(base[0] + off[0]), round(base[1] + off[1]))
            if patch is not None:
                m = sight.find_patch(live, patch, pred, c.patch_radius_px)
                if m is not None:
                    score = m[2]
                    near = float(np.hypot(m[0] - pred[0], m[1] - pred[1])) <= c.patch_accept_px
                    if m[2] >= c.patch_min_score and near:
                        return (m[0], m[1], True, m[2])
            if self._clock() >= deadline or self._stop() or self._user_now():
                return (pred[0], pred[1], False, score)
            if not released and self.held & MOVE_KEYS:
                for k in sorted(self.held & MOVE_KEYS):
                    self._up(k)
                released = True
            self._sleep(c.tick_s)

    def _mouse(self, s: Step) -> None:
        if s.zone == "dialog":
            if not self._close_dialog(self._grab()):
                self.stats.skipped += 1
                self._emit("adventure.click_skipped", step=self.i - 1, why="no dialog open")
            return
        x, y, verified, score = self._locate(s)
        if not verified:
            self.stats.unverified += 1
        cursor0 = self._cursor()
        before = self._grab()
        retries = self._act(s, x, y)
        if s.zone == "items" and s.kind == "click":
            retries += self._expect(
                lambda: self._cursor() != cursor0 or self._items_changed(before),
                lambda: self._act(s, x, y),
            )
        elif s.items_change:
            retries += self._expect(lambda: self._items_changed(before), lambda: self._act(s, x, y))
            if retries >= self.cfg.item_retries and not self._items_changed(before):
                retries += self._closer_and_again(s, before)
        extra = self._finish_group(s, x, y) if s.group_last and s.post >= 0 else 0
        self.stats.clicks += 1
        self.stats.retries += retries
        self.stats.extra_clicks += extra
        self._reconcile()
        self._emit(
            "adventure.click",
            step=self.i - 1,
            zone=s.zone,
            kind=s.kind,
            verified=verified,
            score=round(score, 3),
            x=x,
            y=y,
            dx=x - s.x,
            dy=y - s.y,
            retries=retries,
            extra=extra,
        )

    def _act(self, s: Step, x: int, y: int) -> int:
        if s.kind == "drag":
            x2, y2 = self._drop_point(s, x, y)
            self._in.drag(x, y, x2, y2, steps=self.cfg.drag_steps)
        else:
            self._in.click(x, y, button=s.button)
        return 0

    def _closer_and_again(self, s: Step, before: BgrImage) -> int:
        """The item did not take from here (the paw icon only shows up close): walk to where
        William stood for this click, find the target again and click once more."""
        ok, *_ = self._walk_to(s.t)
        x, y, verified, _score = self._locate(s)
        self._act(s, x, y)
        took = self._expect(lambda: self._items_changed(before), lambda: 0) < self.cfg.item_retries
        self._emit(
            "adventure.item_retry_closer", step=self.i - 1, walked=ok, verified=verified, took=took
        )
        return 1

    def _drop_point(self, s: Step, x: int, y: int) -> tuple[int, int]:
        """A world drag moves with its start; a drag out of the item slots drops on the world
        point, which moves with the camera."""
        if s.zone == "world":
            return (x + (s.x2 - s.x), y + (s.y2 - s.y))
        off = self._offset(self._grab(), s.ref) or (0.0, 0.0)
        return (round(s.x2 + off[0]), round(s.y2 + off[1]))

    def _items_changed(self, before: BgrImage) -> bool:
        roi = self.prof.roi("items_roi")
        return sight.region_change(before, self._grab(), roi) >= self.cfg.item_change_min

    def _expect(self, ok: Callable[[], bool], again: Callable[[], int]) -> int:
        """Wait ``item_check_s`` for ``ok``; redo the action up to ``item_retries`` times."""
        for attempt in range(self.cfg.item_retries + 1):
            deadline = self._clock() + self.cfg.item_check_s
            while self._clock() < deadline:
                if ok():
                    return attempt
                self._sleep(self.cfg.tick_s)
            if attempt < self.cfg.item_retries:
                again()
        self._emit("adventure.item_not_taken", Level.NOTICE, step=self.i - 1, rt=round(self.rt, 1))
        return self.cfg.item_retries

    def _finish_group(self, s: Step, x: int, y: int) -> int:
        """Mirror / rock: click until the area looks like the recorded result."""
        c = self.cfg
        post = self.route.image(s.post)
        half = (c.patch_px * 2, c.patch_px * 2)
        extra = 0
        while extra <= c.repeat_extra_max:
            self._sleep(c.post_frame_s)
            live = self._grab()
            off = self._offset(live, s.post) or (0.0, 0.0)
            if sight.local_similarity(_shift(post, off), live, (x, y), half) >= c.post_min_sim:
                return extra
            if extra == c.repeat_extra_max or self._stop() or self._user_now():
                break
            self._in.click(x, y, button=s.button)
            extra += 1
        self._emit("adventure.group_unmatched", Level.NOTICE, step=self.i - 1, extra=extra)
        return extra

    # --- dialogs and progress ------------------------------------------------------------

    def _close_dialog(self, frame: BgrImage) -> bool:
        pt = sight.find_dialog_x(
            frame, self._x, self.prof.number("dialog_x_min"), self.prof.roi("dialog_x_search_roi")
        )
        if pt is None:
            return False

        def gone() -> bool:
            deadline = self._clock() + DIALOG_GONE_S
            while self._clock() < deadline:
                if (
                    sight.find_dialog_x(
                        self._grab(),
                        self._x,
                        self.prof.number("dialog_x_min"),
                        self.prof.roi("dialog_x_search_roi"),
                    )
                    is None
                ):
                    return True
                self._sleep(self.cfg.tick_s)
            return False

        for k in sorted(self.held & MOVE_KEYS):
            self._up(k)
        ok = self._in.click(pt[0], pt[1], verify=gone, retries=self.cfg.dialog_retries)
        self.stats.dialogs += 1
        self._emit("adventure.dialog_closed", ok=ok, x=pt[0], y=pt[1], rt=round(self.rt, 1))
        self._reconcile()
        return True

    def _objective(self, frame: BgrImage) -> bool:
        """Banner says we are ahead: move on. Behind for too long: retry the segment."""
        if self._ocr is None or not self.route.objectives:
            return False
        text = sight.banner_text(frame, self.prof, self._ocr)
        if not text:
            return False
        scores = [fuzz.token_set_ratio(text, o.text) for o in self.route.objectives]
        k_live = int(np.argmax(scores))
        if scores[k_live] < self.cfg.objective_fuzzy:
            return False
        k_exp = self.route.objective_index_at(self.rt)
        if k_live > k_exp:
            self._jump(self.route.objectives[k_live].t, "objective_ahead")
            return True
        if k_live == k_exp or k_exp < 0:
            self._behind_since = None
            return False
        return self._behind(k_live)

    def _behind(self, k_live: int) -> bool:
        """The route already moved past objective ``k_live``; the game has not. Hold still for
        ``behind_grace_s`` (a moth still flying), then retry the segment."""
        now = self._clock()
        if self._behind_since is None:
            self._behind_since = now
            self._release_all()
            self._emit(
                "adventure.behind",
                objective=self.route.objectives[k_live].text,
                rt=round(self.rt, 1),
            )
            return True
        if now - self._behind_since < self.cfg.behind_grace_s:
            return False
        return self._retry_segment(k_live)

    def _retry_segment(self, k_live: int) -> bool:
        k_next = k_live + 1  # the change the game has not made yet
        change = self.route.objectives[k_next].t
        n = self._retries.get(k_next, 0)
        if n >= self.cfg.segment_retries:
            self._help(f"objective still '{self.route.objectives[k_live].text}' after {n} retries")
            return True
        self._retries[k_next] = n + 1
        start = max(self.route.objectives[k_live].t, change - self.cfg.retry_back_s)
        self._jump(start, f"retry_segment_{n + 1}")
        self._go_to(start, "segment_start")
        return True

    # --- course --------------------------------------------------------------------------

    def _scene_gate(self, frame: BgrImage, now: float) -> bool:
        """The recording shows another scene at this route time (the level still loading, the
        drop to the next floor, a cutscene): hold the route clock until the live view catches
        up; after ``scene_hold_max_s`` re-localise, else ask for help."""
        c = self.cfg
        i = self.route.frame_index_at(self.rt)
        sim = float(self.route.thumbs[i] @ sight.thumb(frame, self.prof, c.thumb_w, c.thumb_h))
        # the same scene merely shifted (drift) still aligns: that is the course's job, no hold
        if sim >= c.scene_hold_sim or self._offset(frame, i) is not None:
            if self._hold_since is not None:
                self._emit("adventure.scene_ready", held_s=round(now - self._hold_since, 1))
                self._hold_since = None
            return False
        if self._hold_since is None:
            self._hold_since = now
            for k in sorted(self.held & MOVE_KEYS):
                self._up(k)
            self._emit("adventure.scene_wait", sim=round(sim, 3), rt=round(self.rt, 1))
        elif now - self._hold_since > c.scene_hold_max_s:
            self._hold_since = None
            t, sure, score = self._resume_point(frame, self.rt)
            if sure:
                self._jump(t, "scene_resync", score)
            else:
                self._help(
                    f"the screen has not looked like the route for {c.scene_hold_max_s:.0f}s"
                )
        return True

    def _course(self, frame: BgrImage) -> bool:
        """Drift: the minimap dot catches the gross kind (one minimap px is ~70 scene px), the
        scene alignment the fine kind. Only timing off: move the route clock; else walk back."""
        c = self.cfg
        live_dot = sight.minimap_dot(frame, self.prof)
        ref_dot = self.route.dot_at(self.rt)
        gross = (
            live_dot is not None
            and ref_dot is not None
            and float(np.hypot(live_dot[0] - ref_dot[0], live_dot[1] - ref_dot[1])) > c.drift_dot_px
        )
        if not gross:
            off = self._offset(frame, self.route.frame_index_at(self.rt))
            if off is None or np.hypot(*off) <= c.drift_scene_px:
                return False
        t = self._warp(frame, live_dot if gross else None)
        if t is not None:
            self._jump(t, "timing", keep_steps=True)
            self.stats.warps += 1
            return True
        return self._go_to(self.rt, "drift")

    def _warp(self, frame: BgrImage, live_dot: tuple[float, float] | None) -> float | None:
        """A route time within ``warp_window_s`` where the recording looks like now."""
        c = self.cfg
        lo = self.route.frame_index_at(self.rt - c.warp_window_s)
        hi = self.route.frame_index_at(self.rt + c.warp_window_s)
        best: tuple[float, float] | None = None
        for j in range(lo, hi + 1):
            f = self.route.frames[j]
            if live_dot is not None and f.dot is not None:
                err = float(np.hypot(live_dot[0] - f.dot[0], live_dot[1] - f.dot[1]))
                tol = c.drift_dot_px
            else:
                off = self._offset(frame, j)
                if off is None:
                    continue
                err, tol = float(np.hypot(*off)), float(c.servo_tol_scene_px)
            if err <= tol and (best is None or err < best[0]):
                best = (err, f.t)
        return best[1] if best else None

    def _go_to(self, t: float, reason: str) -> bool:
        """Walk back to where the route is at ``t``; still lost: the stuck handling."""
        self._release_all()
        ok, by_dot, by_scene, bursts = self._walk_to(t)
        self.stats.course_fixes += ok
        self._emit(
            "adventure.course",
            reason=reason,
            ok=ok,
            dot=by_dot,
            scene=by_scene,
            bursts=bursts,
            rt=round(self.rt, 1),
        )
        self._reconcile()
        if not ok:
            return self._unstick("course " + reason)
        return True

    def _walk_to(self, t: float) -> tuple[bool, str, str, int]:
        """By the dot while it is far, then by the scene. (ok, dot, scene, bursts)."""
        i = self.route.frame_index_at(t)
        target = self.route.dot_at(t)
        bursts = 0
        by_dot = "blind"
        if target is not None:

            def dot_err() -> tuple[float, float] | None:
                d = sight.minimap_dot(self._grab(), self.prof)
                return None if d is None else (target[0] - d[0], target[1] - d[1])

            by_dot, n = self._servo(dot_err, self.cfg.servo_tol_dot_px, self._dot_speed)
            bursts += n
        by_scene, n = self._servo(
            lambda: self._offset(self._grab(), i), float(self.cfg.servo_tol_scene_px), self._walk
        )
        bursts += n
        ok = by_scene == "ok" or (by_scene == "blind" and by_dot == "ok")
        return (ok, by_dot, by_scene, bursts)

    def _servo(
        self, err: Callable[[], tuple[float, float] | None], tol: float, speed: float
    ) -> tuple[str, int]:
        """Closed loop: hold the arrow keys toward the error, short bursts, sidestep when no
        progress. (+x error = walk right, +y = walk down.) Returns ("ok" | "fail" | "blind",
        bursts); blind = it could not measure the error at all."""
        c = self.cfg
        deadline = self._clock() + c.servo_timeout_s
        best = float("inf")
        no_prog = sidesteps = bursts = 0
        while self._clock() < deadline and not self._stop() and not self._user_now():
            e = err()
            if e is None:
                return ("blind" if bursts == 0 else "fail", bursts)
            mag = float(np.hypot(*e))
            if mag <= tol:
                return ("ok", bursts)
            if mag < best - PROGRESS_EPS:
                best, no_prog = mag, 0
            else:
                no_prog += 1
            if no_prog >= c.servo_no_progress:
                if sidesteps >= MAX_SIDESTEPS:
                    return ("fail", bursts)
                sidesteps += 1
                no_prog = 0
                e = (-e[1], e[0]) if sidesteps % 2 else (e[1], -e[0])  # perpendicular
            keys = _keys_toward(e, tol / 2)
            burst = min(max(mag / speed, c.servo_burst_min_s), c.servo_burst_max_s)
            for k in keys:
                self._in.key_down(k)
            self._sleep(burst)
            for k in keys:
                self._in.key_up(k)
            bursts += 1
        return ("fail", bursts)

    # --- stuck, help, William ------------------------------------------------------------

    def _check_stuck(self, frame: BgrImage, now: float) -> bool:
        c = self.cfg
        if not self.held & MOVE_KEYS or self._held_since is None:
            return False
        self._motion.append((now, sight.minimap_dot(frame, self.prof), self._gray(frame)))
        while self._motion and now - self._motion[0][0] > c.stuck_window_s:
            self._motion.popleft()
        still = self._still_for(now)
        if still is None or not self._route_moved(self.rt - still, self.rt):
            return False  # moving, or William stood still here too (a wall, an NPC talking)
        return self._unstick("no motion with a key held")

    def _still_for(self, now: float) -> float | None:
        """Seconds without dot or scene motion while a movement key is held, or None."""
        c = self.cfg
        held_since = self._held_since
        if held_since is None or now - held_since < c.stuck_window_s or len(self._motion) < 2:
            return None
        (t0, d0, g0), (_t1, d1, g1) = self._motion[0], self._motion[-1]
        if now - t0 < c.stuck_window_s * STUCK_FILL:
            return None
        dot_moved = (
            d0 is not None
            and d1 is not None
            and float(np.hypot(d1[0] - d0[0], d1[1] - d0[1])) > c.stuck_dot_px
        )
        dx, dy, resp = sight.scene_shift(g0, g1, c.align_scale)
        scene_moved = resp >= c.scene_min_response and float(np.hypot(dx, dy)) > c.stuck_min_px
        return None if dot_moved or scene_moved else now - t0

    def _route_moved(self, a: float, b: float) -> bool:
        da, db = self.route.dot_at(a), self.route.dot_at(b)
        if da is not None and db is not None:
            return bool(np.hypot(db[0] - da[0], db[1] - da[1]) > self.cfg.stuck_dot_px)
        i, j = self.route.frame_index_at(a), self.route.frame_index_at(b)
        return sum(f.motion for f in self.route.frames[i + 1 : j + 1]) > self.cfg.stuck_min_px

    def _unstick(self, why: str) -> bool:
        self.stats.stuck += 1
        self._release_all()
        self._emit("adventure.stuck", Level.NOTICE, why=why, rt=round(self.rt, 1))
        fix = self.fixes.near(self.route.session.name, self.rt, self.cfg.fix_match_s)
        if fix is not None:
            self._play_inputs(fix["inputs"])
            t, ok, score = self._resume_point(self._grab(), self.rt)
            self.fixes.add(
                {
                    "route": self.route.session.name,
                    "rt": round(self.rt, 2),
                    "reason": "reuse",
                    "from": fix.get("logged"),
                    "ok": ok,
                    "inputs": [],
                }
            )
            if ok:
                self._jump(t, "stored_fix", score)
                return True
        target = self.rt + self.cfg.lookahead_s
        ok, _by_dot, _by_scene, bursts = self._walk_to(target)
        self.fixes.add(
            {
                "route": self.route.session.name,
                "rt": round(self.rt, 2),
                "reason": "self",
                "why": why,
                "ok": ok,
                "bursts": bursts,
                "inputs": [],
            }
        )
        if ok:
            self._jump(target, "unstuck", keep_steps=True)
            return True
        self._help(f"stuck ({why})")
        return True

    def _help(self, why: str) -> None:
        self._release_all()
        self.state = "HELP"
        self.stats.helps += 1
        self._emit(
            "adventure.help",
            Level.HIGH,
            rt=round(self.rt, 1),
            message=f"Adventure farmer needs a hand: {why}. Move me where I should be; "
            "I continue when you let go.",
        )

    def _user_now(self) -> bool:
        return self._presence is not None and self._presence.user_active()

    def _user_turn(self) -> None:
        """William steers: release everything, record what he does, resume where he left it."""
        self._release_all()
        before, prev = self.rt, self.state
        self.state = "USER"
        self.stats.user_turns += 1
        self._emit("adventure.user_took_over", rt=round(before, 1), was=prev)
        if self._tape is not None:
            self._tape.begin()
        assert self._presence is not None
        while not self._stop() and (
            self._presence.user_active()
            or self._presence.seconds_since_user_input() < self.cfg.resume_idle_s
        ):
            self._sleep(self.cfg.tick_s * 4)
        inputs = self._tape.take() if self._tape is not None else []
        if self._stop():
            return
        t, ok, score = self._resume_point(self._grab(), before)
        self.fixes.add(
            {
                "route": self.route.session.name,
                "rt": round(before, 2),
                "rt_after": round(t, 2),
                "reason": "user",
                "was": prev,
                "score": round(score, 3),
                "ok": ok,
                "inputs": inputs,
            }
        )
        self._emit(
            "adventure.user_resumed",
            rt_from=round(before, 1),
            rt_to=round(t, 1),
            score=round(score, 3),
            inputs=len(inputs),
            ok=ok,
        )
        self.state = "PLAY"
        old_i = self.i
        self._jump(t if ok else before, "after_you" if ok else "after_you_unsure", score)
        skipped = [s for s in self.route.steps[old_i : self.i] if s.kind != "key"]
        if skipped and not any(ev.get("kind") == "click" for ev in inputs):
            # he only walked: the clicks the route passed on the way are still to be done
            self._emit("adventure.catch_up", clicks=len(skipped), rt=round(self.rt, 1))
            for s in skipped:
                if self._stop() or self._user_now():
                    break
                self._mouse(s)

    def _play_inputs(self, inputs: list[dict[str, Any]]) -> None:
        """Replay a stored correction (William's keys and clicks, his timing)."""
        t0 = self._clock()
        held: set[str] = set()
        for ev in inputs:
            wait = float(ev["dt"]) - (self._clock() - t0)
            if wait > 0:
                self._sleep(wait)
            if self._stop() or self._user_now():
                break
            if ev["kind"] == "key" and ev["key"] in MOVE_KEYS:
                if ev["down"] and ev["key"] not in held:
                    self._in.key_down(ev["key"])
                    held.add(ev["key"])
                elif not ev["down"] and ev["key"] in held:
                    self._in.key_up(ev["key"])
                    held.discard(ev["key"])
            elif ev["kind"] == "click":
                self._in.click(int(ev["x"]), int(ev["y"]), button=ev.get("button", "left"))
        for k in sorted(held):
            self._in.key_up(k)

    # --- localisation --------------------------------------------------------------------

    def localize(
        self, frame: BgrImage, around: float | None, window: float | None
    ) -> tuple[float, float]:
        """(route time, score) of the recorded frame most like ``frame`` (``route.locate``)."""
        r = self.route
        th = sight.thumb(frame, self.prof, self.cfg.thumb_w, self.cfg.thumb_h)
        if around is None or window is None:
            idx = np.arange(len(r.frames))
        else:
            lo, hi = r.frame_index_at(around - window), r.frame_index_at(around + window)
            idx = np.arange(lo, hi + 1)
        dot = sight.minimap_dot(frame, self.prof)
        return locate(r, th, dot, idx, self.cfg.dot_weight)

    def _resume_point(self, frame: BgrImage, around: float) -> tuple[float, bool, float]:
        """Where to continue after a stop: from ``resume_back_s`` before ``around`` to
        ``resume_ahead_s`` after it, the minimap dot first, then closeness to ``around``, then
        the thumbnail. (time, sure, score); sure = the dot is right there or the view matches."""
        c, r = self.cfg, self.route
        lo, hi = (
            r.frame_index_at(around - c.resume_back_s),
            r.frame_index_at(around + c.resume_ahead_s),
        )
        idx = np.arange(lo, hi + 1)
        th = sight.thumb(frame, self.prof, c.thumb_w, c.thumb_h)
        sims = r.thumbs[idx] @ th
        dot = sight.minimap_dot(frame, self.prof)
        times = np.array([r.frames[int(j)].t for j in idx])
        if dot is None:
            score = sims - np.abs(times - around) / c.resync_window_s
            k = int(np.argmax(score))
            return (float(times[k]), bool(sims[k] >= c.resync_min_sim), float(sims[k]))
        dist = np.array(
            [
                min(c.resume_dot_cap, float(np.hypot(dot[0] - fd[0], dot[1] - fd[1])))
                if (fd := r.frames[int(j)].dot) is not None
                else c.resume_dot_cap
                for j in idx
            ]
        )
        score = -dist - np.abs(times - around) / c.resume_time_scale_s + RESUME_THUMB_WEIGHT * sims
        k = int(np.argmax(score))
        sure = bool(dist[k] <= c.resume_dot_px or sims[k] >= c.resync_min_sim)
        return (float(times[k]), sure, float(sims[k]))

    def _emit(self, event_type: str, level: Level = Level.INFO, **p: object) -> None:
        if self._bus is not None:
            self._bus.publish(
                Event(type=event_type, source="adventure", level=level, payload=dict(p))
            )


REF_CACHE = 48  # aligned recorded frames kept
RESUME_THUMB_WEIGHT = 0.5  # the thumbnail breaks ties between frames at the same dot
MAX_SIDESTEPS = 4
STUCK_FILL = 0.8  # the motion window must cover this much of stuck_window_s


def _keys_toward(e: tuple[float, float], dead: float) -> list[str]:
    keys = []
    if abs(e[0]) > dead:
        keys.append("right" if e[0] > 0 else "left")
    if abs(e[1]) > dead:
        keys.append("down" if e[1] > 0 else "up")
    return keys or (["right" if e[0] >= 0 else "left"])


def _shift(img: BgrImage, off: tuple[float, float]) -> BgrImage:
    """``img`` moved by ``off`` px (to compare a recorded result with the live view)."""
    import cv2

    m = np.array([[1, 0, off[0]], [0, 1, off[1]]], dtype=np.float32)
    return np.asarray(cv2.warpAffine(img, m, (img.shape[1], img.shape[0])), np.uint8)
