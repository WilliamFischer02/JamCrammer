"""Orchestrates steps 1-9 and writes ``analysis/result.json`` plus thumbnails into the session."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2

from jamcrammer.core.config import AnalysisConfig
from jamcrammer.core.logging import get_logger
from jamcrammer.core.paths import Paths
from jamcrammer.learn.analyze import behavior, features, text
from jamcrammer.learn.analyze.proposal import write_proposal
from jamcrammer.learn.recorder import read_meta
from jamcrammer.vision.ocr import normalize_text

log = get_logger("learn.analyze")


@dataclass
class StateInfo:
    id: int
    frames: int
    representative: str  # relative path of the thumbnail
    median_duration_s: float
    sample_frames: list[str]
    tags: list[str] = field(default_factory=list)


@dataclass
class AnalysisResult:
    session: str
    target: str
    frames: int
    unique_frames: int
    states: list[StateInfo]
    cycle: list[int]
    transitions: int
    regions: list[dict[str, Any]]
    zones: dict[str, Any]
    rounds: list[dict[str, Any]]
    leaderboard: list[dict[str, Any]]
    answers: list[dict[str, Any]]
    proposal: dict[str, Any]
    missing: list[str]
    elapsed_s: float
    generated: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _merge_banner_scan(
    session_dir: Path,
    paths: Paths,
    fact_region: text.TextRegion | None,
    answers: list[dict[str, Any]],
    zones: dict[str, Any],
) -> None:
    """Best Guess: read the banner through the original video (question -> True!/False!)
    and add every paired fact to the seeded answers; summarise rounds in ``zones``."""
    from jamcrammer.learn.analyze.bestguess_banner import OPEN_END, scan_session

    b = fact_region.box if fact_region else None
    fallback = (int(b[0]), int(b[1]), int(b[2]), int(b[3])) if b else None
    scan = scan_session(session_dir, paths, fact_fallback=fallback)
    if scan is None:
        return
    known = {a["normalized"] for a in answers}
    for f in scan.facts:
        norm = normalize_text(f.question)
        if norm in known:
            continue
        known.add(norm)
        answers.append(
            {
                "normalized": norm,
                "display": f.question,
                "answer": f.answer,
                "confidence": 0.9 if f.seen > 1 else 0.85,
                "marker_ts": f.reveal_ts,
                "source": "banner",
            }
        )
    zones["banner"] = {
        "facts": len(scan.facts),
        "reveals": sum(e.kind == "reveal" for e in scan.events),
        "rounds": [{"ts": r.ts, "correct": r.correct, "gems": r.gems} for r in scan.rounds],
        "outside_s": round(sum(e - s for s, e in scan.outside if e != OPEN_END), 1),
    }


def analyze_session(  # noqa: PLR0915
    session_dir: Path, cfg: AnalysisConfig, paths: Paths | None = None, write_profile: bool = True
) -> AnalysisResult:
    t0 = time.perf_counter()
    paths = paths or Paths()
    meta = read_meta(session_dir)
    header = next((m for m in meta if m.get("t") == "session"), {})
    target = str(header.get("target", "custom"))
    out_dir = session_dir / "analysis"
    (out_dir / "states").mkdir(parents=True, exist_ok=True)

    # 1. frames, features, stability
    frames = features.load_frames(session_dir, meta)
    features.compute_features(frames)
    features.mark_stability(frames, cfg.duplicate_distance, cfg.transition_distance)
    unique = [f for f in frames if f.duplicate_of is None]

    # 2. scene clustering
    groups = features.cluster_states(unique, cfg.cluster_distance, cfg.min_cluster)
    for f in frames:  # duplicates inherit the state of their original
        if f.duplicate_of is not None:
            f.state = frames[f.duplicate_of].state
    model = behavior.temporal_model(frames)
    states: list[StateInfo] = []
    for sid, members in enumerate(groups):
        rep = features.medoid(unique, members)
        thumb_rel = f"analysis/states/state_{sid}.png"
        cv2.imwrite(str(session_dir / thumb_rel), features.thumbnail(unique[rep].load()))
        samples = []
        for i in members[: cfg.samples_per_state]:
            rel = f"analysis/states/state_{sid}_s{i}.png"
            cv2.imwrite(str(session_dir / rel), features.thumbnail(unique[i].load(), 160))
            samples.append(rel)
        tags = sorted({t for i in members for t in unique[i].tags})
        states.append(
            StateInfo(
                sid, len(members), thumb_rel, round(model.median_duration(sid), 2), samples, tags
            )
        )

    # 3. text regions (reuse the recorder's OCR lines when present)
    cached = {
        str(m["file"]): list(m.get("lines", []))
        for m in meta
        if m.get("t") == "ocr" and "file" in m and "lines" in m
    }
    regions = text.discover_regions(unique, cached, cfg.ocr_upscale) if cfg.ocr else []
    fact_region = next((r for r in regions if r.kind == "fact"), None)
    gem_region = next((r for r in regions if r.kind == "gem_counter"), None)
    countdown = next((r for r in regions if r.kind == "countdown"), None)

    # 5. zones from behavior
    inputs = [m for m in meta if m.get("t") == "input"]
    markers = [m for m in meta if m.get("t") == "marker"]
    width = 1920  # frames are stage-normalized
    by_state = behavior.clicks_by_state(frames, inputs)
    all_clicks = [c for cs in by_state.values() for c in cs]
    zones: dict[str, Any] = {"clicks_by_state": {str(k): len(v) for k, v in by_state.items()}}
    if target == "bestguess":
        zones.update(behavior.side_zones([p for _, p in all_clicks], width))
    elif target == "double_up":
        grid = behavior.grid_from_clicks([p for _, p in all_clicks], (4, 4))
        zones["grid"] = grid

    # 6-7. outcomes and answers
    labels = behavior.label_outcomes(frames, markers, all_clicks, fact_region, gem_region, width)
    leaderboard = behavior.strategy_leaderboard(labels, frames)
    answers = behavior.seed_answers(labels)
    rounds = [asdict(lab) for lab in labels]
    if target == "bestguess":
        _merge_banner_scan(session_dir, paths, fact_region, answers, zones)

    # 8. proposal
    proposal, missing = write_proposal(
        target=target,
        session_dir=session_dir,
        paths=paths,
        fact_region=fact_region,
        countdown=countdown,
        gem_region=gem_region,
        zones=zones,
        labels=labels,
        frames=frames,
        write=write_profile,
    )

    result = AnalysisResult(
        session=session_dir.name,
        target=target,
        frames=len(frames),
        unique_frames=len(unique),
        states=states,
        cycle=model.cycle,
        transitions=len(model.transitions),
        regions=[
            {
                "kind": r.kind,
                "box": r.box,
                "frames": r.frames,
                "distinct": r.distinct,
                "confidence": round(r.confidence, 2),
                "samples": [t for _, t in r.samples[:5]],
            }
            for r in regions[: cfg.max_regions]
        ],
        zones=zones,
        rounds=rounds,
        leaderboard=leaderboard,
        answers=answers,
        proposal=proposal,
        missing=missing,
        elapsed_s=round(time.perf_counter() - t0, 2),
        generated=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    (out_dir / "result.json").write_text(
        json.dumps(result.as_dict(), indent=2, default=str), encoding="utf-8"
    )
    # per-frame state labels for the replay check (hand labels are never overwritten)
    if not (out_dir / "labels_source.txt").exists():
        rows = [{"file": str(f.meta.get("file")), "ts": f.ts, "state": f.state} for f in frames]
        (out_dir / "labels.json").write_text(json.dumps(rows), encoding="utf-8")
    log.info(
        "analysis of %s: %d frames, %d states, %d rounds, %d answers in %.1fs",
        session_dir.name,
        len(frames),
        len(states),
        len(rounds),
        len(answers),
        result.elapsed_s,
    )
    return result
