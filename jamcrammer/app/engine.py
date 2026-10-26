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

log = get_logger("engine")

MODES = ("hunt", "farm", "opportunist")
MODE_LABELS = {"hunt": "Best Guess Hunter", "farm": "Arcade Farmer", "opportunist": "Opportunist"}
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
        return bool(info and bring_to_foreground(info.hwnd, self.cfg.input.foreground_timeout_s))

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
        self._mode_stop.clear()
        self.state.mode = name
        self.state.mode_state = "STARTING"
        self.state.mode_detail = ""
        target = {"hunt": self._run_hunt}.get(name, self._run_placeholder)
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

    def _should_stop(self) -> bool:
        return self._mode_stop.is_set() or self.kill.tripped

    def _status(self, text: str) -> None:
        parts = text.split(" · ", 1)
        self.state.mode_state = parts[0]
        self.state.mode_detail = parts[1] if len(parts) > 1 else ""

    def _run_hunt(self) -> None:
        from jamcrammer.modes.bestguess.hunter import Hunter
        from jamcrammer.modes.party_watcher import BannerLibrary, PanelAnalyzer, PartyWatcher

        analyzer = PanelAnalyzer(
            self.profile("parties"), BannerLibrary(self.paths.assets / "banners"), paths=self.paths
        )
        watcher = PartyWatcher(
            analyzer,
            self.cfg.hunt,
            self.input,
            lambda: self.grab().image,
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
        self._mode_obj = hunter
        self.lease.acquire("hunt")
        hunter.run(lambda: self._should_stop() or self.pause.paused, self._status)
        while hunter.stats.joined and not self._should_stop():
            self._status("JOINED · recording")
            time.sleep(PREVIEW_IDLE_S)
        self.state.stats["hunt"] = hunter.stats.__dict__.copy()

    def _on_joined(self, _banner: Any) -> None:
        if self._recorder is None:
            self.start_learn("bestguess", name="autojoin")

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
