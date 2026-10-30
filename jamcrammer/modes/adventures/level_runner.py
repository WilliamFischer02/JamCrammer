"""The moth chamber of The Mystery Below from the level model (docs/MYSTERY_BELOW.md):

- the plan is the reference run's acts in order (light this crystal, pick from that fungus),
  each at a spot on the minimap;
- each act is: walk there by the minimap (``nav.py``), find the thing on screen by colour
  (``objects.py``), act on it, and verify by the item counter (``inventory.py``), which is
  exact: a use takes a blossom, a pickup fills the slot;
- out of blossoms: the nearest fungus spot first;
- William's input pauses it; after ``resume_idle_s`` of quiet it goes on with the next act;
- the banner moving past the moth phase ends it: the caller continues by route replay.

Every act's outcome is an event; three failed acts in a row raise a HIGH help event and wait
for William, as the route farmer does."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from jamcrammer.core.config import Level, LevelConfig
from jamcrammer.core.events import Event, EventBus
from jamcrammer.core.profiles import Profile
from jamcrammer.modes.adventures import objects, sight
from jamcrammer.modes.adventures.inventory import Inventory, Ocr
from jamcrammer.modes.adventures.level import Act, Spot
from jamcrammer.modes.adventures.level import Level as LevelModel
from jamcrammer.modes.adventures.nav import Floor, Navigator, keys_toward
from jamcrammer.modes.adventures.replay import Input, Presence

BgrImage = npt.NDArray[np.uint8]
AREA = "moths"
DIALOG_GONE_S = 0.6
HELP_AFTER_FAILS = 3
BLOB_KIND = {"light": "unlit", "pick": "fungus"}
SLOT_OF = {"light": 0, "pick": 0, "boom": 1}


@dataclass
class LevelStats:
    acts: int = 0
    lit: int = 0
    picked: int = 0
    skipped: int = 0
    restocks: int = 0
    walks_failed: int = 0
    blocks: int = 0
    dialogs: int = 0
    user_turns: int = 0
    helps: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d.pop("notes")
        return d


class LevelRunner:
    def __init__(
        self,
        lvl: LevelModel,
        prof: Profile,
        cfg: LevelConfig,
        inp: Input,
        grab: Callable[[], BgrImage],
        *,
        banner_ocr: Callable[[BgrImage], str],
        counter_ocr: Ocr,
        counter_retry: Ocr | None = None,
        dialog_x: BgrImage,
        presence: Presence | None = None,
        bus: EventBus | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        tick_s: float = 0.05,
    ) -> None:
        self.lvl, self.prof, self.cfg = lvl, prof, cfg
        self._in, self._grab = inp, grab
        self._banner_ocr, self._x = banner_ocr, dialog_x
        self._presence, self._bus = presence, bus
        self._clock, self._sleep, self._tick = clock, sleep, tick_s
        self.inv = Inventory(prof, counter_ocr, counter_retry)
        mx, my, _mw, _mh = lvl.minimap_roi
        self.nav = Navigator(
            Floor(lvl.floors[AREA], (mx, my)),
            prof,
            cfg,
            key_down=inp.key_down,
            key_up=inp.key_up,
            grab=grab,
            clock=clock,
            sleep=sleep,
            tick_s=tick_s,
            hook=self._close_dialog,
        )
        self.stats = LevelStats()
        self.state = "IDLE"
        self.phase = ""
        self.k = 0  # next act
        self._stop: Callable[[], bool] = lambda: False
        self._fails = 0
        self._last_banner = -1e9

    @property
    def screen_known(self) -> bool:
        return True

    # --- the loop ------------------------------------------------------------------------

    def run(
        self, should_stop: Callable[[], bool], status: Callable[[str], None] | None = None
    ) -> str:
        """Returns handover (the banner moved past the plan), done (plan finished and the
        banner still there), stopped or help (William never came)."""
        self._stop = should_stop
        self.state = "PLAN"
        acts = self.lvl.plan.get(AREA, [])
        try:
            while not should_stop():
                if self._user_now():
                    self._user_turn()
                    continue
                frame = self._grab()
                beyond = self._banner(frame)
                if beyond:
                    return "handover"
                if self._close_dialog(frame):
                    continue
                if self.k >= len(acts):
                    self.state = "DONE"
                    if status is not None:
                        status(self._status_text())
                    self._sleep(self.cfg.banner_every_s)
                    continue
                act = acts[self.k]
                if status is not None:
                    status(self._status_text())
                outcome = self._do(act)
                self.stats.acts += 1
                self._emit(
                    "adventure.act",
                    action=act.action,
                    spot=act.spot,
                    k=self.k,
                    outcome=outcome,
                    counts=[self.inv.counts.blossoms, self.inv.counts.boomseeds],
                )
                self.k += 1
                if outcome in ("lit", "picked", "arrived", "already"):
                    self._fails = 0
                else:
                    self._fails += 1
                    if self._fails >= HELP_AFTER_FAILS:
                        self._help(outcome)
            return "stopped"
        finally:
            self.nav.release()

    def _status_text(self) -> str:
        c = self.inv.counts
        items = f"blossoms {c.blossoms} seeds {c.boomseeds}"
        return f"{self.state} · {self.phase or '?'} · act {self.k} · {items}"

    # --- banner, dialogs -----------------------------------------------------------------

    def _banner(self, frame: BgrImage) -> bool:
        """Read the objective; True once it is a phase beyond the plan (the door, torches)."""
        now = self._clock()
        if now - self._last_banner < self.cfg.banner_every_s:
            return False
        self._last_banner = now
        text = sight.banner_text(frame, self.prof, self._banner_ocr)
        p = self.lvl.phase_of(text)
        if p is None:
            return False
        if p.key != self.phase:
            self.phase = p.key
            self._emit("adventure.phase", phase=p.key, area=p.area, mode=p.mode)
        return p.mode != "plan"

    def _close_dialog(self, frame: BgrImage) -> bool:
        pt = sight.find_dialog_x(
            frame, self._x, self.prof.number("dialog_x_min"), self.prof.roi("dialog_x_search_roi")
        )
        if pt is None:
            return False
        self.nav.release()

        def gone() -> bool:
            self._sleep(DIALOG_GONE_S)
            return (
                sight.find_dialog_x(
                    self._grab(),
                    self._x,
                    self.prof.number("dialog_x_min"),
                    self.prof.roi("dialog_x_search_roi"),
                )
                is None
            )

        ok = self._in.click(pt[0], pt[1], verify=gone, retries=1)
        self.stats.dialogs += 1
        self._emit("adventure.dialog_closed", ok=ok, x=pt[0], y=pt[1])
        return True

    # --- acts ----------------------------------------------------------------------------

    def _do(self, act: Act) -> str:
        spot = self.lvl.spot(act.spot)
        if act.action == "door":
            res = self.nav.go_to((spot.x, spot.y), stop=self._stop, tol=self.cfg.approach_px)
            self.stats.blocks += res.blocks
            return "arrived" if res.ok else f"walk_{res.reason}"
        if act.action == "light":
            self.inv.update(self._grab(), self._clock())
            if self.inv.counts.blossoms == 0 and not self._restock():
                return "no_blossoms"
        return self._act_at(spot, act.action)

    def _act_at(self, spot: Spot, action: str) -> str:
        """Walk to the spot, find the thing, act, verify by the counter."""
        c = self.cfg
        res = self.nav.go_to((spot.x, spot.y), stop=self._stop, tol=c.approach_px)
        self.stats.blocks += res.blocks
        if not res.ok and res.reason != "timeout":
            self.stats.walks_failed += 1
            self._emit(
                "adventure.walk_failed", spot=spot.id, reason=res.reason, dist=round(res.dist, 1)
            )
            return f"walk_{res.reason}"
        kind = BLOB_KIND[action]
        slot = SLOT_OF[action]
        for attempt in range(c.act_retries + 1):
            blob = self._find(spot, kind)
            if blob is None:
                return "not_seen"
            if action == "pick":
                # the pin with the paw shows over a fungus in reach; that is what takes
                pin = self._pin(blob)
                if pin is not None:
                    blob = pin
            before = self.inv.update(self._grab(), self._clock()).get(slot)
            if action == "light":
                sx, sy = self.prof.point("item_slot_1")
                if attempt % 2 == 0:
                    self._in.drag(sx, sy, blob.x, blob.y)
                else:  # William's other way: click the slot, then the crystal
                    self._in.click(sx, sy)
                    self._sleep(0.2)
                    self._in.click(blob.x, blob.y)
                want = "down"
            else:
                self._in.click(blob.x, blob.y)
                want = "up"
            if self._counter_moved(slot, before, want):
                if action == "light":
                    self.stats.lit += 1
                    return "lit"
                self.stats.picked += 1
                return "picked"
        self.stats.skipped += 1
        return "not_taken"

    def _find(self, spot: Spot, kind: str) -> objects.Blob | None:
        """The nearest blob of ``kind`` to where the spot should be on screen, walked up to
        when out of reach. None when nothing of the kind shows after the looks."""
        c = self.cfg
        avatar = (float(self.lvl.avatar[0]), float(self.lvl.avatar[1]))
        for _look in range(c.look_retries + 1):
            frame = self._grab()
            dot = sight.minimap_dot(frame, self.prof)
            pred = self.lvl.to_screen(dot, (spot.x, spot.y)) if dot else avatar
            blobs = (
                objects.crystals(frame, self.prof)
                if kind != "fungus"
                else objects.fungi(frame, self.prof)
            )
            blob = objects.nearest(blobs, pred, kind)
            if blob is None:
                self._emit(
                    "adventure.not_seen",
                    spot=spot.id,
                    kind=kind,
                    pred=[round(pred[0]), round(pred[1])],
                )
                return None
            if blob.dist(avatar) <= c.reach_px:
                return blob
            # walk at it a little and look again
            keys = keys_toward(blob.x - avatar[0], blob.y - avatar[1])
            for k in sorted(keys):
                self._in.key_down(k)
            self._sleep(c.walk_toward_s)
            for k in sorted(keys):
                self._in.key_up(k)
        return blob

    def _pin(self, fungus: objects.Blob) -> objects.Blob | None:
        """Walk up to the fungus until its paw pin shows (``pin_near_px``), then the pin."""
        c = self.cfg
        avatar = (float(self.lvl.avatar[0]), float(self.lvl.avatar[1]))
        for _look in range(c.look_retries + 1):
            frame = self._grab()
            pin = objects.find_marker(frame, self.prof, (fungus.x, fungus.y))
            if pin is not None and pin.dist((fungus.x, fungus.y)) <= c.pin_near_px:
                return pin
            near = objects.nearest(objects.fungi(frame, self.prof), (fungus.x, fungus.y))
            if near is not None:
                fungus = near
            if fungus.dist(avatar) <= c.pin_near_px * 0.5:
                self._sleep(0.3)  # close already: the pin may just be appearing
                continue
            keys = keys_toward(fungus.x - avatar[0], fungus.y - avatar[1])
            for k in sorted(keys):
                self._in.key_down(k)
            self._sleep(c.walk_toward_s * 0.5)
            for k in sorted(keys):
                self._in.key_up(k)
        self._emit("adventure.no_pin", fungus=[fungus.x, fungus.y])
        return None

    def _counter_moved(self, slot: int, before: int | None, want: str) -> bool:
        end = self._clock() + self.cfg.act_verify_s
        while self._clock() < end and not self._stop():
            now = self.inv.update(self._grab(), self._clock()).get(slot)
            if before is None:
                return now is not None  # unreadable before: any reading counts as done
            if now is not None and ((now < before) if want == "down" else (now > before)):
                return True
            self._sleep(self._tick)
        return False

    def _restock(self) -> bool:
        """Blossoms from the nearest fungus spots (up to three), nearest first."""
        dot = sight.minimap_dot(self._grab(), self.prof)
        here = dot if dot is not None else (self.lvl.avatar[0], self.lvl.avatar[1])
        fungi = sorted(
            self.lvl.spots_of(AREA, "fungus"),
            key=lambda s: float(np.hypot(s.x - here[0], s.y - here[1])),
        )
        for spot in fungi[:3]:
            self.stats.restocks += 1
            out = self._act_at(spot, "pick")
            self._emit("adventure.restock", spot=spot.id, outcome=out)
            if out == "picked":
                return True
        return False

    # --- William --------------------------------------------------------------------------

    def _user_now(self) -> bool:
        return (
            self._presence is not None
            and self._presence.user_active()
            and self._presence.seconds_since_user_input() < self.cfg.resume_idle_s
        )

    def _user_turn(self) -> None:
        assert self._presence is not None
        self.nav.release()
        was = self.state
        self.state = "USER"
        self.stats.user_turns += 1
        self._emit("adventure.user_took_over", k=self.k, was=was)
        while (
            not self._stop() and self._presence.seconds_since_user_input() < self.cfg.resume_idle_s
        ):
            self._sleep(self._tick)
        self.state = "PLAN"
        self._fails = 0
        self._emit("adventure.user_resumed", k=self.k)

    def _help(self, why: str) -> None:
        self.state = "HELP"
        self.stats.helps += 1
        self._emit(
            "adventure.help",
            Level.HIGH,
            k=self.k,
            message=f"Adventure farmer needs a hand: {why} three times running. "
            "Take over; I go on when you let go.",
        )
        while not self._stop() and not self._user_now():
            self._sleep(self._tick)
        if not self._stop():
            self._user_turn()

    def _emit(self, event_type: str, level: Level = Level.INFO, **p: object) -> None:
        if self._bus is not None:
            self._bus.publish(
                Event(type=event_type, source="adventure", level=level, payload=dict(p))
            )
