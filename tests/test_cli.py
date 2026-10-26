from __future__ import annotations

import pytest

from jamcrammer.cli import build_parser

SPEC_SUBCOMMANDS = (
    "gui",
    "hunt",
    "farm",
    "opportunist",
    "learn",
    "ingest",
    "replay",
    "snap",
    "report",
    "test-notify",
    "activity-test",
)


def test_parser_has_all_spec_subcommands() -> None:
    parser = build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    names = set(sub.choices or {})
    for name in SPEC_SUBCOMMANDS:
        assert name in names


def test_learn_requires_target() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["learn"])
    ns = build_parser().parse_args(["learn", "--target", "bestguess"])
    assert ns.target == "bestguess"
