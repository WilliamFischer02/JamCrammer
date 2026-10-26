"""Best Guess Phase C against the synthetic arena: detector accuracy, brain, farmer loop."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from jamcrammer.core.config import BestGuessConfig
from jamcrammer.core.events import EventBus
from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import Profile, load_profile
from jamcrammer.core.store import Store
from jamcrammer.learn.recorder import read_meta
from jamcrammer.modes.bestguess.brain import Brain
from jamcrammer.modes.bestguess.farmer import BestGuessFarmer
from jamcrammer.modes.bestguess.state import BestGuessDetector, BgState
from jamcrammer.replay.harness import replay
from jamcrammer.vision.ocr import ocr_image
from tests.synthetic import bestguess as synth


def _ocr_ok() -> bool:
    import numpy as np

    return not ocr_image(np.full((40, 120, 3), 255, dtype=np.uint8)).error


def _profile() -> Profile:
    prof = load_profile("bestguess", Paths())
    prof.set_value("fact_roi", list(synth.FACT_ROI))
    prof.set_value("countdown_roi", list(synth.COUNTDOWN_ROI))
    prof.set_value("gem_counter_roi", list(synth.GEM_ROI))
    prof.set_value("true_point", list(synth.TRUE_POINT))
    prof.set_value("false_point", list(synth.FALSE_POINT))
    prof.set_value("reveal_roi", list(synth.REVEAL_ROI))
    return prof


@pytest.fixture(scope="module")
def session(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, object]]:
    out = tmp_path_factory.mktemp("bg") / "session"
    info = synth.generate_session(out, rounds=4, fps=5.0, seed=11, video=False, pngs=False)
    return out, info


def test_detector_state_accuracy_on_synthetic_session(
    session: tuple[Path, dict[str, object]],
) -> None:
    if not _ocr_ok():
        pytest.skip("winocr unavailable")
    out, _ = session
    det = BestGuessDetector(_profile(), BestGuessConfig())
    meta = [m for m in read_meta(out) if m.get("t") == "keyframe"]
    hits = 0
    for m in meta:
        import cv2

        img = cv2.imread(str(out / str(m["file"])))
        assert img is not None
        obs = det.observe(np.asarray(img, dtype=np.uint8), float(m["ts"]))
        hits += str(obs.state) == str(m["synthetic_state"])
    assert hits / len(meta) >= 0.95, f"{hits}/{len(meta)}"


def test_replay_harness_scores_against_named_states(
    session: tuple[Path, dict[str, object]],
) -> None:
    if not _ocr_ok():
        pytest.skip("winocr unavailable")
    out, _ = session
    meta = [m for m in read_meta(out) if m.get("t") == "keyframe"]
    (out / "analysis").mkdir(exist_ok=True)
    (out / "analysis" / "labels.json").write_text(
        json.dumps(
            [{"file": m["file"], "ts": m["ts"], "state": m["synthetic_state"]} for m in meta]
        )
    )
    res = replay(out, BestGuessDetector(_profile(), BestGuessConfig()))
    assert res.accuracy >= 0.95


def test_brain_fuzzy_lookup_policy_and_majority(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    brain = Brain(
        store, fuzzy_threshold=90, unknown_policy="learned_prior", answer_mode="bank_policy"
    )
    assert brain.decide("Cats purr when happy").source == "prior"  # empty bank → prior (True)
    brain.learn("Cats purr when happy", "T")
    d = brain.decide("cats purr  when HAPPY!")  # OCR noise: case, spacing, punctuation
    assert d.side == "T" and d.source == "bank"
    assert brain.decide("Cats").source == "prior"  # too short to match (length ratio guard)
    brain.learn("Elephants can jump", "F")
    brain.learn("Elephants can jump", "F")
    brain.learn("Elephants can jump", "T")  # one conflicting observation
    assert brain.decide("Elephants can jump").side == "F"  # majority keeps F
    brain.learn("Elephants can jump", "T")
    brain.learn("Elephants can jump", "T")  # conflicts now outnumber confirmations
    assert brain.decide("Elephants can jump").side == "T"
    skip = Brain(store, fuzzy_threshold=90, unknown_policy="skip", answer_mode="bank_policy")
    assert skip.decide("Totally new fact here").side is None
    learn_only = Brain(
        store, fuzzy_threshold=90, unknown_policy="learned_prior", answer_mode="learn_only"
    )
    assert learn_only.decide("Cats purr when happy").side is None
    coin = Brain(
        store, fuzzy_threshold=90, unknown_policy="skip", answer_mode="coin_flip", rng=lambda: 0.9
    )
    assert coin.decide("anything at all").side == "F"
    store.close()


class Arena:
    """Simulated Best Guess: rounds advance with virtual time; clicks pick a side."""

    def __init__(self, rounds: list[tuple[str, bool]], fps: float = 5.0) -> None:
        self.rounds = rounds
        self.t = 0.0
        self.gems = 1000
        self.clicks: list[tuple[float, int, int]] = []
        self.chosen: dict[int, bool] = {}
        self.round_len = 3.0 + 10.0 + 4.0  # waiting 3 s, question 10 s, reveal 4 s

    def round_index(self) -> int:
        return int(self.t // self.round_len)

    def image(self):  # type: ignore[no-untyped-def]
        r = self.round_index()
        if r >= len(self.rounds):
            return synth.frame_waiting(self.gems)
        fact, answer = self.rounds[r]
        phase = self.t - r * self.round_len
        if phase < 3.0:
            return synth.frame_waiting(self.gems)
        if phase < 13.0:
            return synth.frame_question(fact, int(10 - (phase - 3.0)), self.gems)
        if phase - 13.0 < 0.01 and r in self.chosen and self.chosen[r] == answer:
            self.gems += 10
        return synth.frame_reveal(fact, answer, self.gems)

    def click(self, x: int, y: int) -> bool:
        self.clicks.append((self.t, x, y))
        r = self.round_index()
        phase = self.t - r * self.round_len
        if 3.0 <= phase < 13.0 - 5.0:  # can't switch sides in the last 5 s
            self.chosen[r] = x < 960
        return True


def test_farmer_answers_learns_and_counts(tmp_path: Path) -> None:
    if not _ocr_ok():
        pytest.skip("winocr unavailable")
    rounds = [
        ("Cats purr when happy", True),
        ("Elephants can jump", False),
        ("Cats purr when happy", True),
        ("Honey never spoils", True),
    ]
    arena = Arena(rounds)
    store = Store(tmp_path / "t.db")
    brain = Brain(store, fuzzy_threshold=85, unknown_policy="fixed_true", answer_mode="bank_policy")
    bus = EventBus()
    events: list[str] = []
    bus.subscribe("*", lambda e: events.append(e.type))
    cfg = BestGuessConfig(fps=5.0, commit_after_s=2.0)

    def sleep(s: float) -> None:
        arena.t += s

    farmer = BestGuessFarmer(
        _profile(),
        cfg,
        brain,
        arena,
        arena.image,
        store=store,
        bus=bus,
        clock=lambda: arena.t,
        sleep=sleep,
    )
    farmer.run(lambda: arena.t > len(rounds) * arena.round_len + 1.0)
    s = farmer.stats
    assert s.rounds == 4 and s.answered == 4, s
    # fixed_true: round 1 right, round 2 wrong (learns F), round 3 from the bank, round 4 right
    assert s.correct == 3 and s.learned == 4, s
    assert s.multiplier >= 2 and s.gems_est >= 30
    facts = {
        r["normalized_text"]: r["answer"]
        for r in store.query("SELECT normalized_text, answer FROM bg_facts")
    }
    assert facts.get("elephants can jump") == "F" and facts.get("cats purr when happy") == "T"
    assert "bestguess.answer" in events and "bestguess.reveal" in events
    assert store.query("SELECT COUNT(*) AS n FROM bg_rounds")[0]["n"] == 4
    assert farmer.state in (BgState.WAITING_FOR_ROUND, BgState.REVEAL)
    store.close()
