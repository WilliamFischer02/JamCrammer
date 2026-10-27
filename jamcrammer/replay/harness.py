"""Offline replay harness: run a state detector over a recorded session, compare with labels.

M5 ships the generic nearest-state classifier built from the session's own analysis (a
self-consistency check that proves the plumbing). Mode-specific detectors (M7/M8) plug in via
``StateDetector`` and are scored against the same ``analysis/labels.json``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from jamcrammer.learn.analyze import features
from jamcrammer.platform.capture import Frame
from jamcrammer.replay.sources import SessionSource
from jamcrammer.vision.match import phash_of


class StateDetector(Protocol):
    def detect(self, frame: Frame) -> str: ...


@dataclass
class ReplayResult:
    session: str
    frames: int
    correct: int
    timeline: list[tuple[float, str, str]] = field(default_factory=list)  # ts, predicted, labeled
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def accuracy(self) -> float:
        return self.correct / self.frames if self.frames else 0.0


def load_state_names(session_dir: Path) -> dict[str, str]:
    """``analysis/state_names.json``: analysis cluster id -> semantic state (review wizard)."""
    path = session_dir / "analysis" / "state_names.json"
    if not path.exists():
        return {}
    return {str(k): str(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def load_intervals(session_dir: Path) -> list[tuple[float, float, str]]:
    """``analysis/intervals.json``: [{start, end, state}] with state QUESTION | REVEAL | UNKNOWN."""
    path = session_dir / "analysis" / "intervals.json"
    if not path.exists():
        return []
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [(float(r["start"]), float(r["end"]), str(r["state"])) for r in rows]


def interval_labeler(
    intervals: list[tuple[float, float, str]], question_s: float, lock_seconds: int
) -> Callable[[float], str]:
    """Label a timestamp from intervals; QUESTION splits into OPEN/LOCKED by elapsed time."""

    def label(ts: float) -> str:
        for start, end, state in intervals:
            if start <= ts < end:
                if state == "QUESTION":
                    left = round(question_s - (ts - start))
                    return "LOCKED" if left <= lock_seconds else "QUESTION_OPEN"
                return state
        return "?"

    return label


def load_labels(session_dir: Path) -> dict[str, str]:
    path = session_dir / "analysis" / "labels.json"
    if not path.exists():
        return {}
    names = load_state_names(session_dir)
    out = {}
    for rec in json.loads(path.read_text(encoding="utf-8")):
        state = str(rec["state"])
        out[str(rec["file"])] = names.get(state, state)
    return out


class NearestStateDetector:
    """Classify a frame by the nearest analysis state (pHash bits + HSV histogram centroid)."""

    def __init__(self, session_dir: Path) -> None:
        from jamcrammer.learn.recorder import read_meta

        meta = read_meta(session_dir)
        frames = features.load_frames(session_dir, meta)
        labels = load_labels(session_dir)
        features.compute_features(frames)
        by_state: dict[str, list[np.ndarray]] = {}
        for f in frames:
            lab = labels.get(str(f.meta.get("file")))
            if lab is not None:
                by_state.setdefault(lab, []).append(features.feature_vector(f))
        self.centroids = {s: np.stack(v).mean(axis=0) for s, v in by_state.items()}

    def detect(self, frame: Frame) -> str:
        rec = features.FrameRec(index=0, ts=frame.ts, path=Path("<live>"))
        rec.phash = phash_of(frame.image, features.PHASH_WIDTH)
        rec.dhash = features.dhash_of(frame.image)
        rec.hist = features.color_hist(frame.image)
        x = features.feature_vector(rec)
        best, best_d = "?", float("inf")
        for s, c in self.centroids.items():
            d = float(np.linalg.norm(x - c))
            if d < best_d:
                best, best_d = s, d
        return best


def replay(
    session_dir: Path,
    detector: StateDetector,
    frames: Iterable[Frame] | None = None,
    label_map: Callable[[str], str] | None = None,
    labeler: Callable[[float], str] | None = None,
) -> ReplayResult:
    """Score ``detector`` against per-file labels (keyframes) or, with ``labeler``, against
    time intervals for any frame stream. Frames labelled '?' are not counted."""
    labels = load_labels(session_dir)
    source = SessionSource(session_dir)
    result = ReplayResult(session=session_dir.name, frames=0, correct=0)
    files = [str(rec["file"]) for rec in source._records]
    for i, frame in enumerate(frames if frames is not None else source.frames()):
        predicted = detector.detect(frame)
        if labeler is not None:
            labeled = labeler(frame.ts)
        else:
            labeled = labels.get(files[i], "?") if i < len(files) else "?"
        if label_map is not None:
            labeled = label_map(labeled)
        if labeled == "?":
            continue
        result.frames += 1
        result.correct += predicted == labeled
        result.timeline.append((frame.ts, predicted, labeled))
        result.confusion.setdefault(labeled, {})
        result.confusion[labeled][predicted] = result.confusion[labeled].get(predicted, 0) + 1
    return result


def format_timeline(result: ReplayResult, max_rows: int = 200) -> str:
    lines = [f"{'ts':>8}  {'predicted':<12} {'labeled':<12}"]
    last = None
    for ts, pred, lab in result.timeline[:max_rows]:
        mark = "" if pred == lab else "  <-- miss"
        if (pred, lab) != last:
            lines.append(f"{ts:8.2f}  {pred:<12} {lab:<12}{mark}")
        last = (pred, lab)
    lines.append(f"accuracy {result.accuracy:.1%} over {result.frames} frames")
    return "\n".join(lines)


def summarize(result: ReplayResult) -> dict[str, Any]:
    return {
        "session": result.session,
        "frames": result.frames,
        "accuracy": round(result.accuracy, 4),
        "confusion": result.confusion,
    }
