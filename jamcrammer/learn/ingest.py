"""Inbox ingest (KICKOFF §11.1): PNG screenshots and MP4/MKV videos become Learn sessions.

Videos are decoded with OpenCV into change-triggered keyframes (same pHash rule as the live
recorder). Originals move to ``inbox/_processed/<ts>_<target>/``; nothing is deleted.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt

from jamcrammer.core.config import LearnConfig
from jamcrammer.core.logging import get_logger
from jamcrammer.core.paths import Paths
from jamcrammer.vision.match import hamming, phash_of
from jamcrammer.vision.stage import StageMap

log = get_logger("learn.ingest")
BgrImage = npt.NDArray[np.uint8]

IMAGE_EXT = {".png", ".jpg", ".jpeg"}
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi"}
TAG_RE = re.compile(r"__([a-z0-9_]+)$", re.IGNORECASE)
INBOX_TARGETS: dict[str, str] = {
    "bestguess": "bestguess",
    "parties": "parties",
    "popups": "popups",
    "arcade/double_up": "double_up",
    "arcade/jamaa_derby": "jamaa_derby",
    "arcade/gem_ball": "gem_ball",
    "arcade/other": "custom",
}


@dataclass
class IngestResult:
    sessions: list[Path] = field(default_factory=list)
    images: int = 0
    videos: int = 0
    keyframes: int = 0
    skipped: list[str] = field(default_factory=list)


def tags_of(path: Path) -> list[str]:
    m = TAG_RE.search(path.stem)
    return [m.group(1).lower()] if m else []


def _normalize(image: np.ndarray) -> BgrImage:
    return StageMap(int(image.shape[1]), int(image.shape[0])).normalize(
        np.asarray(image, dtype=np.uint8)
    )


def ingest_inbox(
    paths: Paths, cfg: LearnConfig, targets: dict[str, str] | None = None
) -> IngestResult:
    """Walk the inbox folders; each folder with files becomes one session."""
    result = IngestResult()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    for rel, target in (targets or INBOX_TARGETS).items():
        folder = paths.inbox / rel
        if not folder.exists():
            continue
        files = sorted(
            p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXT | VIDEO_EXT
        )
        if not files:
            continue
        session_dir = paths.sessions / f"{stamp}_{target}_inbox"
        n = ingest_files(files, session_dir, target, cfg, result)
        processed = paths.inbox / "_processed" / f"{stamp}_{rel.replace('/', '_')}"
        processed.mkdir(parents=True, exist_ok=True)
        for p in files:
            shutil.move(str(p), str(processed / p.name))
        result.sessions.append(session_dir)
        log.info(
            "ingested %d files from %s -> %s (%d keyframes)", len(files), rel, session_dir.name, n
        )
    return result


def ingest_files(
    files: list[Path],
    session_dir: Path,
    target: str,
    cfg: LearnConfig,
    result: IngestResult | None = None,
) -> int:
    """Build one session from explicit files (images and/or videos). Returns keyframe count."""
    result = result or IngestResult()
    frames_dir = session_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    meta: list[dict[str, Any]] = [
        {
            "t": "session",
            "target": target,
            "started_ts": time.time(),
            "window": None,
            "source": "inbox",
            "files": [p.name for p in files],
            "learn_cfg": cfg.model_dump(),
        }
    ]
    seq = 0
    t = 0.0
    for p in files:
        if p.suffix.lower() in IMAGE_EXT:
            img = cv2.imread(str(p), cv2.IMREAD_COLOR)
            if img is None:
                result.skipped.append(p.name)
                continue
            img = _normalize(img)
            seq += 1
            name = f"kf_{seq:06d}_{int(t * 1000)}.png"
            cv2.imwrite(str(frames_dir / name), img)
            meta.append(
                {
                    "t": "keyframe",
                    "ts": t,
                    "seq": seq,
                    "file": f"frames/{name}",
                    "size": [img.shape[1], img.shape[0]],
                    "source_file": p.name,
                    "tags": tags_of(p),
                    "phash": str(phash_of(img, cfg.phash_width)),
                }
            )
            result.images += 1
            t += 1.0
        elif p.suffix.lower() in VIDEO_EXT:
            added, t = _ingest_video(p, frames_dir, meta, seq, t, cfg)
            seq += added
            result.videos += 1
    meta.append({"t": "end", "ended_ts": time.time(), "stats": {"keyframes": seq}})
    with (session_dir / "meta.jsonl").open("w", encoding="utf-8") as fh:
        for rec in meta:
            fh.write(json.dumps(rec, default=str) + "\n")
    result.keyframes += seq
    return seq


def _ingest_video(  # noqa: PLR0917
    path: Path, frames_dir: Path, meta: list[dict[str, Any]], seq0: int, t0: float, cfg: LearnConfig
) -> tuple[int, float]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        meta.append({"t": "error", "file": path.name, "error": "could not open video"})
        return (0, t0)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, round(fps / cfg.ring_fps))
    last_hash = None
    last_kf_t = -1e9
    index = 0
    added = 0
    t = t0
    while True:
        ok, raw = cap.read()
        if not ok:
            break
        if index % step:
            index += 1
            continue
        t = t0 + index / fps
        img = _normalize(np.asarray(raw, dtype=np.uint8))
        h = phash_of(img, cfg.phash_width)
        dist = hamming(last_hash, h) if last_hash is not None else None
        if dist is None or dist >= cfg.keyframe_threshold or t - last_kf_t >= cfg.heartbeat_s:
            seq = seq0 + added + 1
            name = f"kf_{seq:06d}_{int(t * 1000)}.png"
            cv2.imwrite(str(frames_dir / name), img)
            meta.append(
                {
                    "t": "keyframe",
                    "ts": t,
                    "seq": seq,
                    "file": f"frames/{name}",
                    "size": [img.shape[1], img.shape[0]],
                    "source_file": path.name,
                    "video_frame": index,
                    "phash": str(h),
                    "distance": dist,
                }
            )
            last_hash, last_kf_t = h, t
            added += 1
        index += 1
    cap.release()
    return (added, t + 1.0)
