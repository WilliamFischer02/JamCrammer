"""Answer brain (KICKOFF §8.3): fuzzy-match the fact against the bank, apply the policy."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from rapidfuzz import fuzz

from jamcrammer.core.config import AnswerMode, UnknownFactPolicy
from jamcrammer.core.store import Store
from jamcrammer.vision.ocr import normalize_text

Side = Literal["T", "F"]
MIN_LENGTH_RATIO = 0.6


@dataclass(frozen=True)
class BankMatch:
    normalized: str
    answer: str
    score: float
    seen: int
    confirmed: int
    conflicts: int


@dataclass(frozen=True)
class Decision:
    side: Side | None
    source: str  # bank | prior | fixed | coin | skip | learn_only
    match: BankMatch | None = None


class Brain:
    def __init__(
        self,
        store: Store,
        *,
        fuzzy_threshold: int,
        unknown_policy: UnknownFactPolicy,
        answer_mode: AnswerMode,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._store = store
        self.fuzzy_threshold = fuzzy_threshold
        self.unknown_policy = unknown_policy
        self.answer_mode = answer_mode
        self._rng = rng
        self._cache: list[tuple[str, str, int, int, int]] | None = None

    def invalidate(self) -> None:
        self._cache = None

    def _facts(self) -> list[tuple[str, str, int, int, int]]:
        if self._cache is None:
            rows = self._store.query(
                "SELECT normalized_text, answer, seen_count, confirmed_count, conflict_count"
                " FROM bg_facts"
            )
            self._cache = [(r[0], r[1], r[2], r[3], r[4]) for r in rows]
        return self._cache

    def lookup(self, fact_text: str) -> BankMatch | None:
        """Best fuzzy match with a length-ratio guard (token_set_ratio alone loves short text)."""
        norm = normalize_text(fact_text)
        if not norm:
            return None
        best: BankMatch | None = None
        for key, answer, seen, confirmed, conflicts in self._facts():
            ratio = min(len(norm), len(key)) / max(len(norm), len(key), 1)
            if ratio < MIN_LENGTH_RATIO:
                continue
            score = float(fuzz.token_set_ratio(norm, key))
            if score >= self.fuzzy_threshold and (best is None or score > best.score):
                best = BankMatch(key, answer, score, seen, confirmed, conflicts)
        return best

    def prior(self) -> Side:
        """Whichever side wins more often across the bank (ties → True)."""
        facts = self._facts()
        t = sum(1 for f in facts if f[1] == "T")
        f = sum(1 for x in facts if x[1] == "F")
        return "T" if t >= f else "F"

    def decide(self, fact_text: str) -> Decision:  # noqa: PLR0911 - one return per policy
        if self.answer_mode == "learn_only":
            return Decision(None, "learn_only", self.lookup(fact_text))
        if self.answer_mode == "coin_flip":
            return Decision("T" if self._rng() < 0.5 else "F", "coin")
        match = self.lookup(fact_text)
        if match is not None and match.answer in ("T", "F"):
            return Decision(match.answer, "bank", match)  # type: ignore[arg-type]
        if self.unknown_policy == "learned_prior":
            return Decision(self.prior(), "prior")
        if self.unknown_policy == "fixed_true":
            return Decision("T", "fixed")
        if self.unknown_policy == "fixed_false":
            return Decision("F", "fixed")
        return Decision(None, "skip")

    def learn(
        self, fact_text: str, correct_side: Side, source: str = "learned", ts: float | None = None
    ) -> None:
        """Record the outcome of a round. Conflicting records keep the stored answer and bump
        the conflict counter (majority is enforced by confirmed vs conflict counts)."""
        norm = normalize_text(fact_text)
        if not norm:
            return
        match = self.lookup(fact_text)
        key = match.normalized if match is not None else norm
        existing = next((f for f in self._facts() if f[0] == key), None)
        conflict = (
            existing is not None and existing[1] in ("T", "F") and existing[1] != correct_side
        )
        self._store.upsert_fact(
            key,
            fact_text,
            correct_side,
            source,
            ts=ts,
            confirmed=not conflict,
            conflict=conflict,
        )
        if conflict and existing is not None:
            # majority: if conflicts now outnumber confirmations, flip the stored answer
            _seen, confirmed, conflicts = existing[2] + 1, existing[3], existing[4] + 1
            if conflicts > confirmed:
                self._store._exec(
                    "UPDATE bg_facts SET answer=?, confirmed_count=?, conflict_count=?"
                    " WHERE normalized_text=?",
                    (correct_side, conflicts, confirmed, key),
                )
        self.invalidate()
