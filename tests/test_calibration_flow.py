"""The guided calibration flow's pure parts: profile metadata, the step model, captures."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from jamcrammer.app.pages.calibration_flow import (
    NOT_DRAWN,
    PROFILE_SCREENS,
    PROFILE_TITLES,
    WizardModel,
    build_steps,
    capture_path,
    capture_stamp,
    describe_value,
    field_hint,
    field_state,
    is_drawn,
    load_capture,
    run_check,
    save_capture,
    screens_for,
)
from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import Profile, ProfileField, load_profile

EXAMPLES = tuple(PROFILE_TITLES)


def _fields(**kw: ProfileField) -> dict[str, ProfileField]:
    return dict(kw)


# --- metadata ---------------------------------------------------------------------------------


@pytest.mark.parametrize("name", EXAMPLES)
def test_every_field_of_every_example_profile_has_a_label_and_help(name: str) -> None:
    prof = Profile.from_yaml(Paths().profiles / f"{name}.example.yaml")
    for key, f in prof.fields.items():
        assert f.label.strip(), f"{name}.{key} has no label"
        assert f.help.strip(), f"{name}.{key} has no help"
        assert f.label != key, f"{name}.{key}: the label is just the key"
        assert f.display_label(key) == f.label


@pytest.mark.parametrize("name", EXAMPLES)
def test_every_screen_field_exists_in_its_profile(name: str) -> None:
    prof = Profile.from_yaml(Paths().profiles / f"{name}.example.yaml")
    for spec in PROFILE_SCREENS[name]:
        for key in spec.fields:
            # a key may be live-only (bestguess.multiplier_roi); then it must still be sane
            assert key.isidentifier(), key
        if spec.check is not None:
            for key in (spec.check.roi_key, spec.check.template_key, spec.check.threshold_key):
                assert not key or key in prof.fields, f"{name}/{spec.key} check uses {key}"
    drawn = {k for k, f in prof.fields.items() if is_drawn(name, k, f)}
    claimed = {k for spec in PROFILE_SCREENS[name] for k in spec.fields}
    unclaimed = drawn - claimed
    assert not unclaimed, f"{name}: no screen for {sorted(unclaimed)}"


def test_range_flag_defaults_false_and_round_trips(tmp_path: Path) -> None:
    prof = load_profile("jamaa_derby", Paths(root=Paths().root))
    assert prof.field("horse_box_roi").range is False
    assert not prof.is_range("horse_box_roi")
    prof.set_range("horse_box_roi", True)
    with pytest.raises(Exception, match="only an roi"):
        prof.set_range("horse_front_x", True)
    prof.save(tmp_path / "jamaa_derby.yaml")
    text = (tmp_path / "jamaa_derby.yaml").read_text(encoding="utf-8")
    assert "range: true" in text
    back = Profile.from_yaml(tmp_path / "jamaa_derby.yaml")
    assert back.is_range("horse_box_roi") and not back.is_range("strip_roi")
    assert back.field("horse_box_roi").label == prof.field("horse_box_roi").label


# --- the step model ---------------------------------------------------------------------------


def test_steps_follow_screens_then_fields_then_numbers() -> None:
    fields = _fields(
        party_hat_point=ProfileField(type="point", value=[1, 2], status="calibrated"),
        title_template=ProfileField(type="template", value="t.png", status="proposed"),
        title_search_roi=ProfileField(type="roi", value=None),
        close_x_point=ProfileField(type="point", value=[3, 4], status="calibrated"),
        template_threshold=ProfileField(type="number", value=0.8, status="proposed"),
        known_labels=ProfileField(type="list", value=["a"], status="proposed"),
    )
    steps = build_steps("parties", fields)
    kinds = [(s.kind, s.screen.key, s.field) for s in steps]
    assert kinds == [
        ("screen", "room", ""),
        ("field", "room", "party_hat_point"),
        ("screen", "my_parties", ""),
        ("field", "my_parties", "title_template"),
        ("field", "my_parties", "title_search_roi"),
        ("field", "my_parties", "close_x_point"),
        ("numbers", "numbers", ""),
    ]
    assert steps[-1].fields == ("template_threshold", "known_labels")
    assert steps[3].fields == ("title_template", "title_search_roi", "close_x_point")


def test_unclaimed_drawn_fields_get_the_other_screen_and_xlines_are_drawn() -> None:
    fields = _fields(
        mystery_point=ProfileField(type="point", value=[1, 1], status="calibrated"),
        mystery_x=ProfileField(type="number", value=900, status="calibrated"),
        plain_number=ProfileField(type="number", value=3, status="calibrated"),
    )
    screens = screens_for("jamaa_derby", fields)
    assert screens[-1].key == "other"
    assert set(screens[-1].fields) == {"mystery_point", "mystery_x"}
    assert is_drawn("jamaa_derby", "mystery_x", fields["mystery_x"])
    assert not is_drawn("jamaa_derby", "plain_number", fields["plain_number"])
    rel = ProfileField(type="roi", value=[-10, -10, 20, 20], status="calibrated")
    assert "nametag_box" in NOT_DRAWN["adventure"]
    assert not is_drawn("adventure", "nametag_box", rel)


def test_model_navigation_and_decisions() -> None:
    fields = _fields(
        party_hat_point=ProfileField(type="point", value=[1, 2], status="calibrated"),
        close_x_point=ProfileField(type="point", value=None),
        title_search_roi=ProfileField(type="roi", value=[1, 1, 2, 2], status="proposed"),
    )
    m = WizardModel.for_profile("parties", fields)
    assert m.progress == (1, 5) and not m.can_back and m.can_next
    assert m.step.kind == "screen"
    m.back()  # cannot go before the start
    assert m.index == 0
    m.next()
    assert m.step.field == "party_hat_point"
    assert m.siblings() == ()
    m.confirm()
    assert m.decisions == {"party_hat_point": "confirmed"}
    assert m.step.kind == "screen" and m.step.screen.key == "my_parties"
    m.next()
    assert m.step.field == "title_search_roi" and m.siblings() == ("close_x_point",)
    m.skip()
    assert m.decisions["title_search_roi"] == "skipped"
    assert m.step.field == "close_x_point" and not m.can_next
    m.confirm()  # the last step stays put
    assert m.index == len(m.steps) - 1
    assert m.summary() == {"fields": 3, "confirmed": 2, "skipped": 1, "remaining": 0}
    m.go_to_screen("room")
    assert m.index == 0 and m.step.screen.key == "room"


def test_field_state_value_text_and_hints() -> None:
    assert field_state(ProfileField(type="roi", value=None)) == "missing"
    assert (
        field_state(ProfileField(type="roi", value=[1, 2, 3, 4], status="uncalibrated"))
        == "missing"
    )
    assert (
        field_state(ProfileField(type="roi", value=[1, 2, 3, 4], status="proposed")) == "proposed"
    )
    assert (
        field_state(ProfileField(type="roi", value=[1, 2, 3, 4], status="calibrated"))
        == "calibrated"
    )
    roi = ProfileField(type="roi", value=[10, 20, 30, 40], status="calibrated", range=True)
    assert describe_value(roi) == "x 10, y 20, 30 x 40 px  (range)"
    assert describe_value(ProfileField(type="point", value=[1, 2])) == "(1, 2)"
    assert describe_value(ProfileField(type="color", value=[[1, 2, 3], [4, 5, 6]])) == (
        "HSV 1-4 / 2-5 / 3-6"
    )
    assert describe_value(ProfileField(type="fraction_box", value=[0.25, 0.2, 0.75, 0.8])) == (
        "fractions 0.25, 0.20, 0.75, 0.80"
    )
    assert describe_value(ProfileField(type="list", value=[1, 2, 3])) == "3 items"
    assert describe_value(ProfileField(type="number", value=None)) == "not set"
    assert "Double-click" in field_hint(ProfileField(type="point"))
    assert "Sample" in field_hint(ProfileField(type="color"))


# --- captures and checks ----------------------------------------------------------------------


def test_capture_paths_and_stamps(tmp_path: Path) -> None:
    paths = Paths(root=tmp_path)
    path = capture_path(paths, "parties", "my_parties")
    assert path == tmp_path / "captures" / "calibration" / "parties" / "my_parties.png"
    assert capture_stamp(path) is None and load_capture(path) is None
    img = np.zeros((8, 16, 3), dtype=np.uint8)
    img[:, :8] = (0, 0, 255)
    save_capture(path, img)
    stamp = capture_stamp(path)
    assert stamp is not None and len(stamp) == len("2026-11-03 14:02")
    back = load_capture(path)
    assert back is not None and back.shape == img.shape and int(back[0, 0, 2]) == 255


def test_run_check_never_raises(tmp_path: Path) -> None:
    img = np.zeros((1080, 1920, 3), dtype=np.uint8)
    paths = Paths(root=Paths().root)

    def profile_of(name: str) -> Profile:
        return load_profile(name, paths)

    res = run_check(None, img, "parties", profile_of, paths=paths)
    assert res.ok is None
    for name in EXAMPLES:
        for spec in PROFILE_SCREENS[name]:
            res = run_check(spec.check, img, name, profile_of, paths=paths, ocr=lambda _i: "")
            assert res.text, f"{name}/{spec.key}"
            if spec.check is not None and spec.check.kind == "ocr":
                assert res.ok is False  # the empty OCR cannot contain the word
