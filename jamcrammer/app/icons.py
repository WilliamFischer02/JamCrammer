"""SVG icons tinted from the palette and rendered crisp at the device pixel ratio.

The icon set lives in ``assets/icons/*.svg`` (24x24, stroke 2, ``currentColor``). Rendering goes
through QSvgRenderer at ``size * dpr`` so nothing raster is ever scaled (KICKOFF §12.1).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from jamcrammer.core.paths import Paths

DEFAULT_ICON_PX = 20


def icon_dir() -> Path:
    return Paths().assets / "icons"


@lru_cache(maxsize=256)
def _svg_text(name: str) -> str:
    path = icon_dir() / f"{name}.svg"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def render_svg(name: str, color: str, size_px: int, dpr: float = 1.0) -> QPixmap:
    """Render ``assets/icons/<name>.svg`` tinted with ``color`` at exact pixel size."""
    svg = _svg_text(name).replace("currentColor", color)
    px = round(size_px * dpr)
    image = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    if svg:
        renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, px, px))
        painter.end()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def make_icon(name: str, color: str, size_px: int = DEFAULT_ICON_PX, dpr: float = 1.0) -> QIcon:
    icon = QIcon()
    for ratio in sorted({1.0, dpr, 1.5, 2.0}):
        icon.addPixmap(render_svg(name, color, size_px, ratio))
    return icon


def render_svg_file(path: Path, size_px: int) -> QImage:
    """Render any SVG file (e.g. the app icon) at an exact pixel size."""
    renderer = QSvgRenderer(str(path))
    image = QImage(size_px, size_px, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size_px, size_px))
    painter.end()
    return image
