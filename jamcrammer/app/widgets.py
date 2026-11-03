"""Reusable building blocks that only use theme tokens: cards, stat tiles, meters, chips,
pills, animated buttons, the run panel every bot page shares and the run strip the main
window shows when a bot runs on another page.

Animation rules (KICKOFF §12 "tactile"): every button press pulses, every meter and stat
tile animates to its new value, pages fade in, the run strip slides. All animations are
short (``theme.ANIM_*``) and never block the GUI thread.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QProgressBar,
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
PRESS_OPACITY = 0.55


def repolish(widget: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property changed."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_prop(widget: QWidget, name: str, value: Any) -> None:
    if widget.property(name) != value:
        widget.setProperty(name, value)
        repolish(widget)


# --- animation helpers ---------------------------------------------------------------------


def fade_in(widget: QWidget, ms: int = theme.ANIM_MS) -> None:
    """Fade a widget in; the effect is removed afterwards so it costs nothing at rest."""
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(ms)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def done() -> None:
        widget.setGraphicsEffect(None)  # type: ignore[arg-type]

    anim.finished.connect(done)
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


def animate_height(widget: QWidget, to: int, ms: int = theme.ANIM_MS) -> QPropertyAnimation:
    anim = QPropertyAnimation(widget, b"maximumHeight", widget)
    anim.setDuration(ms)
    anim.setStartValue(widget.maximumHeight())
    anim.setEndValue(to)
    anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim


class PressPulse(QPushButton):
    """A button whose press dips its opacity and springs back: the click is felt."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._effect: QGraphicsOpacityEffect | None = None
        self.pressed.connect(self._pulse)

    def _pulse(self) -> None:
        if self._effect is None:
            self._effect = QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(self._effect)
        anim = QVariantAnimation(self)
        anim.setDuration(theme.ANIM_FAST_MS)
        anim.setStartValue(PRESS_OPACITY)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        effect = self._effect
        anim.valueChanged.connect(lambda v: effect.setOpacity(float(v)))
        anim.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)


# --- cards -----------------------------------------------------------------------------------


class Card(QFrame):
    def __init__(self, title: str = "", help_text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(theme.PAD_MD, theme.PAD_MD, theme.PAD_MD, theme.PAD_MD)
        self.body.setSpacing(theme.PAD_SM)
        self.head = QHBoxLayout()
        if title:
            self.title = QLabel(title)
            self.title.setObjectName("CardTitle")
            self.head.addWidget(self.title)
            self.head.addStretch(1)
            self.body.addLayout(self.head)
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
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.set_status(status)

    def set_status(self, status: str) -> None:
        set_prop(self, "status", status)


class KindTag(QLabel):
    """A small outlined tag in the page's accent: BOT, TOOL, SYSTEM."""

    def __init__(self, text: str, accent: str, parent: QWidget | None = None) -> None:
        super().__init__(text.upper(), parent)
        self.setObjectName("KindTag")
        self.setProperty("accent", accent)


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
        self._curve = self._plot.plot(
            pen=pg.mkPen(QColor(color), width=2),
            fillLevel=0,
            brush=pg.mkBrush(QColor(color).red(), QColor(color).green(), QColor(color).blue(), 40),
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._plot)
        self.setFixedHeight(36)

    def push(self, value: float) -> None:
        if self._values and self._values[-1] == float(value) and len(self._values) > 2:
            self._values.append(float(value))
        else:
            self._values.append(float(value))
        self._curve.setData(list(self._values))


def _tabular(label: QLabel) -> None:
    font = label.font()
    if hasattr(font, "setFeature") and hasattr(QFont, "Tag"):
        font.setFeature(QFont.Tag("tnum"), 1)  # tabular numerals (Qt >= 6.7)
    label.setFont(font)


class StatTile(Card):
    """A number with a caption and a sparkline; the number counts to its new value."""

    def __init__(
        self,
        label: str,
        color: str,
        unit: str = "",
        parent: QWidget | None = None,
        *,
        spark: bool = True,
        size: str = "lg",
    ) -> None:
        super().__init__(parent=parent)
        self.unit = unit
        self._value: float | int | str = 0
        self._anim: QVariantAnimation | None = None
        self.value_label = QLabel("–")
        self.value_label.setObjectName("StatValue")
        if size != "lg":
            self.value_label.setProperty("size", size)
        _tabular(self.value_label)
        self.caption = QLabel(label.upper())
        self.caption.setObjectName("StatLabel")
        self.body.addWidget(self.value_label)
        self.body.addWidget(self.caption)
        self.spark: Sparkline | None = Sparkline(color) if spark else None
        if self.spark is not None:
            self.body.addWidget(self.spark)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _fmt(self, value: float | int | str) -> str:
        if isinstance(value, bool):
            return "yes" if value else "no"
        if isinstance(value, int):
            return f"{value:,}{self.unit}"
        if isinstance(value, float):
            return f"{value:,.1f}{self.unit}"
        return f"{value}{self.unit}"

    def set_value(self, value: float | int | str, push: bool = True) -> None:
        old = self._value
        self._value = value
        if isinstance(value, (int, float)) and isinstance(old, (int, float)) and old != value:
            if self._anim is not None:
                self._anim.stop()
            anim = QVariantAnimation(self)
            anim.setDuration(theme.ANIM_SLOW_MS)
            anim.setStartValue(float(old))
            anim.setEndValue(float(value))
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            is_int = isinstance(value, int)
            anim.valueChanged.connect(
                lambda v: self.value_label.setText(self._fmt(round(v) if is_int else float(v)))
            )
            anim.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)
            self._anim = anim
        else:
            self.value_label.setText(self._fmt(value))
        if push and self.spark is not None and isinstance(value, (int, float)):
            self.spark.push(float(value))


class Meter(QWidget):
    """A labelled bar with its value on the right; the bar animates to new values."""

    def __init__(
        self,
        label: str,
        accent: str = "tool",
        *,
        unit: str = "%",
        maximum: int = 100,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.unit = unit
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        top = QHBoxLayout()
        self.label = QLabel(label.upper())
        self.label.setObjectName("MeterLabel")
        self.value_label = QLabel("–")
        self.value_label.setObjectName("MeterValue")
        _tabular(self.value_label)
        top.addWidget(self.label)
        top.addStretch(1)
        top.addWidget(self.value_label)
        lay.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setRange(0, max(1, maximum))
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setProperty("accent", accent)
        self.bar.setFixedHeight(8)
        lay.addWidget(self.bar)
        self._anim: QPropertyAnimation | None = None

    def set_value(self, value: float, text: str | None = None, maximum: int | None = None) -> None:
        if maximum is not None and maximum != self.bar.maximum():
            self.bar.setRange(0, max(1, maximum))
        target = round(min(self.bar.maximum(), max(0.0, value)))
        if target != self.bar.value():
            if self._anim is not None:
                self._anim.stop()
            anim = QPropertyAnimation(self.bar, b"value", self)
            anim.setDuration(theme.ANIM_SLOW_MS)
            anim.setStartValue(self.bar.value())
            anim.setEndValue(target)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
            self._anim = anim
        self.value_label.setText(text if text is not None else f"{value:,.0f}{self.unit}")


class StatePill(QLabel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("IDLE", parent)
        self.setObjectName("StatePill")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_state("IDLE", "idle")

    def set_state(self, text: str, kind: str) -> None:
        if self.text() != text:
            self.setText(text)
        set_prop(self, "state", kind)


def icon_button(
    name: str,
    text: str = "",
    *,
    color: str | None = None,
    kind: str | None = None,
    accent: str | None = None,
    size: str | None = None,
    on_click: Callable[[], object] | None = None,
    tooltip: str = "",
) -> QPushButton:
    """A text button with a mono icon beside it. ``kind`` picks the fill (primary, ok,
    danger, ghost, accent with ``accent``); ``size="big"`` is the page's main action."""
    btn = PressPulse(text)
    if kind in ("primary", "ok", "danger", "accent"):
        color = color or theme.PALETTE.bg
    btn.setIcon(make_icon(name, color or theme.PALETTE.text))
    if kind:
        btn.setProperty("kind", kind)
    if accent:
        btn.setProperty("accent", accent)
    if size:
        btn.setProperty("size", size)
        btn.setMinimumHeight(theme.BIG_BUTTON_HEIGHT)
    if tooltip:
        btn.setToolTip(tooltip)
    if on_click is not None:
        btn.clicked.connect(on_click)
    return btn


def tool_button(
    name: str,
    tooltip: str,
    *,
    on_click: Callable[[], object] | None = None,
    checkable: bool = False,
) -> QToolButton:
    """An icon-only button for toolbars (zoom, layers, refresh)."""
    btn = QToolButton()
    btn.setIcon(make_icon(name, theme.PALETTE.text))
    btn.setProperty("kind", "icon")
    btn.setToolTip(tooltip)
    btn.setCheckable(checkable)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_click is not None:
        btn.clicked.connect(on_click)
    return btn


def nav_button(name: str, text: str, color: str, accent: str) -> QToolButton:
    btn = QToolButton()
    btn.setCheckable(True)
    btn.setIcon(make_icon(name, color, 20))
    btn.setText(text)
    btn.setProperty("accent", accent)
    btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def section(text: str) -> QLabel:
    lab = QLabel(text.upper())
    lab.setObjectName("SectionLabel")
    return lab


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
        head = QHBoxLayout()
        head.setSpacing(theme.PAD_SM)
        self.title_label = QLabel(self.title)
        self.title_label.setObjectName("PageTitle")
        head.addWidget(self.title_label)
        kind = theme.PAGE_KINDS.get(self.key, "tool")
        if kind in ("bot", "tool", "system"):
            head.addWidget(KindTag(kind, self.accent))
        head.addStretch(1)
        self.head = head
        self.content.addLayout(head)
        if self.subtitle:
            s = QLabel(self.subtitle)
            s.setObjectName("PageSubtitle")
            s.setWordWrap(True)
            self.content.addWidget(s)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

    @property
    def accent(self) -> str:
        kind = theme.PAGE_KINDS.get(self.key, "tool")
        if kind == "bot":
            return self.key
        return "home" if kind == "home" else "tool"

    def finish(self) -> None:
        self.content.addStretch(1)

    def on_show(self) -> None:
        pass

    def on_tick(self) -> None:
        pass


# --- the run panel: every bot page's controls and live stats ------------------------------


class RunPanel(QFrame):
    """The top of every bot page: one status line, Start / Stop / Pause, Dry run, and the
    bot's own stat tiles and chart. The main window shows a compact strip of the same state
    only while the bot runs and another page is open."""

    start_clicked = Signal()
    stop_clicked = Signal()
    pause_clicked = Signal()
    dry_toggled = Signal(bool)
    hunt_toggled = Signal(bool)

    def __init__(self, title: str, accent: str, icon: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RunPanel")
        self.setProperty("accent", accent)
        self.accent = accent
        body = QVBoxLayout(self)
        body.setContentsMargins(theme.PAD_MD, theme.PAD_MD, theme.PAD_MD, theme.PAD_MD)
        body.setSpacing(theme.PAD_SM)
        top = QHBoxLayout()
        top.setSpacing(theme.PAD_SM)
        ic = QLabel()
        ic.setPixmap(make_icon(icon, theme.accent_color(accent), 22).pixmap(22, 22))
        top.addWidget(ic)
        self.title = QLabel(title)
        self.title.setObjectName("CardTitle")
        top.addWidget(self.title)
        self.pill = StatePill()
        top.addWidget(self.pill)
        top.addStretch(1)
        self.detail = QLabel("")
        self.detail.setObjectName("StatusDetail")
        top.addWidget(self.detail)
        body.addLayout(top)
        self.status = QLabel("Ready")
        self.status.setObjectName("StatusLine")
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(self.status)
        controls = QHBoxLayout()
        controls.setSpacing(theme.PAD_SM)
        self.start = icon_button(
            "play",
            "Start",
            kind="accent",
            accent=accent,
            size="big",
            on_click=self.start_clicked.emit,
            tooltip="F9",
        )
        self.stop = icon_button(
            "stop", "Stop", kind="danger", size="big", on_click=self.stop_clicked.emit, tooltip="F9"
        )
        self.pause = icon_button(
            "pause",
            "Pause",
            kind="ghost",
            size="big",
            on_click=self.pause_clicked.emit,
            tooltip="F11",
        )
        self.dry = QCheckBox("Dry run")
        self.dry.setToolTip("Capture, analyse and log, but never send input")
        self.dry.toggled.connect(self.dry_toggled.emit)
        self.hunt = QCheckBox("Hunt Best Guess")
        self.hunt.setToolTip(
            "Before farming, read when Best Guess is due from the party tray; farm until then, "
            "leave at a round boundary, join Best Guess and hand over to the Best Guess Farmer"
        )
        self.hunt.toggled.connect(self.hunt_toggled.emit)
        self.hunt.setVisible(False)  # farmer bots show it (ModePage)
        for w in (self.start, self.stop, self.pause):
            controls.addWidget(w)
        controls.addSpacing(theme.PAD_SM)
        controls.addWidget(self.dry)
        controls.addWidget(self.hunt)
        controls.addStretch(1)
        self.hint = QLabel("F9 start · F11 pause · F12 kill")
        self.hint.setObjectName("HotkeyHint")
        controls.addWidget(self.hint)
        body.addLayout(controls)
        self.stats_row = QHBoxLayout()
        self.stats_row.setSpacing(theme.PAD_SM)
        body.addLayout(self.stats_row)
        self.chart_row = QVBoxLayout()
        self.chart_row.setSpacing(theme.PAD_SM)
        body.addLayout(self.chart_row)

    def add_stats(self, tiles: Sequence[QWidget]) -> None:
        for t in tiles:
            self.stats_row.addWidget(t)

    def add_chart(self, widget: QWidget) -> None:
        self.chart_row.addWidget(widget)

    def set_running(self, running: bool, paused: bool, *, can_start: bool = True) -> None:
        """Start and Dry run follow ``can_start`` (the den gate); Stop and Pause follow the
        bot, so a running bot can always be stopped."""
        self.start.setEnabled(not running and can_start)
        self.dry.setEnabled(running or can_start)
        self.stop.setEnabled(running)
        self.pause.setEnabled(running)
        self.pause.setText("Resume" if paused else "Pause")
        self.pause.setIcon(make_icon("play" if paused else "pause", theme.PALETTE.text))

    def set_status(self, line: str, detail: str = "") -> None:
        if self.status.text() != line:
            self.status.setText(line)
        if self.detail.text() != detail:
            self.detail.setText(detail)


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


# --- calibration wizard pieces ---------------------------------------------------------------


class StepProgress(QWidget):
    """'Step k of n' with a thin accent bar underneath."""

    def __init__(self, accent: str = "tool", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        top = QHBoxLayout()
        self.label = QLabel("")
        self.label.setObjectName("MeterLabel")
        self.detail = QLabel("")
        self.detail.setObjectName("MeterValue")
        _tabular(self.detail)
        top.addWidget(self.label)
        top.addStretch(1)
        top.addWidget(self.detail)
        lay.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setProperty("accent", accent)
        self.bar.setFixedHeight(6)
        lay.addWidget(self.bar)

    def set_step(self, k: int, n: int, caption: str = "") -> None:
        self.label.setText(caption.upper() if caption else "PROGRESS")
        self.detail.setText(f"Step {k} of {n}")
        if self.bar.maximum() != max(1, n):
            self.bar.setRange(0, max(1, n))
        self.bar.setValue(max(0, min(n, k)))


class Segmented(QWidget):
    """Two or more mutually exclusive icon+text buttons (Guided / Advanced)."""

    changed = Signal(str)

    def __init__(
        self, options: Sequence[tuple[str, str, str]], current: str, parent: QWidget | None = None
    ) -> None:
        """``options`` = (key, icon name, text)."""
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self._buttons: dict[str, QToolButton] = {}
        for key, icon, text in options:
            b = QToolButton()
            b.setText(text)
            b.setIcon(make_icon(icon, theme.PALETTE.text))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setProperty("kind", "icon")
            b.setCheckable(True)
            b.setChecked(key == current)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, k=key: self.select(k))
            self._buttons[key] = b
            lay.addWidget(b)
        self.current = current

    def select(self, key: str) -> None:
        for k, b in self._buttons.items():
            b.setChecked(k == key)
        if key != self.current:
            self.current = key
            self.changed.emit(key)
