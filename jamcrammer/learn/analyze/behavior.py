"""Steps 4-7: temporal model, click zones, outcome labeling (learner leaderboard), answers."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from jamcrammer.learn.analyze.features import FrameRec
from jamcrammer.learn.analyze.text import TextRegion
from jamcrammer.vision.ocr import normalize_text

Box = tuple[int, int, int, int]
Point = tuple[int, int]

LEFT_RIGHT_SPLIT = 0.5  # fraction of the stage width that separates True (left) from False
REVEAL_WINDOW_S = 3.0
DIFF_DOWNSCALE = (96, 54)
GRID_TOLERANCE = 0.35


@dataclass
class Transition:
    from_state: int
    to_state: int
    ts: float


@dataclass
class TemporalModel:
    durations: dict[int, list[float]] = field(default_factory=dict)
    transitions: list[Transition] = field(default_factory=list)
    cycle: list[int] = field(default_factory=list)

    def median_duration(self, state: int) -> float:
        d = sorted(self.durations.get(state, []))
        return d[len(d) // 2] if d else 0.0


def temporal_model(frames: list[FrameRec]) -> TemporalModel:
    """Step 4: state runs over time, their durations and the most common cycle."""
    model = TemporalModel()
    if not frames:
        return model
    run_state, run_start = frames[0].state, frames[0].ts
    order: list[int] = [run_state]
    for prev, cur in itertools.pairwise(frames):
        if cur.state != run_state:
            model.durations.setdefault(run_state, []).append(cur.ts - run_start)
            model.transitions.append(Transition(run_state, cur.state, cur.ts))
            run_state, run_start = cur.state, cur.ts
            order.append(cur.state)
        else:
            _ = prev
    model.durations.setdefault(run_state, []).append(frames[-1].ts - run_start)
    model.cycle = _dominant_cycle(order)
    return model


def _dominant_cycle(order: list[int]) -> list[int]:
    best: list[int] = []
    best_count = 0
    for length in range(2, 6):
        counts: dict[tuple[int, ...], int] = {}
        for i in range(len(order) - length + 1):
            window = tuple(order[i : i + length])
            if len(set(window)) == length:
                counts[window] = counts.get(window, 0) + 1
        if counts:
            window, count = max(counts.items(), key=lambda kv: (kv[1] * len(kv[0]), kv[1]))
            if count >= 2 and count * length > best_count:
                best, best_count = list(window), count * length
    return best


def clicks_by_state(
    frames: list[FrameRec], inputs: list[dict[str, Any]]
) -> dict[int, list[tuple[float, Point]]]:
    """Map each of William's clicks to the state visible at that moment."""
    out: dict[int, list[tuple[float, Point]]] = {}
    for rec in inputs:
        if rec.get("kind") != "mouse" or rec.get("event") != "press" or rec.get("cx") is None:
            continue
        ts = float(rec["ts"])
        frame = _frame_at(frames, ts)
        if frame is None:
            continue
        out.setdefault(frame.state, []).append((ts, (int(rec["cx"]), int(rec["cy"]))))
    return out


def _frame_at(frames: list[FrameRec], ts: float) -> FrameRec | None:
    best = None
    for f in frames:
        if f.ts <= ts:
            best = f
        else:
            break
    return best


def side_zones(points: list[Point], width: int) -> dict[str, Any]:
    """Step 5 (Best Guess): split clicks left/right → True/False target points."""
    left = [p for p in points if p[0] < width * LEFT_RIGHT_SPLIT]
    right = [p for p in points if p[0] >= width * LEFT_RIGHT_SPLIT]

    def center(ps: list[Point]) -> Point | None:
        if not ps:
            return None
        return (int(np.median([p[0] for p in ps])), int(np.median([p[1] for p in ps])))

    return {
        "true_point": center(left),
        "false_point": center(right),
        "left_clicks": len(left),
        "right_clicks": len(right),
    }


def grid_from_clicks(points: list[Point], shape: tuple[int, int]) -> dict[str, Any] | None:
    """Step 5 (Double Up): tile clicks → grid origin / pitch / tile size."""
    cols, rows = shape
    if len(points) < cols + rows:
        return None
    xs = sorted(p[0] for p in points)
    ys = sorted(p[1] for p in points)

    def levels(values: list[int], n: int) -> list[int] | None:
        groups: list[list[int]] = [[values[0]]]
        span = (values[-1] - values[0]) / max(1, n - 1)
        for v in values[1:]:
            if v - groups[-1][-1] > span * GRID_TOLERANCE:
                groups.append([v])
            else:
                groups[-1].append(v)
        if len(groups) != n:
            return None
        return [int(np.median(g)) for g in groups]

    lx, ly = levels(xs, cols), levels(ys, rows)
    if lx is None or ly is None:
        return None
    px = int(np.median(np.diff(lx))) if cols > 1 else 0
    py = int(np.median(np.diff(ly))) if rows > 1 else 0
    return {
        "grid_origin": (lx[0], ly[0]),
        "grid_pitch": (px, py),
        "tile_size": (int(px * 0.8), int(py * 0.8)),
        "board_roi": (lx[0] - px // 2, ly[0] - py // 2, px * cols, py * rows),
    }


@dataclass
class RoundLabel:
    marker_ts: float
    outcome: str  # correct | wrong
    chosen: str | None  # T | F | None
    fact: str | None
    gem_delta: int | None
    reveal_box: Box | None


def label_outcomes(  # noqa: PLR0917
    frames: list[FrameRec],
    markers: list[dict[str, Any]],
    clicks: list[tuple[float, Point]],
    fact_region: TextRegion | None,
    gem_region: TextRegion | None,
    width: int,
) -> list[RoundLabel]:
    """Step 6: around each F7/F8 marker, find the region of maximal change, the side William
    chose, the gem delta and the fact text shown just before."""
    labels: list[RoundLabel] = []
    for m in markers:
        if m.get("kind") not in ("correct", "wrong"):
            continue
        ts = float(m["ts"])
        before = [f for f in frames if ts - REVEAL_WINDOW_S * 3 <= f.ts <= ts - REVEAL_WINDOW_S]
        after = [f for f in frames if ts - REVEAL_WINDOW_S < f.ts <= ts + 0.5]
        reveal_box = _max_change_box(before[-1], after[-1]) if before and after else None
        chosen: str | None = None
        recent = [p for t, p in clicks if ts - 15.0 <= t <= ts]
        if recent:
            chosen = "T" if recent[-1][0] < width * LEFT_RIGHT_SPLIT else "F"
        fact = _last_text(fact_region, ts, frames) if fact_region else None
        gem_delta = _gem_delta(gem_region, ts, frames) if gem_region else None
        labels.append(RoundLabel(ts, str(m["kind"]), chosen, fact, gem_delta, reveal_box))
    return labels


def _max_change_box(a: FrameRec, b: FrameRec) -> Box | None:
    ia, ib = a.load(), b.load()
    da = cv2.resize(
        cv2.cvtColor(cv2.absdiff(ia, ib), cv2.COLOR_BGR2GRAY),
        DIFF_DOWNSCALE,
        interpolation=cv2.INTER_AREA,
    )
    _, mask = cv2.threshold(da, 40, 255, cv2.THRESH_BINARY)
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(mask)
    if n <= 1:
        return None
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    sx = ia.shape[1] / DIFF_DOWNSCALE[0]
    sy = ia.shape[0] / DIFF_DOWNSCALE[1]
    return (
        int(stats[i, 0] * sx),
        int(stats[i, 1] * sy),
        int(stats[i, 2] * sx),
        int(stats[i, 3] * sy),
    )


def _last_text(region: TextRegion, ts: float, frames: list[FrameRec]) -> str | None:
    best = None
    for idx, text in region.samples:
        if frames[idx].ts <= ts and len(text) >= 8:
            best = text
    return best


def _gem_delta(region: TextRegion, ts: float, frames: list[FrameRec]) -> int | None:
    from jamcrammer.vision.ocr import parse_int

    before: list[int] = []
    after: list[int] = []
    for i, t in region.samples:
        v = parse_int(t)
        if v is None:
            continue
        if frames[i].ts < ts - 1.0:
            before.append(v)
        elif frames[i].ts <= ts + 3.0:
            after.append(v)
    if not before or not after:
        return None
    return after[-1] - before[-1]


def strategy_leaderboard(labels: list[RoundLabel], frames: list[FrameRec]) -> list[dict[str, Any]]:
    """Rank 'which answer was correct' strategies by agreement with the F7/F8 markers."""
    rows = []
    n = len(labels)
    gem_hits = sum(
        1
        for lab in labels
        if lab.gem_delta is not None and ((lab.gem_delta > 0) == (lab.outcome == "correct"))
    )
    gem_known = sum(1 for lab in labels if lab.gem_delta is not None)
    rows.append(
        {"strategy": "gem-counter delta", "agree": gem_hits, "known": gem_known, "total": n}
    )
    reveal_known = sum(1 for lab in labels if lab.reveal_box is not None)
    rows.append(
        {
            "strategy": "reveal-region change",
            "agree": reveal_known,
            "known": reveal_known,
            "total": n,
            "note": "ROI proposed; color rule needs a template",
        }
    )
    rows.append(
        {
            "strategy": "own-animal dance (frame diff)",
            "agree": 0,
            "known": 0,
            "total": n,
            "note": "needs own_animal_roi",
        }
    )

    def score(r: dict[str, Any]) -> tuple[float, int]:
        agree, known = int(r["agree"]), int(r["known"])
        return (agree / known if known else 0.0, known)

    rows.sort(key=score, reverse=True)
    return rows


def seed_answers(labels: list[RoundLabel]) -> list[dict[str, Any]]:
    """Step 7: fact + chosen side + outcome → answer rows with confidence."""
    out = []
    for lab in labels:
        if not lab.fact or lab.chosen is None:
            continue
        correct = lab.outcome == "correct"
        answer = lab.chosen if correct else ("F" if lab.chosen == "T" else "T")
        conf = 0.9 if lab.gem_delta is not None else 0.7
        out.append(
            {
                "normalized": normalize_text(lab.fact),
                "display": lab.fact,
                "answer": answer,
                "confidence": conf,
                "marker_ts": lab.marker_ts,
            }
        )
    return out
