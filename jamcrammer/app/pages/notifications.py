"""Notifications: per-channel toggles with test buttons, levels, ntfy topic, Discord webhook."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QLabel, QLineEdit

from jamcrammer.app import theme
from jamcrammer.app.engine import Engine
from jamcrammer.app.widgets import Card, Page, icon_button, row
from jamcrammer.core.config import Level


class NotificationsPage(Page):
    key = "notifications"
    title = "Notifications"
    subtitle = "INFO = log only · NOTICE = toast · HIGH = toast + repeating sound + push. Each channel has a test button."
    icon = "notifications"

    def __init__(self, engine: Engine, window: Any) -> None:
        super().__init__()
        self.engine = engine
        n = engine.cfg.notify
        channels = Card("Channels")
        form = QFormLayout()
        self.toast = QCheckBox("Windows toast")
        self.toast.setChecked(n.toast)
        self.toast.toggled.connect(lambda v: setattr(n, "toast", v))
        self.sound = QCheckBox("Sound (repeats for HIGH)")
        self.sound.setChecked(n.sound)
        self.sound.toggled.connect(lambda v: setattr(n, "sound", v))
        self.ntfy = QLineEdit(n.ntfy_topic)
        self.ntfy.setPlaceholderText("ntfy.sh topic (empty = off)")
        self.ntfy.textChanged.connect(lambda v: setattr(n, "ntfy_topic", v.strip()))
        self.discord = QLineEdit(n.discord_webhook)
        self.discord.setPlaceholderText("Discord webhook URL (empty = off)")
        self.discord.setEchoMode(QLineEdit.EchoMode.Password)
        self.discord.textChanged.connect(lambda v: setattr(n, "discord_webhook", v.strip()))
        form.addRow("", self.toast)
        form.addRow("", self.sound)
        form.addRow("ntfy topic", self.ntfy)
        form.addRow("Discord webhook", self.discord)
        channels.add_layout(form)
        self.result = QLabel("")
        self.result.setObjectName("CardHelp")
        channels.add_layout(
            row(
                icon_button(
                    "bell-ring",
                    "Test all channels",
                    kind="primary",
                    color=theme.PALETTE.bg,
                    on_click=self.test_all,
                ),
                self.result,
                stretch_last=True,
            )
        )
        note = QLabel(
            "Toast banners are hidden by Windows while AJC is fullscreen (Focus Assist); they still land in the Action Center. Sound and push are the alerts that reach you."
        )
        note.setObjectName("CardHelp")
        note.setWordWrap(True)
        channels.add(note)
        self.content.addWidget(channels)

        levels = Card("Minimum level per channel")
        form2 = QFormLayout()
        for attr, label in (
            ("toast", "Toast"),
            ("sound", "Sound"),
            ("push", "Push (ntfy + Discord)"),
        ):
            box = QComboBox()
            for lv in Level:
                box.addItem(str(lv))
            box.setCurrentText(str(getattr(n.levels, attr)))
            box.currentTextChanged.connect(lambda v, a=attr: setattr(n.levels, a, Level(v)))
            form2.addRow(label, box)
        levels.add_layout(form2)
        self.content.addWidget(levels)
        self.finish()

    def test_all(self) -> None:
        results = self.engine.test_notifications()
        self.result.setText(
            "  ·  ".join(
                f"{r.channel}: {'ok' if r.ok else ('off' if not r.enabled else 'FAIL')}"
                for r in results
            )
        )
