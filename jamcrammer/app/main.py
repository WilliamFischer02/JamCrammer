"""GUI entry point. M0: a placeholder window proving PySide6 launches from run.bat.

The real shell (nav rail, command bar, pages) lands in M4. Keep this file the single place
``jamcrammer gui`` calls into.
"""

from __future__ import annotations

import sys

from jamcrammer import __version__

WINDOW_TITLE = "JamCrammer"
PLACEHOLDER_SIZE = (960, 600)


def run_gui(argv: list[str] | None = None) -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(WINDOW_TITLE)

    window = QMainWindow()
    window.setWindowTitle(f"{WINDOW_TITLE} {__version__}")
    window.resize(*PLACEHOLDER_SIZE)

    root = QWidget()
    layout = QVBoxLayout(root)
    label = QLabel(
        "JamCrammer M0 placeholder\n\n"
        "The GUI shell arrives in M4.\n"
        "Until then use the CLI:  .venv\\Scripts\\python -m jamcrammer --help"
    )
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(label)
    window.setCentralWidget(root)
    window.show()
    return app.exec()
