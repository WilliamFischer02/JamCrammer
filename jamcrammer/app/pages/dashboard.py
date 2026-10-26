"""Dashboard: mode cards, live preview with overlay, stat tiles, event timeline."""

from __future__ import annotations

import time
from typing import Any

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from jamcrammer.app import theme
from jamcrammer.app.engine import MODE_LABELS, MODES, Engine
from jamcrammer.app.widgets import Card, Chip, Page, StatTile, grid_of, icon_button, row
from jamcrammer.core.events import Event

PREVIEW_W = 640
TIMELINE_MAX = 200
THUMB_W = 96
OVERLAY_THICKNESS = 2


class ModeCard(Card):
    def __init__(self, engine: Engine, mode: str, window: Any) -> None:
        super().__init__(MODE_LABELS[mode])
        self.engine = engine
        self.mode = mode
        self.color = theme.PALETTE.for_mode(mode)
        self.status = Chip("idle", "neutral")
        self.head.addWidget(self.status)
        self.last = QLabel("No events yet")
        self.last.setObjectName("CardHelp")
        self.last.setWordWrap(True)
        self.rate = QLabel("0 gems/hr")
        self.rate.setObjectName("StatValue")
        self.add(self.rate)
        self.add(self.last)
        btns = row(
            icon_button("play", "Start", color=theme.PALETTE.bg, kind="ok", on_click=self._start),
            icon_button("settings", "Settings", on_click=lambda: window.show_page_key(self.mode)),
            stretch_last=True,
        )
        self.add_layout(btns)

    def _start(self) -> None:
        self.engine.selected_mode = self.mode
        self.engine.start_mode(self.mode)

    def refresh(self) -> None:
        st = self.engine.state
        if st.mode == self.mode:
            self.status.setText(st.mode_state.lower())
            self.status.set_status("calibrated" if not st.paused else "proposed")
        else:
            self.status.setText("idle")
            self.status.set_status("neutral")
        gems = self.engine.ledger.stats.by_mode.get(self.mode, 0)
        hours = max(1e-6, (time.time() - self.engine.started_ts) / 3600.0)
        self.rate.setText(f"{gems / hours:,.0f} gems/hr")

    def note(self, text: str) -> None:
        self.last.setText(text)


class DashboardPage(Page):
    key = "dashboard"
    title = "Dashboard"
    subtitle = "What is running, what the game looks like right now, and what just happened."
    icon = "dashboard"

    def __init__(self, engine: Engine, window: Any) -> None:
        super().__init__()
        self.engine = engine
        self.window_ref = window
        self.cards = [ModeCard(engine, m, window) for m in MODES]
        self.content.addLayout(grid_of(self.cards, 3))

        mid = QHBoxLayout()
        mid.setSpacing(theme.PAD_MD)
        preview = Card("Live preview", "The captured client, normalized to the 1920x1080 stage.")
        self.overlay = QCheckBox("Overlay ROIs and click targets")
        self.overlay.setChecked(True)
        preview.head.addWidget(self.overlay)
        self.image = QLabel("Capture not running. Press Start, or Learn → Start Learning.")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setFixedSize(PREVIEW_W, PREVIEW_W * 9 // 16)
        self.image.setWordWrap(True)
        preview.add(self.image)
        snap = icon_button(
            "camera",
            "Start capture",
            on_click=self._start_capture,
        )
        preview.add_layout(row(snap, stretch_last=True))
        preview.body.addStretch(1)
        mid.addWidget(preview, 3)

        stats = QVBoxLayout()
        stats.setSpacing(theme.PAD_MD)
        self.tiles = {
            "gems": StatTile("Gems this session", theme.PALETTE.arcade),
            "rate": StatTile("Gems / hour", theme.PALETTE.opportunist),
            "accuracy": StatTile("Accuracy", theme.PALETTE.bestguess, unit="%"),
            "streak": StatTile("Streak", theme.PALETTE.warn),
            "popups": StatTile("Popups dismissed", theme.PALETTE.danger),
        }
        for t in self.tiles.values():
            stats.addWidget(t)
        mid.addLayout(stats, 1)
        self.content.addLayout(mid)

        timeline = Card("Event timeline", "NOTICE and HIGH events with screenshots when available.")
        self.timeline = QListWidget()
        self.timeline.setMinimumHeight(220)
        self.timeline.setIconSize(QPixmap(THUMB_W, THUMB_W * 9 // 16).size())
        timeline.add(self.timeline)
        self.content.addWidget(timeline)
        self.finish()

    def _start_capture(self) -> None:
        self.engine.start_capture()

    def on_tick(self) -> None:
        for c in self.cards:
            c.refresh()
        self._refresh_preview()
        st = self.engine.ledger.stats
        hours = max(1e-6, (time.time() - self.engine.started_ts) / 3600.0)
        self.tiles["gems"].set_value(int(st.session_delta))
        self.tiles["rate"].set_value(float(st.session_delta / hours))
        hs = self.engine.state.stats.get("hunt", {})
        self.tiles["accuracy"].set_value(float(hs.get("accuracy", 0.0)))
        self.tiles["streak"].set_value(int(hs.get("streak", 0)))
        self.tiles["popups"].set_value(int(self.engine.watchdog.stats.cleared))

    def _refresh_preview(self) -> None:
        frame = self.engine.snapshot()
        if frame is None:
            return
        img = frame.image
        if self.overlay.isChecked():
            img = img.copy()
            self._draw_overlay(img)
        h, w = img.shape[:2]
        scale = PREVIEW_W / w
        small = cv2.resize(img, (PREVIEW_W, int(h * scale)), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        qimg = QImage(
            rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888
        )
        self.image.setPixmap(QPixmap.fromImage(qimg.copy()))

    def _draw_overlay(self, img: np.ndarray) -> None:
        shared = self.engine.profile("shared")
        parties = self.engine.profile("parties")
        c_mask = _bgr(theme.PALETTE.muted)
        for key in ("hud_top_band", "hud_bottom_band", "animal_center_box"):
            if shared.has(key):
                x, y, w, h = shared.roi(key)
                cv2.rectangle(img, (x, y), (x + w, y + h), c_mask, OVERLAY_THICKNESS)
        if shared.has("popup_center_box"):
            x0, y0, x1, y1 = shared.fraction_box("popup_center_box")
            fh, fw = img.shape[:2]
            cv2.rectangle(
                img,
                (int(x0 * fw), int(y0 * fh)),
                (int(x1 * fw), int(y1 * fh)),
                _bgr(theme.PALETTE.warn),
                OVERLAY_THICKNESS,
            )
        for key, color in (
            ("party_hat_point", theme.PALETTE.bestguess),
            ("close_x_point", theme.PALETTE.danger),
        ):
            if parties.has(key):
                x, y = parties.point(key)
                cv2.circle(img, (x, y), 14, _bgr(color), OVERLAY_THICKNESS)
                cv2.line(img, (x - 20, y), (x + 20, y), _bgr(color), 1)
                cv2.line(img, (x, y - 20), (x, y + 20), _bgr(color), 1)
        cv2.putText(
            img,
            self.engine.state.pill_text,
            (24, 1060),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.2,
            _bgr(theme.PALETTE.text),
            2,
            cv2.LINE_AA,
        )

    def on_event(self, event: Event) -> None:
        if event.level == "INFO" and not event.type.startswith(
            ("hunter.", "parties.scan", "learn.")
        ):
            return
        stamp = time.strftime("%H:%M:%S", time.localtime(event.ts))
        msg = event.payload.get("message") or ", ".join(
            f"{k}={v}" for k, v in event.payload.items() if k != "message"
        )
        item = QListWidgetItem(f"{stamp}  {event.level:<6}  {event.type}  {msg}"[:200])
        item.setForeground(QColor(theme.PALETTE.for_level(str(event.level))))
        if event.screenshot_path is not None and event.screenshot_path.exists():
            pm = QPixmap(str(event.screenshot_path))
            if not pm.isNull():
                item.setIcon(pm.scaledToWidth(THUMB_W, Qt.TransformationMode.SmoothTransformation))
        self.timeline.insertItem(0, item)
        while self.timeline.count() > TIMELINE_MAX:
            self.timeline.takeItem(self.timeline.count() - 1)
        for c in self.cards:
            if event.source in (
                c.mode,
                "hunter" if c.mode == "hunt" else "",
                "parties" if c.mode == "hunt" else "",
            ):
                c.note(f"{stamp} {event.type}")


def _bgr(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b, g, r)
