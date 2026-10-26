"""``test-notify`` and ``activity-test`` subcommands (M2)."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from jamcrammer.core.config import AppConfig, Level
from jamcrammer.core.events import EventBus
from jamcrammer.core.logging import attach_bus, get_logger
from jamcrammer.core.profiles import load_profile
from jamcrammer.core.safety import InputLease, KillSwitch
from jamcrammer.platform.capture import open_stage_backend
from jamcrammer.platform.input import InputController
from jamcrammer.platform.window import WindowTracker, bring_to_foreground, ensure_dpi_awareness
from jamcrammer.services.antiafk import AntiAfk, RoomDriftGuard
from jamcrammer.services.notify import Notifier
from jamcrammer.services.watchdog import Detection, PopupDetector, Watchdog

log = get_logger("cmd.services")

EXIT_NO_WINDOW = 3
EXIT_FAILED = 1
DEFAULT_PHASES = ("movement", "panel", "ingame")
STATUS_EVERY_S = 30.0
PANEL_CLOSE_SETTLE_S = 1.5


def run_test_notify(args: argparse.Namespace) -> int:
    cfg: AppConfig = args.cfg
    icon = args.paths.assets / "icons" / "app.png"
    notifier = Notifier(cfg.notify, icon=icon if icon.exists() else None)
    results = notifier.test_all()
    ok_all = True
    for r in results:
        state = "disabled" if not r.enabled else ("ok" if r.ok else f"FAIL {r.error}")
        print(f"  {r.channel:8s} {state}  ({r.ms:.0f} ms)")
        ok_all &= (not r.enabled) or r.ok
    if not cfg.notify.ntfy_topic:
        print("ntfy: set notify.ntfy_topic in config.yaml to enable phone pushes")
    return 0 if ok_all else EXIT_FAILED


def run_activity_test(args: argparse.Namespace) -> int:  # noqa: PLR0915 - CLI orchestration
    """KICKOFF §7.3: which inputs count as activity? Run each phase and log idle popups.

    Phases: ``idle`` (control: nothing), ``movement`` (cancelling pairs only), ``panel``
    (My Parties open/close only; needs the parties profile), ``ingame`` (William plays a
    mini-game manually; we just watch). Results: data/activity_test_<ts>.json.
    """
    cfg: AppConfig = args.cfg
    ensure_dpi_awareness()
    tracker = WindowTracker(cfg.window_title_match, cfg.window_process_name)
    if tracker.refresh() is None:
        print("AJC window not found")
        return EXIT_NO_WINDOW
    info = tracker.require()

    bus = EventBus()
    attach_bus(bus)
    notifier = Notifier(cfg.notify)
    notifier.attach(bus)
    shared = load_profile("shared", args.paths)
    lease = InputLease()
    kill = KillSwitch()
    inp = InputController(tracker.provider(), cfg.input, dry_run=args.dry_run, bus=bus)
    backend = open_stage_backend(cfg.capture_backend, tracker.provider())
    shots = args.paths.captures / "activity_test"
    detector = PopupDetector(shared, paths=args.paths)

    popups: list[dict[str, object]] = []
    current_phase = {"name": ""}

    def on_fatal(det: Detection) -> None:
        kill.trip(f"watchdog:{det.kind}")

    def on_pause(reason: str) -> None:
        log.warning("watchdog paused clicking: %s", reason)

    watchdog = Watchdog(
        cfg.watchdog,
        detector,
        inp,
        bus=bus,
        lease=lease,
        on_fatal=on_fatal,
        on_pause=on_pause,
        screenshot_dir=shots,
        unknown_dir=args.paths.unknown_popups,
        antiafk_active=lambda: current_phase["name"] == "movement",
    )
    bus.subscribe(
        "watchdog.popup_detected",
        lambda e: popups.append({"phase": current_phase["name"], "ts": e.ts, **e.payload}),
    )
    guard = RoomDriftGuard(
        cfg.drift,
        [
            shared.roi("hud_top_band"),
            shared.roi("hud_bottom_band"),
            shared.roi("animal_center_box"),
        ],
    )
    antiafk = AntiAfk(
        cfg.antiafk,
        inp,
        guard,
        bus=bus,
        ensure_focus=lambda: bring_to_foreground(info.hwnd, cfg.input.foreground_timeout_s),
    )

    phases = tuple(args.phase) if args.phase else DEFAULT_PHASES
    minutes = args.minutes if args.minutes is not None else cfg.activity_test.phase_minutes
    results: dict[str, object] = {
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        "phase_minutes": minutes,
        "dry_run": args.dry_run,
        "phases": {},
    }
    out = args.paths.data / f"activity_test_{time.strftime('%Y%m%d_%H%M%S')}.json"
    print(f"Activity test: phases={phases} {minutes} min each. F12 = abort. Results -> {out}")

    try:
        first = backend.grab()
        guard.set_baseline(first.image)
        watchdog.set_baseline(first.image)
        for phase in phases:
            if kill.tripped:
                break
            current_phase["name"] = phase
            seen_before = len(popups)
            print(f"\n== phase {phase}: {minutes} min ==")
            if phase == "ingame":
                print(
                    "Start a mini-game now (e.g. Double Up) and keep playing until this phase ends."
                )
            elif phase == "panel" and not shared.has("party_hat_point"):
                print("party_hat_point is not calibrated; skipping this phase")
                continue
            t_end = time.time() + minutes * 60.0
            next_action = time.time()
            next_status = time.time() + STATUS_EVERY_S
            period = 1.0 / cfg.watchdog.fps
            while time.time() < t_end and not kill.tripped:
                frame = backend.grab()
                watchdog.tick(frame)
                now = time.time()
                if now >= next_action and not watchdog.paused:
                    if phase == "movement":
                        antiafk.cycle(lambda: backend.grab().image)
                        next_action = now + cfg.activity_test.movement_interval_s
                    elif phase == "panel":
                        _panel_open_close(inp, shared, cfg)
                        next_action = now + cfg.activity_test.panel_interval_s
                    else:
                        next_action = t_end
                if now >= next_status:
                    remaining = int(t_end - now)
                    n_popups = len(popups) - seen_before
                    print(f"  {phase}: {remaining:4d}s left, popups so far={n_popups}")
                    next_status = now + STATUS_EVERY_S
                time.sleep(period)
            phase_popups = popups[seen_before:]
            results["phases"][phase] = {  # type: ignore[index]
                "popups": len(phase_popups),
                "first_popup_after_s": (
                    round(float(phase_popups[0]["ts"]) - (t_end - minutes * 60.0), 1)  # type: ignore[arg-type]
                    if phase_popups
                    else None
                ),
                "drift_events": antiafk.drift_events,
                "aborted": kill.tripped,
            }
            print(f"  -> {phase}: popups={len(phase_popups)}")
    finally:
        backend.close()
        results["popups"] = popups
        results["kill_reason"] = kill.reason
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        notifier.stop()
    print(json.dumps(results["phases"], indent=2))
    print(f"saved {out}")
    return 0


def _panel_open_close(inp: InputController, shared: object, cfg: AppConfig) -> None:
    """Open My Parties via the party hat, then close it. Close target comes from the parties
    profile (M3); until then we press Escape, which may or may not close it."""
    from jamcrammer.core.profiles import Profile

    assert isinstance(shared, Profile)
    px, py = shared.point("party_hat_point")
    inp.click(px, py)
    time.sleep(PANEL_CLOSE_SETTLE_S)
    try:
        parties = load_profile("parties")
        if parties.has("close_x_point"):
            cx, cy = parties.point("close_x_point")
            inp.click(cx, cy)
            return
    except Exception:
        pass
    inp.key_tap("escape", cfg.input.click_hold_ms)


def notify_level_for(level_name: str) -> Level:
    return Level(level_name)


__all__ = ["Path", "run_activity_test", "run_test_notify"]
