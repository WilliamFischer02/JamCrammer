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
from typing import Any

from jamcrammer import __version__
from jamcrammer.core.config import load_config
from jamcrammer.core.logging import setup_logging
from jamcrammer.core.paths import Paths

LEARN_TARGETS = (
    "bestguess",
    "double_up",
    "jamaa_derby",
    "gem_ball",
    "parties",
    "mystery_below",
    "forgotten_desert",
    "custom",
)
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
    p.add_argument("--stop-file", default=None, help="stop when this file appears (pilot)")
    p.add_argument(
        "--duration", type=float, default=None, help="stop automatically after this many seconds"
    )

    sub.add_parser("ingest", help="import inbox/ PNG and MP4 files into Learn sessions")

    p = sub.add_parser("replay", help="run a mode offline against a recorded session")
    p.add_argument("session")
    p.add_argument("--mode", choices=("bestguess", "double_up", "parties", "watchdog"))
    p.add_argument(
        "--detector",
        choices=("nearest", "bestguess", "derby"),
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
    p = sub.add_parser("seed-facts", help="load a True/False fact list into the Best Guess bank")
    p.add_argument(
        "file", nargs="?", default=None, help="text file; default assets/bestguess/facts_wiki.txt"
    )
    p.add_argument("--source", default="wiki")
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
    _pilot_parser(sub)
    p = sub.add_parser("_bench", help=argparse.SUPPRESS)  # one backend, JSON out; used by snap
    p.add_argument("--backend", choices=("wgc", "mss"), required=True)
    p.add_argument("--frames", type=int, required=True)
    return parser


def _pilot_parser(sub: Any) -> None:
    """``pilot``: Claude at the controls, one command per call (docs/PILOT.md, MAPPING.md)."""
    p = sub.add_parser("pilot", help="Claude at the controls, one command per call")
    ps = p.add_subparsers(dest="pilot_command", required=True)
    s = ps.add_parser("start", help="start recording a pilot session (time-boxed)")
    s.add_argument("--target", default="mystery_below")
    s.add_argument("--budget", type=float, default=None, help="minutes of input allowed")
    s.add_argument("--goal", nargs="*", default=None, help="what this session is for")
    ps.add_parser("stop", help="end the pilot session's recording")
    ps.add_parser("status", help="the session, the server and the switch")
    ps.add_parser("serve", help="one process that answers pilot commands quickly")
    ps.add_parser("quit", help="stop the pilot server")
    ps.add_parser("look", help="one frame: detections, OCR, position, a half-size PNG to read")
    ps.add_parser("where", help="the fused position and what the map knows around it")
    ps.add_parser("scan", help="put what the detectors see on the map")
    ps.add_parser("save", help="write the world map now")
    k = ps.add_parser("key", help="hold a key (arrows are tracked on the map)")
    k.add_argument("key")
    k.add_argument("seconds", type=float)
    pr = ps.add_parser("probe", help="walk a direction until something stops the animal")
    pr.add_argument("direction", help="up down left right ul ur dl dr")
    pr.add_argument("--max", type=float, default=None, help="seconds at most")
    pr.add_argument("--note", nargs="*", default=None, help="what stopped it, if known")
    sv = ps.add_parser("survey", help="probe every direction from here and come back")
    sv.add_argument("--max", type=float, default=None, help="seconds per direction at most")
    sv.add_argument("--dirs", type=int, choices=(4, 8), default=8)
    c = ps.add_parser("click", help="click a stage point inside the client")
    c.add_argument("x", type=int)
    c.add_argument("y", type=int)
    d = ps.add_parser("drag", help="drag between two stage points")
    for n in ("x0", "y0", "x1", "y1"):
        d.add_argument(n, type=int)
    _pilot_map_parser(ps)


def _pilot_map_parser(ps: Any) -> None:
    """The pilot's walking and mapping commands."""
    g = ps.add_parser("goto", help="walk to a minimap point (or a world point with --world)")
    g.add_argument("x", type=float)
    g.add_argument("y", type=float)
    g.add_argument("--timeout", type=float, default=30.0)
    g.add_argument("--tol", type=float, default=None, help="arrived within this (minimap px)")
    g.add_argument("--world", action="store_true", help="x y are world px, not minimap px")
    m = ps.add_parser("mark", help="put a thing on the map at a screen point (default: here)")
    m.add_argument("kind")
    m.add_argument("x", type=float, nargs="?", default=None)
    m.add_argument("y", type=float, nargs="?", default=None)
    m.add_argument("--status", default=None, help="unlit | lit | muddy | broken | ...")
    m.add_argument("--note", nargs="*", default=None)
    m.add_argument("--world", action="store_true", help="x y are world px")
    f = ps.add_parser("forget", help="a thing on the map is not there")
    f.add_argument("id", type=int)
    u = ps.add_parser("unblock", help="no wall here after all (world px; default: here)")
    u.add_argument("x", type=float, nargs="?", default=None)
    u.add_argument("y", type=float, nargs="?", default=None)
    mp = ps.add_parser("map", help="render the world map to a PNG")
    mp.add_argument("--area", default=None)
    mp.add_argument("--crop", type=int, nargs=4, default=None, metavar=("X", "Y", "W", "H"))
    mp.add_argument("--zoom", type=float, default=0.1, help="image px per world px")
    mp.add_argument("--no-labels", action="store_true")
    n = ps.add_parser("note", help="write a note into the session log and the map")
    n.add_argument("text", nargs="+")
    cr = ps.add_parser("crop", help="save a template crop of the live frame")
    for name in ("x", "y", "w", "h"):
        cr.add_argument(name, type=int)
    cr.add_argument("name")


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
    "seed-facts": _lazy("jamcrammer.commands.services_cmds", "run_seed_facts"),
    "activity-test": _lazy("jamcrammer.commands.services_cmds", "run_activity_test"),
    "input-test": _lazy("jamcrammer.commands.platform_cmds", "run_input_test"),
    "window": _lazy("jamcrammer.commands.platform_cmds", "run_window"),
    "_bench": _lazy("jamcrammer.commands.platform_cmds", "run_bench_one"),
    "pilot": _lazy("jamcrammer.commands.pilot_cmd", "run_pilot"),
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
