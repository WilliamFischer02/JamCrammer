"""Step 3: text-region discovery. Full-frame OCR per unique frame → boxes grouped by position →
fact / countdown / gem-counter candidates."""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Any

from jamcrammer.learn.analyze.features import FrameRec
from jamcrammer.vision.ocr import OcrResult, ocr_image, parse_int

Box = tuple[int, int, int, int]
DIGITS = re.compile(r"^\s*\d{1,4}\s*$")
GEM_WORDS = ("gem", "gems")
MIN_FACT_CHARS = 12
IOU_GROUP = 0.35


@dataclass
class TextRegion:
    box: Box
    samples: list[tuple[int, str]] = field(default_factory=list)  # (frame index, text)
    kind: str = "text"  # text | fact | countdown | gem_counter
    confidence: float = 0.0

    @property
    def distinct(self) -> int:
        return len({t.strip().lower() for _, t in self.samples})

    @property
    def frames(self) -> int:
        return len({i for i, _ in self.samples})


def _iou(a: Box, b: Box) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def _merge(a: Box, b: Box) -> Box:
    x0, y0 = min(a[0], b[0]), min(a[1], b[1])
    x1, y1 = max(a[0] + a[2], b[0] + b[2]), max(a[1] + a[3], b[1] + b[3])
    return (x0, y0, x1 - x0, y1 - y0)


def ocr_lines_for(
    frame: FrameRec, cached: dict[str, list[dict[str, Any]]], upscale: float
) -> list[tuple[str, Box]]:
    """Use the recorder's cached OCR lines when present; otherwise run Windows OCR now."""
    rel = str(frame.meta.get("file", ""))
    if rel in cached:
        out = []
        for line in cached[rel]:
            words = line.get("words", [])
            if not words:
                continue
            xs = [w["box"][0] for w in words]
            ys = [w["box"][1] for w in words]
            x1 = max(w["box"][0] + w["box"][2] for w in words)
            y1 = max(w["box"][1] + w["box"][3] for w in words)
            out.append((str(line.get("text", "")), (min(xs), min(ys), x1 - min(xs), y1 - min(ys))))
        return out
    res: OcrResult = ocr_image(frame.load(), upscale=upscale)
    return res.boxes_in_source()


def discover_regions(
    frames: list[FrameRec], cached: dict[str, list[dict[str, Any]]], upscale: float
) -> list[TextRegion]:
    regions: list[TextRegion] = []
    for f in frames:
        if f.duplicate_of is not None:
            continue
        for text, box in ocr_lines_for(f, cached, upscale):
            if not text.strip():
                continue
            target = next((r for r in regions if _iou(r.box, box) >= IOU_GROUP), None)
            if target is None:
                regions.append(TextRegion(box=box, samples=[(f.index, text)]))
            else:
                target.box = _merge(target.box, box)
                target.samples.append((f.index, text))
    for r in regions:
        _classify(r, frames)
    regions.sort(key=lambda r: (r.kind == "text", -r.confidence, -r.frames))
    return regions


def _classify(region: TextRegion, frames: list[FrameRec]) -> None:
    texts = [t.strip() for _, t in sorted(region.samples)]
    if all(DIGITS.match(t) for t in texts) and len(texts) >= 3:
        values = [int(t) for t in texts]
        diffs = [b - a for a, b in itertools.pairwise(values)]
        if diffs and sum(1 for d in diffs if d < 0) >= 0.6 * len(diffs):
            region.kind, region.confidence = "countdown", min(1.0, 0.5 + len(texts) / 20)
            return
    lowered = [t.lower() for t in texts]
    if any(any(w in t for w in GEM_WORDS) for t in lowered) or (
        all(parse_int(t) is not None for t in texts) and len(texts) >= 2
    ):
        ints: list[int] = [v for v in (parse_int(t) for t in texts) if v is not None]
        if len(ints) >= 2 and all(b >= a for a, b in itertools.pairwise(ints)):
            region.kind, region.confidence = "gem_counter", 0.8
            return
    long_texts = [t for t in texts if len(t) >= MIN_FACT_CHARS]
    if long_texts and region.distinct >= 2 and region.frames >= 2:
        region.kind = "fact"
        region.confidence = min(1.0, 0.4 + 0.1 * region.distinct)
