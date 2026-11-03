"""Main window: grouped nav rail, page stack, the run strip, tray, mini HUD, overlap warning.

Navigation rules (William, 2026-11-02): starting a bot takes you to its page; the page's
own run panel holds Start / Stop / Pause and the live stats. The **run strip** at the top
appears only while a bot runs and you are looking at another page; it slides in with the
bot's state and a button back to its page, and slides away when you return."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
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
from jamcrammer.app.engine import MODE_LABELS, Engine
from jamcrammer.app.hud import MiniHud
from jamcrammer.app.icons import make_icon, render_svg_file
from jamcrammer.app.pages import build_pages
from jamcrammer.app.widgets import (
    Page,
    StatePill,
    animate_height,
    fade_in,
    icon_button,
    nav_button,
    repolish,
    set_prop,
)
from jamcrammer.core.events import Event
from jamcrammer.core.paths import Paths

TICK_MS = 500
OVERLAP_CHECK_MS = 1000
TRAY_ICON_PX = 32
MODE_ICONS = {
    "hunt": "bestguess",
    "farm": "arcade",
    "adventure": "adventure",
    "opportunist": "opportunist",
}


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
        self.toggle.setToolTip("Collapse the rail")
        self.toggle.clicked.connect(self.toggle_collapsed)
        head.addWidget(self.brand)
        head.addStretch(1)
        head.addWidget(self.toggle)
        lay.addLayout(head)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: list[QToolButton] = []
        self.labels: list[QLabel] = []
        by_key = {p.key: (i, p) for i, p in enumerate(pages)}
        for title, keys in theme.NAV_GROUPS:
            present = [by_key[k] for k in keys if k in by_key]
            if not present:
                continue
            if title:
                lab = QLabel(title)
                lab.setObjectName("NavGroup")
                lay.addWidget(lab)
                self.labels.append(lab)
            for i, page in present:
                color = theme.PALETTE.for_page(page.key)
                btn = nav_button(page.icon, page.title, color, page.accent)
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
        btn = self.group.button(index)
        if btn is not None:
            btn.setChecked(True)

    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        self.setFixedWidth(theme.NAV_COLLAPSED if self._collapsed else theme.NAV_WIDTH)
        self.brand.setVisible(not self._collapsed)
        self.version.setVisible(not self._collapsed)
        for lab in self.labels:
            lab.setVisible(not self._collapsed)
        style = (
            Qt.ToolButtonStyle.ToolButtonIconOnly
            if self._collapsed
            else Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        for b in self.buttons:
            b.setToolButtonStyle(style)


class RunStrip(QFrame):
    """The running bot's state while another page is open: name, one line, Pause / Stop,
    and a button back to the bot's page."""

    def __init__(self, engine: Engine, go_to: Callable[[str], None]) -> None:
        super().__init__()
        self.setObjectName("RunStrip")
        self.engine = engine
        self._go_to = go_to
        self._shown = False
        self.setMaximumHeight(0)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(theme.PAD_MD, theme.PAD_SM, theme.PAD_MD, theme.PAD_SM)
        lay.setSpacing(theme.PAD_MD)
        self.icon = QLabel()
        self.title = QLabel("")
        self.title.setObjectName("StripTitle")
        self.pill = StatePill()
        self.line = QLabel("")
        self.line.setObjectName("StatusDetail")
        self.pause_btn = icon_button("pause", "Pause", kind="ghost", on_click=engine.toggle_pause)
        self.stop_btn = icon_button("stop", "Stop", kind="danger", on_click=engine.stop_mode)
        self.back_btn = icon_button("arrow-right", "Open", kind="ghost", on_click=self._open)
        self.lease = QLabel("")
        self.lease.setObjectName("LeaseIndicator")
        for w in (self.icon, self.title, self.pill, self.line):
            lay.addWidget(w)
        lay.addStretch(1)
        lay.addWidget(self.lease)
        for b in (self.pause_btn, self.stop_btn, self.back_btn):
            lay.addWidget(b)
        self._mode = ""

    def _open(self) -> None:
        if self._mode:
            self._go_to(self._mode)

    def refresh(self, current_page_key: str) -> None:
        st = self.engine.state
        mode = st.mode or ""
        want = bool(mode) and current_page_key != mode
        if mode and mode != self._mode:
            self._mode = mode
            self.title.setText(MODE_LABELS.get(mode, mode))
            self.icon.setPixmap(
                make_icon(MODE_ICONS.get(mode, "bot"), theme.PALETTE.for_mode(mode), 20).pixmap(
                    20, 20
                )
            )
            set_prop(self, "accent", mode)
        if want:
            self.pill.set_state(st.mode_state, st.pill_kind)
            self.line.setText(st.mode_detail)
            self.pause_btn.setText("Resume" if st.paused else "Pause")
            owner = self.engine.lease.owner or "nobody"
            handoff = f"  ({st.handoff})" if st.handoff else ""
            self.lease.setText(
                f"input: {owner}{handoff}" + ("  (dry run)" if st.dry_run else "")
            )
        if want != self._shown:
            self._shown = want
            animate_height(self, theme.RUN_STRIP_HEIGHT if want else 0)


class MainWindow(QMainWindow):
    def __init__(self, engine: Engine, paths: Paths | None = None) -> None:
        super().__init__()
        self.engine = engine
        self.paths = paths or Paths()
        self.setWindowTitle(f"JamCrammer {__version__}")
        self.resize(1360, 860)
        self.setMinimumSize(1180, 700)
        self._app_icon = self._load_app_icon()
        self.setWindowIcon(self._app_icon)

        self.pages: list[Page] = build_pages(engine, self)
        self.stack = QStackedWidget()
        for p in self.pages:
            self.stack.addWidget(p)
        self.nav = NavRail(self.pages, self.show_page)
        self.strip = RunStrip(engine, self.show_page_key)

        central = QWidget()
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self.strip)
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
        self._current = -1
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
        if index == self._current:
            return
        self._current = index
        page = self.pages[index]
        self.stack.setCurrentIndex(index)
        self.nav.select(index)
        page.on_show()
        if self.isVisible():
            fade_in(page)
        self.strip.refresh(page.key)

    def show_page_key(self, key: str) -> None:
        for i, p in enumerate(self.pages):
            if p.key == key:
                self.show_page(i)
                return

    @property
    def current_key(self) -> str:
        return self.pages[self.stack.currentIndex()].key

    # --- updates ------------------------------------------------------------------------------

    def _tick(self) -> None:
        self.strip.refresh(self.current_key)
        self._update_tray_icon(self.engine.state.pill_kind)
        page = self.pages[self.stack.currentIndex()]
        page.on_tick()
        self.hud.refresh()
        if self.engine.state.last_error:
            self.status.showMessage(self.engine.state.last_error, 4000)

    def _on_event(self, event: Event) -> None:
        if event.type in ("engine.mode_started", "engine.handoff"):
            # a start, or a farmer handing over to the Best Guess Farmer: show the bot's page
            mode = str(event.payload.get("mode", ""))
            if mode:
                self.show_page_key(mode)
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
            "GUI overlaps the game window: it will steal clicks" if over else ""
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
