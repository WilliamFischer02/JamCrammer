"""``farm``: Double Up farmer from the CLI (M7). Uses the engine so watchdog, presence, hotkeys
and the ledger behave exactly as in the GUI."""

from __future__ import annotations

import argparse
import sys
import time

from jamcrammer.core.config import AppConfig
from jamcrammer.core.gate import gate_status

EXIT_NO_WINDOW = 3
EXIT_LOCKED = 5
STATUS_TICK_S = 0.5


def run_farm(args: argparse.Namespace) -> int:
    from jamcrammer.app.engine import Engine

    cfg: AppConfig = args.cfg
    if args.dry_run:
        cfg.dry_run = True
    gate = gate_status("double_up", args.paths)
    print(f"Double Up gate: {gate.summary()}")
    if not gate.unlocked:
        print(
            "Calibrate the double_up profile (Learn → Review wizard, or Calibration) and replay ≥95% first."
        )
        return EXIT_LOCKED
    engine = Engine(cfg, args.paths)
    if engine.refresh_window() is None:
        print("AJC window not found")
        return EXIT_NO_WINDOW
    engine.selected_mode = "farm"
    if not engine.start_mode("farm"):
        print(engine.state.last_error or "could not start")
        return 1
    deadline = time.time() + args.duration if args.duration else None
    print("Farming Double Up · F11 pause · F12 stop")
    try:
        while engine.state.mode == "farm":
            if deadline is not None and time.time() >= deadline:
                engine.stop_mode()
                break
            sys.stdout.write(f"\r  {engine.state.pill_text:<70}")
            sys.stdout.flush()
            time.sleep(STATUS_TICK_S)
    except KeyboardInterrupt:
        engine.stop_mode()
    finally:
        print()
        print(f"stats: {engine.state.stats.get('farm', {})}  ledger: {engine.ledger.stats.by_mode}")
        engine.shutdown()
    return 0
