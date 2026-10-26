from __future__ import annotations

from pathlib import Path

from jamcrammer.app.engine import EngineState
from jamcrammer.app.theme import PALETTE, build_qss


def test_pill_text_and_kind() -> None:
    st = EngineState()
    assert st.pill_text == "IDLE" and st.pill_kind == "idle"
    st.mode, st.mode_state, st.mode_detail = "hunt", "HUNTING", "next scan 3:42"
    assert st.pill_text == "HUNTING · next scan 3:42" and st.pill_kind == "running"
    st.paused = True
    assert st.pill_text == "PAUSED" and st.pill_kind == "paused"
    st.paused = False
    st.killed = True
    assert st.pill_text == "KILLED" and st.pill_kind == "error"
    st2 = EngineState(learn_session=Path("x"))
    assert st2.pill_text == "RECORDING" and st2.pill_kind == "running"


def test_qss_uses_only_tokens() -> None:
    qss = build_qss()
    for token in (PALETTE.bg, PALETTE.surface, PALETTE.accent, PALETTE.danger):
        assert token in qss
    assert "#StatePill" in qss and "#NavRail" in qss and "QFrame#Card" in qss


def test_palette_mode_and_state_colors() -> None:
    assert PALETTE.for_mode("hunt") == PALETTE.bestguess
    assert PALETTE.for_mode("farm") == PALETTE.arcade
    assert PALETTE.for_state("PAUSED_USER") == PALETTE.warn
    assert PALETTE.for_state("IDLE") == PALETTE.idle
    assert PALETTE.for_level("HIGH") == PALETTE.danger
