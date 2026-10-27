"""Learn System v1 against the synthetic Best Guess session (KICKOFF M5 acceptance)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, cast

import pytest

from jamcrammer.core.config import AnalysisConfig, LearnConfig
from jamcrammer.core.paths import Paths
from jamcrammer.learn.analyze import analyze_session
from jamcrammer.learn.ingest import ingest_inbox, tags_of
from jamcrammer.learn.report import render_report
from jamcrammer.replay.harness import NearestStateDetector, replay
from jamcrammer.vision.ocr import ocr_image
from tests.synthetic.bestguess import FALSE_POINT, TRUE_POINT, generate_session

ROOT = Paths().root


def _ocr_available() -> bool:
    import numpy as np

    return not ocr_image(np.full((60, 200, 3), 255, dtype=np.uint8)).error


@pytest.fixture(scope="module")
def session(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Paths, dict[str, object]]:
    root = tmp_path_factory.mktemp("learn")
    paths = Paths(root=root)
    (root / "profiles").mkdir()
    for name in ("bestguess", "double_up", "shared", "parties"):
        shutil.copy2(
            ROOT / "profiles" / f"{name}.example.yaml", root / "profiles" / f"{name}.example.yaml"
        )
    (root / "assets" / "templates").mkdir(parents=True)
    session_dir = paths.sessions / "20260101_000000_bestguess_synthetic"
    info = generate_session(session_dir, rounds=4, fps=5.0, seed=3)
    return session_dir, paths, info


def test_analysis_finds_states_regions_zones_and_answers(
    session: tuple[Path, Paths, dict[str, object]],
) -> None:
    session_dir, paths, info = session
    if not _ocr_available():
        pytest.skip("winocr unavailable")
    result = analyze_session(session_dir, AnalysisConfig(), paths)
    assert result.frames == info["keyframes"]
    assert result.unique_frames < result.frames  # duplicates collapsed
    # the generic clusterer sees one scene for Best Guess (the real 2026-10-26 session too):
    # round semantics come from the Best Guess detector and the banner scan instead
    assert len(result.states) >= 1
    kinds = {r["kind"] for r in result.regions}
    assert "fact" in kinds, result.regions
    assert "countdown" in kinds or "gem_counter" in kinds
    tp, fp = result.zones["true_point"], result.zones["false_point"]
    assert tp is not None and abs(tp[0] - TRUE_POINT[0]) < 40 and abs(tp[1] - TRUE_POINT[1]) < 40
    assert fp is not None and abs(fp[0] - FALSE_POINT[0]) < 40
    assert len(result.rounds) == 4
    assert all(r["reveal_box"] is not None for r in result.rounds)
    assert len(result.answers) >= 3
    for a in result.answers:
        assert a["answer"] in ("T", "F")
    assert (
        "fact_roi" in result.proposal
        and "true_point" in result.proposal
        and "reveal_roi" in result.proposal
    )
    proposed = paths.profiles / "bestguess.proposed.yaml"
    assert proposed.exists()
    report = render_report(session_dir, result.as_dict())
    text = report.read_text(encoding="utf-8")
    assert "Learn Report" in text and "Proposed profile" in text and "data:image/png;base64" in text
    assert (session_dir / "analysis" / "labels.json").exists()


def test_answers_match_ground_truth(session: tuple[Path, Paths, dict[str, object]]) -> None:
    session_dir, _paths, info = session
    if not (session_dir / "analysis" / "result.json").exists():
        pytest.skip("analysis did not run")
    import json

    result = json.loads((session_dir / "analysis" / "result.json").read_text(encoding="utf-8"))
    rounds = cast(list[dict[str, Any]], info["rounds"])
    truth = {str(r["fact"]).lower(): bool(r["answer"]) for r in rounds}
    hits = 0
    for a in result["answers"]:
        key = next((k for k in truth if k[:12] in a["display"].lower()), None)
        if key is not None:
            hits += (a["answer"] == "T") == truth[key]
    assert hits >= len(result["answers"]) - 1  # one OCR slip allowed


def test_replay_self_consistency(session: tuple[Path, Paths, dict[str, object]]) -> None:
    session_dir, _, _ = session
    if not (session_dir / "analysis" / "labels.json").exists():
        pytest.skip("analysis did not run")
    result = replay(session_dir, NearestStateDetector(session_dir))
    assert result.frames > 0
    assert result.accuracy >= 0.95


def test_ingest_inbox_video_and_tagged_pngs(
    tmp_path: Path, session: tuple[Path, Paths, dict[str, object]]
) -> None:
    _, _, info = session
    paths = Paths(root=tmp_path)
    inbox = paths.inbox / "bestguess"
    inbox.mkdir(parents=True)
    for p in Path(str(info["inbox"])).iterdir():
        shutil.copy2(p, inbox / p.name)
    assert tags_of(Path("bg_002__reveal_correct.png")) == ["reveal_correct"]
    res = ingest_inbox(paths, LearnConfig(keyframe_threshold=6))
    assert res.videos == 1 and res.images == 3
    assert len(res.sessions) == 1
    assert res.keyframes >= 10  # change-triggered keyframes out of ~90 video frames
    assert not any(inbox.iterdir())  # originals moved
    assert list((paths.inbox / "_processed").glob("*/bestguess_sample.mp4"))
    from jamcrammer.learn.recorder import read_meta

    meta = read_meta(res.sessions[0])
    tagged = [m for m in meta if m.get("t") == "keyframe" and m.get("tags")]
    assert {t for m in tagged for t in m["tags"]} == {"question", "reveal_correct", "reveal_wrong"}
