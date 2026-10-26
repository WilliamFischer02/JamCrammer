"""AJC window discovery and geometry (Win32).

Facts this module relies on (discovered 2026-10-26, see CLAUDE.md): the client is an Electron
app, title exactly ``Animal Jam Classic``, process ``AJ Classic.exe``. A browser tab with a
title that *starts* with the same words also exists, so matching is exact-title plus
process-name preference plus browser-class exclusion.
"""

from __future__ import annotations

import contextlib
import ctypes
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import asdict, dataclass
from typing import Any

import win32con
import win32gui
import win32process

USER32 = ctypes.windll.user32
KERNEL32 = ctypes.windll.kernel32

DEFAULT_DPI = 96
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MAX_IMAGE_PATH = 1024
FOREGROUND_POLL_S = 0.02
# Chrome_WidgetWin_1 is shared by Chrome and Electron, so it cannot be excluded by class.
BROWSER_CLASSES = frozenset({"MozillaWindowClass", "ApplicationFrameWindow", "IEFrame"})


class WindowNotFoundError(RuntimeError):
    """The AJC window is not on screen."""


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.right, self.bottom)


@dataclass(frozen=True)
class WindowCandidate:
    """Pure data about one visible top-level window; selection logic is testable on these."""

    hwnd: int
    title: str
    class_name: str
    pid: int
    process_name: str


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    class_name: str
    pid: int
    process_name: str
    window_rect: Rect
    client_rect: Rect  # screen coordinates
    dpi: int

    @property
    def scale(self) -> float:
        return self.dpi / DEFAULT_DPI

    @property
    def client_size(self) -> tuple[int, int]:
        return self.client_rect.size

    def to_client(self, sx: int, sy: int) -> tuple[int, int]:
        return (sx - self.client_rect.left, sy - self.client_rect.top)

    def to_screen(self, cx: int, cy: int) -> tuple[int, int]:
        return (cx + self.client_rect.left, cy + self.client_rect.top)

    def contains_screen_point(self, sx: int, sy: int) -> bool:
        return self.client_rect.contains(sx, sy)

    def same_geometry(self, other: WindowInfo) -> bool:
        return (
            self.hwnd == other.hwnd
            and self.window_rect == other.window_rect
            and self.client_rect == other.client_rect
            and self.dpi == other.dpi
        )

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["window_rect"] = self.window_rect.as_tuple()
        d["client_rect"] = self.client_rect.as_tuple()
        d["scale"] = self.scale
        return d


def ensure_dpi_awareness() -> bool:
    """Make this process per-monitor DPI aware (v2) so every coordinate is a physical pixel.

    Must run before Qt or mss touch the display. Returns True if awareness is now set.
    """
    try:
        ok = bool(
            USER32.SetProcessDpiAwarenessContext(
                ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
            )
        )
    except (AttributeError, OSError):
        ok = False
    if not ok:
        # Already set (e.g. by Qt) or old OS; fall back to system awareness.
        try:
            USER32.SetProcessDPIAware()
        except (AttributeError, OSError):
            return False
    return True


def process_name_of(pid: int) -> str:
    handle = KERNEL32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(MAX_IMAGE_PATH)
        size = wintypes.DWORD(MAX_IMAGE_PATH)
        if KERNEL32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1]
        return ""
    finally:
        KERNEL32.CloseHandle(handle)


def get_dpi_for_window(hwnd: int) -> int:
    try:
        dpi = int(USER32.GetDpiForWindow(hwnd))
    except (AttributeError, OSError):
        dpi = 0
    return dpi or DEFAULT_DPI


def enumerate_candidates() -> list[WindowCandidate]:
    out: list[WindowCandidate] = []

    def cb(hwnd: int, _: Any) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        out.append(
            WindowCandidate(
                hwnd=hwnd,
                title=title,
                class_name=win32gui.GetClassName(hwnd) or "",
                pid=pid,
                process_name=process_name_of(pid),
            )
        )
        return True

    win32gui.EnumWindows(cb, None)
    return out


def select_window(
    candidates: list[WindowCandidate], title: str, process_name: str
) -> WindowCandidate | None:
    """Exact title, never a browser class, prefer the configured process name."""
    exact = [c for c in candidates if c.title == title and c.class_name not in BROWSER_CLASSES]
    by_process = [c for c in exact if c.process_name.lower() == process_name.lower()]
    if by_process:
        return by_process[0]
    if exact:
        return exact[0]
    return None


def inspect_window(c: WindowCandidate) -> WindowInfo:
    left, top, right, bottom = win32gui.GetWindowRect(c.hwnd)
    _, _, cw, ch = win32gui.GetClientRect(c.hwnd)
    sx, sy = win32gui.ClientToScreen(c.hwnd, (0, 0))
    return WindowInfo(
        hwnd=c.hwnd,
        title=c.title,
        class_name=c.class_name,
        pid=c.pid,
        process_name=c.process_name,
        window_rect=Rect(left, top, right, bottom),
        client_rect=Rect(sx, sy, sx + cw, sy + ch),
        dpi=get_dpi_for_window(c.hwnd),
    )


def refresh_window(info: WindowInfo) -> WindowInfo | None:
    """Re-read geometry for a known hwnd; None if the window is gone."""
    if not win32gui.IsWindow(info.hwnd) or not win32gui.IsWindowVisible(info.hwnd):
        return None
    candidate = WindowCandidate(
        hwnd=info.hwnd,
        title=win32gui.GetWindowText(info.hwnd),
        class_name=info.class_name,
        pid=info.pid,
        process_name=info.process_name,
    )
    return inspect_window(candidate)


def find_ajc_window(title: str, process_name: str) -> WindowInfo | None:
    chosen = select_window(enumerate_candidates(), title, process_name)
    return inspect_window(chosen) if chosen else None


def is_foreground(hwnd: int) -> bool:
    return bool(win32gui.GetForegroundWindow() == hwnd)


def bring_to_foreground(hwnd: int, timeout_s: float) -> bool:
    """Focus the window and verify. Uses AttachThreadInput, then the ALT-tap workaround."""
    if is_foreground(hwnd):
        return True
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

    fg = win32gui.GetForegroundWindow()
    fg_thread = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
    me = int(KERNEL32.GetCurrentThreadId())
    attached = bool(fg_thread and fg_thread != me and USER32.AttachThreadInput(me, fg_thread, True))
    try:
        _try_set_foreground(hwnd)
        if _wait_foreground(hwnd, timeout_s / 2):
            return True
        USER32.keybd_event(win32con.VK_MENU, 0, 0, 0)
        USER32.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        _try_set_foreground(hwnd)
        return _wait_foreground(hwnd, timeout_s / 2)
    finally:
        if attached:
            USER32.AttachThreadInput(me, fg_thread, False)


def _try_set_foreground(hwnd: int) -> None:
    # pywintypes.error when the OS refuses; the caller verifies afterwards.
    with contextlib.suppress(Exception):
        win32gui.SetForegroundWindow(hwnd)


def _wait_foreground(hwnd: int, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if is_foreground(hwnd):
            return True
        time.sleep(FOREGROUND_POLL_S)
    return is_foreground(hwnd)


GeometryChange = str  # "lost" | "moved" | "resized" | "dpi"


def classify_change(before: WindowInfo, after: WindowInfo | None) -> GeometryChange | None:
    if after is None:
        return "lost"
    if after.dpi != before.dpi:
        return "dpi"
    if after.client_rect.size != before.client_rect.size:
        return "resized"
    if after.client_rect != before.client_rect or after.window_rect != before.window_rect:
        return "moved"
    return None


class WindowTracker:
    """Holds the current WindowInfo and reports geometry changes between polls."""

    def __init__(self, title: str, process_name: str) -> None:
        self._title = title
        self._process_name = process_name
        self._info: WindowInfo | None = None

    @property
    def info(self) -> WindowInfo | None:
        return self._info

    def require(self) -> WindowInfo:
        info = self._info or self.refresh()
        if info is None:
            msg = f"window titled {self._title!r} ({self._process_name}) not found"
            raise WindowNotFoundError(msg)
        return info

    def refresh(self) -> WindowInfo | None:
        if self._info is not None:
            fresh = refresh_window(self._info)
            if fresh is not None:
                self._info = fresh
                return fresh
        self._info = find_ajc_window(self._title, self._process_name)
        return self._info

    def poll(self) -> GeometryChange | None:
        """Refresh and return what changed since the previous poll (None if nothing)."""
        before = self._info
        after = self.refresh()
        if before is None:
            return None
        return classify_change(before, after)

    def provider(self) -> Callable[[], WindowInfo | None]:
        return lambda: self._info
