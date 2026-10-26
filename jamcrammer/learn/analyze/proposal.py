"""Step 8: write ``profiles/<mode>.proposed.yaml`` with a confidence per field, crop templates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2

from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import Profile, load_profile
from jamcrammer.learn.analyze.behavior import RoundLabel
from jamcrammer.learn.analyze.features import FrameRec
from jamcrammer.learn.analyze.text import TextRegion

PAD = 12
REQUIRED: dict[str, list[str]] = {
    "bestguess": ["fact_roi", "true_point", "false_point", "reveal_roi"],
    "double_up": [
        "grid_origin",
        "grid_pitch",
        "tile_size",
        "board_roi",
        "play_again_point",
        "end_screen_gems_roi",
    ],
}


def _pad(box: tuple[int, int, int, int], w: int = 1920, h: int = 1080) -> list[int]:
    x, y, bw, bh = box
    x0, y0 = max(0, x - PAD), max(0, y - PAD)
    return [x0, y0, min(w - x0, bw + 2 * PAD), min(h - y0, bh + 2 * PAD)]


def write_proposal(
    *,
    target: str,
    session_dir: Path,
    paths: Paths,
    fact_region: TextRegion | None,
    countdown: TextRegion | None,
    gem_region: TextRegion | None,
    zones: dict[str, Any],
    labels: list[RoundLabel],
    frames: list[FrameRec],
    write: bool,
) -> tuple[dict[str, Any], list[str]]:
    try:
        prof = load_profile(target, paths)
    except Exception:
        return ({}, [])
    proposed: dict[str, Any] = {}

    def propose(key: str, value: Any, confidence: float, note: str) -> None:
        if key in prof.fields and value is not None:
            prof.set_value(
                key, value, status="proposed", confidence=round(confidence, 2), note=note
            )
            proposed[key] = {"value": value, "confidence": round(confidence, 2)}

    if target == "bestguess":
        if fact_region:
            propose(
                "fact_roi",
                _pad(fact_region.box),
                fact_region.confidence,
                f"text changing across {fact_region.distinct} rounds",
            )
        if countdown:
            propose(
                "countdown_roi", _pad(countdown.box), countdown.confidence, "digits decreasing ~1/s"
            )
        if gem_region:
            propose(
                "gem_counter_roi",
                _pad(gem_region.box),
                gem_region.confidence,
                "digits only increasing",
            )
        if zones.get("true_point"):
            propose(
                "true_point",
                list(zones["true_point"]),
                min(1.0, 0.5 + zones.get("left_clicks", 0) / 10),
                f"median of {zones.get('left_clicks', 0)} left clicks",
            )
        if zones.get("false_point"):
            propose(
                "false_point",
                list(zones["false_point"]),
                min(1.0, 0.5 + zones.get("right_clicks", 0) / 10),
                f"median of {zones.get('right_clicks', 0)} right clicks",
            )
        boxes = [lab.reveal_box for lab in labels if lab.reveal_box]
        if boxes:
            xs = sorted(b[0] for b in boxes)
            ys = sorted(b[1] for b in boxes)
            ws = sorted(b[2] for b in boxes)
            hs = sorted(b[3] for b in boxes)
            mid = len(boxes) // 2
            propose(
                "reveal_roi",
                _pad((xs[mid], ys[mid], ws[mid], hs[mid])),
                min(1.0, 0.4 + 0.15 * len(boxes)),
                f"region of maximal change at {len(boxes)} reveals",
            )
    elif target == "double_up" and zones.get("grid"):
        g = zones["grid"]
        for key in ("grid_origin", "grid_pitch", "tile_size", "board_roi"):
            propose(key, list(g[key]), 0.7, "from tile clicks")

    missing = [k for k in REQUIRED.get(target, []) if not prof.has(k)]
    if write and proposed:
        prof.save(paths.profiles / f"{target}.proposed.yaml")
        _crop_templates(target, prof, frames, paths, session_dir)
    return (proposed, missing)


def _crop_templates(
    target: str, prof: Profile, frames: list[FrameRec], paths: Paths, session_dir: Path
) -> None:
    """Crop proposed ROIs from a representative frame into assets/templates/<mode>/ (review)."""
    if not frames:
        return
    out = paths.assets / "templates" / target
    out.mkdir(parents=True, exist_ok=True)
    img = frames[len(frames) // 2].load()
    for key, f in prof.fields.items():
        if f.type == "roi" and f.usable and f.status == "proposed":
            x, y, w, h = (int(v) for v in f.value)
            crop = img[max(0, y) : y + h, max(0, x) : x + w]
            if crop.size:
                cv2.imwrite(str(out / f"{key}_{session_dir.name}.png"), crop)
