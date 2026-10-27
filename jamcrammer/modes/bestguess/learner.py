"""Pluggable 'which answer was correct' strategies (KICKOFF §8.3). The farmer runs all of
them on every reveal and trusts the confidence-weighted vote."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

import cv2
import numpy as np
import numpy.typing as npt

from jamcrammer.core.config import BestGuessConfig
from jamcrammer.core.profiles import Profile
from jamcrammer.modes.bestguess.state import Observation
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


def _flip(side: Side) -> Side:
    return "F" if side == "T" else "T"


class RevealWordLearner:
    """The banner's big word: green True! / orange False! (already decoded into obs)."""

    name = "reveal_word"

    def judge(
        self,
        before: BgrImage,
        reveal: BgrImage,
        obs: Observation,
        chosen: Side | None,
        gems_before: int | None,
    ) -> Verdict:
        if obs.reveal_side in ("T", "F"):
            return Verdict(self.name, obs.reveal_side, 0.95)  # type: ignore[arg-type]
        return Verdict(self.name, None, 0.0)


class ScoreDeltaLearner:
    """The round score went up → the side we chose was right."""

    name = "score_delta"

    def judge(
        self,
        before: BgrImage,
        reveal: BgrImage,
        obs: Observation,
        chosen: Side | None,
        gems_before: int | None,
    ) -> Verdict:
        if chosen is None or gems_before is None or obs.score is None:
            return Verdict(self.name, None, 0.0)
        if obs.score > gems_before:
            return Verdict(self.name, chosen, 0.85)
        if obs.score == gems_before:
            return Verdict(self.name, _flip(chosen), 0.5)
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
            return Verdict(self.name, chosen, 0.4)
        return Verdict(self.name, _flip(chosen), 0.2)


def default_learners(profile: Profile, cfg: BestGuessConfig) -> list[Learner]:
    return [
        RevealWordLearner(),
        ScoreDeltaLearner(),
        DanceLearner(profile, cfg),
    ]


def vote(verdicts: list[Verdict]) -> tuple[Side | None, float, list[Verdict]]:
    weight: dict[str, float] = {"T": 0.0, "F": 0.0}
    for v in verdicts:
        if v.side is not None:
            weight[v.side] += v.confidence
    if weight["T"] == weight["F"]:
        return (None, 0.0, verdicts)
    side: Side = "T" if weight["T"] > weight["F"] else "F"
    total = weight["T"] + weight["F"]
    return (side, abs(weight["T"] - weight["F"]) / total if total else 0.0, verdicts)
