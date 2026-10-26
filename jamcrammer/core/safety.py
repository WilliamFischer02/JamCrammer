"""Safety primitives: kill switch, pause, input lease, user-presence detection, hotkeys.

KICKOFF §7.4. ``GetLastInputInfo`` counts our own injected input too, so presence detection
compares the OS last-input time against the controller's last injected timestamp.
"""

from __future__ import annotations

import contextlib
import ctypes
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass

from jamcrammer.core.config import HotkeyConfig, Level
from jamcrammer.core.events import EventBus

USER32 = ctypes.windll.user32
KERNEL32 = ctypes.windll.kernel32
MS_PER_S = 1000.0


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = (("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD))


def last_user_input_time() -> float:
    """Wall-clock time (time.time()) of the last input event the OS saw, injected or not."""
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
    if not USER32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    now_tick = int(KERNEL32.GetTickCount64())
    # dwTime is a 32-bit tick; GetTickCount64 avoids our own wrap but dwTime still wraps.
    elapsed_ms = (now_tick - int(info.dwTime)) & 0xFFFFFFFF
    return time.time() - elapsed_ms / MS_PER_S


def is_user_active(last_user_input_ts: float, last_bot_input_ts: float, slack_s: float) -> bool:
    """True if the OS saw input later than our own last injection (plus slack)."""
    return last_user_input_ts > last_bot_input_ts + slack_s


class KillSwitch:
    """Set once by F12 or a fatal condition. Everything that loops checks ``tripped``."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self.reason = ""

    @property
    def tripped(self) -> bool:
        return self._event.is_set()

    def trip(self, reason: str) -> None:
        self.reason = reason
        self._event.set()

    def wait(self, timeout_s: float | None = None) -> bool:
        return self._event.wait(timeout_s)


class PauseFlag:
    def __init__(self) -> None:
        self._paused = threading.Event()
        self.reason = ""

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def pause(self, reason: str) -> None:
        self.reason = reason
        self._paused.set()

    def resume(self) -> None:
        self.reason = ""
        self._paused.clear()

    def toggle(self, reason: str = "hotkey") -> bool:
        if self.paused:
            self.resume()
        else:
            self.pause(reason)
        return self.paused


class InputLease:
    """Exactly one owner may send input. The watchdog can preempt (KICKOFF §5 scheduler)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owner: str | None = None

    @property
    def owner(self) -> str | None:
        return self._owner

    def acquire(self, owner: str) -> bool:
        with self._lock:
            if self._owner in (None, owner):
                self._owner = owner
                return True
            return False

    def preempt(self, owner: str) -> str | None:
        with self._lock:
            previous = self._owner
            self._owner = owner
            return previous

    def release(self, owner: str) -> None:
        with self._lock:
            if self._owner == owner:
                self._owner = None

    def held_by(self, owner: str) -> bool:
        return self._owner == owner


@dataclass
class PresenceDetector:
    """Decides whether William is back at the keyboard."""

    bot_last_input_ts: Callable[[], float]
    slack_s: float
    resume_after_idle_s: float

    def user_active(self) -> bool:
        return is_user_active(last_user_input_time(), self.bot_last_input_ts(), self.slack_s)

    def seconds_since_user_input(self) -> float:
        return max(0.0, time.time() - last_user_input_time())

    def may_resume(self) -> bool:
        return self.seconds_since_user_input() >= self.resume_after_idle_s


class Hotkeys:
    """Global hotkeys via the ``keyboard`` package. Each one publishes an event on the bus."""

    EVENTS: tuple[tuple[str, str], ...] = (
        ("kill", "hotkey.kill"),
        ("pause", "hotkey.pause"),
        ("toggle_mode", "hotkey.toggle_mode"),
        ("learn_mark", "hotkey.learn_mark"),
        ("learn_correct", "hotkey.learn_correct"),
        ("learn_wrong", "hotkey.learn_wrong"),
    )

    def __init__(self, cfg: HotkeyConfig, bus: EventBus) -> None:
        self._cfg = cfg
        self._bus = bus
        self._handles: list[object] = []

    def start(self) -> None:
        import keyboard

        for attr, event_type in self.EVENTS:
            combo = getattr(self._cfg, attr)
            level = Level.HIGH if attr == "kill" else Level.INFO

            def fire(
                event_type: str = event_type, level: Level = level, combo: str = combo
            ) -> None:
                self._bus.emit(event_type, source="hotkeys", level=level, combo=combo)

            self._handles.append(keyboard.add_hotkey(combo, fire, suppress=False))

    def stop(self) -> None:
        import keyboard

        for handle in self._handles:
            with contextlib.suppress(KeyError, ValueError):
                keyboard.remove_hotkey(handle)
        self._handles.clear()
