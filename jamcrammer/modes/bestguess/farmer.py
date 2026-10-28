"""Best Guess farmer, Phase C (KICKOFF §8.3).

Main loop: WAITING_FOR_ROUND → QUESTION_OPEN → ANSWER_COMMITTED → LOCKED → REVEAL → (learn) →
WAITING_FOR_ROUND. UNKNOWN persists past ``unknown_pause_s`` → pause and alert. The watchdog
owns INACTIVITY_POPUP / DISCONNECTED and preempts this loop through the lease.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import numpy as np
import numpy.typing as npt

from jamcrammer.core.config import BestGuessConfig, Level
from jamcrammer.core.events import Event, EventBus
from jamcrammer.core.logging import get_logger
from jamcrammer.core.profiles import Profile
from jamcrammer.core.store import Store
from jamcrammer.modes.bestguess.brain import Brain, Decision
from jamcrammer.modes.bestguess.learner import Learner, default_learners, vote
from jamcrammer.modes.bestguess.navigate import count_crowd, find_own_tag, key_for, side_of_x
from jamcrammer.modes.bestguess.state import BestGuessDetector, BgState, Observation

log = get_logger("bestguess")

BgrImage = npt.NDArray[np.uint8]
Side = Literal["T", "F"]
GrabFn = Callable[[], BgrImage]
REQUIRED = ("fact_roi", "true_point", "false_point", "reveal_word_roi")
GEMS_PER_CORRECT = 10
MAX_MULTIPLIER = 4


class Clicker(Protocol):
    def click(self, cx: int, cy: int) -> bool: ...

    def key_down(self, key: str) -> None: ...

    def key_up(self, key: str) -> None: ...


@dataclass
class FarmerStats:
    rounds: int = 0
    answered: int = 0
    correct: int = 0
    streak: int = 0
    best_streak: int = 0
    multiplier: int = 1
    gems_est: int = 0
    unknown_facts: int = 0
    learned: int = 0
    last_fact: str = ""
    last_decision: str = ""

    @property
    def accuracy(self) -> float:
        return self.correct / self.answered if self.answered else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "accuracy": round(self.accuracy, 3)}


@dataclass
class RoundCtx:
    fact: str
    opened_ts: float
    decision: Decision | None = None
    chosen: Side | None = None
    committed_ts: float | None = None
    gems_before: int | None = None
    before_image: BgrImage | None = field(default=None, repr=False)


class BestGuessFarmer:
    def __init__(
        self,
        profile: Profile,
        cfg: BestGuessConfig,
        brain: Brain,
        clicker: Clicker,
        grab: GrabFn,
        *,
        detector: BestGuessDetector | None = None,
        store: Store | None = None,
        bus: EventBus | None = None,
        learners: list[Learner] | None = None,
        screenshot: Callable[[str, BgrImage], Any] | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        missing = profile.missing(list(REQUIRED))
        if missing:
            msg = f"bestguess profile missing {missing}"
            raise ValueError(msg)
        self.profile = profile
        self.cfg = cfg
        self.brain = brain
        self._click = clicker
        self._grab = grab
        self.detector = detector or BestGuessDetector(profile, cfg)
        self._store = store
        self._bus = bus
        self.learners: list[Learner] = learners or default_learners(profile, cfg)
        self._screenshot = screenshot
        self._clock = clock
        self._sleep = sleep
        self.stats = FarmerStats()
        self.state = BgState.WAITING_FOR_ROUND
        self._round: RoundCtx | None = None
        self._unknown_since: float | None = None
        self._last_input_ts = 0.0
        self._last_side: Side | None = None
        self._held_key: str | None = None
        self._release_at = 0.0
        self._verify_at = 0.0
        self._moves_this_round = 0
        self.paused_reason = ""
        self.screen_known = True  # watchdog hint; benefit of the doubt until the first frame

    # --- loop ------------------------------------------------------------------------------------

    def step(self) -> Observation:
        """One capture tick. Returns the observation; side effects are the state machine."""
        image = self._grab()
        now = self._clock()
        self._service_keys(now)
        obs = self.detector.observe(image, now)
        seen = obs.state
        self.screen_known = seen != BgState.UNKNOWN
        if seen == BgState.UNKNOWN:
            self._on_unknown(image, now)
            return obs
        self._unknown_since = None

        if (
            self.state in (BgState.WAITING_FOR_ROUND, BgState.REVEAL)
            and seen == BgState.QUESTION_OPEN
        ):
            self._open_round(obs, image, now)
        elif self.state == BgState.QUESTION_OPEN and seen in (
            BgState.QUESTION_OPEN,
            BgState.LOCKED,
        ):
            self._maybe_commit(obs, image, now)
        elif self.state == BgState.ANSWER_COMMITTED and seen in (
            BgState.QUESTION_OPEN,
            BgState.LOCKED,
        ):
            self._verify_commit(obs, image, now)
            if seen == BgState.LOCKED:
                self.state = BgState.LOCKED
        elif (
            self.state in (BgState.QUESTION_OPEN, BgState.ANSWER_COMMITTED, BgState.LOCKED)
            and seen == BgState.REVEAL
        ):
            self._on_reveal(obs, image, now)
        elif self.state == BgState.REVEAL and seen == BgState.WAITING_FOR_ROUND:
            self.state = BgState.WAITING_FOR_ROUND
        elif self.state == BgState.LOCKED and seen == BgState.WAITING_FOR_ROUND:
            self.state = BgState.WAITING_FOR_ROUND  # reveal missed; round lost
            self._round = None
        self._heartbeat(now)
        return obs

    def run(
        self, should_stop: Callable[[], bool], status: Callable[[str], None] | None = None
    ) -> None:
        period = 1.0 / self.cfg.fps
        try:
            self._run_loop(should_stop, status, period)
        finally:
            self._release_key()

    def _run_loop(
        self,
        should_stop: Callable[[], bool],
        status: Callable[[str], None] | None,
        period: float,
    ) -> None:
        while not should_stop() and not self.paused_reason:
            self.step()
            if status is not None:
                s = self.stats
                status(
                    f"{self.state} · Q{s.rounds} · {s.multiplier}x · "
                    f"{s.correct}/{s.answered} · {s.gems_est} gems est"
                )
            self._sleep(period)

    # --- transitions -----------------------------------------------------------------------

    def _open_round(self, obs: Observation, image: BgrImage, now: float) -> None:
        self.state = BgState.QUESTION_OPEN
        self.stats.rounds += 1
        self._moves_this_round = 0
        self._round = RoundCtx(
            fact=obs.fact_text,
            opened_ts=now,
            gems_before=obs.score if obs.score is not None else obs.gems,
            before_image=image.copy(),
        )
        self.stats.last_fact = obs.fact_text
        self._emit("bestguess.question", fact=obs.fact_text[:120], seconds=obs.seconds_left)

    def _maybe_commit(self, obs: Observation, image: BgrImage, now: float) -> None:
        rnd = self._round
        if rnd is None:
            return
        if obs.fact_text and len(obs.fact_norm) > len(rnd.fact) * 0.8:
            rnd.fact = obs.fact_text  # OCR of a later frame may be cleaner
        elapsed = now - rnd.opened_ts
        too_late = obs.seconds_left is not None and obs.seconds_left <= self.cfg.lock_seconds
        if elapsed < self.cfg.commit_after_s and not too_late:
            return
        rnd.decision = self.brain.decide(rnd.fact)
        if rnd.decision.side is None and rnd.decision.source == "crowd":
            wait = (
                self.profile.number("crowd_commit_after_s")
                if self.profile.has("crowd_commit_after_s")
                else 5.0
            )
            if elapsed < wait and not too_late:
                return  # let the crowd settle first
            crowd = count_crowd(image, self._floor_roi(), self.cfg)
            side = crowd.side(self.cfg.crowd_min_ratio)
            if side is None:
                rnd.decision = Decision(self.brain.prior(), "prior")
            else:
                rnd.decision = Decision(side, "crowd")  # type: ignore[arg-type]
            self._emit(
                "bestguess.crowd", left=crowd.left, right=crowd.right, side=rnd.decision.side
            )
        self.stats.last_decision = rnd.decision.source
        if rnd.decision.side is None:
            self.stats.unknown_facts += rnd.decision.source != "learn_only"
            self._emit("bestguess.skip", fact=rnd.fact[:120], reason=rnd.decision.source)
            self.state = BgState.LOCKED if too_late else BgState.ANSWER_COMMITTED
            return
        self._commit(rnd.decision.side, now)
        rnd.chosen = rnd.decision.side
        rnd.committed_ts = now
        self.stats.answered += 1
        self.state = BgState.ANSWER_COMMITTED
        self._emit(
            "bestguess.answer",
            fact=rnd.fact[:120],
            side=rnd.chosen,
            source=rnd.decision.source,
            score=rnd.decision.match.score if rnd.decision.match else None,
        )

    def _floor_roi(self) -> tuple[int, int, int, int]:
        if self.profile.has("floor_roi"):
            return self.profile.roi("floor_roi")
        return (90, 250, 1740, 760)

    def _commit(self, side: Side, now: float) -> None:
        """Walk to a side: hold the arrow key (released in a later tick) or click the floor."""
        if self.cfg.move_method == "keys":
            self._release_key()
            key = key_for(side)
            self._click.key_down(key)
            self._held_key = key
            self._release_at = now + self.cfg.move_hold_s
            self._verify_at = self._release_at + self.cfg.move_verify_after_s
        else:
            x, y = self.profile.point("true_point" if side == "T" else "false_point")
            self._click.click(x, y)
        self._moves_this_round += 1
        self._last_input_ts = now
        self._last_side = side

    def _release_key(self) -> None:
        if self._held_key is not None:
            self._click.key_up(self._held_key)
            self._held_key = None

    def _service_keys(self, now: float) -> None:
        if self._held_key is not None and now >= self._release_at:
            self._release_key()

    def _verify_commit(self, obs: Observation, image: BgrImage, now: float) -> None:
        """Postcondition: our side should light up. Without a calibrated side-indicator ROI we
        re-click once shortly after the commit (idempotent: walking to the same side)."""
        rnd = self._round
        if rnd is None or rnd.chosen is None or rnd.committed_ts is None:
            return
        if self.cfg.move_method == "keys":
            if self._held_key is not None or now < self._verify_at or self._moves_this_round >= 2:
                return
            x = find_own_tag(image, self._floor_roi(), self.cfg.own_name, self.cfg)
            self._verify_at = now + 60.0  # once per round
            if x is None:
                self._emit("bestguess.own_tag_unseen", side=rnd.chosen)
                return
            if side_of_x(x) == rnd.chosen:
                self._emit("bestguess.own_tag", x=x, side=rnd.chosen)
                return
            self._commit(rnd.chosen, now)
            self._emit("bestguess.recommit", Level.NOTICE, side=rnd.chosen, own_x=x)
            return
        if not self.profile.has("side_indicator_roi") or self._side_lit(image, rnd.chosen):
            return
        if (
            now - rnd.committed_ts >= self.cfg.verify_after_s
            and now - self._last_input_ts >= self.cfg.verify_after_s
        ):
            self._commit(rnd.chosen, now)
            self._emit("bestguess.recommit", Level.NOTICE, side=rnd.chosen)

    def _side_lit(self, image: BgrImage, side: Side) -> bool:
        from jamcrammer.modes.bestguess.state import dominant_side
        from jamcrammer.vision.ocr import crop

        lit, _ = dominant_side(crop(image, self.profile.roi("side_indicator_roi")), self.cfg)
        return lit == side

    def _on_reveal(self, obs: Observation, image: BgrImage, now: float) -> None:
        self.state = BgState.REVEAL
        rnd = self._round
        self._round = None
        if rnd is None:
            return
        before = rnd.before_image if rnd.before_image is not None else image
        verdicts = [
            lrn.judge(before, image, obs, rnd.chosen, rnd.gems_before) for lrn in self.learners
        ]
        side, conf, _ = vote(verdicts)
        if side is None:
            self._emit("bestguess.reveal_unclear", Level.NOTICE, fact=rnd.fact[:120])
            return
        correct = rnd.chosen == side if rnd.chosen is not None else None
        if rnd.chosen is not None:
            if correct:
                self.stats.correct += 1
                self.stats.streak += 1
                self.stats.best_streak = max(self.stats.best_streak, self.stats.streak)
                self.stats.gems_est += GEMS_PER_CORRECT * self.stats.multiplier
                self.stats.multiplier = min(MAX_MULTIPLIER, self.stats.multiplier + 1)
            else:
                self.stats.streak = 0
                self.stats.multiplier = 1
        self.brain.learn(rnd.fact, side, source="learned", ts=now)
        self.stats.learned += 1
        if self._store is not None:
            self._store.add_bg_round(
                ts=rnd.opened_ts,
                fact_key=rnd.fact[:200],
                ocr_text=rnd.fact,
                ocr_conf=None,
                match_score=rnd.decision.match.score
                if rnd.decision and rnd.decision.match
                else None,
                chosen=rnd.chosen,
                outcome=("correct" if correct else "wrong") if correct is not None else "observed",
                multiplier=self.stats.multiplier,
                gems_est=self.stats.gems_est,
            )
        self._emit(
            "bestguess.reveal",
            fact=rnd.fact[:120],
            correct_side=side,
            chosen=rnd.chosen,
            outcome=correct,
            confidence=round(conf, 2),
            verdicts={v.strategy: v.side for v in verdicts},
        )

    def _on_unknown(self, image: BgrImage, now: float) -> None:
        if self._unknown_since is None:
            self._unknown_since = now
            if self._screenshot is not None:
                self._screenshot("bestguess_unknown", image)
            self._emit("bestguess.unknown", Level.NOTICE)
        elif now - self._unknown_since >= self.cfg.unknown_pause_s:
            self.paused_reason = "unknown_state"
            self._emit(
                "bestguess.paused",
                Level.HIGH,
                message=(
                    f"Best Guess screen unrecognised for {self.cfg.unknown_pause_s:.0f}s; paused."
                ),
            )

    def _heartbeat(self, now: float) -> None:
        """In-game activity: if we sent nothing for heartbeat_s, click the side we stand on."""
        if (
            self._last_side is not None
            and now - self._last_input_ts >= self.cfg.heartbeat_s
            and self.state == BgState.WAITING_FOR_ROUND
        ):
            self._commit(self._last_side, now)
            self._emit("bestguess.heartbeat", side=self._last_side)

    def _emit(self, event_type: str, level: Level = Level.INFO, **p: object) -> None:
        if self._bus is not None:
            self._bus.publish(
                Event(type=event_type, source="bestguess", level=level, payload=dict(p))
            )
