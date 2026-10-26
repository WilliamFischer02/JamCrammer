"""Synthetic Best Guess session generator (KICKOFF §14): frames, an MP4, tagged PNGs and a
``meta.jsonl`` with William-style clicks and F7/F8 markers, so Learn can be tested offline."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

BgrImage = npt.NDArray[np.uint8]

W, H = 1920, 1080
FACT_ROI = (360, 40, 1200, 120)
COUNTDOWN_ROI = (900, 180, 120, 80)
GEM_ROI = (1560, 20, 300, 70)
TRUE_POINT = (480, 700)
FALSE_POINT = (1440, 700)
REVEAL_ROI = (760, 300, 400, 160)
FACTS = [
    ("Cats purr when happy", True),
    ("Elephants can jump", False),
    ("Owls can turn their heads 270 degrees", True),
    ("Sharks are mammals", False),
    ("Honey never spoils", True),
    ("Bats are blind", False),
]


@dataclass
class Round:
    fact: str
    answer: bool
    chosen: bool
    t0: float
    correct: bool = field(init=False)

    def __post_init__(self) -> None:
        self.correct = self.chosen == self.answer


def arena(gems: int) -> BgrImage:
    img = np.zeros((H, W, 3), dtype=np.uint8)
    img[:, : W // 2] = (90, 170, 80)  # green left (True)
    img[:, W // 2 :] = (160, 80, 150)  # purple right (False)
    cv2.rectangle(img, (0, 0), (W, 160), (40, 40, 60), -1)  # banner strip for the fact
    cv2.rectangle(img, (0, 1000), (W, H), (50, 40, 30), -1)  # HUD
    x, y, w, h = GEM_ROI
    cv2.rectangle(img, (x, y), (x + w, y + h), (30, 30, 30), -1)
    cv2.putText(
        img,
        f"Gems {gems:,}",
        (x + 10, y + 50),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.4,
        (255, 255, 255),
        3,
        cv2.LINE_AA,
    )
    return img


def frame_question(fact: str, seconds_left: int, gems: int) -> BgrImage:
    img = arena(gems)
    x, y, _w, _h = FACT_ROI
    cv2.putText(
        img, fact, (x + 20, y + 80), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (255, 255, 255), 3, cv2.LINE_AA
    )
    cx, cy, _cw, _ch = COUNTDOWN_ROI
    cv2.putText(
        img,
        str(seconds_left),
        (cx + 20, cy + 60),
        cv2.FONT_HERSHEY_SIMPLEX,
        2.0,
        (255, 230, 80),
        4,
        cv2.LINE_AA,
    )
    return img


def frame_reveal(fact: str, answer: bool, gems: int) -> BgrImage:
    img = frame_question(fact, 0, gems)
    x, y, w, h = REVEAL_ROI
    color = (80, 220, 80) if answer else (220, 80, 220)
    cv2.rectangle(img, (x, y), (x + w, y + h), color, -1)
    cv2.putText(
        img,
        "TRUE" if answer else "FALSE",
        (x + 60, y + 110),
        cv2.FONT_HERSHEY_SIMPLEX,
        2.5,
        (0, 0, 0),
        6,
        cv2.LINE_AA,
    )
    return img


def frame_waiting(gems: int) -> BgrImage:
    img = arena(gems)
    cv2.putText(
        img,
        "Get ready...",
        (760, 110),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.6,
        (200, 200, 200),
        3,
        cv2.LINE_AA,
    )
    return img


def generate_session(
    out_dir: Path,
    rounds: int = 4,
    fps: float = 5.0,
    seed: int = 1,
    video: bool = True,
    pngs: bool = True,
) -> dict[str, object]:
    """Write a complete fake *live* Learn session (frames/, meta.jsonl) and sample inbox files."""
    rng = random.Random(seed)
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    meta: list[dict[str, object]] = []
    t = 0.0
    gems = 1000
    seq = 0
    writer = None
    inbox_dir = out_dir / "inbox_samples"
    inbox_dir.mkdir(exist_ok=True)
    if video:
        writer = cv2.VideoWriter(
            str(inbox_dir / "bestguess_sample.mp4"), cv2.VideoWriter.fourcc(*"mp4v"), fps, (W, H)
        )

    def emit(
        img: BgrImage, kind: str, extra: dict[str, object] | None = None, dt: float | None = None
    ) -> None:
        nonlocal t, seq
        seq += 1
        name = f"kf_{seq:06d}_{int(t * 1000)}.png"
        cv2.imwrite(str(frames_dir / name), img)
        rec: dict[str, object] = {
            "t": "keyframe",
            "ts": t,
            "seq": seq,
            "file": f"frames/{name}",
            "size": [W, H],
            "synthetic_state": kind,
        }
        if extra:
            rec.update(extra)
        meta.append(rec)
        if writer is not None:
            writer.write(img)
        t += (1.0 / fps) if dt is None else dt

    meta.append(
        {
            "t": "session",
            "target": "bestguess",
            "started_ts": 0.0,
            "window": {"client_rect": [0, 0, W, H], "dpi": 96},
            "source": "synthetic",
        }
    )
    rounds_out: list[Round] = []
    for r in range(rounds):
        fact, answer = FACTS[r % len(FACTS)]
        for _ in range(3):
            emit(frame_waiting(gems), "WAITING_FOR_ROUND")
        t0 = t
        chosen = answer if rng.random() < 0.75 else not answer
        rnd = Round(fact, answer, chosen, t0)
        for i in range(10, 0, -1):
            # one keyframe per countdown second, as a change-triggered recorder would store it
            emit(frame_question(fact, i, gems), "QUESTION_OPEN" if i > 5 else "LOCKED", dt=1.0)
            if i == 8:  # William clicks a side ~2 s in
                px, py = TRUE_POINT if chosen else FALSE_POINT
                meta.append(
                    {
                        "t": "input",
                        "ts": t,
                        "kind": "mouse",
                        "event": "press",
                        "button": "left",
                        "sx": px,
                        "sy": py,
                        "cx": px,
                        "cy": py,
                        "inside": True,
                    }
                )
        if rnd.correct:
            gems += 10
        for _ in range(4):
            emit(frame_reveal(fact, answer, gems), "REVEAL")
        meta.append(
            {
                "t": "input",
                "ts": t,
                "kind": "key",
                "event": "press",
                "key": "f7" if rnd.correct else "f8",
            }
        )
        meta.append(
            {
                "t": "marker",
                "ts": t,
                "kind": "correct" if rnd.correct else "wrong",
                "note": "",
                "dir": f"m{r:03d}",
                "past_frames": 0,
            }
        )
        rounds_out.append(rnd)
    meta.append(
        {"t": "end", "ended_ts": t, "stats": {"frames": seq, "keyframes": seq, "markers": rounds}}
    )
    if writer is not None:
        writer.release()
    with (out_dir / "meta.jsonl").open("w", encoding="utf-8") as fh:
        for rec in meta:
            fh.write(json.dumps(rec) + "\n")
    if pngs:
        cv2.imwrite(str(inbox_dir / "bg_001__question.png"), frame_question(FACTS[0][0], 7, 1000))
        cv2.imwrite(
            str(inbox_dir / "bg_002__reveal_correct.png"), frame_reveal(FACTS[0][0], True, 1010)
        )
        cv2.imwrite(
            str(inbox_dir / "bg_003__reveal_wrong.png"), frame_reveal(FACTS[1][0], False, 1010)
        )
    return {"rounds": [r.__dict__ for r in rounds_out], "keyframes": seq, "inbox": inbox_dir}
