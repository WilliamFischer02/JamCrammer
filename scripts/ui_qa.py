"""UI QA: launch the GUI, visit every page (plus the HUD and onboarding), screenshot each one.

    .venv\\Scripts\\python scripts\\ui_qa.py            # 100% scaling → docs/ui/100/
    set QT_SCALE_FACTOR=1.5 && .venv\\Scripts\\python scripts\\ui_qa.py   # 150% → docs/ui/150/

The window is shown on screen (Qt needs a real display for crisp DPI rendering); it closes
itself when done. Screenshots are taken with ``grab()`` at the device pixel ratio.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SETTLE_MS = 700


def main() -> int:
    from jamcrammer.platform.window import ensure_dpi_awareness

    ensure_dpi_awareness()
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from jamcrammer.app import theme
    from jamcrammer.app.engine import Engine
    from jamcrammer.app.main_window import MainWindow
    from jamcrammer.app.onboarding import OnboardingWizard
    from jamcrammer.core.config import load_config
    from jamcrammer.core.paths import Paths

    scale = os.environ.get("QT_SCALE_FACTOR", "1")
    tag = str(round(float(scale) * 100))
    out = ROOT / "docs" / "ui" / tag
    out.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    font = QFont(theme.FONT_FAMILY, theme.FONT_SIZES["sm"] * 72 // 96)
    app.setFont(font)
    app.setStyleSheet(theme.build_qss())
    paths = Paths()
    engine = Engine(load_config(paths), paths)
    window = MainWindow(engine, paths)
    window.move(-1900, 100)  # the second monitor, away from the game
    window.show()
    engine.start_capture()
    wizard = OnboardingWizard(engine)
    hud = window.hud

    steps: list[tuple[str, object]] = []
    for i, page in enumerate(window.pages):
        steps.append((page.key, lambda i=i: window.show_page(i)))
    steps.append(("hud", lambda: (window.hud_action.setChecked(True), hud.move(-1500, 900))))
    steps.append(("onboarding", lambda: (wizard.show(), wizard._detect())))
    results: list[str] = []
    state = {"i": 0, "shot": False}

    def step() -> None:
        i = state["i"]
        if i >= len(steps):
            wizard.close()
            window.close()
            app.quit()
            return
        key, action = steps[i]
        if not state["shot"]:
            action()
            state["shot"] = True
            QTimer.singleShot(SETTLE_MS, step)
            return
        target = {"hud": hud, "onboarding": wizard}.get(key, window)
        pm = target.grab()
        path = out / f"{key}.png"
        pm.save(str(path))
        results.append(f"{key}: {pm.width()}x{pm.height()} dpr={pm.devicePixelRatio():.2f}")
        if key == "onboarding":
            for n in range(1, 7):
                wizard.next()
                app.processEvents()
                wizard.grab().save(str(out / f"onboarding_{n + 1}.png"))
        state["i"] = i + 1
        state["shot"] = False
        QTimer.singleShot(150, step)

    QTimer.singleShot(1500, step)
    code = app.exec()
    print("\n".join(results))
    print(f"saved {len(results)} screenshots to {out}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
