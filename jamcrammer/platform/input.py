"""Ordinary OS-level input via SendInput, bounds-checked to the AJC client area.

Rules (KICKOFF §3, §7.4): only click inside the client rect; every action records its
timestamp so presence detection can ignore our own input; dry-run never sends anything;
every click can carry a postcondition check with one retry.
"""

from __future__ import annotations

import ctypes
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Literal

import win32api

from jamcrammer.core.config import Direction, InputConfig, Level
from jamcrammer.core.events import Event, EventBus
from jamcrammer.platform.window import WindowInfo

USER32 = ctypes.windll.user32

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
MAPVK_VK_TO_VSC = 0
CURSOR_TOLERANCE_PX = 1

Button = Literal["left", "right"]
KeyMethod = Literal["scancode", "vk"]

VK_CODES: dict[str, int] = {
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "space": 0x20,
    "enter": 0x0D,
    "escape": 0x1B,
    "tab": 0x09,
    "shift": 0x10,
    "ctrl": 0x11,
    "alt": 0x12,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "insert": 0x2D,
    "delete": 0x2E,
}
VK_CODES.update({chr(c): c for c in range(ord("0"), ord("9") + 1)})
VK_CODES.update({chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)})
EXTENDED_KEYS = frozenset(
    {"left", "up", "right", "down", "home", "end", "pageup", "pagedown", "insert", "delete"}
)

ULONG_PTR = ctypes.c_size_t


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = (
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    )


class _INPUTUNION(ctypes.Union):
    _fields_ = (("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT))


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = (("type", wintypes.DWORD), ("u", _INPUTUNION))


class InputError(RuntimeError):
    pass


class InputRefusedError(InputError):
    """Target outside the client rect, or no window."""


@dataclass
class InputStats:
    clicks: int = 0
    keys: int = 0
    refused: int = 0
    retries: int = 0
    postcondition_failures: int = 0
    last_injected_ts: float = 0.0  # time.time(); 0 = never
    history: list[tuple[float, str]] = field(default_factory=list)


def _send(inputs: list[_INPUT]) -> None:
    arr = (_INPUT * len(inputs))(*inputs)
    sent = USER32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))
    if sent != len(inputs):
        msg = f"SendInput sent {sent}/{len(inputs)} events (error {ctypes.GetLastError()})"
        raise InputError(msg)


def _mouse_input(flags: int) -> _INPUT:
    inp = _INPUT()
    inp.type = INPUT_MOUSE
    inp.mi = _MOUSEINPUT(0, 0, 0, flags, 0, 0)
    return inp


def _key_input(key: str, up: bool, method: KeyMethod) -> _INPUT:
    name = key.lower()
    if name not in VK_CODES:
        msg = f"unknown key {key!r}"
        raise InputError(msg)
    vk = VK_CODES[name]
    flags = KEYEVENTF_KEYUP if up else 0
    if name in EXTENDED_KEYS:
        flags |= KEYEVENTF_EXTENDEDKEY
    inp = _INPUT()
    inp.type = INPUT_KEYBOARD
    if method == "scancode":
        scan = int(USER32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC))
        inp.ki = _KEYBDINPUT(0, scan, flags | KEYEVENTF_SCANCODE, 0, 0)
    else:
        inp.ki = _KEYBDINPUT(vk, 0, flags, 0, 0)
    return inp


class InputController:
    """Sends mouse/keyboard input into the AJC client area. One instance per process."""

    def __init__(
        self,
        window: Callable[[], WindowInfo | None],
        cfg: InputConfig,
        dry_run: bool = False,
        bus: EventBus | None = None,
    ) -> None:
        self._window = window
        self._cfg = cfg
        self.dry_run = dry_run
        self._bus = bus
        self.stats = InputStats()

    # --- helpers -----------------------------------------------------------------------

    def _touch(self, what: str) -> None:
        now = time.time()
        self.stats.last_injected_ts = now
        self.stats.history.append((now, what))
        if len(self.stats.history) > self._cfg.history_len:
            del self.stats.history[: -self._cfg.history_len]

    def _emit(self, event_type: str, level: Level = Level.INFO, **payload: object) -> None:
        if self._bus is not None:
            self._bus.publish(
                Event(type=event_type, source="input", level=level, payload=dict(payload))
            )

    def to_screen(self, cx: int, cy: int) -> tuple[int, int]:
        """Client → screen with the bounds check. Raises InputRefusedError outside the client."""
        info = self._window()
        if info is None:
            self.stats.refused += 1
            self._emit("input.refused", Level.NOTICE, reason="no_window", cx=cx, cy=cy)
            msg = "no AJC window; refusing input"
            raise InputRefusedError(msg)
        w, h = info.client_size
        if not (0 <= cx < w and 0 <= cy < h):
            self.stats.refused += 1
            self._emit("input.refused", Level.NOTICE, reason="out_of_bounds", cx=cx, cy=cy)
            msg = f"({cx},{cy}) outside client area {w}x{h}; refusing input"
            raise InputRefusedError(msg)
        return info.to_screen(cx, cy)

    # --- mouse -------------------------------------------------------------------------

    def move(self, cx: int, cy: int) -> bool:
        sx, sy = self.to_screen(cx, cy)
        if self.dry_run:
            self._emit("input.move", dry_run=True, cx=cx, cy=cy)
            return True
        for attempt in range(self._cfg.postcondition_retries + 1):
            win32api.SetCursorPos((sx, sy))
            self._touch("move")
            time.sleep(self._cfg.cursor_settle_ms / 1000.0)
            px, py = win32api.GetCursorPos()
            if abs(px - sx) <= CURSOR_TOLERANCE_PX and abs(py - sy) <= CURSOR_TOLERANCE_PX:
                return True
            self.stats.retries += attempt < self._cfg.postcondition_retries
        self._emit("input.move_failed", Level.NOTICE, cx=cx, cy=cy)
        return False

    def click(
        self,
        cx: int,
        cy: int,
        *,
        button: Button = "left",
        hold_ms: int | None = None,
        verify: Callable[[], bool] | None = None,
        retries: int | None = None,
    ) -> bool:
        """Move, press, release. If ``verify`` is given, call it; retry once on False."""
        attempts = (self._cfg.postcondition_retries if retries is None else retries) + 1
        hold = (self._cfg.click_hold_ms if hold_ms is None else hold_ms) / 1000.0
        down, up = (
            (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP)
            if button == "left"
            else (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP)
        )
        for attempt in range(attempts):
            if not self.move(cx, cy):
                continue
            if self.dry_run:
                self._emit("input.click", dry_run=True, cx=cx, cy=cy, button=button)
                return True
            _send([_mouse_input(down)])
            time.sleep(hold)
            _send([_mouse_input(up)])
            self.stats.clicks += 1
            self._touch(f"click:{button}")
            self._emit("input.click", cx=cx, cy=cy, button=button, attempt=attempt)
            if verify is None or verify():
                return True
            self.stats.postcondition_failures += 1
            if attempt + 1 < attempts:
                self.stats.retries += 1
                self._emit("input.retry", Level.NOTICE, cx=cx, cy=cy, attempt=attempt + 1)
        return False

    # --- keyboard ----------------------------------------------------------------------

    def key_down(self, key: str, method: KeyMethod | None = None) -> None:
        self._key(key, up=False, method=method)

    def key_up(self, key: str, method: KeyMethod | None = None) -> None:
        self._key(key, up=True, method=method)

    def _key(self, key: str, up: bool, method: KeyMethod | None) -> None:
        if self._window() is None:
            self.stats.refused += 1
            msg = "no AJC window; refusing key"
            raise InputRefusedError(msg)
        inp = _key_input(key, up, method or self._cfg.key_method)
        if self.dry_run:
            self._emit("input.key", dry_run=True, key=key, up=up)
            return
        _send([inp])
        self.stats.keys += 1
        self._touch(f"key:{key}:{'up' if up else 'down'}")

    def key_tap(self, key: str, hold_ms: int, method: KeyMethod | None = None) -> None:
        self.key_down(key, method)
        time.sleep(hold_ms / 1000.0)
        self.key_up(key, method)
        self._emit("input.key_tap", key=key, hold_ms=hold_ms)

    def movement_pair(
        self,
        pair: tuple[Direction, Direction],
        tap_ms: int,
        gap_ms: int,
        method: KeyMethod | None = None,
    ) -> None:
        """A cancelling pair: tap one direction, wait, tap its opposite (KICKOFF §7.2)."""
        first, second = pair
        self.key_tap(first, tap_ms, method)
        time.sleep(gap_ms / 1000.0)
        self.key_tap(second, tap_ms, method)
        self._emit("input.movement_pair", pair=list(pair), tap_ms=tap_ms, gap_ms=gap_ms)
