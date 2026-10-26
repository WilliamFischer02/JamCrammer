from __future__ import annotations

import pytest

from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import load_profile

EXAMPLES = ("shared", "parties", "bestguess", "double_up")


@pytest.mark.parametrize("name", EXAMPLES)
def test_every_example_profile_parses(name: str) -> None:
    """A YAML slip in a committed profile must fail here, not when the GUI opens."""
    prof = load_profile(name, Paths())
    assert prof.name == name
    assert prof.fields
    for key, f in prof.fields.items():
        assert f.status in ("calibrated", "proposed", "uncalibrated"), key
        if f.status != "uncalibrated":
            assert f.value is not None, f"{name}.{key} is {f.status} without a value"
