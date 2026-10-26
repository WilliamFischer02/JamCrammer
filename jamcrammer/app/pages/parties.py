"""Parties: rotation timeline (Gantt by party), stats, unknown-banner labeling queue."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
)

from jamcrammer.app import theme
from jamcrammer.app.engine import Engine
from jamcrammer.app.widgets import Card, Page, icon_button, row
from jamcrammer.core.events import Event
from jamcrammer.modes.party_watcher import rotation_report, slug

TIMELINE_HOURS = 6
PALETTE_CYCLE = ("#9B6BFF", "#3AA0FF", "#2FD08A", "#F5B941", "#FF5D5D", "#E7EAF0")


class PartiesPage(Page):
    key = "parties"
    title = "Parties"
    subtitle = "The rotation log built from every My Parties scan, and the banners still waiting for a name."
    icon = "parties"

    def __init__(self, engine: Engine, window: Any) -> None:
        super().__init__()
        self.engine = engine
        import pyqtgraph as pg

        stats = QHBoxLayout()
        stats.setSpacing(theme.PAD_MD)
        self.distinct = QLabel("–")
        self.since = QLabel("–")
        self.next = QLabel("–")
        for label, w in (
            ("Distinct parties seen", self.distinct),
            ("Time since slot changed", self.since),
            ("Next expected change", self.next),
        ):
            c = Card(label)
            w.setObjectName("StatValue")
            c.add(w)
            stats.addWidget(c)
        self.content.addLayout(stats)

        tl = Card("Rotation timeline", f"Last {TIMELINE_HOURS} h, one lane per party.")
        self.plot = pg.PlotWidget(background=None)
        self.plot.setMinimumHeight(240)
        self.plot.setMouseEnabled(False, False)
        self.plot.hideButtons()
        self.plot.getAxis("left").setPen(theme.PALETTE.border)
        self.plot.getAxis("bottom").setPen(theme.PALETTE.border)
        tl.add(self.plot)
        self.content.addWidget(tl)

        table = Card("Sightings")
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Party", "Slot", "First seen", "Last seen", "Minutes"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(200)
        table.add(self.table)
        self.content.addWidget(table)

        unk = Card(
            "Unknown banners",
            "Crops the watcher could not identify. Name one and it joins the banner library.",
        )
        self.unknown = QListWidget()
        self.unknown.setIconSize(QPixmap(160, 48).size())
        self.unknown.setMinimumHeight(140)
        unk.add(self.unknown)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Party name for the selected crop, e.g. Best Guess Game")
        unk.add_layout(
            row(
                self.name,
                icon_button(
                    "check",
                    "Label",
                    kind="primary",
                    color=theme.PALETTE.bg,
                    on_click=self.label_selected,
                ),
                icon_button("refresh", "Refresh", on_click=self.refresh),
            )
        )
        self.content.addWidget(unk)
        self.finish()
        self.refresh()

    def refresh(self) -> None:
        rows = rotation_report(self.engine.store, since_ts=time.time() - TIMELINE_HOURS * 3600)
        slots = [r for r in rows if r["slot"] >= 2] or rows
        labels = sorted({r["label"] for r in slots})
        self.distinct.setText(str(len(labels)))
        if slots:
            latest = max(slots, key=lambda r: r["last_seen"])
            since_min = (time.time() - latest["first_seen"]) / 60.0
            self.since.setText(f"{since_min:.0f} min")
            self.next.setText(
                f"~{max(0.0, 30 - since_min):.0f} min" if since_min < 60 else "unknown"
            )
        self.table.setRowCount(0)
        for r in rows:
            i = self.table.rowCount()
            self.table.insertRow(i)
            for c, v in enumerate(
                (
                    r["label"],
                    r["slot"],
                    time.strftime("%H:%M", time.localtime(r["first_seen"])),
                    time.strftime("%H:%M", time.localtime(r["last_seen"])),
                    r["duration_min"],
                )
            ):
                self.table.setItem(i, c, QTableWidgetItem(str(v)))
        self._draw_timeline(rows, labels)
        self._refresh_unknown()

    def _draw_timeline(self, rows: list[dict[str, Any]], labels: list[str]) -> None:
        import pyqtgraph as pg

        self.plot.clear()
        now = time.time()
        lanes = {label: i for i, label in enumerate(labels)}
        for r in rows:
            if r["label"] not in lanes:
                continue
            y = lanes[r["label"]]
            x0 = (r["first_seen"] - now) / 60.0
            x1 = max(x0 + 1.0, (r["last_seen"] - now) / 60.0)
            color = PALETTE_CYCLE[y % len(PALETTE_CYCLE)]
            bar = pg.BarGraphItem(
                x0=[x0], x1=[x1], y=[y], height=0.6, brush=QColor(color), pen=pg.mkPen(None)
            )
            self.plot.addItem(bar)
        axis = self.plot.getAxis("left")
        axis.setTicks([[(i, label) for label, i in lanes.items()]])
        self.plot.setLabel("bottom", "minutes ago")
        self.plot.setXRange(-TIMELINE_HOURS * 60, 0)
        self.plot.setYRange(-0.5, max(0.5, len(lanes) - 0.5))

    def _refresh_unknown(self) -> None:
        self.unknown.clear()
        for p in sorted(self.engine.paths.unknown.glob("*.png"), reverse=True)[:50]:
            item = QListWidgetItem(
                QPixmap(str(p)).scaledToHeight(48, Qt.TransformationMode.SmoothTransformation),
                p.name,
            )
            item.setData(Qt.ItemDataRole.UserRole, str(p))
            self.unknown.addItem(item)

    def label_selected(self) -> None:
        item = self.unknown.currentItem()
        name = self.name.text().strip()
        if item is None or not name:
            return
        src = Path(item.data(Qt.ItemDataRole.UserRole))
        import cv2

        from jamcrammer.modes.party_watcher import BannerLibrary

        img = cv2.imread(str(src))
        if img is None:
            return
        lib = BannerLibrary(self.engine.paths.assets / "banners")
        lib.add(slug(name), np.asarray(img, dtype=np.uint8))
        prof = self.engine.profile("parties")
        known = prof.items("known_labels")
        if name not in known:
            prof.set_value("known_labels", [*known, name], status="calibrated")
            prof.save(self.engine.paths.profiles / "parties.yaml")
        src.rename(src.with_suffix(".labeled.png"))
        self.engine.emit("parties.labeled", name=name, message=f"Banner labeled: {name}")
        self.name.clear()
        self.refresh()

    def on_event(self, event: Event) -> None:
        if event.type in ("parties.scan", "parties.unknown_banner"):
            self.refresh()

    def on_show(self) -> None:
        self.refresh()
