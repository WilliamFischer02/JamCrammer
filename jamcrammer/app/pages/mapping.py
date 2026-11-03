"""Mapping: the level as the agent has seen it (docs/MAPPING.md), filling in as it explores.

The picture is the full-resolution mosaic, stitched from the tiles the pilot's observations
wrote (``world/tiles/<area>/``), over the low-resolution mosaic and the occupancy grid; the
things the map knows sit on top with their ids. A meter says how much of the level's known
floor has been seen and walked. The page re-reads the world every few seconds while a
session runs, so the map grows on screen like a chart being explored. Export writes one
PNG of everything observed so far, to look at or to edit."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPen, QPixmap, QWheelEvent
from PySide6.QtWidgets import (
    QComboBox,
    QGraphicsItemGroup,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from jamcrammer.app import theme
from jamcrammer.app.engine import Engine
from jamcrammer.app.widgets import Card, Meter, Page, StatTile, icon_button, tool_button
from jamcrammer.modes.adventures.world import (
    BLOCKED,
    FLOOR,
    FREE,
    KIND_COLOURS,
    World,
    world_dir,
)
from jamcrammer.pilot import PilotSession, pointer_path, server_alive

REFRESH_S = 3.0
ZOOM_STEP = 1.25
MIN_ZOOM = 0.02
MAX_ZOOM = 4.0
TILE_ZOOM = 0.3  # full-resolution tiles are shown from this zoom on
TILE_MARGIN = 1  # tiles beyond the view kept loaded
GRID_ALPHA = 150
THING_RADIUS = 28.0
AREAS = (("moths", "Moth chamber"), ("torches", "Torch hall"), ("city", "Lost city"))


def _qcolor(bgr: tuple[int, int, int], alpha: int = 255) -> QColor:
    return QColor(bgr[2], bgr[1], bgr[0], alpha)


def _pixmap_alpha(bgr: np.ndarray, weight: np.ndarray) -> QPixmap:
    """The mosaic with the unseen part transparent, so the grid shows through."""
    rgba = cv2.cvtColor(np.ascontiguousarray(bgr), cv2.COLOR_BGR2RGBA)
    rgba[:, :, 3] = np.where(weight > 0, 255, 0).astype(np.uint8)
    rgba = np.ascontiguousarray(rgba)
    img = QImage(
        rgba.data, rgba.shape[1], rgba.shape[0], rgba.strides[0], QImage.Format.Format_RGBA8888
    )
    return QPixmap.fromImage(img.copy())


def _pixmap(bgr: np.ndarray) -> QPixmap:
    rgb = cv2.cvtColor(np.ascontiguousarray(bgr), cv2.COLOR_BGR2RGB)
    img = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888)
    return QPixmap.fromImage(img.copy())


class MapView(QGraphicsView):
    """World px scene: the mosaic layers, the grid, the things, the animal. Wheel zooms,
    drag pans; full-resolution tiles load for the visible part once zoomed in."""

    def __init__(self) -> None:
        super().__init__()
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QBrush(QColor(theme.PALETTE.bg)))
        self.setMinimumHeight(540)
        self.world: World | None = None
        self.area = "moths"
        self.base: QGraphicsPixmapItem | None = None
        self.grid: QGraphicsPixmapItem | None = None
        self.tiles = QGraphicsItemGroup()
        self.things = QGraphicsItemGroup()
        self.cols = QGraphicsItemGroup()
        self.marker = QGraphicsItemGroup()
        for g in (self.tiles, self.things, self.cols, self.marker):
            self.scene_.addItem(g)
        self._tile_items: dict[tuple[int, int], QGraphicsPixmapItem] = {}
        self._tile_mtime: dict[tuple[int, int], float] = {}
        self.layers = {
            "mosaic": True,
            "grid": True,
            "things": True,
            "collisions": True,
            "labels": True,
        }
        self._zoom = 0.06
        self._fitted = False

    # --- layers --------------------------------------------------------------------------

    def set_world(self, world: World, area: str) -> None:
        self.world, self.area = world, area
        a = world.area(area)
        self.scene_.setSceneRect(QRectF(0, 0, a.w, a.h))
        # the low-resolution mosaic, scaled up to world px
        if self.base is None:
            self.base = self.scene_.addPixmap(QPixmap())
            self.base.setZValue(1)
            self.base.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.base.setPixmap(_pixmap_alpha(a.mosaic, a.weight))
        self.base.setScale(1.0 / world.cfg.mosaic_scale)
        # the grid as a translucent colour layer
        colour = np.zeros((*a.grid.shape, 4), np.uint8)
        for s, bgr in ((FLOOR, (70, 70, 70)), (FREE, (60, 110, 60)), (BLOCKED, (40, 40, 220))):
            colour[a.grid == s] = (bgr[2], bgr[1], bgr[0], GRID_ALPHA)
        img = QImage(
            colour.data,
            colour.shape[1],
            colour.shape[0],
            colour.strides[0],
            QImage.Format.Format_RGBA8888,
        )
        if self.grid is None:
            self.grid = self.scene_.addPixmap(QPixmap())
            self.grid.setZValue(0)
            self.grid.setTransformationMode(Qt.TransformationMode.FastTransformation)
        self.grid.setPixmap(QPixmap.fromImage(img.copy()))
        self.grid.setScale(float(world.cfg.grid_px))
        self.tiles.setZValue(2)
        self.things.setZValue(4)
        self.cols.setZValue(3)
        self.marker.setZValue(5)
        self._rebuild_things()
        self._rebuild_collisions()
        self._refresh_tiles(force=True)
        self.apply_layers()
        if not self._fitted:
            self.fit()
            self._fitted = True

    def _clear(self, group: QGraphicsItemGroup) -> None:
        for it in group.childItems():
            group.removeFromGroup(it)
            self.scene_.removeItem(it)

    def _rebuild_things(self) -> None:
        self._clear(self.things)
        if self.world is None:
            return
        font = QFont(theme.FONT_FAMILY)
        font.setPixelSize(40)
        for o in self.world.objects:
            if o.area != self.area or o.gone:
                continue
            c = _qcolor(KIND_COLOURS.get(o.kind, (200, 200, 200)))
            r = THING_RADIUS if o.confidence >= 0.8 else THING_RADIUS * 0.7
            dot = self.scene_.addEllipse(
                o.x - r,
                o.y - r,
                2 * r,
                2 * r,
                QPen(c, 6),
                QBrush(c) if o.confidence >= 0.8 else QBrush(Qt.BrushStyle.NoBrush),
            )
            self.things.addToGroup(dot)
            if self.layers["labels"]:
                text = self.scene_.addText(
                    f"{o.id} {o.kind}" + (f" · {o.status}" if o.status else ""), font
                )
                text.setDefaultTextColor(c)
                text.setPos(o.x + r, o.y - r - 20)
                self.things.addToGroup(text)

    def _rebuild_collisions(self) -> None:
        self._clear(self.cols)
        if self.world is None:
            return
        pen = QPen(QColor(theme.PALETTE.danger), 8)
        for col in self.world.collisions:
            if col.area != self.area:
                continue
            line = self.scene_.addLine(col.x, col.y, col.x + col.dx * 50, col.y + col.dy * 50, pen)
            self.cols.addToGroup(line)

    def set_position(self, pos: tuple[float, float] | None) -> None:
        self._clear(self.marker)
        if pos is None:
            return
        c = QColor(theme.PALETTE.warn)
        ring = self.scene_.addEllipse(pos[0] - 60, pos[1] - 60, 120, 120, QPen(c, 10))
        self.marker.addToGroup(ring)
        core = self.scene_.addEllipse(pos[0] - 12, pos[1] - 12, 24, 24, QPen(c, 2), QBrush(c))
        self.marker.addToGroup(core)

    def _refresh_tiles(self, force: bool = False) -> None:
        """Load the full-resolution tiles under the view (when zoomed in enough), drop the
        ones far outside it, reload the ones whose file changed."""
        if self.world is None:
            return
        store = self.world.tiles
        if self._zoom < TILE_ZOOM or not self.layers["mosaic"]:
            for key in list(self._tile_items):
                self._drop_tile(key)
            return
        g = store.tile_px
        rect = self.mapToScene(self.viewport().rect()).boundingRect()
        tx0, ty0 = int(rect.left() // g) - TILE_MARGIN, int(rect.top() // g) - TILE_MARGIN
        tx1, ty1 = int(rect.right() // g) + TILE_MARGIN, int(rect.bottom() // g) + TILE_MARGIN
        wanted = {
            (tx, ty) for tx, ty in store.tiles(self.area) if tx0 <= tx <= tx1 and ty0 <= ty <= ty1
        }
        for key in list(self._tile_items):
            if key not in wanted:
                self._drop_tile(key)
        for key in wanted:
            path = store.path(self.area, *key)
            mtime = path.stat().st_mtime if path is not None and path.exists() else 0.0
            if not force and key in self._tile_items and self._tile_mtime.get(key) == mtime:
                continue
            raw = cv2.imread(str(path)) if path is not None and path.exists() else None
            if raw is None:
                continue
            if key in self._tile_items:
                self._tile_items[key].setPixmap(_pixmap(raw))
            else:
                item = self.scene_.addPixmap(_pixmap(raw))
                item.setPos(key[0] * g, key[1] * g)
                item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
                self.tiles.addToGroup(item)
                self._tile_items[key] = item
            self._tile_mtime[key] = mtime

    def _drop_tile(self, key: tuple[int, int]) -> None:
        item = self._tile_items.pop(key)
        self._tile_mtime.pop(key, None)
        self.tiles.removeFromGroup(item)
        self.scene_.removeItem(item)

    def apply_layers(self) -> None:
        if self.base is not None:
            self.base.setVisible(self.layers["mosaic"])
        if self.grid is not None:
            self.grid.setVisible(self.layers["grid"])
        self.things.setVisible(self.layers["things"])
        self.cols.setVisible(self.layers["collisions"])
        self._refresh_tiles()

    # --- zoom / pan ----------------------------------------------------------------------

    def fit(self) -> None:
        self.fitInView(self.scene_.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom = self.transform().m11()
        self._refresh_tiles()

    def zoom_by(self, factor: float) -> None:
        new = min(MAX_ZOOM, max(MIN_ZOOM, self._zoom * factor))
        factor = new / self._zoom
        self._zoom = new
        self.scale(factor, factor)
        self._refresh_tiles()

    def wheelEvent(self, e: QWheelEvent) -> None:  # noqa: N802 - Qt override
        self.zoom_by(ZOOM_STEP if e.angleDelta().y() > 0 else 1 / ZOOM_STEP)

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # noqa: N802 - Qt override
        super().scrollContentsBy(dx, dy)
        self._refresh_tiles()


class MappingPage(Page):
    key = "mapping"
    title = "Mapping"
    subtitle = "The level as the agent has seen it. It fills in as the pilot explores."
    icon = "map"

    def __init__(self, engine: Engine, window: Any) -> None:
        super().__init__()
        self.engine = engine
        self.window_ref = window
        self.world: World | None = None
        self._mtime = 0.0
        self._last_check = 0.0
        self.area = "moths"

        top = QHBoxLayout()
        top.setSpacing(theme.PAD_MD)
        progress = Card("Mapped")
        self.m_seen = Meter("Seen", "tool")
        self.m_walked = Meter("Walked", "tool")
        progress.add(self.m_seen)
        progress.add(self.m_walked)
        self.coverage_line = QLabel("")
        self.coverage_line.setObjectName("CardHelp")
        self.coverage_line.setWordWrap(True)
        progress.add(self.coverage_line)
        top.addWidget(progress, 2)
        self.t_things = StatTile("Things", theme.PALETTE.tool, spark=False, size="md")
        self.t_sure = StatTile("Confirmed", theme.PALETTE.ok, spark=False, size="md")
        self.t_cols = StatTile("Collisions", theme.PALETTE.danger, spark=False, size="md")
        self.t_tiles = StatTile("Tiles", theme.PALETTE.tool, spark=False, size="md")
        for t in (self.t_things, self.t_sure, self.t_cols, self.t_tiles):
            top.addWidget(t, 1)
        self.content.addLayout(top)

        agent = Card("Agent")
        self.agent_line = QLabel("No session")
        self.agent_line.setObjectName("StatusLine")
        self.agent_detail = QLabel("")
        self.agent_detail.setObjectName("StatusDetail")
        self.agent_detail.setWordWrap(True)
        agent.add(self.agent_line)
        agent.add(self.agent_detail)
        self.m_budget = Meter("Session budget", "tool", unit=" min")
        agent.add(self.m_budget)
        self.content.addWidget(agent)

        view_card = Card("Map")
        bar = QHBoxLayout()
        bar.setSpacing(theme.PAD_SM)
        self.area_box = QComboBox()
        for key, label in AREAS:
            self.area_box.addItem(label, key)
        self.area_box.currentIndexChanged.connect(self._area_changed)
        bar.addWidget(self.area_box)
        self.view = MapView()
        bar.addWidget(
            tool_button("zoom-in", "Zoom in", on_click=lambda: self.view.zoom_by(ZOOM_STEP))
        )
        bar.addWidget(
            tool_button("zoom-out", "Zoom out", on_click=lambda: self.view.zoom_by(1 / ZOOM_STEP))
        )
        bar.addWidget(tool_button("maximize", "Fit the area", on_click=self.view.fit))
        bar.addSpacing(theme.PAD_MD)
        self.layer_buttons: dict[str, Any] = {}
        for key, icon, tip in (
            ("mosaic", "image", "Mosaic"),
            ("grid", "grid", "Floor and walls"),
            ("things", "map-pin", "Things"),
            ("collisions", "x", "Collisions"),
            ("labels", "list", "Labels"),
        ):
            b = tool_button(icon, tip, checkable=True)
            b.setChecked(True)
            b.toggled.connect(lambda on, k=key: self._layer(k, on))
            bar.addWidget(b)
            self.layer_buttons[key] = b
        bar.addStretch(1)
        bar.addWidget(icon_button("refresh", "Refresh", kind="ghost", on_click=self.reload))
        bar.addWidget(
            icon_button(
                "download", "Export PNG", kind="accent", accent="tool", on_click=self.export
            )
        )
        bar.addWidget(icon_button("folder", "Folder", kind="ghost", on_click=self.open_folder))
        view_card.add_layout(bar)
        view_card.add(self.view)
        self.legend = QLabel(
            "grey floor from the recordings · green walked · red blocked · yellow ring = the "
            "animal · dots = things (filled when confirmed) · red ticks = collisions. "
            "Wheel zooms, drag pans; the full-resolution picture appears when zoomed in."
        )
        self.legend.setObjectName("CardHelp")
        self.legend.setWordWrap(True)
        view_card.add(self.legend)
        self.export_line = QLabel("")
        self.export_line.setObjectName("CardHelp")
        view_card.add(self.export_line)
        self.content.addWidget(view_card)
        self.finish()

    # --- data ----------------------------------------------------------------------------

    def _dir(self) -> Path:
        return world_dir(self.engine.paths.profiles, self.engine.cfg.adventures.route.target)

    def reload(self) -> None:
        path = self._dir() / "world.json"
        if not path.exists():
            self.coverage_line.setText("No world map yet: the first pilot session creates it.")
            return
        world = World.load(self._dir(), self.engine.cfg.adventures.world)
        if world is None:
            return
        self.world = world
        self._mtime = path.stat().st_mtime
        self.view.set_world(world, self.area)
        self._update_numbers()

    def _changed(self) -> bool:
        path = self._dir() / "world.json"
        return path.exists() and path.stat().st_mtime != self._mtime

    def _update_numbers(self) -> None:
        if self.world is None:
            return
        cov = self.world.coverage(self.area)
        self.m_seen.set_value(cov["percent_seen"], f"{cov['percent_seen']:.1f}%")
        self.m_walked.set_value(cov["percent_walked"], f"{cov['percent_walked']:.1f}%")
        g = self.world.cfg.grid_px
        self.coverage_line.setText(
            f"{cov['seen_cells']:,} of {cov['floor_cells']:,} floor cells seen, "
            f"{cov['walked_cells']:,} walked, {cov['blocked_cells']:,} blocked; "
            f"a cell is {g} x {g} scene px. Estimated from the floor the recordings revealed."
        )
        self.t_things.set_value(int(cov["things"]))
        self.t_sure.set_value(int(cov["things_sure"]))
        self.t_cols.set_value(int(cov["collisions"]))
        self.t_tiles.set_value(int(cov["tiles"]))

    def _update_agent(self) -> None:
        data = self.engine.paths.data
        session = PilotSession.load(pointer_path(data))
        alive = server_alive(data)
        if session is None:
            self.agent_line.setText("No session")
            self.agent_detail.setText("Start one: jamcrammer pilot start --goal ...")
            self.m_budget.set_value(0, "–")
            self.view.set_position(None)
            return
        minutes = (time.time() - session.started_ts) / 60.0
        state = "stopped" if session.stopped else ("live" if alive else "paused (no server)")
        self.agent_line.setText(f"{session.goal or session.target} · {state}")
        self.m_budget.set_value(
            100.0 * min(1.0, minutes / max(1.0, session.budget_min)),
            f"{minutes:.0f} of {session.budget_min:.0f} min",
        )
        last = self._last_command(session)
        self.agent_detail.setText(last)
        self.view.set_position(self._position(data))

    def _last_command(self, session: PilotSession) -> str:
        path = session.log_path
        if not path.exists():
            return ""
        try:
            with path.open("rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 4000))
                tail = fh.read().decode("utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        for line in reversed(tail):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            cmd = str(rec.get("cmd", ""))
            if cmd in ("look", "after", "dialog_closed"):
                continue
            stamp = time.strftime("%H:%M:%S", time.localtime(float(rec.get("ts", 0))))
            extra = ""
            if cmd == "probe":
                extra = f" {rec.get('direction', '')}: {rec.get('reason', '')}, {rec.get('travelled', 0)} px"
            elif cmd == "note":
                extra = f": {rec.get('text', '')}"
            elif cmd == "mark":
                extra = f": {rec.get('kind', '')} {rec.get('status', '')}"
            elif cmd == "scan":
                extra = f": {len(rec.get('found', []))} things"
            return f"{stamp}  {cmd}{extra}"
        return ""

    def _position(self, data: Path) -> tuple[float, float] | None:
        path = data / "pilot" / "position.json"
        if not path.exists():
            return None
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if doc.get("area") != self.area or not doc.get("pos"):
            return None
        return (float(doc["pos"][0]), float(doc["pos"][1]))

    # --- actions -------------------------------------------------------------------------

    def _area_changed(self, index: int) -> None:
        self.area = str(self.area_box.itemData(index))
        self.view._fitted = False
        if self.world is not None:
            self.view.set_world(self.world, self.area)
            self._update_numbers()

    def _layer(self, key: str, on: bool) -> None:
        self.view.layers[key] = on
        if key == "labels" and self.world is not None:
            self.view._rebuild_things()
        self.view.apply_layers()

    def export(self) -> None:
        if self.world is None:
            self.export_line.setText("nothing to export yet")
            return
        out = self.engine.paths.captures / "maps"
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = out / f"{self.world.target}_{self.area}_{stamp}.png"
        box = self.world.tiles.export(self.area, path)
        if box is None:
            self.export_line.setText("nothing observed in this area yet")
            return
        x0, y0, x1, y1 = box
        self.export_line.setText(
            f"saved {path.name}: {x1 - x0} x {y1 - y0} px, world ({x0}, {y0}) to ({x1}, {y1})"
        )
        os.startfile(str(path))

    def open_folder(self) -> None:
        folder = self._dir()
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))

    # --- refresh -------------------------------------------------------------------------

    def on_show(self) -> None:
        self.reload()
        self._update_agent()

    def on_tick(self) -> None:
        now = time.time()
        if now - self._last_check < REFRESH_S:
            return
        self._last_check = now
        if self._changed():
            self.reload()
        elif self.world is not None:
            self.view._refresh_tiles()
        self._update_agent()


__all__ = ["MappingPage", "QVBoxLayout", "QWidget"]
