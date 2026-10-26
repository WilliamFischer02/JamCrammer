"""Profit Lab: gems/hr by mode (estimated vs ledger), round times, overhead, recommendation."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem

from jamcrammer.app import theme
from jamcrammer.app.engine import MODE_LABELS, Engine
from jamcrammer.app.widgets import Card, Page

GAMES = ("bestguess", "double_up", "jamaa_derby")


class ProfitLabPage(Page):
    key = "profitlab"
    title = "Profit Lab"
    subtitle = "Which mode actually earns the most, measured from the HUD gem total and end-screen payouts."
    icon = "profitlab"

    def __init__(self, engine: Engine, window: Any) -> None:
        super().__init__()
        self.engine = engine
        import pyqtgraph as pg

        top = QHBoxLayout()
        top.setSpacing(theme.PAD_MD)
        chart = Card(
            "Gems per hour by mode",
            "Ledger-verified (solid) vs estimated (outline). Fills in as modes run.",
        )
        self.plot = pg.PlotWidget(background=None)
        self.plot.setMinimumHeight(240)
        self.plot.setMouseEnabled(False, False)
        self.plot.hideButtons()
        chart.add(self.plot)
        top.addWidget(chart, 2)
        rec = Card("Recommendation")
        self.reco = QLabel("Not enough data yet. Run Double Up and Best Guess sessions first.")
        self.reco.setWordWrap(True)
        self.reco.setObjectName("StatValue")
        rec.add(self.reco)
        self.overhead = QLabel("Opportunist overhead: – s per check")
        self.overhead.setObjectName("CardHelp")
        rec.add(self.overhead)
        top.addWidget(rec, 1)
        self.content.addLayout(top)

        table = Card(
            "Rounds", "Per-game round time distribution and payouts (arcade_rounds / bg_rounds)."
        )
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Game", "Rounds", "Median round (s)", "Gems / round", "Gems / hour"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(180)
        table.add(self.table)
        self.content.addWidget(table)
        self.finish()
        self.refresh()

    def refresh(self) -> None:
        import pyqtgraph as pg

        store = self.engine.store
        hours = max(1e-6, (time.time() - self.engine.started_ts) / 3600.0)
        by_mode = self.engine.ledger.stats.by_mode
        self.plot.clear()
        names = list(MODE_LABELS)
        ys = [by_mode.get(m, 0) / hours for m in names]
        colors = [QColor(theme.PALETTE.for_mode(m)) for m in names]
        for i, (y, color) in enumerate(zip(ys, colors, strict=True)):
            self.plot.addItem(
                pg.BarGraphItem(x=[i], height=[y], width=0.6, brush=color, pen=pg.mkPen(None))
            )
        self.plot.getAxis("bottom").setTicks([[(i, MODE_LABELS[m]) for i, m in enumerate(names)]])
        self.plot.setLabel("left", "gems / hour")
        self.table.setRowCount(0)
        for game in GAMES:
            rows = (
                store.query(
                    "SELECT duration_s, gems_reported FROM arcade_rounds WHERE game=?", (game,)
                )
                if game != "bestguess"
                else store.query(
                    "SELECT NULL AS duration_s, gems_est AS gems_reported FROM bg_rounds"
                )
            )
            n = len(rows)
            durations = sorted(float(r["duration_s"]) for r in rows if r["duration_s"] is not None)
            median = durations[len(durations) // 2] if durations else None
            gems = [int(r["gems_reported"] or 0) for r in rows]
            per_round = (sum(gems) / n) if n else 0.0
            per_hour = (per_round * 3600.0 / median) if median else 0.0
            i = self.table.rowCount()
            self.table.insertRow(i)
            for c, v in enumerate(
                (
                    game,
                    n,
                    f"{median:.1f}" if median else "–",
                    f"{per_round:.1f}",
                    f"{per_hour:,.0f}",
                )
            ):
                self.table.setItem(i, c, QTableWidgetItem(str(v)))
        best = max(by_mode.items(), key=lambda kv: kv[1], default=None)
        if best and best[1] > 0:
            self.reco.setText(
                f"{MODE_LABELS.get(best[0], best[0])} earns most right now ({best[1] / hours:,.0f} gems/hr)."
            )

    def on_show(self) -> None:
        self.refresh()
