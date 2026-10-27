"""Best Guess state detection (KICKOFF §8.3), driven by ``profiles/bestguess.yaml``.

Calibrated on William's 2026-10-26 recording:
- the fact sits in a parchment banner at the top (``fact_roi``);
- the reveal replaces it with a big coloured word on the banner's top line
  (``reveal_word_roi``): green "True!" or orange "False!" (False also prints the corrected
  statement below it);
- the top-left HUD shows the round score (``score_roi``), +10 x multiplier per correct answer;
- the countdown is a dial, not digits, so LOCKED comes from elapsed time;
- no parchment in ``fact_roi``, or no colourful gem icon in ``arena_anchor_roi``, means we are
  not in the arena at all (UNKNOWN).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

import cv2
import numpy as np
import numpy.typing as npt
from rapidfuzz import fuzz

from jamcrammer.core.config import BestGuessConfig
from jamcrammer.core.profiles import Profile
from jamcrammer.platform.capture import Frame
from jamcrammer.vision.ocr import OcrResult, crop, normalize_text, ocr_image, parse_int

BgrImage = npt.NDArray[np.uint8]
HsvRange = tuple[tuple[int, int, int], tuple[int, int, int]]
REVEAL_WORD_FUZZ = 80
# OCR renderings of the stylised reveal word seen on real frames
TRUE_ALIASES = frozenset({"true", "truel", "tru", "trve"})
FALSE_ALIASES = frozenset({"false", "fuse", "falsel", "fals", "faise"})


class BgState(StrEnum):
    WAITING_FOR_ROUND = "WAITING_FOR_ROUND"
    QUESTION_OPEN = "QUESTION_OPEN"
    ANSWER_COMMITTED = "ANSWER_COMMITTED"
    LOCKED = "LOCKED"
    REVEAL = "REVEAL"
    INACTIVITY_POPUP = "INACTIVITY_POPUP"
    DISCONNECTED = "DISCONNECTED"
    UNKNOWN = "UNKNOWN"


SCREEN_STATES = (BgState.WAITING_FOR_ROUND, BgState.QUESTION_OPEN, BgState.LOCKED, BgState.REVEAL)


@dataclass
class Observation:
    state: BgState
    fact_text: str = ""
    fact_norm: str = ""
    seconds_left: int | None = None
    gems: int | None = None  # HUD gem total when a gem_counter_roi exists
    score: int | None = None  # round score (top-left in the arena)
    reveal_side: str | None = None  # "T" | "F" when the reveal says which answer was right
    reveal_score: float = 0.0
    banner: bool = True  # the parchment banner is on screen (we are in the arena)


def color_fill(image: BgrImage, rng: HsvRange) -> float:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(rng[0], np.uint8), np.array(rng[1], np.uint8))
    return float(mask.mean() / 255.0)


def two_color_verdict(
    image: BgrImage, a: HsvRange, b: HsvRange, min_fill: float, ratio: float
) -> tuple[str | None, float]:
    """'a' or 'b' when one colour fills at least ``min_fill`` of the region and beats the
    other by ``ratio``; the second value is the winner's fill."""
    fa, fb = color_fill(image, a), color_fill(image, b)
    if max(fa, fb) < min_fill:
        return (None, max(fa, fb))
    if fa >= fb * ratio:
        return ("a", fa)
    if fb >= fa * ratio:
        return ("b", fb)
    return (None, max(fa, fb))


def dominant_side(reveal: BgrImage, cfg: BestGuessConfig) -> tuple[str | None, float]:
    """Green (True) vs orange (False) reveal word."""
    which, fill = two_color_verdict(
        reveal, cfg.true_hsv, cfg.false_hsv, cfg.reveal_min_fill, cfg.reveal_ratio
    )
    return ({"a": "T", "b": "F"}.get(which or ""), fill)


def reveal_side_from_text(norm: str) -> str | None:
    """'true'/'false' as the first OCR word (fuzzy: 'fuse', 'truel')."""
    first = norm.split(" ", 1)[0] if norm else ""
    if not first:
        return None
    if first in TRUE_ALIASES or fuzz.ratio(first, "true") >= REVEAL_WORD_FUZZ:
        return "T"
    if first in FALSE_ALIASES or fuzz.ratio(first, "false") >= REVEAL_WORD_FUZZ:
        return "F"
    return None


def in_arena(image: BgrImage, profile: Profile, cfg: BestGuessConfig) -> bool:
    """Parchment banner in fact_roi and the colourful gem icon in arena_anchor_roi."""
    if profile.has("fact_roi"):
        parchment = color_fill(crop(image, profile.roi("fact_roi")), cfg.parchment_hsv)
        if parchment < cfg.banner_min_parchment:
            return False
    if profile.has("arena_anchor_roi"):
        sat = color_fill(crop(image, profile.roi("arena_anchor_roi")), cfg.anchor_hsv)
        if sat < cfg.anchor_min_fill:
            return False
    return True


class BestGuessDetector:
    """Classifies a frame from the banner text, the reveal word colour and the overlays."""

    def __init__(
        self,
        profile: Profile,
        cfg: BestGuessConfig,
        ocr: Callable[[BgrImage], OcrResult] | None = None,
    ) -> None:
        self.profile = profile
        self.cfg = cfg
        self._ocr = ocr or (lambda img: ocr_image(img, upscale=cfg.ocr_upscale))
        self._last_fact_norm = ""
        self._question_opened_ts: float | None = None

    def _text(self, image: BgrImage, key: str, upscale: float | None = None) -> str:
        if not self.profile.has(key):
            return ""
        roi = crop(image, self.profile.roi(key))
        if upscale is not None:
            return ocr_image(roi, upscale=upscale).text
        return self._ocr(roi).text

    def _in_arena(self, image: BgrImage) -> bool:
        return in_arena(image, self.profile, self.cfg)

    def observe(self, image: BgrImage, ts: float | None = None) -> Observation:
        p = self.profile
        if not self._in_arena(image):
            self._last_fact_norm = ""
            return Observation(BgState.UNKNOWN, banner=False)
        fact = self._text(image, "fact_roi").strip()
        norm = normalize_text(fact)
        seconds = parse_int(self._text(image, "countdown_roi")) if p.has("countdown_roi") else None
        gems = parse_int(self._text(image, "gem_counter_roi")) if p.has("gem_counter_roi") else None
        score = None
        if p.has("score_roi"):
            score = parse_int(self._text(image, "score_roi", self.cfg.score_upscale))
        side, fill = (None, 0.0)
        word_key = next((k for k in ("reveal_word_roi", "reveal_roi") if p.has(k)), None)
        if word_key is not None:
            side, fill = dominant_side(crop(image, p.roi(word_key)), self.cfg)
        text_side = reveal_side_from_text(norm)
        if side is None and text_side is not None:
            side = text_side

        is_fact = (
            side is None
            and len(norm) >= self.cfg.fact_min_chars
            and not any(k in norm for k in self.cfg.waiting_keywords)
        )
        if is_fact and norm != self._last_fact_norm:
            self._question_opened_ts = ts  # a new fact appeared: the question clock starts
        if is_fact:
            self._last_fact_norm = norm
        elif side is None:
            self._last_fact_norm = ""
        if seconds is None and is_fact and ts is not None and self._question_opened_ts is not None:
            seconds = max(0, round(self.cfg.question_s - (ts - self._question_opened_ts)))
        if side is not None:
            state = BgState.REVEAL
        elif is_fact:
            locked = seconds is not None and seconds <= self.cfg.lock_seconds
            state = BgState.LOCKED if locked else BgState.QUESTION_OPEN
        else:
            state = BgState.WAITING_FOR_ROUND
        return Observation(state, fact, norm, seconds, gems, score, side, fill)

    def detect(self, frame: Frame) -> str:
        """StateDetector protocol for the replay harness."""
        return str(self.observe(frame.image, frame.ts).state)
