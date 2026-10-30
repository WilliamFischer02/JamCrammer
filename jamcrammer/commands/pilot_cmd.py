"""``pilot`` subcommands: Claude at the controls, one short process per command
(docs/PILOT.md). The recording runs in a separate ``learn --stop-file`` process between
``pilot start`` and ``pilot stop``."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

import cv2
import numpy as np

from jamcrammer.core.config import AppConfig
from jamcrammer.core.events import EventBus
from jamcrammer.core.logging import attach_bus, get_logger
from jamcrammer.core.profiles import load_profile
from jamcrammer.core.safety import last_user_input_time
from jamcrammer.modes.adventures import sight
from jamcrammer.modes.adventures.inventory import DigitBank
from jamcrammer.modes.adventures.nav import Floor, Navigator
from jamcrammer.pilot import (
    PilotRefusedError,
    PilotSession,
    check_enabled,
    check_key,
    check_point,
    check_yield,
    contact,
    load_level,
    pointer_path,
    summarize,
)
from jamcrammer.platform.capture import open_stage_backend
from jamcrammer.platform.input import InputController
from jamcrammer.platform.window import WindowTracker, bring_to_foreground, ensure_dpi_awareness
from jamcrammer.vision.ocr import ocr_image

log = get_logger("cmd.pilot")

EXIT_NO_WINDOW = 3
EXIT_REFUSED = 5
BANNER_UPSCALE = 2.0
SETTLE_S = 0.3  # after an act, before the second look
SCENE_SCALE = 0.25
SCENE_SIGMA = 6.0


class _Pilot:
    """One command's worth of eyes and hands."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.cfg: AppConfig = args.cfg
        self.paths = args.paths
        ensure_dpi_awareness()
        self.tracker = WindowTracker(self.cfg.window_title_match, self.cfg.window_process_name)
        if self.tracker.refresh() is None:
            msg = "AJC window not found"
            raise PilotRefusedError(msg)
        self.bus = EventBus()
        attach_bus(self.bus)
        self.backend = open_stage_backend(self.cfg.capture_backend, self.tracker.provider())
        self.prof = load_profile("adventure", self.paths)
        self.lvl = load_level(self.cfg, self.paths.profiles)
        self.input = InputController(self.tracker.provider(), self.cfg.input, bus=self.bus)
        self.session = PilotSession.load(pointer_path(self.paths.data))
        self.out_dir = self.paths.captures / "pilot"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        raw = cv2.imread(str(self.prof.template_path("dialog_x_template", self.paths)))
        self.dialog_x = np.asarray(raw, dtype=np.uint8) if raw is not None else None
        sheet = cv2.imread(str(self.prof.template_path("count_digits_template", self.paths)))
        self.digits = (
            DigitBank(np.asarray(sheet, np.uint8), self.prof) if sheet is not None else None
        )

    def close(self) -> None:
        self.backend.close()

    # --- eyes ----------------------------------------------------------------------------

    def grab(self) -> np.ndarray:
        return np.asarray(self.backend.grab().image, dtype=np.uint8)

    def look(self, tag: str = "look", frame: np.ndarray | None = None) -> dict[str, Any]:
        frame = self.grab() if frame is None else frame
        stamp = time.strftime("%Y%m%d_%H%M%S")
        frame_path = self.out_dir / f"{stamp}_{tag}.png"
        contact_path = self.out_dir / f"{stamp}_{tag}_half.png"
        cv2.imwrite(str(frame_path), frame)
        cv2.imwrite(str(contact_path), contact(frame))
        res = ocr_image(frame)
        banner = sight.banner_text(
            frame, self.prof, lambda im: ocr_image(im, upscale=BANNER_UPSCALE).text
        )
        info = self.tracker.info
        seen = summarize(
            frame,
            self.prof,
            self.lvl,
            res,
            banner,
            digits=self.digits,
            dialog_x=self.dialog_x,
            window=info.as_dict() if info else {},
            frame_path=str(frame_path),
            contact_path=str(contact_path),
        )
        doc = seen.as_dict()
        if self.session is not None:
            self.session.log({"cmd": tag, "seen": {k: v for k, v in doc.items() if k != "ocr"}})
        return doc

    # --- hands ---------------------------------------------------------------------------

    def _arm(self) -> None:
        check_enabled(self.cfg.pilot)
        check_yield(self.cfg.pilot, time.time() - last_user_input_time())
        info = self.tracker.require()
        bring_to_foreground(info.hwnd, self.cfg.input.foreground_timeout_s)

    def _after(self, cmd: dict[str, Any], before: np.ndarray) -> dict[str, Any]:
        time.sleep(SETTLE_S)
        after = self.grab()
        dot0, dot1 = sight.minimap_dot(before, self.prof), sight.minimap_dot(after, self.prof)
        g0 = sight.masked_gray(before, self.prof, SCENE_SCALE, SCENE_SIGMA)
        g1 = sight.masked_gray(after, self.prof, SCENE_SCALE, SCENE_SIGMA)
        dx, dy, resp = sight.scene_shift(g0, g1, SCENE_SCALE)
        out = {
            **cmd,
            "dot_before": [round(v, 1) for v in dot0] if dot0 else None,
            "dot_after": [round(v, 1) for v in dot1] if dot1 else None,
            "scene_shift": [round(dx), round(dy), round(resp, 3)],
            "seen": self.look("after", after),
        }
        if self.session is not None:
            self.session.log({k: v for k, v in out.items() if k != "seen"})
        return out

    def key(self, name: str, hold_s: float) -> dict[str, Any]:
        check_key(self.cfg.pilot, name, hold_s)
        self._arm()
        before = self.grab()
        self.input.key_down(name)
        time.sleep(hold_s)
        self.input.key_up(name)
        return self._after({"cmd": "key", "key": name, "hold_s": hold_s}, before)

    def click(self, x: int, y: int) -> dict[str, Any]:
        check_point(self.cfg.pilot, x, y)
        self._arm()
        before = self.grab()
        ok = self.input.click(x, y)
        return self._after({"cmd": "click", "x": x, "y": y, "ok": ok}, before)

    def drag(self, x0: int, y0: int, x1: int, y1: int) -> dict[str, Any]:
        check_point(self.cfg.pilot, x0, y0)
        check_point(self.cfg.pilot, x1, y1)
        self._arm()
        before = self.grab()
        ok = self.input.drag(x0, y0, x1, y1)
        return self._after({"cmd": "drag", "from": [x0, y0], "to": [x1, y1], "ok": ok}, before)

    def goto(self, mx: float, my: float, timeout_s: float) -> dict[str, Any]:
        """Walk to a minimap point by the level's floor (all floor when there is none)."""
        check_enabled(self.cfg.pilot)
        self._arm()
        area = self.cfg.pilot.area
        roi = self.prof.roi("minimap_roi")
        mask = (
            self.lvl.floors[area]
            if self.lvl is not None and area in self.lvl.floors
            else np.ones((roi[3], roi[2]), np.bool_)
        )
        before = self.grab()
        nav = Navigator(
            Floor(mask, (roi[0], roi[1])),
            self.prof,
            self.cfg.adventures.level,
            key_down=self.input.key_down,
            key_up=self.input.key_up,
            grab=self.grab,
            clock=time.monotonic,
            sleep=time.sleep,
        )
        deadline = time.time() + min(timeout_s, self.cfg.pilot.max_goto_s)

        def stop() -> bool:
            return time.time() > deadline or (time.time() - last_user_input_time()) < 0.5

        res = nav.go_to((mx, my), stop=stop, timeout=min(timeout_s, self.cfg.pilot.max_goto_s))
        cmd = {
            "cmd": "goto",
            "target": [mx, my],
            "ok": res.ok,
            "reason": res.reason,
            "dist": round(res.dist, 1),
            "blocks": res.blocks,
            "trace": nav.trace[-20:],
        }
        return self._after(cmd, before)

    # --- the record ------------------------------------------------------------------------

    def note(self, text: str) -> dict[str, Any]:
        if self.session is None:
            msg = "no pilot session: run `jamcrammer pilot start` first"
            raise PilotRefusedError(msg)
        self.session.log({"cmd": "note", "text": text})
        return {"cmd": "note", "text": text, "log": str(self.session.log_path)}

    def crop(self, x: int, y: int, w: int, h: int, name: str) -> dict[str, Any]:
        frame = self.grab()
        patch = frame[max(0, y) : y + h, max(0, x) : x + w]
        folder = self.out_dir / "templates"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{name}.png"
        cv2.imwrite(str(path), patch)
        if self.session is not None:
            self.session.log({"cmd": "crop", "roi": [x, y, w, h], "name": name, "path": str(path)})
        return {
            "cmd": "crop",
            "path": str(path),
            "size": [int(patch.shape[1]), int(patch.shape[0])],
        }


def _start(args: argparse.Namespace) -> int:
    cfg: AppConfig = args.cfg
    pointer = pointer_path(args.paths.data)
    current = PilotSession.load(pointer)
    if current is not None and not current.stop_file.exists():
        print(f"a pilot session is already running: {current.session_dir}")
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
        "--name",
        "pilot",
        "--stop-file",
        str(stop_file),
    ]
    creation = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
        subprocess, "DETACHED_PROCESS", 0
    )
    log_path = args.paths.logs / "pilot_recorder.log"
    with log_path.open("a", encoding="utf-8") as fh:
        subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, creationflags=creation)
    session = PilotSession(session_dir, stop_file, args.target, time.time())
    session.save(pointer)
    session.log({"cmd": "start", "target": args.target, "enabled": cfg.pilot.enabled})
    print(
        json.dumps(
            {
                "session_dir": str(session_dir),
                "stop_file": str(stop_file),
                "recorder_log": str(log_path),
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


def run_pilot(args: argparse.Namespace) -> int:
    sub = args.pilot_command
    if sub == "start":
        return _start(args)
    if sub == "stop":
        return _stop(args)
    try:
        p = _Pilot(args)
    except PilotRefusedError as exc:
        print(json.dumps({"refused": str(exc)}))
        return EXIT_NO_WINDOW
    actions: dict[str, Callable[[], dict[str, Any]]] = {
        "look": p.look,
        "key": lambda: p.key(args.key, args.seconds),
        "click": lambda: p.click(args.x, args.y),
        "drag": lambda: p.drag(args.x0, args.y0, args.x1, args.y1),
        "goto": lambda: p.goto(args.mx, args.my, args.timeout),
        "note": lambda: p.note(" ".join(args.text)),
        "crop": lambda: p.crop(args.x, args.y, args.w, args.h, args.name),
    }
    try:
        out = actions[sub]()
    except PilotRefusedError as exc:
        print(json.dumps({"refused": str(exc)}))
        return EXIT_REFUSED
    finally:
        p.close()
    print(json.dumps(out, indent=1))
    return 0
