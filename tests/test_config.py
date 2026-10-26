from __future__ import annotations

from pathlib import Path

import pytest

from jamcrammer.core.config import AppConfig, Level, load_config
from jamcrammer.core.paths import Paths


def test_defaults_match_spec() -> None:
    cfg = AppConfig()
    assert cfg.hunt_interval_s == 300
    assert cfg.opportunist_check_s == 300
    assert cfg.resume_after_idle_s == 120
    assert cfg.heartbeat_s == 180
    assert cfg.antiafk.tap_ms == 200
    assert cfg.antiafk.gap_ms == 300
    assert cfg.fuzzy_threshold == 90
    assert cfg.hotkeys.kill == "f12"
    assert cfg.hotkeys.pause == "f11"
    assert cfg.hotkeys.toggle_mode == "f9"


def test_example_yaml_parses_and_equals_defaults() -> None:
    paths = Paths()
    cfg = AppConfig.from_yaml(paths.config_example)
    assert cfg == AppConfig(), "config.example.yaml drifted from code defaults"


def test_unknown_key_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "c.yaml"
    bad.write_text("fps: 4\nnot_a_key: 1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        AppConfig.from_yaml(bad)


def test_load_config_falls_back_to_defaults(tmp_path: Path) -> None:
    cfg = load_config(Paths(root=tmp_path))
    assert cfg == AppConfig()


def test_level_rank_order() -> None:
    assert Level.INFO.rank < Level.NOTICE.rank < Level.HIGH.rank
