from __future__ import annotations

from jamcrammer.platform.window import (
    Rect,
    WindowCandidate,
    WindowInfo,
    classify_change,
    select_window,
)

AJC = WindowCandidate(1, "Animal Jam Classic", "Chrome_WidgetWin_1", 40924, "AJ Classic.exe")
FIREFOX = WindowCandidate(
    2,
    "Animal Jam Classic farming - Claude - Mozilla Firefox",
    "MozillaWindowClass",
    5908,
    "firefox.exe",
)
FIREFOX_EXACT = WindowCandidate(3, "Animal Jam Classic", "MozillaWindowClass", 5908, "firefox.exe")
CHROME_EXACT = WindowCandidate(4, "Animal Jam Classic", "Chrome_WidgetWin_1", 77, "chrome.exe")


def test_select_prefers_process_name() -> None:
    assert (
        select_window([FIREFOX, CHROME_EXACT, AJC], "Animal Jam Classic", "AJ Classic.exe") == AJC
    )


def test_select_requires_exact_title() -> None:
    assert select_window([FIREFOX], "Animal Jam Classic", "AJ Classic.exe") is None


def test_select_rejects_browser_class_even_with_exact_title() -> None:
    assert select_window([FIREFOX_EXACT], "Animal Jam Classic", "AJ Classic.exe") is None


def test_select_falls_back_to_exact_title_when_process_differs() -> None:
    assert select_window([CHROME_EXACT], "Animal Jam Classic", "AJ Classic.exe") == CHROME_EXACT


def _info(client: Rect, window: Rect | None = None, dpi: int = 96) -> WindowInfo:
    return WindowInfo(
        hwnd=1,
        title="Animal Jam Classic",
        class_name="Chrome_WidgetWin_1",
        pid=1,
        process_name="AJ Classic.exe",
        window_rect=window or client,
        client_rect=client,
        dpi=dpi,
    )


def test_rect_and_coordinate_mapping() -> None:
    info = _info(Rect(100, 50, 1100, 650))
    assert info.client_size == (1000, 600)
    assert info.to_client(100, 50) == (0, 0)
    assert info.to_screen(10, 20) == (110, 70)
    assert info.contains_screen_point(1099, 649)
    assert not info.contains_screen_point(1100, 649)


def test_classify_change() -> None:
    base = _info(Rect(0, 0, 1920, 1080))
    assert classify_change(base, None) == "lost"
    assert classify_change(base, _info(Rect(0, 0, 1920, 1080))) is None
    assert classify_change(base, _info(Rect(10, 0, 1930, 1080))) == "moved"
    assert classify_change(base, _info(Rect(0, 0, 1280, 720))) == "resized"
    assert classify_change(base, _info(Rect(0, 0, 1920, 1080), dpi=144)) == "dpi"
