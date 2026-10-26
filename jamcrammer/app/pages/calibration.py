"""Calibration (KICKOFF §12.2, M6): zoom/pan canvas over a snapped frame, point picker, ROI
rectangles with drag handles, color sampler with a pixel inspector, template crop-and-save,
marker-overlay preview, per-field status chips, and the live-mode gate readout."""

from __future__ import annotations

import json
from typing import Any

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QPixmap, QWheelEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
)

from jamcrammer.app import theme
from jamcrammer.app.engine import Engine
from jamcrammer.app.widgets import Card, Chip, Page, icon_button, row
from jamcrammer.core.gate import gate_status
from jamcrammer.vision.calibrate import (
    HANDLE_PX,
    apply_drag,
    crop_template,
    hit_test,
    hsv_range,
    normalize_rect,
)

ZOOM_STEP = 1.25
MIN_ZOOM = 0.2
MAX_ZOOM = 12.0
PROFILES = ("shared", "parties", "bestguess", "double_up", "jamaa_derby")
TOOLS = (
    ("select", "Select / drag"),
    ("roi", "Draw ROI"),
    ("color", "Sample color"),
    ("template", "Crop template"),
)
GATE_MODES = ("hunt", "bestguess", "double_up", "jamaa_derby")
STATUS_COL_PX = 110
Roi = tuple[int, int, int, int]


class Canvas(QGraphicsView):
    """Image view with zoom/pan, hover readout, drag handles for the selected ROI and
    rubber-band rectangles. All callbacks receive stage (image) coordinates."""

    def __init__(self, page: CalibrationPage) -> None:
        super().__init__()
        self.page = page
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.item = QGraphicsPixmapItem()
        self._scene.addItem(self.item)
        self.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setMouseTracking(True)
        self._zoom = 1.0
        self.image: np.ndarray | None = None
        self.overlays: list[tuple[str, tuple[int, ...], str, bool]] = []
        self._drag: tuple[str, Roi, int, int] | None = None
        self._band: tuple[int, int, int, int] | None = None

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

    def _scene_xy(self, e: QMouseEvent) -> tuple[int, int]:
        p = self.mapToScene(e.position().toPoint())
        return (int(p.x()), int(p.y()))

    def _handle_px(self) -> int:
        return max(2, int(HANDLE_PX / max(self._zoom, 0.05)))

    def wheelEvent(self, e: QWheelEvent) -> None:  # noqa: N802 - Qt override
        factor = ZOOM_STEP if e.angleDelta().y() > 0 else 1 / ZOOM_STEP
        new = self._zoom * factor
        if MIN_ZOOM <= new <= MAX_ZOOM:
            self.scale(factor, factor)
            self._zoom = new

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802 - Qt override
        x, y = self._scene_xy(e)
        tool = self.page.tool
        if e.button() == Qt.MouseButton.LeftButton:
            if tool in ("roi", "template"):
                self._band = (x, y, x, y)
                self.setDragMode(QGraphicsView.DragMode.NoDrag)
                return
            if tool == "color":
                self.page.sample_color(x, y)
                return
            roi = self.page.selected_roi()
            if roi is not None:
                mode = hit_test(roi, x, y, self._handle_px())
                if mode is not None:
                    self._drag = (mode, roi, x, y)
                    self.setDragMode(QGraphicsView.DragMode.NoDrag)
                    return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802 - Qt override
        x, y = self._scene_xy(e)
        self.page.hover(x, y)
        if self._drag is not None:
            mode, roi0, x0, y0 = self._drag
            self.page.preview_roi(apply_drag(roi0, mode, x - x0, y - y0, self.page.bounds()))  # type: ignore[arg-type]
            return
        if self._band is not None:
            self._band = (self._band[0], self._band[1], x, y)
            self.viewport().update()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802 - Qt override
        x, y = self._scene_xy(e)
        if self._drag is not None:
            mode, roi0, x0, y0 = self._drag
            self._drag = None
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.page.commit_roi(apply_drag(roi0, mode, x - x0, y - y0, self.page.bounds()))  # type: ignore[arg-type]
            return
        if self._band is not None:
            x0, y0, _, _ = self._band
            self._band = None
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.page.band_done(normalize_rect(x0, y0, x, y))
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:  # noqa: N802 - Qt override
        x, y = self._scene_xy(e)
        self.page.pick_point(x, y)

    def drawForeground(self, painter: QPainter, rect: QRectF | QRect) -> None:  # noqa: N802 - Qt override
        thin = 2.0 / max(self._zoom, 0.01)
        for kind, geom, color, selected in self.overlays:
            pen = QPen(QColor(color))
            pen.setWidthF(thin * (1.5 if selected else 1.0))
            painter.setPen(pen)
            if kind == "roi":
                x, y, w, h = geom
                painter.drawRect(QRectF(x, y, w, h))
                if selected:
                    hs = self._handle_px()
                    for hx, hy in ((x, y), (x + w, y), (x, y + h), (x + w, y + h)):
                        painter.fillRect(QRectF(hx - hs / 2, hy - hs / 2, hs, hs), QColor(color))
            else:
                x, y = geom[0], geom[1]
                r = 12.0 / max(self._zoom, 0.01)
                painter.drawEllipse(QPointF(x, y), r, r)
                painter.drawLine(QPointF(x - 2 * r, y), QPointF(x + 2 * r, y))
                painter.drawLine(QPointF(x, y - 2 * r), QPointF(x, y + 2 * r))
        if self._band is not None:
            pen = QPen(QColor(theme.PALETTE.warn))
            pen.setWidthF(thin)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            x, y, w, h = normalize_rect(*self._band)
            painter.drawRect(QRectF(x, y, w, h))


class CalibrationPage(Page):
    key = "calibration"
    title = "Calibration"
    subtitle = "Snap a frame, then set every point, region, color and template a mode needs. All coordinates are stage pixels (1920x1080)."
    icon = "calibration"

    def __init__(self, engine: Engine, window: Any) -> None:
        super().__init__()
        self.engine = engine
        self.tool = "select"
        self._preview: Roi | None = None
        top = QHBoxLayout()
        top.setSpacing(theme.PAD_MD)

        canvas_card = Card(
            "Canvas",
            "Wheel = zoom · drag = pan · select a field, then drag its handles · double-click = set a point · "
            "Draw ROI / Crop template = rubber-band · Sample color = click a pixel.",
        )
        tools = QHBoxLayout()
        self.tool_group = QButtonGroup(self)
        for key_, label in TOOLS:
            b = QToolButton()
            b.setText(label)
            b.setCheckable(True)
            b.setChecked(key_ == "select")
            b.setProperty("tool", key_)
            self.tool_group.addButton(b)
            tools.addWidget(b)
        self.tool_group.buttonClicked.connect(self._tool_changed)
        tools.addStretch(1)
        canvas_card.add_layout(tools)
        self.canvas = Canvas(self)
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

        fields = Card(
            "Fields",
            "Select a row to edit it on the canvas; double-click a number/list/text to edit it inline.",
        )
        self.profile_box = QComboBox()
        for p in PROFILES:
            self.profile_box.addItem(p)
        self.profile_box.currentTextChanged.connect(lambda _t: self.refresh_fields())
        fields.add(self.profile_box)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Field", "Type", "Value", "Status"])
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(3, STATUS_COL_PX)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._draw_overlays)
        self.table.itemDoubleClicked.connect(self._edit_inline)
        self.table.setMinimumHeight(380)
        fields.add(self.table)
        fields.add_layout(
            row(
                icon_button("check", "Mark calibrated", on_click=self.mark_calibrated),
                icon_button("alert", "Mark uncalibrated", on_click=self.mark_uncalibrated),
                icon_button(
                    "export",
                    "Save profile",
                    kind="primary",
                    color=theme.PALETTE.bg,
                    on_click=self.save,
                ),
                stretch_last=True,
            )
        )
        self.gate_label = QLabel("")
        self.gate_label.setObjectName("CardHelp")
        self.gate_label.setWordWrap(True)
        fields.add(self.gate_label)
        top.addWidget(fields, 2)
        self.content.addLayout(top)
        self.finish()
        self.refresh_fields()

    # --- helpers -----------------------------------------------------------------------------

    def _tool_changed(self, button: QToolButton) -> None:
        self.tool = str(button.property("tool"))

    def profile(self) -> Any:
        return self.engine.profile(self.profile_box.currentText())

    def bounds(self) -> tuple[int, int]:
        img = self.canvas.image
        return (img.shape[1], img.shape[0]) if img is not None else (1920, 1080)

    def _selected_key(self) -> str | None:
        r = self.table.currentRow()
        item = self.table.item(r, 0) if r >= 0 else None
        return item.text() if item is not None else None

    def selected_roi(self) -> Roi | None:
        key = self._selected_key()
        if key is None:
            return None
        f = self.profile().field(key)
        if f.type == "roi" and f.value:
            x, y, w, h = (int(v) for v in f.value)
            return (x, y, w, h)
        return None

    def snap(self) -> None:
        frame = self.engine.snapshot()
        if frame is None and self.engine.start_capture():
            frame = self.engine.grab()
        if frame is not None:
            self.canvas.set_image(frame.image)
            self._draw_overlays()

    # --- canvas callbacks ----------------------------------------------------------------------

    def hover(self, x: int, y: int) -> None:
        img = self.canvas.image
        if img is None or not (0 <= x < img.shape[1] and 0 <= y < img.shape[0]):
            return
        b, g, r = (int(v) for v in img[y, x])
        hsv = cv2.cvtColor(np.array([[[b, g, r]]], dtype=np.uint8), cv2.COLOR_BGR2HSV)[0, 0]
        self.inspector.setText(
            f"x: {x}  y: {y}   RGB {r},{g},{b}   HSV {int(hsv[0])},{int(hsv[1])},{int(hsv[2])}"
        )

    def pick_point(self, x: int, y: int) -> None:
        key = self._selected_key()
        if key is None:
            return
        f = self.profile().field(key)
        if f.type == "point":
            self.profile().set_value(key, [x, y], status="calibrated")
        elif f.type == "roi":
            w, h = (int(f.value[2]), int(f.value[3])) if f.value else (100, 40)
            self.profile().set_value(key, [x, y, w, h], status="proposed")
        self._after_edit(key)

    def preview_roi(self, roi: Roi) -> None:
        self._preview = roi
        self._draw_overlays()

    def commit_roi(self, roi: Roi) -> None:
        key = self._selected_key()
        self._preview = None
        if key is not None:
            self.profile().set_value(key, list(roi), status="calibrated")
            self._after_edit(key)

    def band_done(self, roi: Roi) -> None:
        key = self._selected_key()
        if key is None or self.canvas.image is None:
            return
        f = self.profile().field(key)
        if self.tool == "roi" and f.type == "roi":
            self.profile().set_value(key, list(roi), status="calibrated")
        elif self.tool == "template" and f.type == "template":
            rel = f.value or f"templates/{self.profile().name}/{key}.png"
            path = crop_template(self.canvas.image, roi, self.engine.paths.assets / str(rel))
            rel_out = str(path.relative_to(self.engine.paths.assets)).replace("\\", "/")
            self.profile().set_value(key, rel_out, status="calibrated", note=f"cropped {roi}")
        else:
            self.engine.state.last_error = (
                f"{key} is a {f.type}; pick a matching field for this tool"
            )
            return
        self._after_edit(key)

    def sample_color(self, x: int, y: int) -> None:
        key = self._selected_key()
        if key is None or self.canvas.image is None:
            return
        f = self.profile().field(key)
        if f.type != "color":
            self.engine.state.last_error = f"{key} is not a color field"
            return
        lo, hi = hsv_range(self.canvas.image, x, y)
        self.profile().set_value(
            key, [list(lo), list(hi)], status="calibrated", note=f"sampled at ({x},{y})"
        )
        self._after_edit(key)

    # --- table ---------------------------------------------------------------------------------

    def _edit_inline(self, item: QTableWidgetItem) -> None:
        key_item = self.table.item(item.row(), 0)
        if key_item is None:
            return
        key = key_item.text()
        f = self.profile().field(key)
        if f.type not in ("number", "list", "text", "fraction_box"):
            return
        current = json.dumps(f.value) if f.type != "text" else str(f.value or "")
        label = f"{f.type} value ({'JSON' if f.type != 'text' else 'text'}):"
        text, ok = QInputDialog.getText(self, f"Edit {key}", label, text=current)
        if not ok:
            return
        try:
            value = text if f.type == "text" else json.loads(text)
        except ValueError:
            self.engine.state.last_error = f"{key}: not valid JSON"
            return
        self.profile().set_value(key, value, status="calibrated")
        self._after_edit(key)

    def mark_calibrated(self) -> None:
        key = self._selected_key()
        if key is not None and self.profile().field(key).value is not None:
            self.profile().field(key).status = "calibrated"
            self._after_edit(key)

    def mark_uncalibrated(self) -> None:
        key = self._selected_key()
        if key is not None:
            self.profile().field(key).status = "uncalibrated"
            self._after_edit(key)

    def _after_edit(self, key: str) -> None:
        self.refresh_fields(select=key)
        self._draw_overlays()

    def _draw_overlays(self) -> None:
        prof = self.profile()
        sel = self._selected_key()
        self.canvas.overlays = []
        for key, f in prof.fields.items():
            if not f.usable:
                continue
            selected = key == sel
            color = theme.PALETTE.accent if selected else theme.PALETTE.muted
            if f.type == "point":
                self.canvas.overlays.append(
                    ("point", (int(f.value[0]), int(f.value[1])), color, selected)
                )
            elif f.type == "roi":
                geom = (
                    self._preview if selected and self._preview else tuple(int(v) for v in f.value)
                )
                self.canvas.overlays.append(("roi", geom, color, selected))
            elif f.type == "fraction_box" and self.canvas.image is not None:
                h, w = self.canvas.image.shape[:2]
                x0, y0, x1, y1 = (float(v) for v in f.value)
                box = (int(x0 * w), int(y0 * h), int((x1 - x0) * w), int((y1 - y0) * h))
                self.canvas.overlays.append(
                    ("roi", box, theme.PALETTE.warn if selected else theme.PALETTE.border, selected)
                )
        self.canvas.viewport().update()

    def refresh_fields(self, select: str | None = None) -> None:
        prof = self.profile()
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for key, f in prof.fields.items():
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(key))
            self.table.setItem(r, 1, QTableWidgetItem(f.type))
            val = (
                ""
                if f.value is None
                else (str(f.value) if f.type != "list" else f"{len(f.value)} items")
            )
            self.table.setItem(r, 2, QTableWidgetItem(val[:40]))
            self.table.setCellWidget(r, 3, Chip(f.status, f.status))
            if key == select:
                self.table.selectRow(r)
        self.table.blockSignals(False)
        self.refresh_gate()

    def refresh_gate(self) -> None:
        lines = [
            f"{mode}: {gate_status(mode, self.engine.paths, self.engine._profiles).summary()}"
            for mode in GATE_MODES
        ]
        self.gate_label.setText("Live-mode gate · " + "  ·  ".join(lines))

    def save(self) -> None:
        name = self.profile_box.currentText()
        self.profile().save(self.engine.paths.profiles / f"{name}.yaml")
        self.engine.emit("profile.saved", name=name, message=f"Saved profiles/{name}.yaml")
        self.refresh_gate()

    def on_show(self) -> None:
        self.refresh_fields()
        if self.canvas.image is None:
            self.snap()
