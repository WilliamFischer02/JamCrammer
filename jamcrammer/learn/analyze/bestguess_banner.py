"""Best Guess-specific analysis: read the top banner through a recording.

The banner shows the question ("Bulldog ants eat meat."), then the reveal ("False!" or
"True!" with the corrected statement), and between rounds a "ROUND COMPLETE" scroll with
"N Correct Answers" and "M GEMS EARNED!". Pairing each reveal with the question before it
yields the answer bank; the scroll yields the real payout per round.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np
import numpy.typing as npt

from jamcrammer.core.config import BestGuessConfig
from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import Profile, load_profile
from jamcrammer.learn.recorder import read_meta
from jamcrammer.modes.bestguess.state import dominant_side, in_arena, reveal_side_from_text
from jamcrammer.vision.ocr import crop, normalize_text, ocr_image
from jamcrammer.vision.stage import StageMap

BgrImage = npt.NDArray[np.uint8]
Kind = Literal["question", "reveal", "round_complete", "none"]
Roi = tuple[int, int, int, int]

ROUND_WORDS = ("round complete", "correct answer", "gems earned", "next round")
MIN_QUESTION_CHARS = 10
_CORRECT_RE = re.compile(r"(\d+)\s*correct")
_GEMS_RE = re.compile(r"(\d+)\s*gems")
DEFAULT_SCAN_FPS = 2.0
ROUND_MERGE_S = 10.0  # scroll readings closer than this are the same round-complete screen
OPEN_END = 1e9  # 'until the end of the recording'


@dataclass(frozen=True)
class BannerEvent:
    ts: float
    kind: Kind
    text: str
    norm: str
    side: str | None = None  # T | F for reveals
    correct: int | None = None  # round complete
    gems: int | None = None


@dataclass
class FactRecord:
    question: str
    answer: str  # T | F
    reveal_text: str
    question_ts: float
    reveal_ts: float
    seen: int = 1


@dataclass
class BannerScan:
    events: list[BannerEvent] = field(default_factory=list)
    facts: list[FactRecord] = field(default_factory=list)
    rounds: list[BannerEvent] = field(default_factory=list)
    frames: int = 0
    outside: list[tuple[float, float]] = field(default_factory=list)  # not-in-arena spans

    def as_dict(self) -> dict[str, Any]:
        return {
            "frames": self.frames,
            "events": [e.__dict__ for e in self.events],
            "facts": [f.__dict__ for f in self.facts],
            "rounds": [r.__dict__ for r in self.rounds],
            "outside": self.outside,
        }


def classify(
    text: str, color_side: str | None = None
) -> tuple[Kind, str | None, int | None, int | None]:
    norm = normalize_text(text)
    low = norm
    if color_side is not None:
        return ("reveal", color_side, None, None)
    if any(w in low for w in ROUND_WORDS):
        c = _CORRECT_RE.search(low)
        g = _GEMS_RE.search(low)
        return (
            "round_complete",
            None,
            int(c.group(1)) if c else None,
            int(g.group(1)) if g else None,
        )
    text_side = reveal_side_from_text(low)
    if text_side is not None:
        return ("reveal", text_side, None, None)
    if len(norm) >= MIN_QUESTION_CHARS:
        return ("question", None, None, None)
    return ("none", None, None, None)


def video_frames(
    path: Path, scan_fps: float = DEFAULT_SCAN_FPS
) -> Iterator[tuple[float, BgrImage]]:
    """Stage-normalized frames from a video at ~scan_fps."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        msg = f"cannot open {path}"
        raise FileNotFoundError(msg)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, round(fps / scan_fps))
    index = 0
    smap: StageMap | None = None
    try:
        while True:
            ok = cap.grab()
            if not ok:
                break
            if index % step == 0:
                ok, raw = cap.retrieve()
                if ok:
                    img = np.asarray(raw, dtype=np.uint8)
                    if smap is None:
                        smap = StageMap(img.shape[1], img.shape[0])
                    yield (index / fps, smap.normalize(img))
            index += 1
    finally:
        cap.release()


def scan_banner(  # noqa: PLR0912, PLR0915, PLR0917
    frames: Iterable[tuple[float, BgrImage]],
    fact_roi: Roi,
    ocr: Callable[[BgrImage], str] | None = None,
    round_roi: Roi | None = None,
    reveal_word_roi: Roi | None = None,
    cfg: BestGuessConfig | None = None,
    arena_check: Callable[[BgrImage], bool] | None = None,
) -> BannerScan:
    """OCR the banner in every frame, dedupe runs, pair reveals with the preceding question."""
    read = ocr or (lambda img: ocr_image(img, upscale=1.5).text)
    scan = BannerScan()
    last_norm = ""
    last_question: BannerEvent | None = None
    facts: dict[str, FactRecord] = {}
    outside_since: float | None = None
    for ts, image in frames:
        scan.frames += 1
        if arena_check is not None and not arena_check(image):
            if outside_since is None:
                outside_since = ts
            last_norm = ""
            continue
        if outside_since is not None:
            scan.outside.append((outside_since, ts))
            outside_since = None
        text = read(crop(image, fact_roi))
        color_side = None
        if reveal_word_roi is not None:
            color_side, _ = dominant_side(crop(image, reveal_word_roi), cfg or BestGuessConfig())
        kind, side, correct, gems = classify(text, color_side)
        if kind == "reveal" and not text.strip():
            text = "True!" if side == "T" else "False!"
        if round_roi is not None:
            # the scroll overlays the arena while the banner still shows the last reveal
            rtext = read(crop(image, round_roi))
            rk, _, rcorrect, rgems = classify(rtext)
            rnorm = normalize_text(rtext)
            if rk == "round_complete":
                _add_round(
                    scan.rounds,
                    BannerEvent(ts, "round_complete", rtext.strip(), rnorm, None, rcorrect, rgems),
                )
        norm = normalize_text(text)
        if kind == "reveal":
            norm = f"reveal {side} {norm}"
        if kind == "none" or norm == last_norm:
            if kind == "none":
                last_norm = ""
            continue
        last_norm = norm
        event = BannerEvent(ts, kind, text.strip(), norm, side, correct, gems)
        scan.events.append(event)
        if kind == "question":
            last_question = event
        elif kind == "reveal" and side is not None and last_question is not None:
            key = last_question.norm
            if key in facts and facts[key].answer == side:
                facts[key].seen += 1
            else:
                facts[key] = FactRecord(last_question.text, side, event.text, last_question.ts, ts)
            last_question = None
    if outside_since is not None:
        scan.outside.append((outside_since, OPEN_END))
    scan.facts = sorted(facts.values(), key=lambda f: f.question_ts)
    return scan


def _add_round(rounds: list[BannerEvent], event: BannerEvent) -> None:
    """One record per round-complete screen; a later reading with more numbers wins."""
    if rounds and event.ts - rounds[-1].ts < ROUND_MERGE_S:
        last = rounds[-1]
        better = (event.correct is not None) + (event.gems is not None)
        have = (last.correct is not None) + (last.gems is not None)
        if better > have:
            rounds[-1] = BannerEvent(
                last.ts, last.kind, event.text, event.norm, None, event.correct, event.gems
            )
        return
    rounds.append(event)


def intervals_from_scan(scan: BannerScan) -> list[dict[str, Any]]:
    """Time intervals for the replay gate: QUESTION from a question event to the next event,
    REVEAL from a reveal to the next question, UNKNOWN while outside the arena (listed first
    so the labeler lets them win)."""
    rows: list[dict[str, Any]] = []
    events = [e for e in scan.events if e.kind in ("question", "reveal")]
    for i, e in enumerate(events):
        end = events[i + 1].ts if i + 1 < len(events) else e.ts + 5.0
        if e.kind == "question":
            rows.append({"start": e.ts, "end": end, "state": "QUESTION"})
        elif rows and rows[-1]["state"] == "REVEAL":
            rows[-1]["end"] = end
        else:
            rows.append({"start": e.ts, "end": end, "state": "REVEAL"})
    unknown = [{"start": s, "end": e, "state": "UNKNOWN"} for s, e in scan.outside]
    return unknown + rows


def session_video(session_dir: Path, paths: Paths) -> Path | None:
    """The original inbox recording of a session (moved to inbox/_processed by ingest)."""
    for m in read_meta(session_dir):
        if m.get("t") == "session":
            folder = paths.inbox / "_processed" / session_dir.name.removesuffix("_inbox")
            for name in m.get("files", []):
                p = folder / str(name)
                if p.suffix.lower() in (".mp4", ".mkv", ".mov", ".webm") and p.exists():
                    return p
    return None


def scan_session(
    session_dir: Path,
    paths: Paths,
    cfg: BestGuessConfig | None = None,
    fact_fallback: Roi | None = None,
    profile: Profile | None = None,
) -> BannerScan | None:
    """Scan a session's inbox video with the active Best Guess profile (or ``fact_fallback``
    when the profile has no fact_roi) and write ``analysis/banner.json`` + ``intervals.json``."""
    video = session_video(session_dir, paths)
    if video is None:
        return None
    cfg = cfg or BestGuessConfig()
    prof = profile or load_profile("bestguess", paths)
    fact_roi = prof.roi("fact_roi") if prof.has("fact_roi") else fact_fallback
    if fact_roi is None:
        return None
    word_roi = prof.roi("reveal_word_roi") if prof.has("reveal_word_roi") else None
    round_roi = prof.roi("round_complete_roi") if prof.has("round_complete_roi") else None
    check = (lambda img: in_arena(img, prof, cfg)) if prof.has("fact_roi") else None
    scan = scan_banner(
        video_frames(video),
        fact_roi,
        round_roi=round_roi,
        reveal_word_roi=word_roi,
        cfg=cfg,
        arena_check=check,
    )
    out = session_dir / "analysis"
    out.mkdir(exist_ok=True)
    (out / "banner.json").write_text(json.dumps(scan.as_dict(), indent=1), encoding="utf-8")
    (out / "intervals.json").write_text(
        json.dumps(intervals_from_scan(scan), indent=1), encoding="utf-8"
    )
    return scan
