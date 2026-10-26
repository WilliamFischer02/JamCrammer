"""Learn page: target picker, Start Learning, live stats, inbox, sessions, review wizard entry."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QWidget,
)

from jamcrammer.app import theme
from jamcrammer.app.engine import Engine
from jamcrammer.app.widgets import Card, Page, icon_button, row
from jamcrammer.core.events import Event

TARGETS = ("bestguess", "double_up", "jamaa_derby", "gem_ball", "parties", "custom")
INBOX_FOR = {
    "bestguess": "bestguess",
    "double_up": "arcade/double_up",
    "jamaa_derby": "arcade/jamaa_derby",
    "gem_ball": "arcade/gem_ball",
    "parties": "parties",
    "custom": "arcade/other",
}


class DropArea(QLabel):
    def __init__(self, on_files: Any) -> None:
        super().__init__("Drop PNG / MP4 / MKV files here to import them into the inbox")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(90)
        self.setObjectName("CardHelp")
        self.setAcceptDrops(True)
        self._on_files = on_files

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:  # noqa: N802 - Qt override
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent) -> None:  # noqa: N802 - Qt override
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        self._on_files(paths)


class LearnPage(Page):
    key = "learn"
    title = "Learn"
    subtitle = "First contact: play a game manually while JamCrammer records everything it needs."
    icon = "learn"

    def __init__(self, engine: Engine, window: Any) -> None:
        super().__init__()
        self.engine = engine
        rec = Card(
            "Record live play",
            "Nothing is sent to the game while learning. F6 mark, F7 correct, F8 wrong, F12 stop.",
        )
        form = QFormLayout()
        self.target = QComboBox()
        for t in TARGETS:
            self.target.addItem(t)
        form.addRow("Target", self.target)
        rec.add_layout(form)
        self.start = icon_button(
            "play", "Start Learning", color=theme.PALETTE.bg, kind="primary", on_click=self._start
        )
        self.start.setMinimumHeight(44)
        self.stop = icon_button("stop", "Stop", on_click=self._stop)
        self.mark_btns = [self._mark_button(k) for k in ("mark", "correct", "wrong")]
        rec.add_layout(row(self.start, self.stop, *self.mark_btns, stretch_last=True))
        self.stats = QLabel("Not recording")
        self.stats.setObjectName("CardHelp")
        rec.add(self.stats)
        self.content.addWidget(rec)

        inbox = Card(
            "Inbox", f"Drop files, or copy them into {engine.paths.inbox}. Import runs ingest (M5)."
        )
        self.drop = DropArea(self._dropped)
        inbox.add(self.drop)
        self.inbox_list = QListWidget()
        self.inbox_list.setMaximumHeight(140)
        inbox.add(self.inbox_list)
        inbox.add_layout(
            row(
                icon_button("import", "Import inbox", on_click=self._import),
                icon_button("refresh", "Refresh", on_click=self.refresh_inbox),
                stretch_last=True,
            )
        )
        self.content.addWidget(inbox)

        sessions = Card("Sessions", "Recorded and imported sessions under captures/sessions.")
        self.sessions = QListWidget()
        self.sessions.setMinimumHeight(160)
        sessions.add(self.sessions)
        sessions.add_layout(
            row(
                icon_button("eye", "Open folder", on_click=self._open_session),
                icon_button("learn", "Review wizard (M5)", on_click=lambda: None),
                stretch_last=True,
            )
        )
        self.content.addWidget(sessions)
        self.finish()
        self.refresh_inbox()
        self.refresh_sessions()

    def _mark_button(self, kind: str) -> QPushButton:
        return icon_button("check", kind.title(), on_click=lambda: self.engine.mark(kind))

    def _start(self) -> None:
        self.engine.start_learn(self.target.currentText())

    def _stop(self) -> None:
        self.engine.stop_learn()
        self.refresh_sessions()

    def on_tick(self) -> None:
        s = self.engine.learn_stats()
        if s is None:
            self.stats.setText(
                "Not recording" if self.engine.state.learn_session is None else "Starting…"
            )
            return
        elapsed = time.time() - s["started_ts"]
        self.stats.setText(
            f"Recording {elapsed:.0f}s · frames {s['frames']} · keyframes {s['keyframes']} · markers {s['markers']} · inputs {s['inputs']} · ocr {s['ocr_snapshots']}"
        )

    def _dropped(self, paths: list[Path]) -> None:
        dest = self.engine.paths.inbox / INBOX_FOR[self.target.currentText()]
        dest.mkdir(parents=True, exist_ok=True)
        for p in paths:
            if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".mp4", ".mkv"):
                shutil.copy2(p, dest / p.name)
        self.refresh_inbox()

    def refresh_inbox(self) -> None:
        self.inbox_list.clear()
        root = self.engine.paths.inbox
        for p in sorted(root.rglob("*")):
            if (
                p.is_file()
                and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".mp4", ".mkv")
                and "_processed" not in p.parts
            ):
                self.inbox_list.addItem(str(p.relative_to(root)))
        if self.inbox_list.count() == 0:
            self.inbox_list.addItem("(empty)")

    def _import(self) -> None:
        self.engine.emit("learn.ingest_requested", message="Inbox ingest arrives in M5")

    def refresh_sessions(self) -> None:
        self.sessions.clear()
        root = self.engine.paths.sessions
        for p in sorted(root.glob("*"), reverse=True):
            if p.is_dir():
                n = len(list((p / "frames").glob("*.png"))) if (p / "frames").exists() else 0
                self.sessions.addItem(f"{p.name}   ({n} keyframes)")

    def _open_session(self) -> None:
        item = self.sessions.currentItem()
        if item is None:
            return
        name = item.text().split("   ")[0]
        os.startfile(str(self.engine.paths.sessions / name))

    def on_event(self, event: Event) -> None:
        if event.type in ("learn.stopped", "learn.started"):
            self.refresh_sessions()

    def on_show(self) -> None:
        self.refresh_inbox()
        self.refresh_sessions()


__all__ = ["LearnPage", "QHBoxLayout", "QPushButton", "QWidget"]
