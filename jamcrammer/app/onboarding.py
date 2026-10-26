"""First-run onboarding wizard (KICKOFF §12.3): window → DPI → capture → input test → notify →
parking spot → done."""

from __future__ import annotations

import cv2
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QCheckBox, QLabel, QLineEdit, QVBoxLayout, QWizard, QWizardPage

from jamcrammer.app import theme
from jamcrammer.app.engine import Engine
from jamcrammer.app.widgets import icon_button, row

PREVIEW_W = 560


def _page(title: str, body: str) -> tuple[QWizardPage, QVBoxLayout]:
    page = QWizardPage()
    page.setTitle("")
    lay = QVBoxLayout(page)
    lay.setSpacing(theme.PAD_MD)
    t = QLabel(title)
    t.setObjectName("WizardTitle")
    b = QLabel(body)
    b.setWordWrap(True)
    b.setObjectName("PageSubtitle")
    lay.addWidget(t)
    lay.addWidget(b)
    return page, lay


class OnboardingWizard(QWizard):
    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self.engine = engine
        self.setWindowTitle("JamCrammer setup")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.resize(760, 560)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)

        # 1. window
        p1, l1 = _page(
            "1 · Find Animal Jam Classic",
            "JamCrammer needs the AJC desktop app open. It finds the window by its exact title and process name.",
        )
        self.win_label = QLabel("Not checked yet")
        l1.addWidget(self.win_label)
        l1.addLayout(
            row(icon_button("refresh", "Detect window", on_click=self._detect), stretch_last=True)
        )
        l1.addStretch(1)
        self.addPage(p1)

        # 2. dpi / monitors
        p2, l2 = _page(
            "2 · Monitor and scaling",
            "Keep AJC on its own monitor, unobstructed: input lands wherever the cursor is. Fullscreen (1920x1080 at 100%) is ideal; maximized windows are mapped automatically.",
        )
        self.dpi_label = QLabel("")
        l2.addWidget(self.dpi_label)
        l2.addStretch(1)
        self.addPage(p2)

        # 3. capture test
        p3, l3 = _page(
            "3 · Test capture", "One frame of the client, normalized to the game's 1920x1080 stage."
        )
        self.preview = QLabel("Press Snap")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(PREVIEW_W, PREVIEW_W * 9 // 16)
        l3.addWidget(self.preview)
        l3.addLayout(row(icon_button("camera", "Snap", on_click=self._snap), stretch_last=True))
        self.addPage(p3)

        # 4. input test (consent)
        p4, l4 = _page(
            "4 · Input test",
            "With your consent JamCrammer sends one cancelling movement pair (left, then right) to the game and checks that the animal moved. Nothing else is sent. Watch the game while it runs.",
        )
        self.consent = QCheckBox("I understand; send the movement pair now")
        self.input_result = QLabel("")
        self.input_result.setWordWrap(True)
        l4.addWidget(self.consent)
        l4.addLayout(
            row(
                icon_button("keyboard", "Run input test", on_click=self._input_test),
                stretch_last=True,
            )
        )
        l4.addWidget(self.input_result)
        l4.addStretch(1)
        self.addPage(p4)

        # 5. notifications
        p5, l5 = _page(
            "5 · Notifications",
            "Sound always works. Phone pushes need an ntfy.sh topic (install the ntfy app, subscribe to the topic). Toast banners are hidden while AJC is fullscreen.",
        )
        self.ntfy = QLineEdit(engine.cfg.notify.ntfy_topic)
        self.ntfy.setPlaceholderText("ntfy topic, e.g. jamcrammer-william-7f3a")
        self.ntfy.textChanged.connect(lambda v: setattr(engine.cfg.notify, "ntfy_topic", v.strip()))
        self.notify_result = QLabel("")
        l5.addWidget(self.ntfy)
        l5.addLayout(
            row(
                icon_button("bell-ring", "Send test", on_click=self._notify),
                self.notify_result,
                stretch_last=True,
            )
        )
        l5.addStretch(1)
        self.addPage(p5)

        # 6. parking spot
        p6, l6 = _page(
            "6 · Parking spot",
            "Before leaving the bot alone, park your animal in your own den (locked), standing mid-room away from the exit. Use a land animal; underwater animals render broken in Best Guess.",
        )
        self.park = QCheckBox("I'll park in my locked den before starting a long run")
        self.park.setChecked(True)
        l6.addWidget(self.park)
        l6.addStretch(1)
        self.addPage(p6)

        # 7. done
        p7, l7 = _page(
            "7 · Ready",
            "Dashboard shows the live preview. Learn records your play. Best Guess hunts and alerts. F12 kills everything, F11 pauses, F9 starts or stops the selected mode.",
        )
        l7.addStretch(1)
        self.addPage(p7)

    def _detect(self) -> None:
        info = self.engine.refresh_window()
        if info is None:
            self.win_label.setText("Not found. Open Animal Jam Classic and try again.")
            return
        w, h = info.client_size
        self.win_label.setText(
            f"Found: {info.title} ({info.process_name}) · client {w}x{h} · DPI {info.dpi}"
        )
        self.dpi_label.setText(
            f"Client {w}x{h}, DPI {info.dpi} ({info.scale:.0%}). Stage scale {min(w / 1920, h / 1080):.3f}."
        )

    def _snap(self) -> None:
        if not self.engine.start_capture():
            self.preview.setText(self.engine.state.last_error or "capture failed")
            return
        frame = self.engine.grab()
        img = cv2.cvtColor(
            cv2.resize(frame.image, (PREVIEW_W, PREVIEW_W * 9 // 16), interpolation=cv2.INTER_AREA),
            cv2.COLOR_BGR2RGB,
        )
        q = QImage(
            img.data, img.shape[1], img.shape[0], img.strides[0], QImage.Format.Format_RGB888
        )
        self.preview.setPixmap(QPixmap.fromImage(q.copy()))

    def _input_test(self) -> None:
        if not self.consent.isChecked():
            self.input_result.setText("Tick the consent box first.")
            return
        from jamcrammer.vision.match import mean_abs_diff

        if not self.engine.start_capture() or not self.engine.focus_game():
            self.input_result.setText("Could not focus the game window.")
            return
        import time

        before = self.engine.grab().image
        pair = self.engine.cfg.antiafk.pattern[0]
        self.engine.input.key_tap(pair[0], self.engine.cfg.antiafk.tap_ms)
        time.sleep(0.05)
        mid = self.engine.grab().image
        time.sleep(self.engine.cfg.antiafk.gap_ms / 1000.0)
        self.engine.input.key_tap(pair[1], self.engine.cfg.antiafk.tap_ms)
        time.sleep(0.6)
        after = self.engine.grab().image
        d1, d2 = mean_abs_diff(before, mid), mean_abs_diff(before, after)
        verdict = "moved" if d1 > 3.0 else "no visible movement (try key_method: vk in config)"
        self.input_result.setText(
            f"before→mid diff {d1:.2f}, before→after diff {d2:.2f}: {verdict}"
        )

    def _notify(self) -> None:
        results = self.engine.test_notifications()
        self.notify_result.setText(
            "  ".join(
                f"{r.channel}:{'ok' if r.ok else ('off' if not r.enabled else 'fail')}"
                for r in results
            )
        )
