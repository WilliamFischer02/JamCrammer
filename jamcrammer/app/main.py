"""GUI entry point: theme, engine, main window, first-run onboarding."""

from __future__ import annotations

import sys

from jamcrammer.core.config import load_config
from jamcrammer.core.paths import Paths

WINDOW_TITLE = "JamCrammer"
ONBOARDED_FLAG = "onboarded"


def run_gui(argv: list[str] | None = None, show_onboarding: bool | None = None) -> int:
    from jamcrammer.platform.window import ensure_dpi_awareness

    ensure_dpi_awareness()
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from jamcrammer.app import theme
    from jamcrammer.app.engine import Engine
    from jamcrammer.app.main_window import MainWindow
    from jamcrammer.app.onboarding import OnboardingWizard

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(WINDOW_TITLE)
    app.setStyle("Fusion")
    font = QFont(theme.FONT_FAMILY, theme.FONT_SIZES["sm"] * 72 // 96)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    app.setFont(font)
    app.setStyleSheet(theme.build_qss())

    paths = Paths()
    cfg = load_config(paths)
    engine = Engine(cfg, paths)
    window = MainWindow(engine, paths)
    window.show()

    flag = paths.data / ONBOARDED_FLAG
    first_run = show_onboarding if show_onboarding is not None else not flag.exists()
    if first_run:
        wizard = OnboardingWizard(engine)
        if wizard.exec():
            flag.write_text("done\n", encoding="utf-8")
    return app.exec()
