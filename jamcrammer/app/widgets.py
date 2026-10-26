"""Reusable building blocks that only use theme tokens: cards, stat tiles, chips, pills."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from jamcrammer.app import theme
from jamcrammer.app.icons import make_icon

SPARK_POINTS = 60


def repolish(widget: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property changed."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


class Card(QFrame):
    def __init__(self, title: str = "", help_text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(theme.PAD_MD, theme.PAD_MD, theme.PAD_MD, theme.PAD_MD)
        self.body.setSpacing(theme.PAD_SM)
        if title:
            head = QHBoxLayout()
            self.title = QLabel(title)
            self.title.setObjectName("CardTitle")
            head.addWidget(self.title)
            head.addStretch(1)
            self.head = head
            self.body.addLayout(head)
        if help_text:
            h = QLabel(help_text)
            h.setObjectName("CardHelp")
            h.setWordWrap(True)
            self.body.addWidget(h)

    def add(self, widget: QWidget) -> None:
        self.body.addWidget(widget)

    def add_layout(self, layout: QLayout) -> None:
        self.body.addLayout(layout)


class Chip(QLabel):
    def __init__(self, text: str, status: str = "neutral", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("Chip")
        self.set_status(status)

    def set_status(self, status: str) -> None:
        self.setProperty("status", status)
        repolish(self)


class Sparkline(QWidget):
    """Tiny pyqtgraph plot without axes; keeps the last N values."""

    def __init__(self, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        import pyqtgraph as pg

        self._values: deque[float] = deque(maxlen=SPARK_POINTS)
        self._plot = pg.PlotWidget(background=None)
        self._plot.hideAxis("left")
        self._plot.hideAxis("bottom")
        self._plot.setMouseEnabled(False, False)
        self._plot.setMenuEnabled(False)
        self._plot.hideButtons()
        self._curve = self._plot.plot(pen=pg.mkPen(QColor(color), width=2))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._plot)
        self.setFixedHeight(36)

    def push(self, value: float) -> None:
        self._values.append(float(value))
        self._curve.setData(list(self._values))


class StatTile(Card):
    def __init__(
        self, label: str, color: str, unit: str = "", parent: QWidget | None = None
    ) -> None:
        super().__init__(parent=parent)
        self.unit = unit
        self.value_label = QLabel("–")
        self.value_label.setObjectName("StatValue")
        font = self.value_label.font()
        if hasattr(font, "setFeature") and hasattr(QFont, "Tag"):
            font.setFeature(QFont.Tag("tnum"), 1)  # tabular numerals (Qt >= 6.7)
        self.value_label.setFont(font)
        self.caption = QLabel(label.upper())
        self.caption.setObjectName("StatLabel")
        self.spark = Sparkline(color)
        self.body.addWidget(self.value_label)
        self.body.addWidget(self.caption)
        self.body.addWidget(self.spark)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_value(self, value: float | int | str, push: bool = True) -> None:
        text = (
            f"{value:,}"
            if isinstance(value, int)
            else (f"{value:.1f}" if isinstance(value, float) else value)
        )
        self.value_label.setText(f"{text}{self.unit}")
        if push and isinstance(value, (int, float)):
            self.spark.push(float(value))


class StatePill(QLabel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("IDLE", parent)
        self.setObjectName("StatePill")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_state("IDLE", "idle")

    def set_state(self, text: str, kind: str) -> None:
        if self.text() != text:
            self.setText(text)
        if self.property("state") != kind:
            self.setProperty("state", kind)
            repolish(self)


def icon_button(
    name: str,
    text: str = "",
    *,
    color: str | None = None,
    kind: str | None = None,
    on_click: Callable[[], None] | None = None,
    tooltip: str = "",
) -> QPushButton:
    btn = QPushButton(text)
    btn.setIcon(make_icon(name, color or theme.PALETTE.text))
    if kind:
        btn.setProperty("kind", kind)
    if tooltip:
        btn.setToolTip(tooltip)
    if on_click is not None:
        btn.clicked.connect(on_click)
    return btn


def nav_button(name: str, text: str, color: str) -> QToolButton:
    btn = QToolButton()
    btn.setCheckable(True)
    btn.setIcon(make_icon(name, color, 20))
    btn.setText(text)
    btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


class Page(QWidget):
    """Scrollable page with a title row; subclasses fill ``self.content``."""

    key = "page"
    title = "Page"
    subtitle = ""
    icon = "dashboard"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner.setObjectName("Page")
        self.content = QVBoxLayout(inner)
        self.content.setContentsMargins(theme.PAD_LG, theme.PAD_LG, theme.PAD_LG, theme.PAD_LG)
        self.content.setSpacing(theme.PAD_MD)
        t = QLabel(self.title)
        t.setObjectName("PageTitle")
        self.content.addWidget(t)
        if self.subtitle:
            s = QLabel(self.subtitle)
            s.setObjectName("PageSubtitle")
            s.setWordWrap(True)
            self.content.addWidget(s)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

    def finish(self) -> None:
        self.content.addStretch(1)

    def on_show(self) -> None:
        pass

    def on_tick(self) -> None:
        pass


def row(*widgets: QWidget, stretch_last: bool = False) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(theme.PAD_SM)
    for w in widgets:
        lay.addWidget(w)
    if stretch_last:
        lay.addStretch(1)
    return lay


def grid_of(widgets: Sequence[QWidget], columns: int) -> QGridLayout:
    lay = QGridLayout()
    lay.setSpacing(theme.PAD_MD)
    for i, w in enumerate(widgets):
        lay.addWidget(w, i // columns, i % columns)
    return lay
