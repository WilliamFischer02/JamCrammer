"""Calibration: zoom/pan canvas over a snapped frame, point/ROI pickers, pixel inspector,
per-field status chips. Full editing tools are M6; the canvas and field list land here."""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QPixmap, QWheelEvent
from PySide6.QtWidgets import (
    QComboBox,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from jamcrammer.app import theme
from jamcrammer.app.engine import Engine
from jamcrammer.app.widgets import Card, Chip, Page, icon_button, row

ZOOM_STEP = 1.25
MIN_ZOOM = 0.2
MAX_ZOOM = 12.0
PROFILES = ("shared", "parties")


class Canvas(QGraphicsView):
    def __init__(self, on_hover: Any, on_pick: Any) -> None:
        super().__init__()
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.item = QGraphicsPixmapItem()
        self._scene.addItem(self.item)
        self.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setMouseTracking(True)
        self._on_hover = on_hover
        self._on_pick = on_pick
        self._zoom = 1.0
        self.image: np.ndarray | None = None
        self.overlays: list[tuple[str, tuple[int, int, int, int] | tuple[int, int], str]] = []

    def set_image(self, image: np.ndarray) -> None:
        self.image = image
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        qimg = QImage(
            rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888
        )
        self.item.setPixmap(QPixmap.fromImage(qimg.copy()))
        self._scene.setSceneRect(QRectF(0, 0, image.shape[1], image.shape[0]))
        self.fit()

    def fit(self) -> None:
        self.resetTransform()
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom = self.transform().m11()

    def wheelEvent(self, e: QWheelEvent) -> None:  # noqa: N802 - Qt override
        factor = ZOOM_STEP if e.angleDelta().y() > 0 else 1 / ZOOM_STEP
        new = self._zoom * factor
        if MIN_ZOOM <= new <= MAX_ZOOM:
            self.scale(factor, factor)
            self._zoom = new

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802 - Qt override
        super().mouseMoveEvent(e)
        p = self.mapToScene(e.position().toPoint())
        self._on_hover(int(p.x()), int(p.y()))

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:  # noqa: N802 - Qt override
        p = self.mapToScene(e.position().toPoint())
        self._on_pick(int(p.x()), int(p.y()))

    def drawForeground(self, painter: QPainter, rect: QRectF | QRect) -> None:  # noqa: N802 - Qt override
        for kind, geom, color in self.overlays:
            pen = QPen(QColor(color))
            pen.setWidthF(2.0 / max(self._zoom, 0.01))
            painter.setPen(pen)
            if kind == "roi":
                x, y, w, h = geom  # type: ignore[misc]
                painter.drawRect(QRectF(x, y, w, h))
            else:
                x, y = geom  # type: ignore[misc]
                r = 12.0 / max(self._zoom, 0.01)
                painter.drawEllipse(QPointF(x, y), r, r)
                painter.drawLine(QPointF(x - 2 * r, y), QPointF(x + 2 * r, y))
                painter.drawLine(QPointF(x, y - 2 * r), QPointF(x, y + 2 * r))


class CalibrationPage(Page):
    key = "calibration"
    title = "Calibration"
    subtitle = "Snap a frame, inspect pixels, and set the points and regions each mode needs (stage coordinates)."
    icon = "calibration"

    def __init__(self, engine: Engine, window: Any) -> None:
        super().__init__()
        self.engine = engine
        top = QHBoxLayout()
        top.setSpacing(theme.PAD_MD)
        canvas_card = Card(
            "Canvas",
            "Wheel = zoom, drag = pan, double-click = pick the selected field's point / ROI origin.",
        )
        self.canvas = Canvas(self._hover, self._pick)
        self.canvas.setMinimumHeight(480)
        canvas_card.add(self.canvas)
        self.inspector = QLabel("x: –  y: –   RGB –   HSV –")
        self.inspector.setObjectName("CardHelp")
        canvas_card.add_layout(
            row(
                icon_button("camera", "Snap frame", on_click=self.snap),
                icon_button("refresh", "Fit", on_click=self.canvas.fit),
                self.inspector,
                stretch_last=True,
            )
        )
        top.addWidget(canvas_card, 3)

        fields = Card("Fields", "Status per field: calibrated / proposed / uncalibrated.")
        self.profile_box = QComboBox()
        for p in PROFILES:
            self.profile_box.addItem(p)
        self.profile_box.currentTextChanged.connect(lambda _t: self.refresh_fields())
        fields.add(self.profile_box)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Field", "Type", "Status"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.setMinimumHeight(420)
        fields.add(self.table)
        fields.add_layout(
            row(
                icon_button(
                    "check",
                    "Save profile",
                    kind="primary",
                    color=theme.PALETTE.bg,
                    on_click=self.save,
                ),
                icon_button("export", "Save template crop (M6)", on_click=lambda: None),
                stretch_last=True,
            )
        )
        top.addWidget(fields, 2)
        self.content.addLayout(top)
        self.finish()
        self.refresh_fields()

    def snap(self) -> None:
        frame = self.engine.snapshot()
        if frame is None and self.engine.start_capture():
            frame = self.engine.grab()
        if frame is not None:
            self.canvas.set_image(frame.image)
            self._draw_overlays()

    def _hover(self, x: int, y: int) -> None:
        img = self.canvas.image
        if img is None or not (0 <= x < img.shape[1] and 0 <= y < img.shape[0]):
            return
        b, g, r = (int(v) for v in img[y, x])
        hsv = cv2.cvtColor(np.array([[[b, g, r]]], dtype=np.uint8), cv2.COLOR_BGR2HSV)[0, 0]
        self.inspector.setText(
            f"x: {x}  y: {y}   RGB {r},{g},{b}   HSV {int(hsv[0])},{int(hsv[1])},{int(hsv[2])}"
        )

    def _pick(self, x: int, y: int) -> None:
        key = self._selected_key()
        if key is None:
            return
        prof = self.engine.profile(self.profile_box.currentText())
        f = prof.field(key)
        if f.type == "point":
            prof.set_value(key, [x, y], status="calibrated")
        elif f.type == "roi":
            w, h = (f.value[2], f.value[3]) if f.value else (100, 40)
            prof.set_value(key, [x, y, w, h], status="proposed")
        self.refresh_fields()
        self._draw_overlays()

    def _selected_key(self) -> str | None:
        r = self.table.currentRow()
        item = self.table.item(r, 0) if r >= 0 else None
        return item.text() if item is not None else None

    def _selection_changed(self) -> None:
        self._draw_overlays()

    def _draw_overlays(self) -> None:
        prof = self.engine.profile(self.profile_box.currentText())
        sel = self._selected_key()
        self.canvas.overlays = []
        for key, f in prof.fields.items():
            if not f.usable:
                continue
            color = theme.PALETTE.accent if key == sel else theme.PALETTE.muted
            if f.type == "point":
                self.canvas.overlays.append(("point", (int(f.value[0]), int(f.value[1])), color))
            elif f.type == "roi":
                self.canvas.overlays.append(("roi", tuple(int(v) for v in f.value), color))  # type: ignore[arg-type]
        self.canvas.viewport().update()

    def refresh_fields(self) -> None:
        prof = self.engine.profile(self.profile_box.currentText())
        self.table.setRowCount(0)
        for key, f in prof.fields.items():
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(key))
            self.table.setItem(r, 1, QTableWidgetItem(f.type))
            self.table.setCellWidget(r, 2, Chip(f.status, f.status))
        self.table.resizeColumnsToContents()

    def save(self) -> None:
        name = self.profile_box.currentText()
        prof = self.engine.profile(name)
        prof.save(self.engine.paths.profiles / f"{name}.yaml")
        self.engine.emit("profile.saved", name=name, message=f"Saved profiles/{name}.yaml")

    def on_show(self) -> None:
        self.refresh_fields()
        if self.canvas.image is None:
            self.snap()


__all__ = ["CalibrationPage", "QVBoxLayout"]
