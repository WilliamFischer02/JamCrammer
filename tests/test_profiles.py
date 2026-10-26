from __future__ import annotations

from pathlib import Path

import pytest

from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import Profile, ProfileError, load_profile


def test_shared_example_loads_and_exposes_types() -> None:
    p = load_profile("shared", Paths())
    assert p.name == "shared"
    assert p.point("party_hat_point") == (690, 50)
    assert p.roi("hud_top_band") == (0, 0, 1920, 110)
    lo, hi = p.color("popup_green_hsv")
    assert lo == (40, 120, 120) and hi == (85, 255, 255)
    assert "still playing" in p.items("still_playing_keywords")
    assert p.fraction_box("popup_center_box") == (0.25, 0.20, 0.75, 0.80)


def test_uncalibrated_field_is_refused() -> None:
    p = load_profile("shared", Paths())
    assert not p.has("gem_counter_roi")
    with pytest.raises(ProfileError):
        p.roi("gem_counter_roi")
    assert p.missing(["gem_counter_roi", "party_hat_point"]) == ["gem_counter_roi"]


def test_wrong_type_is_refused() -> None:
    p = load_profile("shared", Paths())
    with pytest.raises(ProfileError):
        p.roi("party_hat_point")


def test_set_value_and_roundtrip(tmp_path: Path) -> None:
    p = load_profile("shared", Paths())
    p.set_value("gem_counter_roi", [10, 20, 100, 30], status="proposed", confidence=0.7)
    out = tmp_path / "shared.yaml"
    p.save(out)
    q = Profile.from_yaml(out)
    assert q.roi("gem_counter_roi") == (10, 20, 100, 30)
    assert q.status("gem_counter_roi") == "proposed"
    assert q.field("gem_counter_roi").confidence == 0.7
    assert q.source_path == out


def test_real_profile_overrides_example(tmp_path: Path) -> None:
    root = tmp_path
    (root / "profiles").mkdir()
    (root / "profiles" / "x.example.yaml").write_text(
        "version: 1\nname: x\nfields:\n  a: {type: number, value: 1, status: proposed}\n",
        encoding="utf-8",
    )
    assert load_profile("x", Paths(root=root)).number("a") == 1
    (root / "profiles" / "x.yaml").write_text(
        "version: 1\nname: x\nfields:\n  a: {type: number, value: 2, status: calibrated}\n",
        encoding="utf-8",
    )
    assert load_profile("x", Paths(root=root)).number("a") == 2
    with pytest.raises(ProfileError):
        load_profile("nope", Paths(root=root))
