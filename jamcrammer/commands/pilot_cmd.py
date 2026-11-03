"""``pilot`` subcommands: Claude at the controls (docs/PILOT.md, docs/MAPPING.md).

``start`` / ``stop`` run the Learn recording in a separate ``learn --stop-file`` process.
``serve`` keeps one executor (window, capture, world map) alive and answers command files;
every other command goes to that server when it is running, else runs in this process."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Any

import cv2
import numpy as np

from jamcrammer.cli import build_parser
from jamcrammer.core.config import AppConfig, load_config
from jamcrammer.core.events import EventBus
from jamcrammer.core.logging import attach_bus, get_logger
from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import load_profile
from jamcrammer.core.safety import last_user_input_time
from jamcrammer.modes.adventures import objects, sight
from jamcrammer.modes.adventures.inventory import DigitBank
from jamcrammer.modes.adventures.objects import CourageText
from jamcrammer.modes.adventures.world import world_dir
from jamcrammer.pilot import (
    Executor,
    PilotRefusedError,
    PilotSession,
    dispatch,
    load_level,
    load_world,
    pointer_path,
    send,
    serve,
    server_alive,
    server_paths,
)
from jamcrammer.platform.capture import open_stage_backend
from jamcrammer.platform.input import InputController
from jamcrammer.platform.window import WindowTracker, bring_to_foreground, ensure_dpi_awareness
from jamcrammer.services.watchdog import PopupDetector, Watchdog
from jamcrammer.vision.ocr import ocr_image

log = get_logger("cmd.pilot")

EXIT_NO_WINDOW = 3
EXIT_REFUSED = 5
BANNER_UPSCALE = 2.0
FOREVER_S = 1e9  # no user input since our own
LOCAL_COMMANDS = ("start", "stop", "serve", "quit", "status", "rebuild")


def _user_input_age(inp: InputController, slack_s: float) -> float:
    """Seconds since William's own input. The OS counts injected input too, so input at or
    just after our last injection is ours (the same rule as the presence detector)."""
    last_user = last_user_input_time()
    if last_user <= inp.stats.last_injected_ts + slack_s:
        return FOREVER_S
    return time.time() - last_user


def _executor(cfg: AppConfig, paths: Paths) -> tuple[Executor, Any]:
    """The real pilot: the AJC window, the stage capture, the input controller, the
    adventure profile, the level and the world map. Returns it with the capture backend
    (to close)."""
    ensure_dpi_awareness()
    tracker = WindowTracker(cfg.window_title_match, cfg.window_process_name)
    if tracker.refresh() is None:
        msg = "AJC window not found"
        raise PilotRefusedError(msg)
    bus = EventBus()
    attach_bus(bus)
    backend = open_stage_backend(cfg.capture_backend, tracker.provider())
    prof = load_profile("adventure", paths)
    lvl = load_level(cfg, paths.profiles)
    world = load_world(cfg, prof, lvl, paths.profiles)
    inp = InputController(tracker.provider(), cfg.input, bus=bus)
    raw = cv2.imread(str(prof.template_path("dialog_x_template", paths)))
    dialog_x = np.asarray(raw, dtype=np.uint8) if raw is not None else None
    sheet = cv2.imread(str(prof.template_path("count_digits_template", paths)))
    digits = DigitBank(np.asarray(sheet, np.uint8), prof) if sheet is not None else None
    tag_raw = (
        cv2.imread(str(prof.template_path("nametag_template", paths)))
        if prof.has("nametag_template")
        else None
    )
    nametag = np.asarray(tag_raw, dtype=np.uint8) if tag_raw is not None else None
    courage_raw = cv2.imread(str(prof.template_path("courage_template", paths)))
    courage = (
        CourageText(np.asarray(courage_raw, dtype=np.uint8), prof)
        if courage_raw is not None
        else None
    )

    watchdog = Watchdog(
        cfg.watchdog,
        PopupDetector(load_profile("shared", paths), paths=paths),
        inp,
        bus=bus,
        screenshot_dir=paths.captures / "watchdog",
        unknown_dir=paths.unknown_popups,
    )

    def foreground() -> None:
        info = tracker.require()
        bring_to_foreground(info.hwnd, cfg.input.foreground_timeout_s)

    def window() -> dict[str, Any]:
        info = tracker.info
        return info.as_dict() if info else {}

    ex = Executor(
        cfg=cfg,
        prof=prof,
        lvl=lvl,
        world=world,
        grab=lambda: np.asarray(backend.grab().image, dtype=np.uint8),
        inp=inp,
        ocr=ocr_image,
        banner_ocr=lambda fr: sight.banner_text(
            fr, prof, lambda im: ocr_image(im, upscale=BANNER_UPSCALE).text
        ),
        user_input_age=lambda: _user_input_age(inp, cfg.presence_slack_s),
        foreground=foreground,
        window=window,
        digits=digits,
        dialog_x=dialog_x,
        nametag=nametag,
        courage=courage,
        watchdog=watchdog,
        out_dir=paths.captures / "pilot",
        world_dir=world_dir(paths.profiles, cfg.adventures.route.target),
        session=PilotSession.load(pointer_path(paths.data)),
        position_path=paths.data / "pilot" / "position.json",
    )
    return ex, backend


def _start(args: argparse.Namespace) -> int:
    cfg: AppConfig = args.cfg
    pointer = pointer_path(args.paths.data)
    current = PilotSession.load(pointer)
    if current is not None and not current.stopped:
        print(json.dumps({"running": str(current.session_dir), "goal": current.goal}))
        return 0
    stamp = time.strftime("%Y%m%d_%H%M%S")
    name = f"{stamp}_{args.target}_pilot"
    session_dir = args.paths.sessions / name
    stop_file = args.paths.data / "pilot" / f"{name}.stop"
    stop_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "jamcrammer",
        "learn",
        "--target",
        args.target,
        "--session-dir",
        name,
        "--stop-file",
        str(stop_file),
    ]
    creation = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
        subprocess, "DETACHED_PROCESS", 0
    )
    log_path = args.paths.logs / "pilot_recorder.log"
    with log_path.open("a", encoding="utf-8") as fh:
        subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, creationflags=creation)
    budget = args.budget if args.budget is not None else cfg.pilot.budget_min
    session = PilotSession(
        session_dir, stop_file, args.target, time.time(), budget, " ".join(args.goal or [])
    )
    session.save(pointer)
    session.log(
        {
            "cmd": "start",
            "target": args.target,
            "enabled": cfg.pilot.enabled,
            "budget_min": budget,
            "goal": session.goal,
        }
    )
    print(
        json.dumps(
            {
                "session_dir": str(session_dir),
                "stop_file": str(stop_file),
                "recorder_log": str(log_path),
                "budget_min": budget,
                "goal": session.goal,
            }
        )
    )
    return 0


def _stop(args: argparse.Namespace) -> int:
    pointer = pointer_path(args.paths.data)
    current = PilotSession.load(pointer)
    if current is None:
        print("no pilot session")
        return 0
    current.stop_file.write_text("stop", encoding="utf-8")
    current.log({"cmd": "stop"})
    print(json.dumps({"stopped": str(current.session_dir)}))
    return 0


def _status(args: argparse.Namespace) -> int:
    pointer = pointer_path(args.paths.data)
    current = PilotSession.load(pointer)
    doc: dict[str, Any] = {
        "server": server_alive(args.paths.data),
        "enabled": args.cfg.pilot.enabled,
        "session": None,
    }
    if current is not None:
        doc["session"] = {
            "dir": str(current.session_dir),
            "stopped": current.stopped,
            "minutes": round((time.time() - current.started_ts) / 60, 1),
            "budget_min": current.budget_min,
            "goal": current.goal,
        }
    print(json.dumps(doc, indent=1))
    return 0


def _quit(args: argparse.Namespace) -> int:
    p = server_paths(args.paths.data)
    if not server_alive(args.paths.data):
        print(json.dumps({"server": "not running"}))
        return 0
    p["stop"].write_text("stop", encoding="utf-8")
    print(json.dumps({"server": "stopping"}))
    return 0


def _serve(args: argparse.Namespace) -> int:
    """One process, many commands: the window, the capture and the world stay open."""
    paths: Paths = args.paths
    if server_alive(paths.data):
        print(json.dumps({"refused": "a pilot server is already running"}))
        return EXIT_REFUSED
    try:
        ex, backend = _executor(args.cfg, paths)
    except PilotRefusedError as exc:
        print(json.dumps({"refused": str(exc)}))
        return EXIT_NO_WINDOW
    parser = build_parser()

    def run(argv: list[str]) -> dict[str, Any]:
        ns = parser.parse_args(["pilot", *argv])
        if ns.pilot_command in LOCAL_COMMANDS:
            msg = f"`pilot {ns.pilot_command}` runs in its own process, not through the server"
            raise PilotRefusedError(msg)
        # William's switch and the session are re-read for every command
        ex.cfg = load_config(paths, override=None)
        ex.session = PilotSession.load(pointer_path(paths.data))
        if ex.session is not None and ex.session.stopped:
            ex.session = None
        t0 = time.monotonic()
        out = dispatch(ex, ns)
        out["took_s"] = round(time.monotonic() - t0, 2)
        return out

    print(json.dumps({"serving": str(server_paths(paths.data)["cmd"]), "pid": os.getpid()}))
    sys.stdout.flush()
    try:
        n = serve(paths.data, run, poll_s=args.cfg.pilot.serve_poll_s)
    except KeyboardInterrupt:
        n = -1
    finally:
        ex.save()
        ex.flush()
        backend.close()
    print(json.dumps({"served": n}))
    return 0


def _rebuild(args: argparse.Namespace) -> int:
    """The offline map rebuild (``world_build.py``): every kept frame of the sessions,
    registered pairwise, solved as a pose graph, composited by the median of the best-lit
    samples; the tiles and the mosaic under the world folder are replaced. Runs in its
    own process (minutes); `pilot reload` afterwards tells a running server."""
    from jamcrammer.modes.adventures.world_build import Dialog, rebuild

    cfg: AppConfig = args.cfg
    paths: Paths = args.paths
    target = cfg.adventures.route.target
    sessions = (
        [paths.sessions / s for s in args.sessions]
        if args.sessions
        else sorted(p for p in paths.sessions.glob(f"*_{target}_pilot") if (p / "obs").exists())
    )
    prof = load_profile("adventure", paths)
    x_raw = cv2.imread(str(prof.template_path("dialog_x_template", paths)))
    dialog = (
        Dialog(
            np.asarray(x_raw, dtype=np.uint8),
            prof.number("dialog_x_min"),
            prof.roi("dialog_x_search_roi") if prof.has("dialog_x_search_roi") else None,
        )
        if x_raw is not None
        else None
    )
    out = rebuild(
        world_dir(paths.profiles, target),
        [s / "obs" for s in sessions],
        cfg.adventures.world,
        area=args.area or cfg.pilot.area,
        hud_rois=[*prof.items("hud_rois")],
        dialog=dialog,
        register_pairs=not args.no_register,
        reuse_origins=args.reuse,
        detect=None
        if args.no_things
        else lambda img: (
            objects.crystals(img, prof, hud=False) + objects.fungi(img, prof, hud=False)
        ),
        fungus_px=(
            prof.number("scan_fungus_base_px") if prof.has("scan_fungus_base_px") else 0.0,
            prof.number("scan_glow_px") if prof.has("scan_glow_px") else 0.0,
        ),
        log=lambda m: print(m, flush=True),
    )
    print(json.dumps(out, indent=1))
    return 0


def _pilot_argv(args: argparse.Namespace) -> list[str]:
    """The pilot part of this process's argv, for the server (sys.argv holds it)."""
    argv = sys.argv[1:]
    if "pilot" in argv:
        return argv[argv.index("pilot") + 1 :]
    return [args.pilot_command]


def run_pilot(args: argparse.Namespace) -> int:
    sub = args.pilot_command
    local = {
        "start": _start,
        "stop": _stop,
        "serve": _serve,
        "quit": _quit,
        "status": _status,
        "rebuild": _rebuild,
    }
    if sub in local:
        return local[sub](args)
    cfg: AppConfig = args.cfg
    if server_alive(args.paths.data):
        out = send(args.paths.data, _pilot_argv(args), cfg.pilot.serve_timeout_s)
        if out is not None:
            print(json.dumps(out, indent=1))
            return EXIT_REFUSED if "refused" in out else 0
    try:
        ex, backend = _executor(cfg, args.paths)
    except PilotRefusedError as exc:
        print(json.dumps({"refused": str(exc)}))
        return EXIT_NO_WINDOW
    try:
        out = dispatch(ex, args)
    except PilotRefusedError as exc:
        print(json.dumps({"refused": str(exc)}))
        return EXIT_REFUSED
    finally:
        try:
            ex.save()
            ex.flush()
        finally:
            backend.close()
    print(json.dumps(out, indent=1))
    return 0
