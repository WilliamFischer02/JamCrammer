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
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt

from jamcrammer.core.config import Level, LevelConfig
from jamcrammer.core.events import Event, EventBus
from jamcrammer.core.profiles import Profile
from jamcrammer.modes.adventures import objects, sight
from jamcrammer.modes.adventures.inventory import DigitBank, Inventory, Ocr
from jamcrammer.modes.adventures.level import Act, Spot
from jamcrammer.modes.adventures.level import Level as LevelModel
from jamcrammer.modes.adventures.nav import Floor, Navigator, keys_toward
from jamcrammer.modes.adventures.replay import Input, Presence

BgrImage = npt.NDArray[np.uint8]
AREA = "moths"
DIALOG_GONE_S = 0.6
HELP_AFTER_FAILS = 3
BLOB_KIND = {"light": "unlit", "pick": "fungus", "smash": "decoy"}
SLOT_OF = {"light": 0, "pick": 0, "boom": 1}
DONE = ("lit", "picked", "arrived", "already", "smashed", "nothing")


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
        digits: DigitBank | None = None,
        dialog_x: BgrImage,
        courage: objects.CourageText | None = None,
        presence: Presence | None = None,
        bus: EventBus | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        tick_s: float = 0.05,
        shots: Path | None = None,
    ) -> None:
        """``shots``: a folder for a screenshot of every failed act (what it saw)."""
        self.lvl, self.prof, self.cfg = lvl, prof, cfg
        self._shots = shots
        self._in, self._grab = inp, grab
        self._banner_ocr, self._x, self._courage = banner_ocr, dialog_x, courage
        self._presence, self._bus = presence, bus
        self._clock, self._sleep, self._tick = clock, sleep, tick_s
        self.inv = Inventory(prof, counter_ocr, counter_retry, digits)
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
        self._bad_drops: list[tuple[float, float]] = []  # this act's drops that lit nothing
        self._helped_k = -1  # the act William was already asked to help with

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
                if not self._phase_reached(act):
                    self._help(f"the banner never reached {act.phase} for act {self.k}")
                    continue
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
                self._after(act, outcome)
            return "stopped"
        finally:
            self.nav.release()

    def _shot(self, tag: str, frame: BgrImage | None = None) -> None:
        if self._shots is None:
            return
        try:
            self._shots.mkdir(parents=True, exist_ok=True)
            name = f"{time.strftime('%Y%m%d_%H%M%S')}_{self.k:02d}_{tag}.jpg"
            img = self._grab() if frame is None else frame
            cv2.imwrite(str(self._shots / name), img, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if tag.startswith("walk_") and self.nav.trace:
                (self._shots / (name[:-4] + ".txt")).write_text(
                    "\n".join(self.nav.trace), encoding="utf-8"
                )
        except OSError:
            pass

    def _after(self, act: Act, outcome: str) -> None:
        """Move on, or not: a crystal left dark breaks its moth's path, so a failed light
        asks for help and is tried again; other failures move on, three in a row ask."""
        if outcome in DONE:
            self._fails = 0
            self.k += 1
        elif act.action == "light" and self._helped_k != self.k:
            self._helped_k = self.k
            self._help(f"crystal {act.spot}: {outcome}")
        elif act.action == "light":
            # helped once and it still fails: William could not fix it either (a spot that
            # is not a crystal, one he lit by hand that reads as nothing); on to the next
            self._emit("adventure.skipped", Level.NOTICE, k=self.k, spot=act.spot, outcome=outcome)
            self.stats.skipped += 1
            self.k += 1
        else:
            self._fails += 1
            self.k += 1
            if self._fails >= HELP_AFTER_FAILS:
                self._help(outcome)

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

    def _phase_reached(self, act: Act) -> bool:
        """An act never runs before the banner it happened under in the recording (the door
        after the first moth, the next room): wait, closing dialogs, up to ``phase_wait_s``.
        The 14:13 run went for the corridor's crystal while still in the start room and
        dropped two blossoms on Cosmo."""
        want = self.lvl.phase_index(act.phase)
        if want < 0:
            return True
        end = self._clock() + self.cfg.phase_wait_s
        waited = False
        while not self._stop():
            frame = self._grab()
            self._last_banner = -1e9  # read it now, not at the usual pace
            self._banner(frame)
            if self.lvl.phase_index(self.phase) >= want:
                return True
            if not waited:
                self._emit("adventure.phase_wait", k=self.k, phase=act.phase, now=self.phase)
                waited = True
            if self._clock() >= end:
                return False
            self._close_dialog(frame)
            self._sleep(1.0)
        return False

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
        if act.action == "smash":
            return self._smash(spot)
        if act.action == "light":
            out = ""
            for _attempt in range(self.cfg.light_attempts):
                stock = self._stock(0)
                if not stock and not self._restock():
                    return "no_blossoms"
                out = self._act_at(spot, "light")
                if out in ("lit", "already") or out.startswith("walk_"):
                    return out
            return out
        return self._act_at(spot, act.action)

    def _stock(self, slot: int) -> int | None:
        """The counter as a number, read for up to ``stock_read_s``; None when it never
        reads (the slot is not there yet: nothing was picked)."""
        end = self._clock() + self.cfg.stock_read_s
        while True:
            self.inv.update(self._grab(), self._clock())
            n = self.inv.fresh.get(slot)  # this reading, never a remembered one
            if n is not None or self._clock() >= end or self._stop():
                return n
            self._sleep(self._tick)

    def _act_at(self, spot: Spot, action: str) -> str:
        """Walk to the spot, find the thing, act, verify by the counter. A target already on
        screen (the next crystal of a path, a fungus by it) is walked at by screen; the
        minimap plan is for the hops where nothing of the kind is in view."""
        c = self.cfg
        self._bad_drops = []
        if self._visible(spot, BLOB_KIND[action]) is None:
            res = self.nav.go_to((spot.x, spot.y), stop=self._stop, tol=c.approach_px)
            self.stats.blocks += res.blocks
            if not res.ok and res.reason != "timeout":
                self.stats.walks_failed += 1
                self._emit(
                    "adventure.walk_failed",
                    spot=spot.id,
                    reason=res.reason,
                    dist=round(res.dist, 1),
                    trace=self.nav.trace[-12:],
                )
                self._shot(f"walk_{res.reason}")
                return f"walk_{res.reason}"
        slot = SLOT_OF[action]
        for attempt in range(c.act_retries + 1):
            blob = self._target(spot, action)
            if blob is None:
                if action == "light" and self._already_lit(spot):
                    return "already"
                self._shot("not_seen")
                return "not_seen"
            if action == "light":
                blob = self._approach(blob, "unlit", c.drop_reach_px, bursts=10) or blob
            before_frame = self._grab()
            self.inv.update(before_frame, self._clock())
            before = self.inv.fresh.get(slot)
            if action == "light":
                sx, sy = self.prof.point("item_slot_1")
                if attempt % 2 == 0:
                    self._in.drag(sx, sy, blob.x, blob.y)
                else:  # William's other way: click the slot, then the crystal
                    self._in.click(sx, sy)
                    self._sleep(0.2)
                    self._in.click(blob.x, blob.y)
                if not self._counter_moved(slot, before, "down"):
                    continue
                if self._lit(before_frame, (blob.x, blob.y)):
                    self.stats.lit += 1
                    return "lit"
                self._emit("adventure.dropped_dark", spot=spot.id, at=[blob.x, blob.y])
                self._shot("dropped_dark")
                self._bad_drops.append((float(blob.x), float(blob.y)))
                continue  # a blossom went, the crystal stayed dark: again, closer
            self._in.click(blob.x, blob.y)
            if self._counter_moved(slot, before, "up"):
                self.stats.picked += 1
                return "picked"
        self.stats.skipped += 1
        self._shot("not_taken")
        return "not_taken"

    def _visible(self, spot: Spot, kind: str) -> objects.Blob | None:
        """A blob of the kind near where the spot should be and within ``screen_walk_px``
        of the animal, after any dialog is closed."""
        frame = self._grab()
        if self._close_dialog(frame):
            frame = self._grab()
        dot = sight.minimap_dot(frame, self.prof)
        avatar = (float(self.lvl.avatar[0]), float(self.lvl.avatar[1]))
        pred = self.lvl.to_screen(dot, (spot.x, spot.y)) if dot else avatar
        blobs = (
            objects.crystals(frame, self.prof)
            if kind != "fungus"
            else objects.fungi(frame, self.prof)
        )
        blob = objects.nearest(self._plausible(blobs, pred), pred, kind)
        if blob is None or blob.dist(avatar) > self.cfg.screen_walk_px:
            return None
        return blob

    def _plausible(
        self, blobs: list[objects.Blob], pred: tuple[float, float]
    ) -> list[objects.Blob]:
        """Blobs near where the map says the spot is, and not at a drop point that left a
        crystal dark in this act (Cosmo's blue coat, a decoy, the floor)."""
        c = self.cfg
        return [
            b
            for b in blobs
            if b.dist(pred) <= c.pred_tol_px
            and all(b.dist(bad) > c.bad_drop_px for bad in self._bad_drops)
        ]

    def _approach(
        self, blob: objects.Blob, kind: str, reach: int, bursts: int = 4
    ) -> objects.Blob | None:
        """Walk at the blob in short bursts until it is within ``reach`` of the animal,
        re-finding it (nearest of its kind) after each burst. The blob as last seen."""
        avatar = (float(self.lvl.avatar[0]), float(self.lvl.avatar[1]))
        for _ in range(bursts):
            if blob.dist(avatar) <= reach:
                return blob
            keys = keys_toward(blob.x - avatar[0], blob.y - avatar[1])
            for k in sorted(keys):
                self._in.key_down(k)
            self._sleep(self.cfg.walk_toward_s * 0.5)
            for k in sorted(keys):
                self._in.key_up(k)
            frame = self._grab()
            self._close_dialog(frame)
            blobs = (
                objects.crystals(frame, self.prof)
                if kind != "fungus"
                else objects.fungi(frame, self.prof)
            )
            again = objects.nearest(blobs, (blob.x, blob.y), kind)
            if again is not None:
                blob = again
        return blob

    def _wait_unlit(self, spot: Spot) -> objects.Blob | None:
        """The next crystal of a path turns from muddy to unlit only once the moth has
        reached the one before: wait up to ``unlit_wait_s`` for one near the spot."""
        c = self.cfg
        if getattr(self, "_waited_spot", None) == spot.id:
            return None  # waited here already
        self._waited_spot = spot.id
        end = self._clock() + c.unlit_wait_s
        avatar = (float(self.lvl.avatar[0]), float(self.lvl.avatar[1]))
        while self._clock() < end and not self._stop():
            frame = self._grab()
            self._close_dialog(frame)
            dot = sight.minimap_dot(frame, self.prof)
            pred = self.lvl.to_screen(dot, (spot.x, spot.y)) if dot else avatar
            blobs = self._plausible(objects.crystals(frame, self.prof), pred)
            blob = objects.nearest(blobs, pred, "unlit")
            if blob is not None:
                return blob
            self._sleep(0.5)
        return None

    def _already_lit(self, spot: Spot) -> bool:
        """No unlit crystal at the spot, but a lit one: done before (a duplicate spot, or
        William lit it)."""
        frame = self._grab()
        dot = sight.minimap_dot(frame, self.prof)
        avatar = (float(self.lvl.avatar[0]), float(self.lvl.avatar[1]))
        pred = self.lvl.to_screen(dot, (spot.x, spot.y)) if dot else avatar
        lit = objects.nearest(objects.crystals(frame, self.prof), pred, "lit")
        return lit is not None and lit.dist(pred) <= self.cfg.pred_tol_px

    def _smash(self, spot: Spot) -> str:
        """A cracked rock or a purple crystal: walk there, click it until the Courage pops
        stop (``smash_quiet`` quiet clicks after a hit), or give up after ``smash_probe``
        clicks with no pop at all (nothing breakable there). Never a failure: these are CP,
        not the path."""
        c = self.cfg
        avatar = (float(self.lvl.avatar[0]), float(self.lvl.avatar[1]))
        target = self._visible(spot, "decoy")
        if target is None:
            res = self.nav.go_to((spot.x, spot.y), stop=self._stop, tol=c.approach_px)
            self.stats.blocks += res.blocks
            if not res.ok and res.reason != "timeout":
                self._emit("adventure.walk_failed", spot=spot.id, reason=res.reason, what="smash")
                return "nothing"
        frame = self._grab()
        dot = sight.minimap_dot(frame, self.prof)
        pred = self.lvl.to_screen(dot, (spot.x, spot.y)) if dot else avatar
        purple = objects.nearest(
            self._plausible(objects.crystals(frame, self.prof), pred), pred, "decoy"
        )
        if purple is not None:
            purple = self._approach(purple, "decoy", c.drop_reach_px, bursts=6) or purple
            at = (purple.x, purple.y)
        else:
            at = (round(pred[0]), round(pred[1]))
            if float(np.hypot(at[0] - avatar[0], at[1] - avatar[1])) > c.screen_walk_px:
                return "nothing"  # the rock would be off screen: the spot is off
        hits, clicks = self._click_until_quiet(at)
        self._emit("adventure.smash", spot=spot.id, at=list(at), hits=hits, clicks=clicks)
        return "smashed" if hits else "nothing"

    def _click_until_quiet(self, at: tuple[int, int]) -> tuple[int, int]:
        """Click ``at`` until the Courage pops stop; (hits, clicks)."""
        c = self.cfg
        hits = quiet = n = 0
        for n in range(1, c.smash_clicks + 1):
            self._in.click(at[0], at[1])
            end = self._clock() + c.smash_gap_s
            popped = False
            while self._clock() < end and not self._stop():
                frame = self._grab()
                if self._courage is not None and (
                    self._courage.near(frame, at, c.courage_radius_px) >= self._courage.min
                ):
                    popped = True
                self._sleep(self._tick)
            hits, quiet = (hits + 1, 0) if popped else (hits, quiet + 1)
            if (hits and quiet >= c.smash_quiet) or (not hits and n >= c.smash_probe):
                break
            if self._stop():
                break
        return hits, n

    def _lit(self, before: BgrImage, at: tuple[int, int]) -> bool:
        """The crystal at ``at`` lit up: the Courage text pops near it, or a glow appears
        where it was dark, within ``lit_verify_s`` (the camera still)."""
        c = self.cfg
        end = self._clock() + c.lit_verify_s
        seen = False
        while self._clock() < end and not self._stop():
            frame = self._grab()
            glow = objects.new_glow(before, frame, at, c.glow_radius_px) >= c.glow_new_px
            text = (
                self._courage is not None
                and self._courage.near(frame, at, c.courage_radius_px) >= self._courage.min
            )
            if glow or text:
                seen = True
                # ... and the dark crystal must be gone from the drop point (a glow next to
                # Cosmo's blue coat is the first crystal still shining, not him lighting up)
                still = objects.nearest(objects.crystals(frame, self.prof), at, "unlit")
                if still is None or still.dist(at) > c.bad_drop_px:
                    return True
            self._sleep(0.1)
        if seen:
            self._emit("adventure.lit_but_dark", at=list(at))
        return False

    def _target(self, spot: Spot, action: str) -> objects.Blob | None:
        """What to click for the act: the crystal, or for a pick the paw pin over the
        fungus (the fungus itself may be hidden under the animal by then)."""
        blob = self._find(spot, BLOB_KIND[action])
        if action != "pick":
            return blob
        pin = self._pin(blob) if blob is not None else None
        if pin is None:
            avatar = (float(self.lvl.avatar[0]), float(self.lvl.avatar[1]))
            pin = objects.find_marker(self._grab(), self.prof, avatar)
            if pin is not None and pin.dist(avatar) > self.cfg.pin_search_px:
                pin = None
        return pin if pin is not None else blob

    def _find(self, spot: Spot, kind: str) -> objects.Blob | None:
        """The nearest blob of ``kind`` to where the spot should be on screen, walked up to
        when out of reach. None when nothing of the kind shows after the looks."""
        c = self.cfg
        avatar = (float(self.lvl.avatar[0]), float(self.lvl.avatar[1]))
        last: objects.Blob | None = None
        for _look in range(c.look_retries + 1):
            frame = self._grab()
            if self._close_dialog(frame):
                continue
            dot = sight.minimap_dot(frame, self.prof)
            pred = self.lvl.to_screen(dot, (spot.x, spot.y)) if dot else avatar
            blobs = (
                objects.crystals(frame, self.prof)
                if kind != "fungus"
                else objects.fungi(frame, self.prof)
            )
            blob = objects.nearest(self._plausible(blobs, pred), pred, kind)
            if blob is None and kind == "unlit" and self._wait_unlit(spot) is not None:
                continue  # it turned unlit as the moth arrived: look again
            if blob is None:
                if last is not None:
                    return last  # seen a moment ago; now under the animal or its tag
                self._emit(
                    "adventure.not_seen",
                    spot=spot.id,
                    kind=kind,
                    pred=[round(pred[0]), round(pred[1])],
                )
                return None
            last = blob
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
        """Walk right up to the fungus (``pin_stop_px``) until its paw pin shows, closing
        Cosmo's dialogs on the way (they pop up next to it), then the pin. The 13:04 run
        stood 120 px off and waited: the pin needs the animal at the fungus."""
        c = self.cfg
        avatar = (float(self.lvl.avatar[0]), float(self.lvl.avatar[1]))
        for _look in range(c.pin_looks):
            frame = self._grab()
            if self._close_dialog(frame):
                continue
            pin = objects.find_marker(frame, self.prof, avatar)
            if pin is not None and pin.dist(avatar) <= c.pin_search_px:
                return pin
            near = objects.nearest(objects.fungi(frame, self.prof), (fungus.x, fungus.y))
            if near is not None:
                fungus = near
            if fungus.dist(avatar) <= c.pin_stop_px:
                self._sleep(0.4)  # at it: the pin may just be appearing
                continue
            keys = keys_toward(fungus.x - avatar[0], fungus.y - avatar[1])
            for k in sorted(keys):
                self._in.key_down(k)
            self._sleep(c.walk_toward_s * 0.35)
            for k in sorted(keys):
                self._in.key_up(k)
        self._emit("adventure.no_pin", fungus=[fungus.x, fungus.y])
        self._shot("no_pin")
        return None

    def _counter_moved(self, slot: int, before: int | None, want: str) -> bool:
        """The counter went the wanted way within ``act_verify_s``. A drop with no readable
        stock before is never a success; a pick is, once the slot reads 1+ (it appears with
        the first pickup)."""
        if before is None and want == "down":
            return False
        end = self._clock() + self.cfg.act_verify_s
        while self._clock() < end and not self._stop():
            self.inv.update(self._grab(), self._clock())
            now = self.inv.fresh.get(slot)
            if now is not None:
                if before is None:
                    if now >= 1:
                        return True
                elif (now < before) if want == "down" else (now > before):
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
            message=f"Adventure farmer needs a hand: {why}. Take over; I go on when you let go.",
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
