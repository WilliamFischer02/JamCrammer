"""Calibration (KICKOFF §12.2, M6): a guided wizard by default (which game screen to open,
capture it, then every field of that screen one at a time, the edited box glowing on the
screenshot) and the power-user canvas as the Advanced view: zoom/pan, point picker, ROI
handles, colour sampler with a pixel inspector, template crop-and-save, marker overlay, per
field status chips and the live-mode gate readout. Stage pixels (1920x1080) throughout."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from PySide6.QtCore import QEasingCurve, QPointF, QRect, QRectF, Qt, QVariantAnimation
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
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QWidget,
)

from jamcrammer.app import theme
from jamcrammer.app.engine import Engine
from jamcrammer.app.pages.calibration_flow import (
    PROFILE_TITLES,
    ROI_DEFAULT_SIZE,
    BgrImage,
    describe_value,
    field_state,
)
from jamcrammer.app.pages.calibration_wizard import STATE_CHIP, WizardPanel
from jamcrammer.app.widgets import Card, Chip, Page, Segmented, icon_button, row
from jamcrammer.core.gate import gate_status
from jamcrammer.core.profiles import Profile, ProfileField
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
PROFILES = tuple(PROFILE_TITLES)
TOOLS = (
    ("select", "Select / drag"),
    ("roi", "Draw ROI"),
    ("color", "Sample colour"),
    ("template", "Crop template"),
)
VIEWS = (("guided", "compass", "Guided"), ("advanced", "list", "Advanced"))
GATE_MODES = ("hunt", "bestguess", "double_up", "jamaa_derby")
STATUS_COL_PX = 110
STAGE = (1920, 1080)
TEMPLATE_SHOW_MIN = 0.6  # a template match drawn on the screenshot (advisory, not a detector)
Roi = tuple[int, int, int, int]


@dataclass(frozen=True)
class Overlay:
    kind: str  # roi | point | xline
    geom: tuple[int, ...]
    color: str
    style: str  # glow | selected | dim | plain


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


class Canvas(QGraphicsView):
    """Image view with zoom/pan, hover readout, drag handles for the active ROI, rubber-band
    rectangles, and the breathing glow around the field being calibrated. All callbacks
    receive stage (image) coordinates."""

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
        self.overlays: list[Overlay] = []
        self._drag: tuple[str, Roi, int, int] | None = None
        self._band: tuple[int, int, int, int] | None = None
        self._glow = 0.0
        self._glow_anim = QVariantAnimation(self)
        self._glow_anim.setDuration(theme.GLOW_PERIOD_MS)
        self._glow_anim.setStartValue(0.0)
        self._glow_anim.setKeyValueAt(0.5, 1.0)
        self._glow_anim.setEndValue(0.0)
        self._glow_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._glow_anim.setLoopCount(-1)
        self._glow_anim.valueChanged.connect(self._on_glow)

    def _on_glow(self, value: object) -> None:
        self._glow = float(value)  # type: ignore[arg-type]
        self.viewport().update()

    def set_overlays(self, overlays: list[Overlay]) -> None:
        self.overlays = overlays
        glowing = any(o.style == "glow" for o in overlays)
        if glowing and self._glow_anim.state() != QVariantAnimation.State.Running:
            self._glow_anim.start()
        elif not glowing and self._glow_anim.state() == QVariantAnimation.State.Running:
            self._glow_anim.stop()
            self._glow = 0.0
        self.viewport().update()

    def set_image(self, image: np.ndarray) -> None:
        self.image = image
        rgb = np.ascontiguousarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
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

    # --- drawing ---------------------------------------------------------------------------

    def _pen(self, color: str, width: float, alpha: float = 1.0) -> QPen:
        c = QColor(color)
        c.setAlphaF(alpha)
        pen = QPen(c)
        pen.setWidthF(width)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        return pen

    def _draw_shape(self, painter: QPainter, ov: Overlay, scale: float) -> None:
        if ov.kind == "roi":
            x, y, w, h = ov.geom
            painter.drawRect(QRectF(x, y, w, h))
        elif ov.kind == "xline":
            x = ov.geom[0]
            hgt = self.image.shape[0] if self.image is not None else STAGE[1]
            painter.drawLine(QPointF(x, 0), QPointF(x, hgt))
        else:
            x, y = ov.geom[0], ov.geom[1]
            r = 12.0 * scale
            painter.drawEllipse(QPointF(x, y), r, r)
            painter.drawLine(QPointF(x - 2 * r, y), QPointF(x + 2 * r, y))
            painter.drawLine(QPointF(x, y - 2 * r), QPointF(x, y + 2 * r))

    def _draw_handles(self, painter: QPainter, ov: Overlay) -> None:
        if ov.kind != "roi":
            return
        x, y, w, h = ov.geom
        hs = self._handle_px()
        for hx, hy in ((x, y), (x + w, y), (x, y + h), (x + w, y + h)):
            painter.fillRect(QRectF(hx - hs / 2, hy - hs / 2, hs, hs), QColor(ov.color))

    def drawForeground(self, painter: QPainter, rect: QRectF | QRect) -> None:  # noqa: N802 - Qt override
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = 1.0 / max(self._zoom, 0.01)
        for ov in sorted(self.overlays, key=lambda o: o.style == "glow"):  # the glow on top
            if ov.style == "dim":
                painter.setPen(self._pen(ov.color, theme.DIM_PX * scale, theme.DIM_ALPHA))
                self._draw_shape(painter, ov, scale)
                continue
            if ov.style == "glow":
                top = _lerp(theme.GLOW_ALPHA_MIN, theme.GLOW_ALPHA_MAX, self._glow)
                for i in range(theme.GLOW_RINGS, 0, -1):
                    width = (theme.OUTLINE_PX + i * theme.GLOW_WIDTH_PX) * scale
                    alpha = top * (1.0 - (i - 1) / theme.GLOW_RINGS) / theme.GLOW_RINGS
                    painter.setPen(self._pen(ov.color, width, alpha))
                    self._draw_shape(painter, ov, scale)
            width = theme.OUTLINE_PX * (1.5 if ov.style in ("glow", "selected") else 1.0)
            painter.setPen(self._pen(ov.color, width * scale))
            self._draw_shape(painter, ov, scale)
            if ov.style in ("glow", "selected"):
                self._draw_handles(painter, ov)
        if self._band is not None:
            pen = self._pen(theme.PALETTE.warn, theme.OUTLINE_PX * scale)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            x, y, w, h = normalize_rect(*self._band)
            painter.drawRect(QRectF(x, y, w, h))


class CalibrationPage(Page):
    key = "calibration"
    title = "Calibration"
    subtitle = (
        "Guided: open the screen the wizard asks for, capture it, then confirm or adjust each "
        "box one at a time. Advanced: the full canvas. Stage pixels (1920x1080)."
    )
    icon = "calibration"

    def __init__(self, engine: Engine, window: Any) -> None:
        super().__init__()
        self.engine = engine
        self.tool = "select"
        self.view = "guided"
        self._preview: Roi | None = None
        self._active_key: str | None = None
        self._siblings: tuple[str, ...] = ()
        self._wizard_profile: str | None = None
        self._tpl_cache: dict[tuple[str, int], Roi | None] = {}

        bar = QHBoxLayout()
        bar.setSpacing(theme.PAD_SM)
        self.view_switch = Segmented(VIEWS, self.view)
        self.view_switch.changed.connect(self._view_changed)
        bar.addWidget(self.view_switch)
        bar.addSpacing(theme.PAD_MD)
        bar.addWidget(QLabel("Profile"))
        self.profile_box = QComboBox()
        for p in PROFILES:
            self.profile_box.addItem(PROFILE_TITLES[p], p)
        self.profile_box.currentIndexChanged.connect(lambda _i: self._profile_changed())
        bar.addWidget(self.profile_box)
        bar.addStretch(1)
        self.content.addLayout(bar)

        top = QHBoxLayout()
        top.setSpacing(theme.PAD_MD)
        canvas_card = Card(
            "Screenshot",
            "Wheel = zoom · drag = pan · drag the glowing box or its handles · double-click = "
            "place a point or a box · Draw ROI / Crop template = rubber-band · Sample colour = "
            "click a pixel.",
        )
        tools = QHBoxLayout()
        self.tool_group = QButtonGroup(self)
        self._tool_buttons: dict[str, QToolButton] = {}
        for key_, label in TOOLS:
            b = QToolButton()
            b.setText(label)
            b.setCheckable(True)
            b.setChecked(key_ == "select")
            b.setProperty("tool", key_)
            self.tool_group.addButton(b)
            self._tool_buttons[key_] = b
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
                icon_button("camera", "Snap live", kind="ghost", on_click=self.snap),
                icon_button("maximize", "Fit", kind="ghost", on_click=self.canvas.fit),
                self.inspector,
                stretch_last=True,
            )
        )
        top.addWidget(canvas_card, 3)

        self.side = QStackedWidget()
        self.wizard_card = Card("Guided calibration")
        self.wizard = WizardPanel(self)
        self.wizard_card.add(self.wizard)
        self.side.addWidget(self.wizard_card)
        self.side.addWidget(self._build_advanced())
        top.addWidget(self.side, 2)
        self.content.addLayout(top)
        self.finish()
        self.refresh_fields()

    def _build_advanced(self) -> QWidget:
        fields = Card(
            "Fields",
            "Select a row to edit it on the canvas; double-click a number, list or text to "
            "edit it inline. Hover a name for its explanation.",
        )
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Field", "Value", "Status"])
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(2, STATUS_COL_PX)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._table_selected)
        self.table.itemDoubleClicked.connect(self._edit_inline)
        self.table.setMinimumHeight(380)
        fields.add(self.table)
        fields.add_layout(
            row(
                icon_button("check", "Calibrated", kind="ghost", on_click=self.mark_calibrated),
                icon_button("alert", "Uncalibrated", kind="ghost", on_click=self.mark_uncalibrated),
                icon_button(
                    "export",
                    "Save profile",
                    kind="accent",
                    accent="tool",
                    on_click=self.save,
                ),
                stretch_last=True,
            )
        )
        self.gate_label = QLabel("")
        self.gate_label.setObjectName("CardHelp")
        self.gate_label.setWordWrap(True)
        fields.add(self.gate_label)
        return fields

    # --- host API for the wizard --------------------------------------------------------------

    def profile(self) -> Profile:
        return self.engine.profile(self.profile_name())

    def profile_name(self) -> str:
        return str(self.profile_box.currentData())

    def live_frame(self) -> BgrImage | None:
        frame = self.engine.snapshot()
        if frame is None and self.engine.start_capture():
            frame = self.engine.grab()
        return None if frame is None else np.asarray(frame.image, dtype=np.uint8)

    def show_image(self, image: BgrImage | None) -> None:
        if image is not None:
            self._tpl_cache.clear()
            self.canvas.set_image(image)
        self._draw_overlays()

    def set_tool(self, tool: str) -> None:
        self.tool = tool
        b = self._tool_buttons.get(tool)
        if b is not None:
            b.setChecked(True)

    def set_active_field(self, key: str | None, siblings: tuple[str, ...]) -> None:
        self._active_key = key
        self._siblings = siblings
        self._preview = None
        self._draw_overlays()

    def edit_value(self, key: str) -> None:
        f = self.profile().field(key)
        current = json.dumps(f.value) if f.type != "text" else str(f.value or "")
        kind = "text" if f.type == "text" else "JSON"
        label = f"{f.display_label(key)} ({f.type}, {kind}):"
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

    def save_profile(self) -> None:
        self.save()

    # --- helpers -----------------------------------------------------------------------------

    def _view_changed(self, key: str) -> None:
        self.view = key
        self.side.setCurrentIndex(0 if key == "guided" else 1)
        if key == "guided":
            self._load_wizard()
        else:
            self._active_key = None
            self._siblings = ()
            if self.canvas.image is None:
                self.snap()
        self._draw_overlays()

    def _profile_changed(self) -> None:
        self._wizard_profile = None
        self.refresh_fields()
        if self.view == "guided":
            self._load_wizard()

    def _load_wizard(self) -> None:
        name = self.profile_name()
        if self._wizard_profile != name:
            self._wizard_profile = name
            self.wizard.load(name)

    def _tool_changed(self, button: QToolButton) -> None:
        self.tool = str(button.property("tool"))

    def bounds(self) -> tuple[int, int]:
        img = self.canvas.image
        return (img.shape[1], img.shape[0]) if img is not None else STAGE

    def _table_key(self) -> str | None:
        r = self.table.currentRow()
        item = self.table.item(r, 0) if r >= 0 else None
        return str(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    def _selected_key(self) -> str | None:
        return self._active_key if self.view == "guided" else self._table_key()

    def _table_selected(self) -> None:
        self._active_key = self._table_key()
        self._draw_overlays()

    def _px_box(self, f: ProfileField) -> Roi | None:
        """An roi or fraction_box field as stage pixels."""
        if not f.value:
            return None
        if f.type == "roi":
            x, y, w, h = (int(v) for v in f.value)
            return (x, y, w, h)
        if f.type == "fraction_box":
            w, h = self.bounds()
            x0, y0, x1, y1 = (float(v) for v in f.value)
            return (int(x0 * w), int(y0 * h), int((x1 - x0) * w), int((y1 - y0) * h))
        return None

    def selected_roi(self) -> Roi | None:
        key = self._selected_key()
        return None if key is None else self._px_box(self.profile().field(key))

    def snap(self) -> None:
        self.show_image(self.live_frame())

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
            w, h = (int(f.value[2]), int(f.value[3])) if f.value else ROI_DEFAULT_SIZE
            self.profile().set_value(key, [x - w // 2, y - h // 2, w, h], status="proposed")
        elif f.type == "number" and key.endswith("_x"):
            self.profile().set_value(key, x, status="calibrated")
        else:
            return
        self._after_edit(key)

    def preview_roi(self, roi: Roi) -> None:
        self._preview = roi
        self._draw_overlays()

    def commit_roi(self, roi: Roi) -> None:
        key = self._selected_key()
        self._preview = None
        if key is None:
            return
        f = self.profile().field(key)
        if f.type == "fraction_box":
            w, h = self.bounds()
            x, y, bw, bh = roi
            self.profile().set_value(
                key, [x / w, y / h, (x + bw) / w, (y + bh) / h], status="calibrated"
            )
        else:
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
            self.engine.state.last_error = f"{key} is not a colour field"
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
        key = str(key_item.data(Qt.ItemDataRole.UserRole))
        if self.profile().field(key).type in ("number", "list", "text", "fraction_box"):
            self.edit_value(key)

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
        self.wizard.refresh()
        self._draw_overlays()

    # --- overlays ------------------------------------------------------------------------------

    def _overlay(self, key: str, f: ProfileField, style: str, color: str) -> Overlay | None:
        if f.value is None:
            return None
        if f.type == "point":
            return Overlay("point", (int(f.value[0]), int(f.value[1])), color, style)
        if f.type in ("roi", "fraction_box"):
            box = self._preview if style in ("glow", "selected") and self._preview else None
            geom = box or self._px_box(f)
            return None if geom is None else Overlay("roi", geom, color, style)
        if f.type == "number" and key.endswith("_x"):
            return Overlay("xline", (int(f.value),), color, style)
        if f.type == "template":
            box = self._template_box(key, f)
            return None if box is None else Overlay("roi", box, color, style)
        return None

    def _template_box(self, key: str, f: ProfileField) -> Roi | None:
        """Where the field's template matches on the shown image (so the user sees what the
        crop finds); cached per image."""
        img = self.canvas.image
        if img is None:
            return None
        cache_key = (key, id(img))
        if cache_key in self._tpl_cache:
            return self._tpl_cache[cache_key]
        box: Roi | None = None
        path = self.engine.paths.assets / str(f.value)
        tpl = cv2.imread(str(path), cv2.IMREAD_COLOR) if path.exists() else None
        if tpl is not None and tpl.shape[0] <= img.shape[0] and tpl.shape[1] <= img.shape[1]:
            res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(res)
            if score >= TEMPLATE_SHOW_MIN:
                box = (int(loc[0]), int(loc[1]), int(tpl.shape[1]), int(tpl.shape[0]))
        self._tpl_cache[cache_key] = box
        return box

    def _draw_overlays(self) -> None:
        prof = self.profile()
        active = self._selected_key()
        out: list[Overlay] = []
        if self.view == "guided":
            for key in self._siblings:
                if key in prof.fields and key != active:
                    ov = self._overlay(key, prof.fields[key], "dim", theme.PALETTE.muted)
                    if ov is not None:
                        out.append(ov)
            if active is not None and active in prof.fields:
                ov = self._overlay(active, prof.fields[active], "glow", theme.PALETTE.tool)
                if ov is not None:
                    out.append(ov)
        else:
            for key, f in prof.fields.items():
                if not f.usable and key != active:
                    continue
                style = "selected" if key == active else "plain"
                color = theme.PALETTE.tool if key == active else theme.PALETTE.muted
                ov = self._overlay(key, f, style, color)
                if ov is not None:
                    out.append(ov)
        self.canvas.set_overlays(out)

    def refresh_fields(self, select: str | None = None) -> None:
        prof = self.profile()
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for key, f in prof.fields.items():
            r = self.table.rowCount()
            self.table.insertRow(r)
            name = QTableWidgetItem(f.display_label(key))
            name.setData(Qt.ItemDataRole.UserRole, key)
            name.setToolTip(f"{key} · {f.type}\n{f.help or f.note}")
            self.table.setItem(r, 0, name)
            val = QTableWidgetItem(describe_value(f))
            val.setToolTip(str(f.value))
            self.table.setItem(r, 1, val)
            state = field_state(f)
            self.table.setCellWidget(r, 2, Chip(state, STATE_CHIP[state]))
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
        name = self.profile_name()
        self.profile().save(self.engine.paths.profiles / f"{name}.yaml")
        self.engine.emit("profile.saved", name=name, message=f"Saved profiles/{name}.yaml")
        self.refresh_gate()

    def on_show(self) -> None:
        self.refresh_fields()
        if self.view == "guided":
            self._load_wizard()
        elif self.canvas.image is None:
            self.snap()
