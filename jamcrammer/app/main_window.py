"""Main window: nav rail, command bar, page stack, tray, mini HUD, overlap warning."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QStackedWidget,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from jamcrammer import __version__
from jamcrammer.app import theme
from jamcrammer.app.bridge import QtBus
from jamcrammer.app.engine import MODE_LABELS, MODES, Engine
from jamcrammer.app.hud import MiniHud
from jamcrammer.app.icons import make_icon, render_svg_file
from jamcrammer.app.pages import build_pages
from jamcrammer.app.widgets import Page, StatePill, icon_button, nav_button, repolish
from jamcrammer.core.events import Event
from jamcrammer.core.paths import Paths

TICK_MS = 500
OVERLAP_CHECK_MS = 1000
TRAY_ICON_PX = 32


class NavRail(QFrame):
    def __init__(self, pages: list[Page], on_select: Callable[[int], None]) -> None:
        super().__init__()
        self.setObjectName("NavRail")
        self.setFixedWidth(theme.NAV_WIDTH)
        self._collapsed = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.PAD_SM, theme.PAD_SM, theme.PAD_SM, theme.PAD_SM)
        lay.setSpacing(2)
        head = QHBoxLayout()
        self.brand = QLabel("JamCrammer")
        self.brand.setObjectName("NavBrand")
        self.toggle = QToolButton()
        self.toggle.setIcon(make_icon("menu", theme.PALETTE.muted))
        self.toggle.setAutoRaise(True)
        self.toggle.clicked.connect(self.toggle_collapsed)
        head.addWidget(self.brand)
        head.addStretch(1)
        head.addWidget(self.toggle)
        lay.addLayout(head)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: list[QToolButton] = []
        for i, page in enumerate(pages):
            color = theme.PALETTE.for_mode(page.key)
            btn = nav_button(page.icon, page.title, color)
            btn.setToolTip(f"{page.title}  (Ctrl+{i + 1})" if i < 9 else page.title)
            self.group.addButton(btn, i)
            lay.addWidget(btn)
            self.buttons.append(btn)
        lay.addStretch(1)
        self.version = QLabel(f"v{__version__}")
        self.version.setObjectName("CardHelp")
        lay.addWidget(self.version)
        self.group.idClicked.connect(on_select)

    def select(self, index: int) -> None:
        self.buttons[index].setChecked(True)

    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        self.setFixedWidth(theme.NAV_COLLAPSED if self._collapsed else theme.NAV_WIDTH)
        self.brand.setVisible(not self._collapsed)
        self.version.setVisible(not self._collapsed)
        style = (
            Qt.ToolButtonStyle.ToolButtonIconOnly
            if self._collapsed
            else Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        for b in self.buttons:
            b.setToolButtonStyle(style)


class CommandBar(QFrame):
    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self.setObjectName("CommandBar")
        self.setFixedHeight(theme.COMMAND_BAR_HEIGHT)
        self.engine = engine
        lay = QHBoxLayout(self)
        lay.setContentsMargins(theme.PAD_MD, theme.PAD_SM, theme.PAD_MD, theme.PAD_SM)
        lay.setSpacing(theme.PAD_MD)
        self.mode = QComboBox()
        for m in MODES:
            self.mode.addItem(MODE_LABELS[m], m)
        self.mode.setCurrentIndex(0)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.mode.setMinimumWidth(170)
        self.pill = StatePill()
        self.pill.setMinimumWidth(200)
        self.pill.setMaximumWidth(320)
        self.start = icon_button(
            "play", "Start", color=theme.PALETTE.bg, kind="ok", on_click=self._start, tooltip="F9"
        )
        self.stop = icon_button(
            "stop", "Stop", color=theme.PALETTE.bg, kind="danger", on_click=self._stop, tooltip="F9"
        )
        self.pause = icon_button("pause", "Pause", on_click=self._pause, tooltip="F11")
        self.dry = QCheckBox("Dry-run")
        self.dry.setChecked(engine.state.dry_run)
        self.dry.setToolTip("Capture, analyse and log, but never send input")
        self.dry.toggled.connect(engine.set_dry_run)
        self.hotkeys = QLabel("F9 · F11 · F12 · F6-F8")
        self.hotkeys.setToolTip("F9 start/stop the selected mode · F11 pause/resume · F12 kill · F6 mark · F7 correct · F8 wrong")
        self.hotkeys.setObjectName("HotkeyHint")
        self.lease = QLabel("input: nobody")
        self.lease.setObjectName("LeaseIndicator")
        for w in (self.mode, self.pill, self.start, self.stop, self.pause, self.dry):
            lay.addWidget(w)
        lay.addStretch(1)
        lay.addWidget(self.hotkeys)
        lay.addSpacing(theme.PAD_MD)
        lay.addWidget(self.lease)
        self.lease.setMinimumWidth(110)

    def _mode_changed(self, index: int) -> None:
        self.engine.selected_mode = str(self.mode.itemData(index))

    def _start(self) -> None:
        self.engine.start_mode(self.engine.selected_mode)

    def _stop(self) -> None:
        self.engine.stop_mode()

    def _pause(self) -> None:
        self.engine.toggle_pause()

    def refresh(self) -> None:
        st = self.engine.state
        self.pill.set_state(st.pill_text, st.pill_kind)
        running = st.mode is not None
        self.start.setEnabled(not running and not st.killed)
        self.stop.setEnabled(running)
        self.pause.setText("Resume" if st.paused else "Pause")
        owner = self.engine.lease.owner or "nobody"
        self.lease.setText(f"input: {owner}" + ("  (dry-run)" if st.dry_run else ""))


class MainWindow(QMainWindow):
    def __init__(self, engine: Engine, paths: Paths | None = None) -> None:
        super().__init__()
        self.engine = engine
        self.paths = paths or Paths()
        self.setWindowTitle(f"JamCrammer {__version__}")
        self.resize(1360, 860)
        self.setMinimumSize(1280, 720)
        self._app_icon = self._load_app_icon()
        self.setWindowIcon(self._app_icon)

        self.pages: list[Page] = build_pages(engine, self)
        self.stack = QStackedWidget()
        for p in self.pages:
            self.stack.addWidget(p)
        self.nav = NavRail(self.pages, self.show_page)
        self.bar = CommandBar(engine)

        central = QWidget()
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self.bar)
        right.addWidget(self.stack, 1)
        h.addWidget(self.nav)
        h.addLayout(right, 1)
        self.setCentralWidget(central)
        self.status = self.statusBar()
        self.overlap_label = QLabel("")
        self.status.addPermanentWidget(self.overlap_label)

        for i in range(min(9, len(self.pages))):
            sc = QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self)
            sc.activated.connect(lambda i=i: self.show_page(i))

        self.hud = MiniHud(engine)
        self.hud_action = QAction("Mini HUD", self, checkable=True)
        self.hud_action.toggled.connect(self._toggle_hud)
        self._build_tray()

        self.bridge = QtBus(engine.bus, self)
        self.bridge.received.connect(self._on_event)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(TICK_MS)
        self.overlap_timer = QTimer(self)
        self.overlap_timer.timeout.connect(self._check_overlap)
        self.overlap_timer.start(OVERLAP_CHECK_MS)
        self.show_page(0)

    # --- icons / tray ------------------------------------------------------------------------

    def _load_app_icon(self) -> QIcon:
        svg = self.paths.assets / "app.svg"
        icon = QIcon()
        if svg.exists():
            for px in (16, 24, 32, 48, 64, 128, 256):
                icon.addPixmap(QPixmap.fromImage(render_svg_file(svg, px)))
        return icon

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self)
        self._tray_state = ""
        menu = QMenu()
        for text, fn in (
            ("Show JamCrammer", self.showNormal),
            ("Start", lambda: self.engine.start_mode(self.engine.selected_mode)),
            ("Stop", self.engine.stop_mode),
            ("Pause / Resume", self.engine.toggle_pause),
        ):
            act = QAction(text, self)
            act.triggered.connect(fn)
            menu.addAction(act)
        menu.addAction(self.hud_action)
        menu.addSeparator()
        quit_act = QAction("Quit", self)
        quit_act.triggered.connect(QApplication.quit)
        menu.addAction(quit_act)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: (
                self.showNormal() if reason == QSystemTrayIcon.ActivationReason.Trigger else None
            )
        )
        self._update_tray_icon("idle")
        self.tray.show()

    def _update_tray_icon(self, kind: str) -> None:
        if kind == self._tray_state:
            return
        self._tray_state = kind
        color = {
            "running": theme.PALETTE.ok,
            "paused": theme.PALETTE.warn,
            "error": theme.PALETTE.danger,
        }.get(kind, theme.PALETTE.idle)
        dpr = self.devicePixelRatioF() if self.windowHandle() else 1.0
        self.tray.setIcon(make_icon("tray", color, TRAY_ICON_PX, dpr))
        self.tray.setToolTip(f"JamCrammer: {self.engine.state.pill_text}")

    def _toggle_hud(self, on: bool) -> None:
        self.hud.setVisible(on)

    # --- navigation ---------------------------------------------------------------------------

    def show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.nav.select(index)
        self.pages[index].on_show()

    def show_page_key(self, key: str) -> None:
        for i, p in enumerate(self.pages):
            if p.key == key:
                self.show_page(i)
                return

    # --- updates ------------------------------------------------------------------------------

    def _tick(self) -> None:
        self.bar.refresh()
        self._update_tray_icon(self.engine.state.pill_kind)
        page = self.pages[self.stack.currentIndex()]
        page.on_tick()
        self.hud.refresh()
        if self.engine.state.last_error:
            self.status.showMessage(self.engine.state.last_error, 4000)

    def _on_event(self, event: Event) -> None:
        for p in self.pages:
            handler = getattr(p, "on_event", None)
            if handler is not None:
                handler(event)

    def _check_overlap(self) -> None:
        g = self.frameGeometry()
        over = (
            self.isVisible()
            and not self.isMinimized()
            and self.engine.overlaps_game(g.left(), g.top(), g.right(), g.bottom())
        )
        self.overlap_label.setText(
            "⚠ GUI overlaps the AJC window: it will steal clicks" if over else ""
        )
        self.overlap_label.setProperty("warn", over)
        repolish(self.overlap_label)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        self.timer.stop()
        self.overlap_timer.stop()
        self.bridge.detach()
        self.hud.close()
        self.tray.hide()
        self.engine.shutdown()
        super().closeEvent(event)
