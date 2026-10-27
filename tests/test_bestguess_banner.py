"""Banner scan (question -> True!/False! pairing, round scroll, intervals) with a fake OCR."""

from __future__ import annotations

import numpy as np

from jamcrammer.learn.analyze.bestguess_banner import (
    BannerScan,
    classify,
    intervals_from_scan,
    scan_banner,
)

FACT = (500, 10, 940, 200)
ROUND = (480, 300, 960, 420)


def test_classify_reads_ocr_noise() -> None:
    assert classify("Fuse! Wolverines are solitary animals.")[0:2] == ("reveal", "F")
    assert classify("True!")[0:2] == ("reveal", "T")
    assert classify("", "F")[0:2] == ("reveal", "F")  # colour wins when OCR finds nothing
    assert classify("Bulldog ants eat meat.")[0] == "question"
    assert classify("Cor 025")[0] == "none"
    kind, _, correct, gems = classify(
        "ROUND COMPLETE! 9 Correct Answers 250 GEMS EARNED! Next Round!"
    )
    assert (kind, correct, gems) == ("round_complete", 9, 250)


def _timeline() -> list[tuple[float, str, str]]:
    """(ts, banner text, scroll text) at 2 fps for two rounds, a scroll, then the lobby."""
    rows: list[tuple[float, str, str]] = []
    t = 0.0

    def add(n: int, banner: str, scroll: str = "chatter") -> None:
        nonlocal t
        for _ in range(n):
            rows.append((t, banner, scroll))
            t += 0.5

    add(20, "Bulldog ants eat meat.")
    add(12, "True!")
    add(20, "Lemmings are not rodents.")
    add(4, "Fuse! Lemmings are rodents.")
    add(8, "Fuse! Lemmings are rodents.", "ROUND COMPLETE! 2 Correct Answers 30 GEMS EARNED!")
    add(10, "LOBBY", "LOBBY")
    return rows


def test_scan_pairs_reveals_with_questions_and_builds_intervals() -> None:
    rows = _timeline()
    texts = {i: r for i, r in enumerate(rows)}
    frames = [
        (ts, np.full((1080, 1920, 3), i, dtype=np.uint8)) for i, (ts, _, _) in enumerate(rows)
    ]

    def ocr(img: np.ndarray) -> str:
        i = int(img[0, 0, 0])
        _, banner, scroll = texts[i]
        return scroll if img.shape[0] == ROUND[3] else banner

    lobby_from = rows[64][0]
    scan: BannerScan = scan_banner(
        frames, FACT, ocr=ocr, round_roi=ROUND, arena_check=lambda img: int(img[0, 0, 0]) < 64
    )
    assert [(f.question, f.answer) for f in scan.facts] == [
        ("Bulldog ants eat meat.", "T"),
        ("Lemmings are not rodents.", "F"),
    ]
    assert len(scan.rounds) == 1 and (scan.rounds[0].correct, scan.rounds[0].gems) == (2, 30)
    assert scan.outside and scan.outside[0][0] == lobby_from
    iv = intervals_from_scan(scan)
    assert iv[0]["state"] == "UNKNOWN" and iv[0]["start"] == lobby_from
    states = [r["state"] for r in iv[1:]]
    assert states == ["QUESTION", "REVEAL", "QUESTION", "REVEAL"]
    assert iv[1]["start"] == 0.0 and iv[1]["end"] == 10.0 and iv[2]["end"] == 16.0
