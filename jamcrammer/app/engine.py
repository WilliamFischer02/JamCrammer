"""Engine: everything the GUI drives, with no Qt dependency.

Owns the bus, store, notifier, capture backend, input controller, watchdog, anti-AFK, modes
and the Learn recorder. All work runs on worker threads; the GUI only reads state and calls
the start/stop methods. Events flow out over the bus (bridged to Qt signals in ``bridge.py``).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from jamcrammer.core.config import AppConfig, Level
from jamcrammer.core.events import Event, EventBus
from jamcrammer.core.logging import attach_bus, get_logger
from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import Profile, load_profile
from jamcrammer.core.safety import Hotkeys, InputLease, KillSwitch, PauseFlag, PresenceDetector
from jamcrammer.core.store import Store
from jamcrammer.learn.recorder import Recorder
from jamcrammer.platform.capture import CaptureBackend, Frame, open_stage_backend
from jamcrammer.platform.input import InputController
from jamcrammer.platform.window import (
    WindowInfo,
    WindowTracker,
    bring_to_foreground,
    ensure_dpi_awareness,
)
from jamcrammer.services.antiafk import AntiAfk, RoomDriftGuard
from jamcrammer.services.ledger import GemLedger
from jamcrammer.services.notify import ChannelResult, Notifier
from jamcrammer.services.watchdog import Detection, PopupDetector, Watchdog
from jamcrammer.vision.stage import StageMap

log = get_logger("engine")

MODES = ("planner", "hunt", "farm", "adventure", "opportunist")
MODE_LABELS = {
    "planner": "Den Planner",
    "hunt": "Best Guess Hunter",
    "farm": "Arcade Farmer",
    "adventure": "Adventure Farmer",
    "opportunist": "Opportunist",
}
PREVIEW_IDLE_S = 0.5
THREAD_JOIN_S = 5.0


@dataclass
class EngineState:
    capture_running: bool = False
    mode: str | None = None
    mode_state: str = "IDLE"
    mode_detail: str = ""
    dry_run: bool = False
    paused: bool = False
    killed: bool = False
    learn_session: Path | None = None
    window: WindowInfo | None = None
    last_error: str = ""
    stats: dict[str, Any] = field(default_factory=dict)
    bg_eta_ts: float | None = None  # epoch when Best Guess is due (planner)
    bg_eta_note: str = ""

    def bg_eta_s(self) -> float | None:
        return None if self.bg_eta_ts is None else self.bg_eta_ts - time.time()

    @property
    def pill_text(self) -> str:
        if self.killed:
            return "KILLED"
        if self.paused:
            return "PAUSED"
        if self.mode is None:
            return "RECORDING" if self.learn_session else "IDLE"
        return f"{self.mode_state} · {self.mode_detail}" if self.mode_detail else self.mode_state

    @property
    def pill_kind(self) -> str:
        if self.killed:
            return "error"
        if self.paused:
            return "paused"
        if self.mode is None and not self.learn_session:
            return "idle"
        return "running"


class Engine:
    def __init__(self, cfg: AppConfig, paths: Paths | None = None) -> None:
        ensure_dpi_awareness()
        self.cfg = cfg
        self.paths = paths or Paths()
        self.paths.ensure_runtime_dirs()
        self.bus = EventBus()
        attach_bus(self.bus)
        self.store = Store(self.paths.db)
        self.bus.subscribe("*", self._persist)
        self.notifier = Notifier(cfg.notify, icon=self._icon_path())
        self.notifier.attach(self.bus)
        self.lease = InputLease()
        self.kill = KillSwitch()
        self.pause = PauseFlag()
        self.tracker = WindowTracker(cfg.window_title_match, cfg.window_process_name)
        self.state = EngineState(dry_run=cfg.dry_run)
        self._seed_facts()  # after self.state: a failure is reported there
        self._backend: CaptureBackend | None = None
        self._latest: Frame | None = None
        self._latest_lock = threading.Lock()
        self._preview_thread: threading.Thread | None = None
        self._preview_stop = threading.Event()
        self._mode_thread: threading.Thread | None = None
        self._mode_stop = threading.Event()
        self._mode_obj: Any = None
        self._recorder: Recorder | None = None
        self.input = InputController(
            self.tracker.provider(), cfg.input, dry_run=cfg.dry_run, bus=self.bus
        )
        self.presence = PresenceDetector(
            lambda: self.input.stats.last_injected_ts, cfg.presence_slack_s, cfg.resume_after_idle_s
        )
        self._profiles: dict[str, Profile] = {}
        self.guard = RoomDriftGuard(cfg.drift, self._drift_masks())
        self.antiafk = AntiAfk(
            cfg.antiafk, self.input, self.guard, bus=self.bus, ensure_focus=self.focus_game
        )
        self.watchdog = Watchdog(
            cfg.watchdog,
            PopupDetector(self.profile("shared"), paths=self.paths),
            self.input,
            bus=self.bus,
            lease=self.lease,
            on_fatal=self._on_fatal,
            on_pause=lambda reason: self.pause.pause(f"watchdog:{reason}"),
            screenshot_dir=self.paths.captures / "watchdog",
            unknown_dir=self.paths.unknown_popups,
            antiafk_active=lambda: self.antiafk.enabled and self.state.mode is not None,
        )
        self.ledger = GemLedger(self.store, self.profile("shared"), bus=self.bus)
        self.hotkeys = Hotkeys(cfg.hotkeys, self.bus)
        self.bus.subscribe("hotkey.kill", lambda e: self.kill_now("F12"))
        self.bus.subscribe("hotkey.pause", self._on_pause_hotkey)
        self.bus.subscribe("hotkey.toggle_mode", lambda e: self.toggle_mode())
        self.bus.subscribe("hotkey.learn_mark", lambda e: self.mark("mark"))
        self.bus.subscribe("hotkey.learn_correct", lambda e: self.mark("correct"))
        self.bus.subscribe("hotkey.learn_wrong", lambda e: self.mark("wrong"))
        self.selected_mode = "hunt"
        self.started_ts = time.time()
        try:
            self.hotkeys.start()
        except Exception as exc:
            self.state.last_error = f"hotkeys unavailable: {exc}"
            log.warning(self.state.last_error)

    # --- helpers -----------------------------------------------------------------------------

    def _icon_path(self) -> Path | None:
        p = self.paths.assets / "icons" / "app.png"
        return p if p.exists() else None

    def _persist(self, event: Event) -> None:
        try:
            self.store.add_event(event)
        except Exception as exc:
            log.warning("store write failed: %s", exc)

    def _drift_masks(self) -> list[tuple[int, int, int, int]]:
        shared = self.profile("shared")
        return [
            shared.roi(k)
            for k in ("hud_top_band", "hud_bottom_band", "animal_center_box")
            if shared.has(k)
        ]

    def profile(self, name: str) -> Profile:
        if name not in self._profiles:
            self._profiles[name] = load_profile(name, self.paths)
        return self._profiles[name]

    def reload_profiles(self) -> None:
        self._profiles.clear()

    def emit(self, event_type: str, level: Level = Level.INFO, **payload: Any) -> None:
        self.bus.emit(event_type, source="engine", level=level, **payload)

    def focus_game(self) -> bool:
        info = self.tracker.info or self.tracker.refresh()
        ok = bool(info and bring_to_foreground(info.hwnd, self.cfg.input.foreground_timeout_s))
        # the focus may tap ALT; count it as ours so presence detection doesn't see "user active"
        self.input._touch("focus")
        return ok

    def _on_fatal(self, det: Detection) -> None:
        self.kill_now(f"watchdog:{det.kind}")

    # --- window & capture --------------------------------------------------------------------

    def refresh_window(self) -> WindowInfo | None:
        info = self.tracker.refresh()
        self.state.window = info
        return info

    def start_capture(self) -> bool:
        if self.state.capture_running:
            return True
        if self.refresh_window() is None:
            self.state.last_error = "AJC window not found"
            self.emit(
                "engine.no_window", Level.NOTICE, message="Animal Jam Classic window not found"
            )
            return False
        try:
            self._backend = open_stage_backend(self.cfg.capture_backend, self.tracker.provider())
        except Exception as exc:
            self.state.last_error = f"capture failed: {exc}"
            self.emit("engine.capture_failed", Level.HIGH, message=str(exc))
            return False
        self._preview_stop.clear()
        self._preview_thread = threading.Thread(
            target=self._preview_loop, name="preview", daemon=True
        )
        self._preview_thread.start()
        self.state.capture_running = True
        self.emit("engine.capture_started", backend=self._backend.name)
        return True

    def stop_capture(self) -> None:
        self._preview_stop.set()
        if self._preview_thread is not None:
            self._preview_thread.join(THREAD_JOIN_S)
            self._preview_thread = None
        if self._backend is not None:
            self._backend.close()
            self._backend = None
        self.state.capture_running = False

    def grab(self) -> Frame:
        if self._backend is None:
            msg = "capture not running"
            raise RuntimeError(msg)
        frame = self._backend.grab()
        with self._latest_lock:
            self._latest = frame
        return frame

    def snapshot(self) -> Frame | None:
        with self._latest_lock:
            return self._latest

    def _preview_loop(self) -> None:
        period = 1.0 / self.cfg.fps
        baseline_set = False
        while not self._preview_stop.is_set():
            try:
                frame = self.grab()
                if not baseline_set:
                    self.watchdog.set_baseline(frame.image)
                    self.guard.set_baseline(frame.image)
                    baseline_set = True
                if self._mode_thread is None and not self.kill.tripped:
                    self.watchdog.tick(frame)
                    self.ledger.tick(frame.image, "idle")
                change = self.tracker.poll()
                if change is not None:
                    self.state.window = self.tracker.info
                    self.emit(
                        "engine.window_changed",
                        Level.NOTICE,
                        change=change,
                        message=f"AJC window {change}; profiles may need a re-check",
                    )
                    if change == "lost":
                        time.sleep(PREVIEW_IDLE_S)
            except Exception as exc:
                self.state.last_error = str(exc)
                time.sleep(PREVIEW_IDLE_S)
            time.sleep(period)

    # --- modes -------------------------------------------------------------------------------

    def set_dry_run(self, on: bool) -> None:
        self.state.dry_run = on
        self.input.dry_run = on
        self.emit("engine.dry_run", on=on)

    def start_mode(self, name: str) -> bool:
        if self._mode_thread is not None:
            return False
        if self.kill.tripped:
            self.state.last_error = "kill switch tripped; restart the app"
            return False
        if not self.start_capture():
            return False
        # On one screen (the laptop) Start leaves the GUI in front of the game: mss would capture
        # the GUI and Space/arrow keys would land on its buttons. Bring the game forward first.
        focused = self.focus_game()
        if not focused:
            self.emit("engine.focus_failed", Level.NOTICE, mode=name)
        # Start means "I checked the screen": clear a watchdog pause and an F11 pause
        self.watchdog.resume()
        if self.pause.paused:
            self.pause.resume()
            self.state.paused = False
        self.state.last_error = ""
        self._mode_stop.clear()
        self.state.mode = name
        self.state.mode_state = "STARTING"
        self.state.mode_detail = ""
        target = {
            "planner": self._run_planner,
            "hunt": self._run_hunt,
            "farm": self._run_farm,
            "opportunist": self._run_opportunist,
            "adventure": self._run_adventure,
        }.get(name, self._run_placeholder)
        self._mode_thread = threading.Thread(
            target=self._mode_wrapper, args=(name, target), name=f"mode-{name}", daemon=True
        )
        self._mode_thread.start()
        self.emit("engine.mode_started", Level.NOTICE, mode=name, dry_run=self.state.dry_run)
        return True

    def _mode_wrapper(self, name: str, target: Callable[[], None]) -> None:
        try:
            target()
        except Exception as exc:
            self.state.last_error = f"{name}: {exc}"
            self.emit("engine.mode_crashed", Level.HIGH, mode=name, message=str(exc))
            log.exception("mode %s crashed", name)
        finally:
            self.state.mode = None
            self.state.mode_state = "IDLE"
            self.state.mode_detail = ""
            self._mode_thread = None
            self._mode_obj = None
            self.lease.release(name)
            self.emit("engine.mode_stopped", mode=name)

    def stop_mode(self) -> None:
        self._mode_stop.set()
        t = self._mode_thread
        if t is not None:
            t.join(THREAD_JOIN_S)

    def toggle_mode(self) -> None:
        if self._mode_thread is not None:
            self.stop_mode()
        else:
            self.start_mode(self.selected_mode)

    def _seed_facts(self) -> None:
        """Load the community wiki answers into the bank once (assets/bestguess/facts_wiki.txt)."""
        from jamcrammer.modes.bestguess.seed import already_seeded, seed_from_file

        path = self.paths.assets / "bestguess" / "facts_wiki.txt"
        try:
            if path.exists() and not already_seeded(self.store, "wiki"):
                n = seed_from_file(self.store, path, "wiki")
                self.emit("bestguess.seeded", count=n, facts_source="wiki")  # emit owns 'source'
        except Exception as exc:
            self.state.last_error = f"fact seeding failed: {exc}"

    def _should_stop(self) -> bool:
        return self._mode_stop.is_set() or self.kill.tripped

    def _screen_known(self) -> bool:
        """The running mode recognises the current screen (its own detector says so)."""
        return bool(getattr(self._mode_obj, "screen_known", False))

    def _watchdog_blocks(self) -> bool:
        if not self.watchdog.paused:
            return False
        if not self.state.last_error:
            self.state.last_error = (
                "stopped: the watchdog saw an unknown dialog; check the screen and press Start"
            )
            self.emit("engine.mode_blocked", Level.HIGH, message=self.state.last_error)
        return True

    def _status(self, text: str) -> None:
        parts = text.split(" · ", 1)
        self.state.mode_state = parts[0]
        self.state.mode_detail = parts[1] if len(parts) > 1 else ""

    def _run_hunt(self) -> None:
        if self._on_derby_track():
            # Start was pressed on the Derby track with the Hunter selected: farm the Derby
            self.emit("engine.track_detected", Level.NOTICE, message="Jamaa Derby track on screen")
            self.cfg.arcade_game = "jamaa_derby"
            self._run_farm()
            return
        if self._already_in_arena() and self._phase_c_allowed():
            # William is standing in the Best Guess arena already: skip the hunt, farm now
            self.emit("engine.arena_detected", Level.NOTICE, message="Best Guess arena on screen")
            self.lease.acquire("hunt")
            self._run_bestguess_farmer()
            return
        self._hunt_then_farm()

    def _make_watcher(self) -> Any:
        from jamcrammer.modes.party_watcher import BannerLibrary, PanelAnalyzer, PartyWatcher

        analyzer = PanelAnalyzer(
            self.profile("parties"), BannerLibrary(self.paths.assets / "banners"), paths=self.paths
        )
        return PartyWatcher(
            analyzer,
            self.cfg.hunt,
            self.input,
            lambda: self.grab().image,
            store=self.store,
            bus=self.bus,
            unknown_dir=self.paths.unknown,
        )

    def _hunt_then_farm(self, presence: bool = True) -> None:
        """Hunter loop (My Parties scans, auto-join), then Phase C once joined."""
        from jamcrammer.modes.bestguess.hunter import Hunter

        hunter = Hunter(
            self.cfg,
            self._make_watcher(),
            self.grab,
            watchdog=self.watchdog,
            antiafk=self.antiafk,
            guard=self.guard,
            bus=self.bus,
            no_join=not self.cfg.auto_join,
            presence=self.presence if presence else None,
            on_joined=self._on_joined,
        )
        self._mode_obj = hunter
        self.lease.acquire("hunt")
        hunter.run(lambda: self._should_stop() or self.pause.paused, self._status)
        if hunter.stats.joined and self._phase_c_allowed():
            self._run_bestguess_farmer()
        while hunter.stats.joined and not self._should_stop():
            self._status("JOINED · recording")
            time.sleep(PREVIEW_IDLE_S)
        self.state.stats["hunt"] = hunter.stats.__dict__.copy()

    # --- den planner ------------------------------------------------------------------------

    def set_best_guess_eta(self, seconds: float | None, note: str = "") -> None:
        self.state.bg_eta_ts = None if seconds is None else time.time() + seconds
        self.state.bg_eta_note = note
        self.emit("planner.eta", seconds=None if seconds is None else int(seconds), note=note)

    def _make_navigator(self) -> Any:
        from jamcrammer.modes.planner import Navigator

        return Navigator(
            self.profile("navigation"),
            self.cfg.planner,
            self.input,
            lambda: self.grab().image,
            self._make_watcher(),
            self.paths,
            bus=self.bus,
        )

    def read_eta_from_game(self) -> tuple[int | None, str]:
        """GUI button: open My Parties + the tray, read the Best Guess time, tidy up."""
        if not self.start_capture():
            return (None, self.state.last_error or "capture failed")
        nav = self._make_navigator()
        eta, note = nav.read_best_guess_eta()
        if eta is not None:
            self.set_best_guess_eta(eta, note)
        else:
            self.state.bg_eta_note = note
            self.emit("planner.eta_unread", Level.NOTICE, message=note)
        return (eta, note)

    def _run_planner(self) -> None:
        """Den -> Jamaa Derby (PRO) until Best Guess is due -> leave -> hunt Best Guess."""
        from jamcrammer.core.gate import gate_status
        from jamcrammer.modes.arcade.derby import DerbyDetector, Screen

        pc = self.cfg.planner
        nav = self._make_navigator()
        self.lease.acquire("planner")
        detector = DerbyDetector(self.profile("jamaa_derby"), self.cfg.derby)

        def track_visible(image: Any) -> bool:
            return detector.screen(image) != Screen.UNKNOWN

        # 1. the demonstration + time read (skipped when we are already on the track)
        if pc.demo and not track_visible(self.grab().image):
            self._status("PLANNER · reading the party tray")
            eta, note = nav.read_best_guess_eta()
            if eta is not None:
                self.set_best_guess_eta(eta, note)
            else:
                self.emit("planner.eta_unread", Level.NOTICE, message=note)
        if self.state.bg_eta_ts is None:
            self.set_best_guess_eta(pc.eta_default_min * 60, "default")
        if self._should_stop():
            return

        # 2. Derby, unless the track is already on screen
        if not track_visible(self.grab().image):
            self._status("PLANNER · going to the den")
            res = nav.go_to_den()
            self.emit("planner.den", Level.INFO if res.ok else Level.HIGH, message=res.note)
            if not res.ok or self._should_stop():
                self.state.last_error = res.note
                return
            gate = gate_status("jamaa_derby", self.paths, self._profiles)
            if not gate.unlocked:
                self.state.last_error = f"jamaa_derby {gate.summary()}"
                self.emit(
                    "engine.gate_locked", Level.HIGH, mode="planner", message=self.state.last_error
                )
                return
            self._status("PLANNER · opening Jamaa Derby (PRO)")
            res = nav.open_derby(track_visible)
            self.emit("planner.derby", Level.INFO if res.ok else Level.HIGH, message=res.note)
            if not res.ok or self._should_stop():
                self.state.last_error = res.note
                return

        # 3. farm until Best Guess is due (leave at a leaderboard or the start line)
        self.cfg.arcade_game = "jamaa_derby"

        def due() -> bool:
            eta = self.state.bg_eta_s()
            return eta is not None and eta <= pc.leave_lead_s

        self._run_farm(extra_stop=due, between_races_only=True)
        if self._should_stop():
            return

        # 4. leave the Derby and hunt Best Guess in My Parties every hunt_interval_s
        self._status("PLANNER · leaving the Derby")
        x, y = self.profile("jamaa_derby").point("close_x_point")
        self.input.click(x, y)
        nav._wait(lambda im: not track_visible(im), pc.derby_timeout_s)
        self.emit("planner.left_derby", Level.NOTICE, message="Best Guess is due; hunting it now")
        saved = self.cfg.hunt_interval_s
        self.cfg.hunt_interval_s = pc.hunt_interval_s
        try:
            self._hunt_then_farm(presence=False)
        finally:
            self.cfg.hunt_interval_s = saved

    def _on_derby_track(self) -> bool:
        """The Derby track, start line or leaderboard is on screen and its gate is open."""
        from jamcrammer.core.gate import gate_status
        from jamcrammer.modes.arcade.derby import DerbyDetector, Screen

        frame = self.snapshot()
        if frame is None:
            return False
        try:
            det = DerbyDetector(self.profile("jamaa_derby"), self.cfg.derby)
            if det.screen(frame.image) == Screen.UNKNOWN:
                return False
            return gate_status("jamaa_derby", self.paths, self._profiles).unlocked
        except Exception:
            return False

    def _already_in_arena(self) -> bool:
        """The parchment banner and the gem icon of the Best Guess arena are on screen now."""
        from jamcrammer.modes.bestguess.state import in_arena

        frame = self.snapshot()
        if frame is None:
            return False
        try:
            return in_arena(frame.image, self.profile("bestguess"), self.cfg.bestguess)
        except Exception:
            return False

    def _phase_c_allowed(self) -> bool:
        from jamcrammer.core.gate import gate_status

        if not self.cfg.start_farming_after_join or self.cfg.answer_mode == "learn_only":
            return False
        return gate_status("bestguess", self.paths, self._profiles).unlocked

    def _on_joined(self, _banner: Any) -> None:
        if self._recorder is None and not self._phase_c_allowed():
            self.start_learn("bestguess", name="autojoin")  # Learn-only until the gate opens

    def _run_bestguess_farmer(self) -> None:
        """Phase C (M8): runs after a join when the gate is open and farming is enabled."""
        from jamcrammer.modes.bestguess.brain import Brain
        from jamcrammer.modes.bestguess.farmer import BestGuessFarmer

        brain = Brain(
            self.store,
            fuzzy_threshold=self.cfg.fuzzy_threshold,
            unknown_policy=self.cfg.unknown_fact_policy,
            answer_mode=self.cfg.answer_mode,
        )
        farmer = BestGuessFarmer(
            self.profile("bestguess"),
            self.cfg.bestguess,
            brain,
            self.input,
            lambda: self.grab().image,
            store=self.store,
            bus=self.bus,
            screenshot=self._save_shot,
        )
        self._mode_obj = farmer
        self.emit("engine.phase_c", Level.NOTICE, message="Best Guess farming started (gate open)")

        def guarded_stop() -> bool:
            frame = self.snapshot()
            if frame is not None:
                self.watchdog.tick(frame, known_screen=self._screen_known())
            return self._should_stop() or self.pause.paused or self._watchdog_blocks()

        farmer.run(guarded_stop, self._status)
        self.state.stats["bestguess"] = farmer.stats.as_dict()
        if farmer.stats.gems_est:
            self.ledger.record_payout(farmer.stats.gems_est, "hunt")

    def _save_shot(self, tag: str, image: Any) -> Path:
        import cv2

        d = self.paths.captures / "bestguess"
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{time.strftime('%Y%m%d_%H%M%S')}_{tag}.png"
        cv2.imwrite(str(path), image)
        return path

    def _run_farm(
        self,
        extra_stop: Callable[[], bool] | None = None,
        between_races_only: bool = False,
    ) -> None:
        """Arcade farmer behind the profile gate. ``extra_stop`` adds a stop condition (the
        planner's deadline); with ``between_races_only`` it is honoured only off the track
        (leaderboard or start line) so a race is never abandoned mid-way."""
        from jamcrammer.core.gate import gate_status
        from jamcrammer.modes.arcade.double_up import DoubleUpFarmer

        game = self.cfg.arcade_game
        gate = gate_status(game, self.paths, self._profiles)
        if not gate.unlocked:
            self.emit(
                "engine.gate_locked", Level.HIGH, mode="farm", message=f"{game} {gate.summary()}"
            )
            self._run_placeholder()
            return
        farmer: Any
        if game == "gem_ball":
            self._run_gem_ball()
            return
        if game == "swoopy_eagle":
            self._run_swoopy()
            return
        if game == "jamaa_derby":
            from jamcrammer.modes.arcade.derby import DerbyFarmer

            farmer = DerbyFarmer(
                self.profile("jamaa_derby"),
                self.cfg.derby,
                self.input,
                lambda: self.grab().image,
                store=self.store,
                bus=self.bus,
                learned_path=self.paths.data / "derby_learned.json",
                hits_dir=self.paths.captures / "derby" / "hits",
            )
        else:
            farmer = DoubleUpFarmer(
                self.profile("double_up"),
                self.cfg.double_up,
                self.input,
                lambda: self.grab().image,
                store=self.store,
                bus=self.bus,
            )
        self._mode_obj = farmer
        self.lease.acquire("farm")

        def guarded_stop() -> bool:
            frame = self.snapshot()
            if frame is not None:
                self.watchdog.tick(frame, known_screen=self._screen_known())
            if extra_stop is not None and extra_stop():
                state = str(getattr(farmer, "state", ""))
                if not between_races_only or state in ("RESULTS", "WAITING", "UNKNOWN"):
                    return True
            return self._should_stop() or self.pause.paused or self._watchdog_blocks()

        farmer.run(guarded_stop, self._status)
        if farmer.stats.gems:
            self.ledger.record_payout(farmer.stats.gems, "farm")
        self.state.stats["farm"] = (
            farmer.stats.as_dict()
            if hasattr(farmer.stats, "as_dict")
            else {
                "rounds": farmer.stats.rounds,
                "perfect": farmer.stats.perfect,
                "gems": farmer.stats.gems,
                "median_round_s": farmer.stats.median_round_s,
            }
        )

    def _run_opportunist(self) -> None:
        """Opportunist (M9): Double Up rounds + party checks + hop to Best Guess."""
        from jamcrammer.core.gate import gate_status
        from jamcrammer.modes.arcade.double_up import DoubleUpFarmer
        from jamcrammer.modes.bestguess.hunter import Hunter
        from jamcrammer.modes.opportunist import ENTRY_FIELDS, ArcadeEntry, Opportunist
        from jamcrammer.modes.party_watcher import BannerLibrary, PanelAnalyzer, PartyWatcher

        gate = gate_status("double_up", self.paths, self._profiles)
        route_missing = self.profile("double_up").missing(list(ENTRY_FIELDS))
        if not gate.unlocked or route_missing:
            self.emit(
                "engine.gate_locked",
                Level.HIGH,
                mode="opportunist",
                message=f"Opportunist needs Double Up {gate.summary()}; entry route missing {route_missing}",
            )
            self._run_placeholder()
            return
        grab_image = lambda: self.grab().image  # noqa: E731
        farmer = DoubleUpFarmer(
            self.profile("double_up"),
            self.cfg.double_up,
            self.input,
            grab_image,
            store=self.store,
            bus=self.bus,
        )
        analyzer = PanelAnalyzer(
            self.profile("parties"), BannerLibrary(self.paths.assets / "banners"), paths=self.paths
        )
        watcher = PartyWatcher(
            analyzer,
            self.cfg.hunt,
            self.input,
            grab_image,
            store=self.store,
            bus=self.bus,
            unknown_dir=self.paths.unknown,
        )
        hunter = Hunter(
            self.cfg,
            watcher,
            self.grab,
            watchdog=self.watchdog,
            antiafk=self.antiafk,
            guard=self.guard,
            bus=self.bus,
            no_join=not self.cfg.auto_join,
            presence=self.presence,
            on_joined=self._on_joined,
        )
        entry = ArcadeEntry(
            self.profile("double_up"),
            self.cfg.opportunist,
            self.input,
            grab_image,
            farmer.board_visible,
        )
        opp = Opportunist(
            self.cfg.opportunist,
            self.cfg.opportunist_check_s,
            farmer,
            hunter,
            entry,
            store=self.store,
            bus=self.bus,
        )
        self._mode_obj = opp
        self.lease.acquire("opportunist")

        def guarded_stop() -> bool:
            frame = self.snapshot()
            if frame is not None:
                self.watchdog.tick(frame, known_screen=self._screen_known())
            return self._should_stop() or self.pause.paused or self._watchdog_blocks()

        opp.run(guarded_stop, self._status)
        self.state.stats["opportunist"] = opp.stats.as_dict()
        if hunter.stats.joined and self._phase_c_allowed():
            self._run_bestguess_farmer()

    def _run_gem_ball(self) -> None:
        """Gem Ball auto-tuner (M11): sweep hold durations, then exploit or declare random."""
        from jamcrammer.modes.arcade.gem_ball import GemBallTuner, gem_delta_outcome
        from jamcrammer.vision.ocr import crop, ocr_image, parse_int

        prof = self.profile("gem_ball")
        roi = prof.roi("gem_counter_roi")
        outcome = gem_delta_outcome(
            lambda img: parse_int(ocr_image(crop(img, roi), upscale=2.0).text),
            top_delta=int(prof.number("top_slot_gems")),
            jackpot_delta=int(prof.number("jackpot_gems")),
        )
        tuner = GemBallTuner(
            prof,
            self.cfg.gem_ball,
            self.input,
            lambda: self.grab().image,
            outcome,
            store=self.store,
            bus=self.bus,
            report_dir=self.paths.data,
        )
        self._mode_obj = tuner
        self.lease.acquire("farm")
        rep = tuner.sweep(self._should_stop)
        if rep.deterministic and not self._should_stop():
            self._status(f"EXPLOITING · {rep.best_duration_ms} ms")
            while not self._should_stop() and not self.pause.paused:
                tuner.exploit(10, self._should_stop)
        self.state.stats["farm"] = rep.as_dict()

    def _run_swoopy(self) -> None:
        """Swoopy Eagle prototype (M11): 60 fps small-ROI loop with a PD flap controller."""
        import cv2

        from jamcrammer.modes.arcade.swoopy import SwoopyPilot
        from jamcrammer.services.watchdog import match_template

        prof = self.profile("swoopy_eagle")
        crash_tpl = None
        if prof.has("crash_template"):
            raw = cv2.imread(str(prof.template_path("crash_template", self.paths)))
            crash_tpl = None if raw is None else np.asarray(raw, dtype=np.uint8)

        def crashed(image: Any) -> bool:
            if crash_tpl is None:
                return False
            _, box = match_template(image, crash_tpl, 0.85)
            return box is not None

        pilot = SwoopyPilot(
            prof, self.cfg.swoopy, self.input, lambda: self.grab().image, bus=self.bus
        )
        self._mode_obj = pilot
        self.lease.acquire("farm")
        stats = pilot.run(lambda: self._should_stop() or self.pause.paused, crashed)
        self.state.stats["farm"] = stats.as_dict()

    def _run_adventure(self) -> None:
        """Adventure route farmer (modes/adventures/replay.py) behind the gate: replays the
        recorded run of ``adventures.route.target`` and keeps it on course; William's own
        input pauses it and it continues where he leaves it."""
        import cv2

        from jamcrammer.core.gate import gate_status
        from jamcrammer.modes.adventures.replay import FixLog, RouteRunner, Tape
        from jamcrammer.modes.adventures.route import latest_session, load_or_build
        from jamcrammer.vision.ocr import ocr_image

        rc = self.cfg.adventures.route
        gate = gate_status(rc.target, self.paths, self._profiles)
        if not gate.unlocked and not rc.supervised:
            self.emit(
                "engine.gate_locked",
                Level.HIGH,
                mode="adventure",
                message=f"{rc.target} {gate.summary()}",
            )
            self._run_placeholder()
            return
        if not gate.unlocked:
            # William's call (adventures.route.supervised): he watches and steers
            self.emit(
                "adventure.supervised",
                Level.NOTICE,  # his own setting: a toast, not the repeating HIGH alarm
                message=f"SUPERVISED run, {rc.target} {gate.summary()}: stay at the keyboard",
            )
        sessions = self.paths.sessions
        session = sessions / rc.session if rc.session else latest_session(sessions, rc.target)
        if session is None or not session.exists():
            self.state.last_error = f"no recorded {rc.target} run under {sessions}"
            self.emit("adventure.no_route", Level.HIGH, message=self.state.last_error)
            return
        prof = self.profile("adventure")
        raw = cv2.imread(str(prof.template_path("dialog_x_template", self.paths)))
        if raw is None:
            self.state.last_error = "dialog X template missing (assets/templates/adventures)"
            self.emit("adventure.no_template", Level.HIGH, message=self.state.last_error)
            return
        x_tmpl = np.asarray(raw, dtype=np.uint8)
        self._status(f"LOADING · route from {session.name}")
        route = load_or_build(session, rc.target, prof, rc, x_tmpl)
        start_rt: float | None = None
        if self.cfg.adventures.level.enabled:
            start_rt = self._run_level(route, prof, x_tmpl)
            if start_rt is None:
                return
        tape = Tape(self._screen_to_stage)
        tape.start()
        runner = RouteRunner(
            route,
            prof,
            rc,
            self.input,
            lambda: self.grab().image,
            dialog_x=x_tmpl,
            presence=self.presence,
            bus=self.bus,
            ocr=lambda img: ocr_image(img).text,
            fixes=FixLog(self.paths.data / "adventures" / f"{rc.target}_fixes.jsonl"),
            tape=tape,
        )
        self._mode_obj = runner
        self.lease.acquire("adventure")

        def guarded_stop() -> bool:
            frame = self.snapshot()
            if frame is not None:
                # the runner closes in-game dialogs itself; logout and the like still stop it
                self.watchdog.tick(frame, known_screen=True)
            return self._should_stop() or self.pause.paused or self._watchdog_blocks()

        try:
            runner.run(guarded_stop, self._status, start_rt=start_rt)
        finally:
            tape.stop()
        self.state.stats["adventure"] = runner.stats.as_dict()

    def _run_level(self, route: Any, prof: Profile, x_tmpl: Any) -> float | None:
        """The menus by state and the moth chamber from the level model
        (modes/adventures/{opening,level_runner}.py). Returns the route time the replay
        continues from (the banner the level runner stopped at), or None to stop."""
        import cv2

        from jamcrammer.modes.adventures.level import Level as LevelModel
        from jamcrammer.modes.adventures.level import level_dir
        from jamcrammer.modes.adventures.level_runner import LevelRunner
        from jamcrammer.modes.adventures.opening import Opening
        from jamcrammer.vision.ocr import ocr_image

        rc, lc = self.cfg.adventures.route, self.cfg.adventures.level
        lvl = LevelModel.load(level_dir(self.paths.profiles, rc.target))
        if lvl is None:
            self.emit(
                "adventure.no_level",
                Level.NOTICE,
                message=f"no level model for {rc.target} (Adventures tab: Rebuild); route replay only",
            )
            return 0.0

        def guarded_stop() -> bool:
            frame = self.snapshot()
            if frame is not None:
                self.watchdog.tick(frame, known_screen=True)
            return self._should_stop() or self.pause.paused or self._watchdog_blocks()

        from jamcrammer.modes.adventures.objects import CourageText

        courage_raw = cv2.imread(str(prof.template_path("courage_template", self.paths)))
        courage = (
            CourageText(np.asarray(courage_raw, dtype=np.uint8), prof)
            if courage_raw is not None
            else None
        )
        grab = lambda: self.grab().image  # noqa: E731
        self.lease.acquire("adventure")  # the menu clicks are ours from here
        self._status("MENUS · into the adventure")
        opening = Opening(
            lvl,
            prof,
            lc,
            click=self.input.click,
            grab=grab,
            ocr=ocr_image,
            dialog_x=x_tmpl,
            bus=self.bus,
            clock=time.monotonic,
            sleep=time.sleep,
            shots=self.paths.captures / "adventure_menus",
        )
        if not opening.run(guarded_stop):
            self.state.stats["adventure_menus"] = {"steps": opening.steps}
            return None
        runner = LevelRunner(
            lvl,
            prof,
            lc,
            self.input,
            grab,
            banner_ocr=lambda img: ocr_image(img).text,
            counter_ocr=lambda img: ocr_image(img, upscale=4.0).text,
            counter_retry=lambda img: ocr_image(img, upscale=2.0).text,
            dialog_x=x_tmpl,
            courage=courage,
            presence=self.presence,
            bus=self.bus,
            shots=self.paths.captures / "adventure_shots",
        )
        self._mode_obj = runner
        out = runner.run(guarded_stop, self._status)
        self.state.stats["adventure_level"] = {"outcome": out, **runner.stats.as_dict()}
        if out == "stopped":
            return None
        # continue by replay at the banner it reached (the door), else from the plan's end
        key = runner.phase or "door"
        for o in route.objectives:
            p = lvl.phase_of(o.text)
            if p is not None and p.key == key and p.mode != "plan":
                self.emit("adventure.handover", Level.NOTICE, phase=key, rt=round(o.t, 1))
                return float(o.t)
        door = next(
            (o for o in route.objectives if (p := lvl.phase_of(o.text)) and p.key == "door"), None
        )
        return float(door.t) if door is not None else 0.0

    def rebuild_route(self, session: Path) -> str:
        """Build the adventure route from a recorded run, validate it and write the replay
        record the gate reads (Adventures page). Minutes for a long run: call off the GUI thread."""
        import cv2

        from jamcrammer.core.gate import write_replay_record
        from jamcrammer.modes.adventures.route import build_route, validate_route

        rc = self.cfg.adventures.route
        prof = self.profile("adventure")
        raw = cv2.imread(str(prof.template_path("dialog_x_template", self.paths)))
        if raw is None:
            return "dialog X template missing (assets/templates/adventures)"
        x_tmpl = np.asarray(raw, dtype=np.uint8)
        route = build_route(session, rc.target, prof, rc, x_tmpl)
        acc, n = validate_route(route, rc)
        write_replay_record(session, acc, n, "route_localize")
        clicks = sum(s.kind != "key" for s in route.steps)
        self.emit(
            "adventure.route_built", session=session.name, steps=len(route.steps), accuracy=acc
        )
        level_note = self._rebuild_level(prof, x_tmpl)
        return (
            f"{len(route.steps)} steps ({clicks} clicks), {len(route.objectives)} objectives; "
            f"localisation check {acc:.0%} over {n} frames; {level_note}"
        )

    def _rebuild_level(self, prof: Profile, x_tmpl: Any) -> str:
        """The level model from every finished run of the target (docs/MYSTERY_BELOW.md),
        saved under profiles/levels/<target>/ for the level runner."""
        from jamcrammer.modes.adventures.level import (
            PHASES_BY_TARGET,
            build_level,
            finished_sessions,
            level_dir,
        )

        rc, lc = self.cfg.adventures.route, self.cfg.adventures.level
        if rc.target not in PHASES_BY_TARGET:
            return "no level model for this adventure"
        sessions = (
            [self.paths.sessions / name for name in lc.sessions]
            if lc.sessions
            else finished_sessions(self.paths.sessions, rc.target)
        )
        sessions = [s for s in sessions if s.exists()]
        if not sessions:
            return "level: no finished run to build from"
        try:
            lvl = build_level(sessions, rc.target, prof, cfg=lc, route_cfg=rc, x_tmpl=x_tmpl)
            lvl.save(level_dir(self.paths.profiles, rc.target))
        except Exception as exc:  # a half-recorded run must not take the page down
            return f"level build failed: {exc}"
        self.emit(
            "adventure.level_built",
            target=rc.target,
            runs=len(sessions),
            spots=len(lvl.spots),
            acts=sum(len(v) for v in lvl.plan.values()),
        )
        return f"level from {len(sessions)} runs: {len(lvl.spots)} spots"

    def _screen_to_stage(self, sx: int, sy: int) -> tuple[int, int] | None:
        """A screen point inside the AJC client as stage pixels (William's corrective clicks)."""
        info = self.tracker.info
        if info is None or not info.contains_screen_point(sx, sy):
            return None
        cx, cy = info.to_client(sx, sy)
        w, h = info.client_size
        return StageMap(w, h).to_stage(cx, cy)

    def _run_placeholder(self) -> None:
        """farm / opportunist arrive in M7 / M9: keep the watchdog alive and say so."""
        self.emit(
            "engine.not_implemented",
            Level.NOTICE,
            mode=self.state.mode,
            message=f"{MODE_LABELS.get(self.state.mode or '', '')} is not built yet (watchdog only).",
        )
        while not self._should_stop():
            self._status("WATCHDOG ONLY · mode not built yet")
            try:
                frame = self.grab()
                if not self.pause.paused:
                    self.watchdog.tick(frame)
            except Exception as exc:
                self.state.last_error = str(exc)
            time.sleep(1.0 / self.cfg.watchdog.fps)

    def _on_pause_hotkey(self, _event: Event) -> None:
        self.toggle_pause()

    def toggle_pause(self) -> bool:
        paused = self.pause.toggle("F11")
        self.state.paused = paused
        if not paused:
            self.watchdog.resume()
        self.emit(
            "engine.paused" if paused else "engine.resumed",
            Level.NOTICE,
            message="Paused (F11)" if paused else "Resumed",
        )
        return paused

    def kill_now(self, reason: str) -> None:
        self.kill.trip(reason)
        self.state.killed = True
        self.emit("engine.killed", Level.HIGH, reason=reason, message=f"Kill switch: {reason}")
        self._mode_stop.set()
        self.stop_learn()

    # --- learn ----------------------------------------------------------------------------------

    def start_learn(self, target: str, name: str | None = None, video: bool = False) -> Path | None:
        if self._recorder is not None:
            return self.state.learn_session
        if not self.start_capture():
            return None
        stamp = time.strftime("%Y%m%d_%H%M%S")
        session_dir = self.paths.sessions / (f"{stamp}_{target}" + (f"_{name}" if name else ""))
        assert self._backend is not None
        self._recorder = Recorder(
            session_dir=session_dir,
            target=target,
            cfg=self.cfg.learn,
            grab=self.grab,
            window=self.tracker.provider(),
            bus=self.bus,
            capture_input=True,
            video=video,
            ocr=self.cfg.learn.ocr_keyframes,
            extra_meta={"source": "gui"},
        )
        self._recorder.start()
        self.state.learn_session = session_dir
        self.store.start_session("learn", target, session_dir, time.time())
        return session_dir

    def stop_learn(self) -> None:
        rec = self._recorder
        if rec is None:
            return
        rec.stop()
        self._recorder = None
        self.state.learn_session = None

    def learn_stats(self) -> dict[str, Any] | None:
        return self._recorder.stats.as_dict() if self._recorder is not None else None

    def mark(self, kind: str) -> None:
        if self._recorder is not None:
            self._recorder.mark(kind)

    def ingest_inbox(self) -> list[Path]:
        """Import inbox files, analyse and report each new session (KICKOFF 11.1)."""
        from jamcrammer.learn.analyze import analyze_session
        from jamcrammer.learn.ingest import ingest_inbox
        from jamcrammer.learn.report import render_report

        res = ingest_inbox(self.paths, self.cfg.learn)
        for session in res.sessions:
            result = analyze_session(session, self.cfg.analysis, self.paths)
            render_report(session, result.as_dict())
        self.emit(
            "learn.ingested",
            Level.NOTICE,
            sessions=[s.name for s in res.sessions],
            keyframes=res.keyframes,
            message=f"Imported {res.images} images, {res.videos} videos into {len(res.sessions)} session(s)",
        )
        return res.sessions

    def analyze(self, session: Path) -> Path:
        from jamcrammer.learn.analyze import analyze_session
        from jamcrammer.learn.report import render_report

        result = analyze_session(session, self.cfg.analysis, self.paths)
        report = render_report(session, result.as_dict())
        self.emit(
            "learn.analyzed",
            Level.NOTICE,
            session=session.name,
            states=len(result.states),
            answers=len(result.answers),
            missing=result.missing,
            message=f"Analysed {session.name}: {len(result.states)} states, {len(result.answers)} answers",
        )
        return report

    # --- misc ---------------------------------------------------------------------------------

    def test_notifications(self) -> list[ChannelResult]:
        return self.notifier.test_all()

    def overlaps_game(self, left: int, top: int, right: int, bottom: int) -> bool:
        info = self.state.window or self.tracker.info
        if info is None:
            return False
        r = info.client_rect
        return left < r.right and r.left < right and top < r.bottom and r.top < bottom

    def shutdown(self) -> None:
        self._mode_stop.set()
        self.stop_learn()
        self.stop_mode()
        self.stop_capture()
        self.hotkeys.stop()
        self.notifier.stop()
        self.store.close()
