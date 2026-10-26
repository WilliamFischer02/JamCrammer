"""Answer Bank: searchable, sortable facts table with inline edit, conflicts filter, CSV I/O."""

from __future__ import annotations

import csv
import time
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QFileDialog, QLineEdit, QTableWidget, QTableWidgetItem

from jamcrammer.app.engine import Engine
from jamcrammer.app.widgets import Card, Page, icon_button, row
from jamcrammer.vision.ocr import normalize_text

COLUMNS = ("fact", "answer", "seen", "confirmed", "conflicts", "source", "last seen")


class AnswerBankPage(Page):
    key = "answerbank"
    title = "Answer Bank"
    subtitle = "Every Best Guess fact seen, with its answer and confidence. Double-click an answer to edit."
    icon = "answerbank"

    def __init__(self, engine: Engine, window: Any) -> None:
        super().__init__()
        self.engine = engine
        card = Card("Facts")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search facts…")
        self.search.textChanged.connect(self.refresh)
        self.conflicts = QCheckBox("Conflicts only")
        self.conflicts.toggled.connect(self.refresh)
        card.add_layout(
            row(
                self.search,
                self.conflicts,
                icon_button("import", "Import CSV", on_click=self.import_csv),
                icon_button("export", "Export CSV", on_click=self.export_csv),
                icon_button("refresh", "Refresh", on_click=self.refresh),
            )
        )
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([c.title() for c in COLUMNS])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        self.table.setMinimumHeight(520)
        self.table.itemChanged.connect(self._edited)
        card.add(self.table)
        self.content.addWidget(card)
        self.finish()
        self._loading = False
        self.refresh()

    def refresh(self) -> None:
        self._loading = True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        q = normalize_text(self.search.text())
        rows = self.engine.store.query("SELECT * FROM bg_facts ORDER BY last_seen DESC")
        for r in rows:
            if q and q not in r["normalized_text"]:
                continue
            if self.conflicts.isChecked() and not r["conflict_count"]:
                continue
            i = self.table.rowCount()
            self.table.insertRow(i)
            values = (
                r["display_text"],
                r["answer"],
                r["seen_count"],
                r["confirmed_count"],
                r["conflict_count"],
                r["source"],
                time.strftime("%Y-%m-%d %H:%M", time.localtime(r["last_seen"])),
            )
            for c, v in enumerate(values):
                item = QTableWidgetItem(str(v))
                if c != 1:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setData(Qt.ItemDataRole.UserRole, r["normalized_text"])
                self.table.setItem(i, c, item)
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        self._loading = False

    def _edited(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 1:
            return
        answer = item.text().strip().upper()[:1]
        if answer not in ("T", "F", "?"):
            self.refresh()
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        self.engine.store._exec(
            "UPDATE bg_facts SET answer=?, source='manual' WHERE normalized_text=?", (answer, key)
        )
        self.engine.emit("answerbank.edited", fact=key, answer=answer)

    def export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export answer bank", "answer_bank.csv", "CSV (*.csv)"
        )
        if not path:
            return
        rows = self.engine.store.query("SELECT * FROM bg_facts ORDER BY display_text")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(
                [
                    "display_text",
                    "answer",
                    "seen_count",
                    "confirmed_count",
                    "conflict_count",
                    "source",
                ]
            )
            for r in rows:
                w.writerow(
                    [
                        r["display_text"],
                        r["answer"],
                        r["seen_count"],
                        r["confirmed_count"],
                        r["conflict_count"],
                        r["source"],
                    ]
                )

    def import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import answer bank", "", "CSV (*.csv)")
        if not path:
            return
        n = 0
        with open(path, newline="", encoding="utf-8") as fh:
            for rec in csv.DictReader(fh):
                text = rec.get("display_text") or rec.get("fact") or ""
                answer = (rec.get("answer") or "?").strip().upper()[:1]
                if text and answer in ("T", "F", "?"):
                    self.engine.store.upsert_fact(normalize_text(text), text, answer, "import")
                    n += 1
        self.engine.emit("answerbank.imported", count=n, message=f"Imported {n} facts")
        self.refresh()

    def on_show(self) -> None:
        self.refresh()
