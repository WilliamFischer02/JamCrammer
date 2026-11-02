"""The Derby Learn analyzer recovers where a player pressed relative to each band, the band's
width class and the stumbles, from 5 fps keyframes plus the wall-clock key log."""

from __future__ import annotations

import json
from pathlib import Path

import cv2

from jamcrammer.learn.analyze.derby_timing import analyze_session
from tests.synthetic import derby as synth
from tests.test_derby import _profile

T0 = 1_800_000_000.0
FPS = 5.0
AHEAD_PX = 120  # the player presses with the band's edge this far ahead of the hooves
LATE_EVERY = 4  # ... except every 4th band, pressed after its edge passed: a stumble


def _record(session: Path, world: synth.DerbyWorld) -> int:
    (session / "frames").mkdir(parents=True)
    meta: list[dict[str, object]] = []
    pressed: set[int] = set()
    late = 0
    next_frame, n = 0.0, 0
    while world.races < 1 and world.t < 200:
        if world.phase == "RACING":
            for i, b in enumerate(world.bands):
                d = b.x - synth.JUMP_TRIGGER_X
                want = -40 if i % LATE_EVERY == LATE_EVERY - 1 else AHEAD_PX
                if i not in pressed and d <= want and d > want - 60:
                    pressed.add(i)
                    late += want < 0
                    world.key_tap("space", 40)
                    meta.append(
                        {
                            "t": "input",
                            "ts": T0 + world.t,
                            "kind": "key",
                            "event": "press",
                            "key": "space",
                        }
                    )
        if world.t >= next_frame:
            n += 1
            name = f"frames/kf_{n:06d}.png"
            cv2.imwrite(str(session / name), world.image())
            meta.append({"t": "keyframe", "ts": T0 + world.t, "file": name})
            next_frame += 1 / FPS
        world.tick(0.005)
    (session / "meta.jsonl").write_text("".join(json.dumps(m) + "\n" for m in meta))
    return late


def test_presses_edges_and_stumbles_from_a_session(tmp_path: Path) -> None:
    world = synth.DerbyWorld(seed=7, stall_s=1.5, race_len_px=20000)
    late = _record(tmp_path / "s", world)
    rep = analyze_session(tmp_path / "s", profile=_profile())
    assert rep["races"] == 1 and rep["unmatched_presses"] == 0, rep
    rows = [
        json.loads(x)
        for x in (tmp_path / "s/analysis/derby_presses.jsonl").read_text().splitlines()
    ]
    on_time = [r for r in rows if r["edge_rel"] is not None and r["edge_rel"] > 0]
    assert on_time and all(abs(r["edge_rel"] - AHEAD_PX) <= 30 for r in on_time), on_time
    assert all(not r["stumbled"] for r in on_time)
    late_rows = [r for r in rows if r["edge_rel"] is not None and r["edge_rel"] < 0]
    assert len(late_rows) == late and all(r["stumbled"] for r in late_rows), late_rows
    kinds = {k.split(":")[0] for k in rep["census"]}
    assert kinds <= {"hay", "mud", "water", "hedge"} and len(kinds) >= 3, rep["census"]
