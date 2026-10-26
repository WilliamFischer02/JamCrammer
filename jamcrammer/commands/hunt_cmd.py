"""``hunt``: Best Guess Hunter Phases A/B from the CLI (M3)."""

from __future__ import annotations

import argparse
import sys
import time

from jamcrammer.core.config import AppConfig
from jamcrammer.core.events import Event, EventBus
from jamcrammer.core.logging import attach_bus, get_logger
from jamcrammer.core.profiles import load_profile
from jamcrammer.core.safety import Hotkeys, InputLease, KillSwitch, PauseFlag, PresenceDetector
from jamcrammer.core.store import Store
from jamcrammer.learn.recorder import Recorder
from jamcrammer.modes.bestguess.hunter import Hunter
from jamcrammer.modes.party_watcher import (
    BannerLibrary,
    PanelAnalyzer,
    PartyWatcher,
    rotation_report,
)
from jamcrammer.platform.capture import open_stage_backend
from jamcrammer.platform.input import InputController
from jamcrammer.platform.window import WindowTracker, bring_to_foreground, ensure_dpi_awareness
from jamcrammer.services.antiafk import AntiAfk, RoomDriftGuard
from jamcrammer.services.notify import Notifier
from jamcrammer.services.watchdog import PopupDetector, Watchdog

log = get_logger("cmd.hunt")

EXIT_NO_WINDOW = 3


def run_hunt(args: argparse.Namespace) -> int:  # noqa: PLR0915 - CLI orchestration
    cfg: AppConfig = args.cfg
    ensure_dpi_awareness()
    tracker = WindowTracker(cfg.window_title_match, cfg.window_process_name)
    if tracker.refresh() is None:
        print("AJC window not found")
        return EXIT_NO_WINDOW
    info = tracker.require()
    dry_run = bool(args.dry_run or cfg.dry_run)

    bus = EventBus()
    attach_bus(bus)
    notifier = Notifier(cfg.notify)
    notifier.attach(bus)
    store = Store(args.paths.db)

    def persist(event: Event) -> None:
        store.add_event(event)

    bus.subscribe("*", persist)
    shared = load_profile("shared", args.paths)
    parties = load_profile("parties", args.paths)
    lease = InputLease()
    kill = KillSwitch()
    pause = PauseFlag()
    hotkeys = Hotkeys(cfg.hotkeys, bus)
    bus.subscribe("hotkey.kill", lambda e: kill.trip("F12"))

    def toggle_pause(_: Event) -> None:
        pause.toggle()

    bus.subscribe("hotkey.pause", toggle_pause)
    hotkeys.start()

    inp = InputController(tracker.provider(), cfg.input, dry_run=dry_run, bus=bus)
    backend = open_stage_backend(cfg.capture_backend, tracker.provider())
    grab_image = lambda: backend.grab().image  # noqa: E731

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
    watchdog = Watchdog(
        cfg.watchdog,
        PopupDetector(shared, paths=args.paths),
        inp,
        bus=bus,
        lease=lease,
        on_fatal=lambda d: kill.trip(f"watchdog:{d.kind}"),
        screenshot_dir=args.paths.captures / "watchdog",
        unknown_dir=args.paths.unknown_popups,
        antiafk_active=lambda: antiafk.enabled,
    )
    analyzer = PanelAnalyzer(
        parties, BannerLibrary(args.paths.assets / "banners"), paths=args.paths
    )
    watcher = PartyWatcher(
        analyzer, cfg.hunt, inp, grab_image, store=store, bus=bus, unknown_dir=args.paths.unknown
    )

    recorder_holder: dict[str, Recorder] = {}

    def on_joined(_banner):  # type: ignore[no-untyped-def]
        """Phase C is not built yet: start a Learn-only recording of the arena (KICKOFF §8.2)."""
        session_dir = args.paths.sessions / f"{time.strftime('%Y%m%d_%H%M%S')}_bestguess_autojoin"
        rec = Recorder(
            session_dir=session_dir,
            target="bestguess",
            cfg=cfg.learn,
            grab=backend.grab,
            window=tracker.provider(),
            bus=bus,
            capture_input=True,
            ocr=cfg.learn.ocr_keyframes,
            extra_meta={"source": "autojoin"},
        )
        rec.start()
        recorder_holder["rec"] = rec
        print(f"\nJoined Best Guess. Learn-only recording -> {session_dir}  (F12 to stop)")

    hunter = Hunter(
        cfg,
        watcher,
        backend.grab,
        watchdog=watchdog,
        antiafk=antiafk if not args.no_antiafk else None,
        guard=guard,
        bus=bus,
        no_join=bool(args.no_join),
        presence=PresenceDetector(
            lambda: inp.stats.last_injected_ts, cfg.presence_slack_s, cfg.resume_after_idle_s
        ),
        on_joined=on_joined,
    )
    if args.interval:
        cfg.hunt_interval_s = float(args.interval)
    started = time.time()
    deadline = started + args.duration if args.duration else None

    def should_stop() -> bool:
        if kill.tripped:
            return True
        return bool(deadline is not None and time.time() >= deadline and not hunter.stats.joined)

    last_status = {"s": ""}

    def status(s: str) -> None:
        if pause.paused:
            s = "PAUSED (F11) · " + s
        if s != last_status["s"]:
            sys.stdout.write(f"\r  {s:<70}")
            sys.stdout.flush()
            last_status["s"] = s

    mode = (
        "DRY-RUN (no input)"
        if dry_run
        else ("scan only, never join" if args.no_join else "auto-join on")
    )
    print(f"Hunting Best Guess every {cfg.hunt_interval_s:.0f}s · {mode} · F11 pause · F12 stop")
    try:
        first = backend.grab()
        guard.set_baseline(first.image)
        watchdog.set_baseline(first.image)
        if args.once:
            res = hunter.cycle()
            target = res.target.label if res.target else None
            print(f"\nbanners: {res.labels} target={target} errors={res.errors}")
        else:
            while not should_stop():
                if pause.paused:
                    status(hunter.state)
                    time.sleep(0.5)
                    continue
                hunter.run(lambda: should_stop() or pause.paused, status)
                if hunter.stats.joined:
                    while not kill.tripped:  # keep recording until F12
                        time.sleep(0.5)
                    break
    except KeyboardInterrupt:
        pass
    finally:
        rec = recorder_holder.get("rec")
        if rec is not None:
            rec.stop()
        hotkeys.stop()
        backend.close()
        notifier.stop()
        print()
        print("Rotation log (this run):")
        for row in rotation_report(store, since_ts=started):
            t0 = time.strftime("%H:%M", time.localtime(row["first_seen"]))
            t1 = time.strftime("%H:%M", time.localtime(row["last_seen"]))
            print(
                f"  slot {row['slot']}  {row['label']:<28} {t0}-{t1}  ({row['duration_min']} min)"
            )
        s = hunter.stats
        print(
            f"cycles={s.cycles} ok={s.scans_ok} failed={s.scans_failed} "
            f"target_seen={s.target_seen} joined={s.joined} kill={kill.reason!r}"
        )
        store.close()
    return 0
