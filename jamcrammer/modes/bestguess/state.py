"""Best Guess state detection (KICKOFF §8.3), driven entirely by ``profiles/bestguess.yaml``.

States: WAITING_FOR_ROUND, QUESTION_OPEN, LOCKED, REVEAL, plus UNKNOWN. ANSWER_COMMITTED is a
farmer-side state (we clicked), not something seen on screen.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

import cv2
import numpy as np
import numpy.typing as npt

from jamcrammer.core.config import BestGuessConfig
from jamcrammer.core.profiles import Profile
from jamcrammer.platform.capture import Frame
from jamcrammer.vision.ocr import OcrResult, crop, normalize_text, ocr_image, parse_int

BgrImage = npt.NDArray[np.uint8]


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
    gems: int | None = None
    reveal_side: str | None = None  # "T" | "F" when the reveal ROI says which side was right
    reveal_score: float = 0.0


def dominant_side(reveal: BgrImage, cfg: BestGuessConfig) -> tuple[str | None, float]:
    """Green (True) vs purple (False) dominance inside the reveal ROI, in HSV."""
    hsv = cv2.cvtColor(reveal, cv2.COLOR_BGR2HSV)
    lo_g, hi_g = cfg.true_hsv
    lo_p, hi_p = cfg.false_hsv
    green = cv2.inRange(hsv, np.array(lo_g, np.uint8), np.array(hi_g, np.uint8)).mean() / 255.0
    purple = cv2.inRange(hsv, np.array(lo_p, np.uint8), np.array(hi_p, np.uint8)).mean() / 255.0
    margin = float(abs(green - purple))
    if max(green, purple) < cfg.reveal_min_fill or margin < cfg.reveal_min_margin:
        # not lit, or a half-green/half-purple background: no verdict
        return (None, margin)
    return ("T" if green >= purple else "F", margin)


class BestGuessDetector:
    """Classifies a frame from the fact ROI text, the countdown digits and the reveal ROI."""

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

    def observe(self, image: BgrImage, ts: float | None = None) -> Observation:
        p = self.profile
        fact = self._ocr(crop(image, p.roi("fact_roi"))).text.strip() if p.has("fact_roi") else ""
        norm = normalize_text(fact)
        seconds = None
        if p.has("countdown_roi"):
            seconds = parse_int(self._ocr(crop(image, p.roi("countdown_roi"))).text)
        gems = (
            parse_int(self._ocr(crop(image, p.roi("gem_counter_roi"))).text)
            if p.has("gem_counter_roi")
            else None
        )
        side, score = (None, 0.0)
        if p.has("reveal_roi"):
            side, score = dominant_side(crop(image, p.roi("reveal_roi")), self.cfg)
        is_fact = len(norm) >= self.cfg.fact_min_chars and not any(
            k in norm for k in self.cfg.waiting_keywords
        )
        if is_fact and norm != self._last_fact_norm:
            self._question_opened_ts = ts  # a new fact appeared: the question clock starts
        if is_fact:
            self._last_fact_norm = norm
        elif side is None:
            self._last_fact_norm = ""
        if seconds is None and is_fact and ts is not None and self._question_opened_ts is not None:
            # the countdown digit is often unreadable: fall back to elapsed time
            seconds = max(0, round(self.cfg.question_s - (ts - self._question_opened_ts)))
        if side is not None:
            state = BgState.REVEAL
        elif is_fact:
            if seconds is not None and seconds <= self.cfg.lock_seconds:
                state = BgState.LOCKED
            else:
                state = BgState.QUESTION_OPEN
        else:
            state = BgState.WAITING_FOR_ROUND
        return Observation(state, fact, norm, seconds, gems, side, score)

    def detect(self, frame: Frame) -> str:
        """StateDetector protocol for the replay harness."""
        return str(self.observe(frame.image, frame.ts).state)
