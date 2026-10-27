"""Command-line interface.

Subcommands (KICKOFF §5): gui | hunt | farm | opportunist | learn | ingest | replay | snap |
report | test-notify | activity-test. M0 wires ``gui`` and the parser; the rest arrive with
their milestones and print a clear "not yet" until then.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from jamcrammer import __version__
from jamcrammer.core.config import load_config
from jamcrammer.core.logging import setup_logging
from jamcrammer.core.paths import Paths

LEARN_TARGETS = ("bestguess", "double_up", "jamaa_derby", "gem_ball", "parties", "custom")
MODES = ("hunt", "farm", "opportunist")
EXIT_NOT_IMPLEMENTED = 2

Command = Callable[[argparse.Namespace], int]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jamcrammer", description="JamCrammer CLI")
    parser.add_argument("--version", action="version", version=f"jamcrammer {__version__}")
    parser.add_argument("--config", type=str, default=None, help="path to a config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging on console")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("gui", help="launch the GUI")

    for mode in MODES:
        p = sub.add_parser(mode, help=f"run the {mode} mode headless")
        p.add_argument("--dry-run", action="store_true", help="never send input")
        p.add_argument("--no-join", action="store_true", help="scan and log, never click GO NOW")
        p.add_argument("--no-antiafk", action="store_true", help="skip the movement pair")
        p.add_argument("--interval", type=float, default=None, help="override hunt_interval_s")
        p.add_argument("--duration", type=float, default=None, help="stop after N seconds")
        p.add_argument("--once", action="store_true", help="one scan cycle, then exit")
        p.add_argument(
            "--game",
            choices=("double_up", "jamaa_derby", "gem_ball", "swoopy_eagle"),
            default=None,
            help="farm: which arcade game",
        )

    p = sub.add_parser("learn", help="record a Learn session while you play manually")
    p.add_argument("--target", choices=LEARN_TARGETS, required=True)
    p.add_argument("--name", default=None, help="optional session name suffix")
    p.add_argument("--video", action="store_true", help="also record continuous low-fps video")
    p.add_argument(
        "--duration", type=float, default=None, help="stop automatically after this many seconds"
    )

    sub.add_parser("ingest", help="import inbox/ PNG and MP4 files into Learn sessions")

    p = sub.add_parser("replay", help="run a mode offline against a recorded session")
    p.add_argument("session")
    p.add_argument("--mode", choices=("bestguess", "double_up", "parties", "watchdog"))
    p.add_argument(
        "--detector",
        choices=("nearest", "bestguess"),
        default="nearest",
        help="nearest-state self-consistency (default) or the real Best Guess detector",
    )
    p.add_argument(
        "--dense",
        action="store_true",
        help="replay the original inbox video at 2 fps against analysis/intervals.json",
    )

    p = sub.add_parser("snap", help="capture one frame of the AJC window to a PNG")
    p.add_argument("--out", default=None)
    p.add_argument("--backend", choices=("auto", "wgc", "mss"), default=None)
    p.add_argument("--benchmark", action="store_true", help="time WGC vs mss and report")

    p = sub.add_parser("report", help="analyse a session and render its Learn Report")
    p.add_argument("session", help="session folder name or path")
    p.add_argument(
        "--no-profile", action="store_true", help="do not write profiles/<mode>.proposed.yaml"
    )

    sub.add_parser("test-notify", help="send a test notification on every enabled channel")
    p = sub.add_parser("activity-test", help="run the idle-popup activity test (KICKOFF 7.3)")
    p.add_argument(
        "--phase", choices=("idle", "movement", "panel", "ingame"), action="append", default=None
    )
    p.add_argument("--minutes", type=float, default=None, help="override phase length")
    p.add_argument("--dry-run", action="store_true", help="never send input")
    p = sub.add_parser("input-test", help="send one cancelling movement pair and verify")
    p.add_argument("--method", choices=("scancode", "vk"), default=None)
    p.add_argument("--backend", choices=("auto", "wgc", "mss"), default=None)
    p.add_argument("--dry-run", action="store_true", help="capture only, send nothing")
    sub.add_parser("window", help="print the discovered AJC window geometry")
    p = sub.add_parser("_bench", help=argparse.SUPPRESS)  # one backend, JSON out; used by snap
    p.add_argument("--backend", choices=("wgc", "mss"), required=True)
    p.add_argument("--frames", type=int, required=True)
    return parser


def _not_yet(milestone: str) -> Command:
    def run(_: argparse.Namespace) -> int:
        print(f"not implemented yet (arrives in {milestone})")
        return EXIT_NOT_IMPLEMENTED

    return run


def _gui(_: argparse.Namespace) -> int:
    from jamcrammer.app.main import run_gui

    return run_gui()


def _lazy(module: str, func: str) -> Command:
    def run(args: argparse.Namespace) -> int:
        import importlib

        fn: Command = getattr(importlib.import_module(module), func)
        return fn(args)

    return run


COMMANDS: dict[str, Command] = {
    "gui": _gui,
    "hunt": _lazy("jamcrammer.commands.hunt_cmd", "run_hunt"),
    "farm": _lazy("jamcrammer.commands.farm_cmd", "run_farm"),
    "opportunist": _lazy("jamcrammer.commands.farm_cmd", "run_opportunist"),
    "learn": _lazy("jamcrammer.commands.learn_cmd", "run_learn"),
    "ingest": _lazy("jamcrammer.commands.learn_cmd", "run_ingest"),
    "replay": _lazy("jamcrammer.commands.learn_cmd", "run_replay"),
    "snap": _lazy("jamcrammer.commands.platform_cmds", "run_snap"),
    "report": _lazy("jamcrammer.commands.learn_cmd", "run_report"),
    "test-notify": _lazy("jamcrammer.commands.services_cmds", "run_test_notify"),
    "activity-test": _lazy("jamcrammer.commands.services_cmds", "run_activity_test"),
    "input-test": _lazy("jamcrammer.commands.platform_cmds", "run_input_test"),
    "window": _lazy("jamcrammer.commands.platform_cmds", "run_window"),
    "_bench": _lazy("jamcrammer.commands.platform_cmds", "run_bench_one"),
}


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    paths = Paths()
    paths.ensure_runtime_dirs()
    setup_logging(paths.logs, console_level=logging.DEBUG if args.verbose else logging.INFO)
    args.paths = paths
    args.cfg = load_config(paths, override=Path(args.config) if args.config else None)
    return COMMANDS[args.command](args)
