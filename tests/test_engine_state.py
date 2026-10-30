from __future__ import annotations

from pathlib import Path
from typing import Any

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


def test_engine_starts_on_a_fresh_clone(tmp_path: Path) -> None:
    """A clone has no data/ yet: the first start seeds the wiki facts and must not crash."""
    import shutil

    from jamcrammer.app.engine import Engine
    from jamcrammer.core.config import AppConfig
    from jamcrammer.core.paths import Paths

    repo = Paths()
    shutil.copytree(repo.profiles, tmp_path / "profiles")
    (tmp_path / "assets" / "bestguess").mkdir(parents=True)
    shutil.copy2(repo.assets / "bestguess" / "facts_wiki.txt", tmp_path / "assets" / "bestguess")
    cfg = AppConfig()
    cfg.notify.toast = cfg.notify.sound = False
    engine = Engine(cfg, Paths(tmp_path))
    try:
        assert engine.state.last_error == ""
        n = engine.store.query("SELECT COUNT(*) AS n FROM bg_facts")[0]["n"]
        assert n > 200
    finally:
        engine.store.close()


def test_the_keepalive_fires_only_when_everyone_is_idle(tmp_path: Path, monkeypatch: Any) -> None:
    """Neither the bot nor William touched the controls for heartbeat_s: one movement pair,
    then nothing until another heartbeat_s has passed; William active: nothing."""
    import shutil
    import time as _time

    from jamcrammer.app import engine as engine_mod
    from jamcrammer.app.engine import Engine
    from jamcrammer.core.config import AppConfig
    from jamcrammer.core.paths import Paths
    from jamcrammer.services.antiafk import CycleResult, DriftResult

    repo = Paths()
    shutil.copytree(repo.profiles, tmp_path / "profiles")
    (tmp_path / "assets" / "bestguess").mkdir(parents=True)
    shutil.copy2(repo.assets / "bestguess" / "facts_wiki.txt", tmp_path / "assets" / "bestguess")
    cfg = AppConfig(heartbeat_s=100.0)
    cfg.notify.toast = cfg.notify.sound = False
    engine = Engine(cfg, Paths(tmp_path))
    calls: list[float] = []

    def fake_cycle(grab: Any) -> CycleResult:
        calls.append(_time.time())
        return CycleResult(("left", "right"), DriftResult(0.0, False, False), sent=True)

    engine.antiafk.cycle = fake_cycle  # type: ignore[method-assign]
    now = _time.time()
    engine.input.stats.last_injected_ts = now - 500
    monkeypatch.setattr(engine_mod, "last_user_input_time", lambda: now - 500)
    engine._keepalive()
    engine._keepalive()
    assert len(calls) == 1  # once per heartbeat
    engine.input.stats.last_injected_ts = now  # the bot just drove: nothing
    engine._last_keepalive = 0.0
    engine._keepalive()
    assert len(calls) == 1
    engine.input.stats.last_injected_ts = now - 500
    monkeypatch.setattr(engine_mod, "last_user_input_time", lambda: now - 5)  # William active
    engine._keepalive()
    assert len(calls) == 1
    engine.store.close()
