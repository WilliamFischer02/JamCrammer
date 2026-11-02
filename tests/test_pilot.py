"""The pilot's guardrails and eyes (jamcrammer/pilot.py), without a window."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from jamcrammer.cli import build_parser
from jamcrammer.core.config import AppConfig, PilotConfig
from jamcrammer.pilot import (
    PilotRefusedError,
    PilotSession,
    check_enabled,
    check_key,
    check_point,
    check_yield,
    contact,
    pointer_path,
    summarize,
)
from jamcrammer.vision.ocr import OcrLine, OcrResult, OcrWord
from tests.synthetic import adventure as synth
from tests.test_adventure_level import _level


def test_the_switch_the_chat_field_and_the_key_list() -> None:
    cfg = PilotConfig()
    with pytest.raises(PilotRefusedError):
        check_enabled(cfg)
    check_enabled(PilotConfig(enabled=True))
    with pytest.raises(PilotRefusedError):
        check_point(cfg, 1000, 1030)  # the chat field
    with pytest.raises(PilotRefusedError):
        check_point(cfg, 1880, 1040)  # log out
    check_point(cfg, 960, 540)
    with pytest.raises(PilotRefusedError):
        check_key(cfg, "a", 0.5)  # never typing
    with pytest.raises(PilotRefusedError):
        check_key(cfg, "up", 10.0)  # too long
    check_key(cfg, "right", 1.0)
    with pytest.raises(PilotRefusedError):
        check_yield(cfg, 0.4)  # William just touched the mouse
    check_yield(cfg, 5.0)


def test_a_session_is_saved_and_logged(tmp_path: Path) -> None:
    s = PilotSession(tmp_path / "sess", tmp_path / "sess.stop", "mystery_below", 1.0)
    pointer = pointer_path(tmp_path)
    s.save(pointer)
    back = PilotSession.load(pointer)
    assert back is not None and back.session_dir == s.session_dir and back.target == "mystery_below"
    back.log({"cmd": "note", "text": "a torch"})
    lines = back.log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and '"cmd": "note"' in lines[0]
    assert PilotSession.load(tmp_path / "nothing.json") is None


def test_the_eyes_report_what_the_detectors_see() -> None:
    world = synth.World(synth.profile())
    synth.layout(world)
    world.px, world.py = 1700.0 - 120, 1100.0  # by fungus 1: the pin shows
    frame = world.image()
    words = (
        OcrWord("Explore", (900, 8, 60, 20)),
        OcrWord("the", (965, 8, 30, 20)),
        OcrWord("darkness.", (1000, 8, 80, 20)),
    )
    res = OcrResult(text="Explore the darkness.", lines=(OcrLine("Explore the darkness.", words),))
    seen = summarize(
        frame,
        synth.profile(),
        _level(world),
        res,
        "explore the darkness",
        digits=None,
        dialog_x=None,
        window={"hwnd": 1},
        frame_path="f.png",
        contact_path="h.png",
    )
    assert seen.menu == "in_adventure" and seen.phase == "start"
    assert seen.dot is not None and seen.pin is not None
    assert any(b["kind"] == "unlit" for b in seen.crystals) and len(seen.fungi) >= 1
    assert seen.ocr[0]["text"] == "Explore the darkness."
    half = contact(frame)
    assert half.shape[1] == 960 and half.shape[0] == 540


def test_the_cli_knows_the_pilot() -> None:
    parser = build_parser()
    args = parser.parse_args(["pilot", "key", "up", "1.5"])
    assert args.command == "pilot" and args.pilot_command == "key" and args.seconds == 1.5
    args = parser.parse_args(["pilot", "crop", "10", "20", "30", "40", "thing"])
    assert args.name == "thing" and args.w == 30
    args = parser.parse_args(["learn", "--target", "mystery_below", "--stop-file", "x.stop"])
    assert args.stop_file == "x.stop"
    assert AppConfig().pilot.enabled is False
    assert np.asarray(AppConfig().pilot.forbidden_rois).shape == (2, 4)
