"""Double Up farmer (KICKOFF §9.2).

Per round: wait for the board → burst-capture the preview → pair the 16 tiles → click each pair
and verify the match as a postcondition (fall back to reveal-and-remember when pairing
confidence is low or a click disagrees) → read the end screen → Play Again → log the round.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from jamcrammer.core.config import DoubleUpConfig, Level
from jamcrammer.core.events import Event, EventBus
from jamcrammer.core.logging import get_logger
from jamcrammer.core.profiles import Profile
from jamcrammer.core.store import Store
from jamcrammer.vision.grid import GridGeometry, crop_tile, describe, pair_board, tile_changed
from jamcrammer.vision.ocr import crop, ocr_image, parse_int

log = get_logger("double_up")

BgrImage = npt.NDArray[np.uint8]
GrabFn = Callable[[], BgrImage]
REQUIRED = (
    "grid_origin",
    "grid_pitch",
    "grid_shape",
    "tile_size",
    "play_again_point",
    "end_screen_gems_roi",
)
STATES = ("WAITING_FOR_BOARD", "PREVIEW", "PAIRING", "CLICKING", "END_SCREEN", "DONE")


class Clicker(Protocol):
    def click(self, cx: int, cy: int) -> bool: ...


@dataclass
class RoundResult:
    started_ts: float
    duration_s: float = 0.0
    pairs_found: int = 0
    mismatches: int = 0
    gems_reported: int | None = None
    strategy: str = "pairing"
    confidence: float = 0.0
    notes: str = ""


@dataclass
class FarmerStats:
    rounds: int = 0
    perfect: int = 0
    gems: int = 0
    mismatches: int = 0
    fallbacks: int = 0
    last_round: RoundResult | None = None
    round_times: list[float] = field(default_factory=list)

    @property
    def median_round_s(self) -> float:
        d = sorted(self.round_times)
        return d[len(d) // 2] if d else 0.0


def geometry_from_profile(profile: Profile) -> GridGeometry:
    ox, oy = profile.point("grid_origin")
    px, py = profile.point("grid_pitch")
    cols, rows = (int(v) for v in profile.items("grid_shape"))
    tw, th = profile.point("tile_size")
    return GridGeometry((ox, oy), (px, py), (cols, rows), (tw, th))


class DoubleUpFarmer:
    def __init__(
        self,
        profile: Profile,
        cfg: DoubleUpConfig,
        clicker: Clicker,
        grab: GrabFn,
        *,
        store: Store | None = None,
        bus: EventBus | None = None,
        ocr: Callable[[BgrImage], str] | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        missing = profile.missing(list(REQUIRED))
        if missing:
            msg = f"double_up profile missing {missing}"
            raise ValueError(msg)
        self.profile = profile
        self.cfg = cfg
        self.geometry = geometry_from_profile(profile)
        self._click = clicker
        self._grab = grab
        self._store = store
        self._bus = bus
        self._ocr = ocr or (lambda img: ocr_image(img, upscale=2.0).text)
        self._clock = clock
        self._sleep = sleep
        self.stats = FarmerStats()
        self.state = "WAITING_FOR_BOARD"
        self._hidden_ref: Any = None

    # --- detection helpers --------------------------------------------------------------------

    def board_visible(self, image: BgrImage) -> bool:
        """The board is on screen when the tile cells look like tiles (not the end screen)."""
        boxes = self.geometry.boxes()
        tiles = [crop_tile(image, b) for b in boxes]
        stds = [float(t.std()) for t in tiles]
        # tiles (hidden or revealed) have structure; a plain background doesn't
        return sum(1 for s in stds if s > self.cfg.tile_min_std) >= len(boxes) * 0.75

    def is_end_screen(self, image: BgrImage) -> bool:
        text = self._ocr(crop(image, self.profile.roi("end_screen_gems_roi"))).lower()
        return any(k in text for k in self.cfg.end_screen_keywords)

    def preview_showing(self, image: BgrImage) -> bool:
        """During the preview every tile is face-up: tiles differ from each other a lot."""
        tiles = [describe(crop_tile(image, b)) for b in self.geometry.boxes()]
        d = [
            tiles[i].distance(tiles[j]) for i in range(len(tiles)) for j in range(i + 1, len(tiles))
        ]
        return bool(d) and float(np.mean(d)) >= self.cfg.preview_diversity

    def all_hidden(self, image: BgrImage) -> bool:
        tiles = [describe(crop_tile(image, b)) for b in self.geometry.boxes()]
        d = [
            tiles[i].distance(tiles[j]) for i in range(len(tiles)) for j in range(i + 1, len(tiles))
        ]
        return bool(d) and float(np.mean(d)) < self.cfg.hidden_uniformity

    # --- one round ----------------------------------------------------------------------------

    def play_round(self) -> RoundResult:
        res = RoundResult(started_ts=self._clock())
        self.state = "WAITING_FOR_BOARD"
        image = self._wait(self.board_visible, self.cfg.board_timeout_s)
        if image is None:
            res.notes = "board never appeared"
            return self._finish(res)
        # preview burst
        self.state = "PREVIEW"
        best = None
        deadline = self._clock() + self.cfg.preview_s
        while self._clock() < deadline:
            frame = self._grab()
            if self.preview_showing(frame):
                pairing = pair_board(frame, self.geometry)
                if best is None or pairing.confidence > best.confidence:
                    best = pairing
            self._sleep(1.0 / self.cfg.burst_fps)
        self.state = "PAIRING"
        if best is not None and best.confidence >= self.cfg.pair_confidence_min:
            res.confidence = best.confidence
            self._emit("double_up.pairs", pairs=best.pairs, confidence=best.confidence)
            self._wait(self.all_hidden, self.cfg.hide_timeout_s)
            ok = self._click_pairs(best.pairs, res)
            if not ok:
                res.strategy = "pairing+remember"
                self.stats.fallbacks += 1
                self._reveal_and_remember(res)
        else:
            res.strategy = "remember"
            self.stats.fallbacks += 1
            self._emit(
                "double_up.fallback", Level.NOTICE, confidence=best.confidence if best else 0.0
            )
            self._wait(self.all_hidden, self.cfg.hide_timeout_s)
            self._reveal_and_remember(res)
        self.state = "END_SCREEN"
        end = self._wait(self.is_end_screen, self.cfg.end_timeout_s)
        if end is not None:
            res.gems_reported = parse_int(
                self._ocr(crop(end, self.profile.roi("end_screen_gems_roi")))
            )
            px, py = self.profile.point("play_again_point")
            self._click.click(px, py)
        else:
            res.notes = "no end screen"
        return self._finish(res)

    def _click_pairs(self, pairs: list[tuple[int, int]], res: RoundResult) -> bool:
        """Click each pair; postcondition = both tiles flipped and stayed. Returns False to
        hand over to reveal-and-remember if a click disagrees with the pairing."""
        for a, b in pairs:
            before = self._grab()
            self._click_tile(a)
            self._click_tile(b)
            self._sleep(self.cfg.flip_ms / 1000.0 + self.cfg.settle_s)
            after = self._grab()
            flipped_a = tile_changed(before, after, self.geometry.box(a), self.cfg.change_threshold)
            flipped_b = tile_changed(before, after, self.geometry.box(b), self.cfg.change_threshold)
            if flipped_a and flipped_b:
                res.pairs_found += 1
                continue
            res.mismatches += 1
            self._emit("double_up.mismatch", Level.NOTICE, pair=[a, b])
            return False
        return True

    def _reveal_and_remember(self, res: RoundResult) -> None:
        """Classic strategy: flip unknown tiles, remember faces, match when a face repeats."""
        known: dict[int, Any] = {}
        matched: set[int] = set()
        order = list(range(self.geometry.count))
        for i in order:
            if i in matched or res.pairs_found >= self.geometry.count // 2:
                continue
            before = self._grab()
            self._click_tile(i)
            self._sleep(self.cfg.settle_s)
            face = self._grab()
            known[i] = describe(crop_tile(face, self.geometry.box(i)))
            partner = next(
                (
                    j
                    for j in known
                    if j != i
                    and j not in matched
                    and known[j].distance(known[i]) <= self.cfg.same_face_distance
                ),
                None,
            )
            if partner is None:
                # open a second tile anyway (the game needs two flips); remember it too
                j = next((k for k in order if k not in matched and k not in known), None)
                if j is None:
                    continue
                self._click_tile(j)
                self._sleep(self.cfg.settle_s)
                face2 = self._grab()
                known[j] = describe(crop_tile(face2, self.geometry.box(j)))
                if known[j].distance(known[i]) <= self.cfg.same_face_distance:
                    matched |= {i, j}
                    res.pairs_found += 1
                else:
                    res.mismatches += 1
                    self._sleep(self.cfg.flip_ms / 1000.0 + self.cfg.settle_s)
                continue
            self._click_tile(partner)
            self._sleep(self.cfg.flip_ms / 1000.0 + self.cfg.settle_s)
            after = self._grab()
            if tile_changed(before, after, self.geometry.box(partner), self.cfg.change_threshold):
                matched |= {i, partner}
                res.pairs_found += 1
            else:
                res.mismatches += 1
        # second pass: pair up everything we remembered
        remaining = [i for i in known if i not in matched]
        while len(remaining) >= 2 and res.pairs_found < self.geometry.count // 2:
            i = remaining.pop(0)
            partner = min(remaining, key=lambda j: known[j].distance(known[i]))
            remaining.remove(partner)
            self._click_tile(i)
            self._click_tile(partner)
            self._sleep(self.cfg.flip_ms / 1000.0 + self.cfg.settle_s)
            matched |= {i, partner}
            res.pairs_found += 1

    def _click_tile(self, index: int) -> None:
        cx, cy = self.geometry.center(index)
        self._click.click(cx, cy)
        self._sleep(self.cfg.click_gap_s)

    def _wait(self, predicate: Callable[[BgrImage], bool], timeout_s: float) -> BgrImage | None:
        deadline = self._clock() + timeout_s
        while True:
            image = self._grab()
            if predicate(image):
                return image
            if self._clock() >= deadline:
                return None
            self._sleep(self.cfg.poll_s)

    def _finish(self, res: RoundResult) -> RoundResult:
        res.duration_s = round(self._clock() - res.started_ts, 2)
        self.stats.rounds += 1
        self.stats.mismatches += res.mismatches
        self.stats.round_times.append(res.duration_s)
        if res.pairs_found == self.geometry.count // 2 and res.mismatches == 0:
            self.stats.perfect += 1
        if res.gems_reported:
            self.stats.gems += res.gems_reported
        self.stats.last_round = res
        if self._store is not None:
            self._store.add_arcade_round(
                ts=res.started_ts,
                game="double_up",
                duration_s=res.duration_s,
                gems_reported=res.gems_reported,
                mismatches=res.mismatches,
                notes=f"{res.strategy} conf={res.confidence} {res.notes}".strip(),
            )
        self._emit(
            "double_up.round",
            pairs=res.pairs_found,
            mismatches=res.mismatches,
            gems=res.gems_reported,
            duration_s=res.duration_s,
            strategy=res.strategy,
        )
        self.state = "DONE"
        return res

    def run(
        self, should_stop: Callable[[], bool], status: Callable[[str], None] | None = None
    ) -> None:
        while not should_stop():
            res = self.play_round()
            if status is not None:
                pairs = self.geometry.count // 2
                status(
                    f"FARMING · round {self.stats.rounds} · {res.pairs_found}/{pairs} · "
                    f"{self.stats.gems} gems"
                )
            if res.notes == "board never appeared":
                self._sleep(self.cfg.poll_s)

    def _emit(self, event_type: str, level: Level = Level.INFO, **p: object) -> None:
        if self._bus is not None:
            self._bus.publish(
                Event(type=event_type, source="double_up", level=level, payload=dict(p))
            )
