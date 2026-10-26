"""Pluggable 'which answer was correct' strategies (KICKOFF §8.3). The farmer runs all of
them on every reveal and trusts the ranked vote; Learn's leaderboard ranks them offline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

import cv2
import numpy as np
import numpy.typing as npt

from jamcrammer.core.config import BestGuessConfig
from jamcrammer.core.profiles import Profile
from jamcrammer.modes.bestguess.state import Observation, dominant_side
from jamcrammer.vision.ocr import crop

BgrImage = npt.NDArray[np.uint8]
Side = Literal["T", "F"]


@dataclass(frozen=True)
class Verdict:
    strategy: str
    side: Side | None
    confidence: float


class Learner(Protocol):
    name: str

    def judge(
        self,
        before: BgrImage,
        reveal: BgrImage,
        obs: Observation,
        chosen: Side | None,
        gems_before: int | None,
    ) -> Verdict: ...


class RevealColorLearner:
    """The reveal ROI lights green (True) or purple (False)."""

    name = "reveal_color"

    def __init__(self, profile: Profile, cfg: BestGuessConfig) -> None:
        self.profile = profile
        self.cfg = cfg

    def judge(
        self,
        before: BgrImage,
        reveal: BgrImage,
        obs: Observation,
        chosen: Side | None,
        gems_before: int | None,
    ) -> Verdict:
        if not self.profile.has("reveal_roi"):
            return Verdict(self.name, None, 0.0)
        side, score = dominant_side(crop(reveal, self.profile.roi("reveal_roi")), self.cfg)
        return Verdict(self.name, side, min(1.0, score * 4))  # type: ignore[arg-type]


class GemDeltaLearner:
    """Gems went up → the side we chose was right."""

    name = "gem_delta"

    def judge(
        self,
        before: BgrImage,
        reveal: BgrImage,
        obs: Observation,
        chosen: Side | None,
        gems_before: int | None,
    ) -> Verdict:
        if chosen is None or gems_before is None or obs.gems is None:
            return Verdict(self.name, None, 0.0)
        if obs.gems > gems_before:
            return Verdict(self.name, chosen, 0.9)
        if obs.gems == gems_before:
            return Verdict(self.name, "F" if chosen == "T" else "T", 0.6)
        return Verdict(self.name, None, 0.0)


class DanceLearner:
    """Our animal dances when we were right: motion inside own_animal_roi at reveal."""

    name = "dance"

    def __init__(self, profile: Profile, cfg: BestGuessConfig) -> None:
        self.profile = profile
        self.cfg = cfg

    def judge(
        self,
        before: BgrImage,
        reveal: BgrImage,
        obs: Observation,
        chosen: Side | None,
        gems_before: int | None,
    ) -> Verdict:
        if chosen is None or not self.profile.has("own_animal_roi"):
            return Verdict(self.name, None, 0.0)
        roi = self.profile.roi("own_animal_roi")
        motion = float(cv2.absdiff(crop(before, roi), crop(reveal, roi)).mean())
        if motion >= self.cfg.dance_motion_min:
            return Verdict(self.name, chosen, 0.5)
        return Verdict(self.name, "F" if chosen == "T" else "T", 0.3)


def vote(verdicts: list[Verdict]) -> tuple[Side | None, float, list[Verdict]]:
    """Confidence-weighted vote across strategies."""
    weight: dict[str, float] = {"T": 0.0, "F": 0.0}
    for v in verdicts:
        if v.side is not None:
            weight[v.side] += v.confidence
    if weight["T"] == weight["F"]:
        return (None, 0.0, verdicts)
    side: Side = "T" if weight["T"] > weight["F"] else "F"
    total = weight["T"] + weight["F"]
    return (side, abs(weight["T"] - weight["F"]) / total if total else 0.0, verdicts)
