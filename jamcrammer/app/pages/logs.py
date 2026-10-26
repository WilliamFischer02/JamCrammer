"""Logs: filterable live event log with copy and export."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QApplication, QComboBox, QFileDialog, QLineEdit, QPlainTextEdit

from jamcrammer.app import theme
from jamcrammer.app.engine import Engine
from jamcrammer.app.widgets import Card, Page, icon_button, row
from jamcrammer.core.config import Level
from jamcrammer.core.events import Event

MAX_LINES = 5000


class LogsPage(Page):
    key = "logs"
    title = "Logs"
    subtitle = "Every event on the bus. Files: data/logs/jamcrammer.log and jamcrammer.jsonl."
    icon = "logs"

    def __init__(self, engine: Engine, window: Any) -> None:
        super().__init__()
        self.engine = engine
        self._lines: list[tuple[str, str, str]] = []  # (level, text, source)
        card = Card("Live log")
        self.level = QComboBox()
        for lv in ("ALL", *[str(x) for x in Level]):
            self.level.addItem(lv)
        self.level.currentTextChanged.connect(lambda _t: self.rebuild())
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by text or source…")
        self.filter.textChanged.connect(lambda _t: self.rebuild())
        card.add_layout(
            row(
                self.level,
                self.filter,
                icon_button("export", "Copy", on_click=self.copy),
                icon_button("export", "Export", on_click=self.export),
            )
        )
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMinimumHeight(560)
        self.view.setMaximumBlockCount(MAX_LINES)
        card.add(self.view)
        self.content.addWidget(card)
        self.finish()

    def on_event(self, event: Event) -> None:
        stamp = time.strftime("%H:%M:%S", time.localtime(event.ts))
        payload = " ".join(f"{k}={v}" for k, v in event.payload.items())
        text = f"{stamp}  {event.level!s:<6}  {event.source:<10}  {event.type}  {payload}"
        self._lines.append((str(event.level), text, event.source))
        if len(self._lines) > MAX_LINES:
            del self._lines[: len(self._lines) - MAX_LINES]
        if self._passes(str(event.level), text):
            self._append(str(event.level), text)

    def _passes(self, level: str, text: str) -> bool:
        want = self.level.currentText()
        if want not in ("ALL", level):
            return False
        q = self.filter.text().strip().lower()
        return not q or q in text.lower()

    def _append(self, level: str, text: str) -> None:
        fmt = QTextCharFormat()
        fmt.setForeground(
            QColor(theme.PALETTE.for_level(level) if level != "INFO" else theme.PALETTE.text)
        )
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text + "\n", fmt)
        self.view.setTextCursor(cursor)

    def rebuild(self) -> None:
        self.view.clear()
        for level, text, _src in self._lines:
            if self._passes(level, text):
                self._append(level, text)

    def copy(self) -> None:
        QApplication.clipboard().setText(self.view.toPlainText())

    def export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export log", "jamcrammer_log.txt", "Text (*.txt)"
        )
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.view.toPlainText())
