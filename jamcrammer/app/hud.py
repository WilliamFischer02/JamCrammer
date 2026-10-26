"""Mini HUD: compact always-on-top widget for a second monitor (KICKOFF §12.3)."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from jamcrammer.app import theme
from jamcrammer.app.engine import MODE_LABELS, Engine
from jamcrammer.app.widgets import StatePill, icon_button


class MiniHud(QFrame):
    def __init__(self, engine: Engine) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.engine = engine
        self.setObjectName("MiniHud")
        self.setFixedSize(*theme.HUD_SIZE)
        self._drag: QPoint | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.PAD_MD, theme.PAD_SM, theme.PAD_MD, theme.PAD_SM)
        top = QHBoxLayout()
        self.mode = QLabel("IDLE")
        self.mode.setObjectName("HudMode")
        self.gems = QLabel("0 gems/hr")
        self.gems.setObjectName("HudGems")
        top.addWidget(self.mode)
        top.addStretch(1)
        top.addWidget(self.gems)
        lay.addLayout(top)
        bottom = QHBoxLayout()
        self.pill = StatePill()
        self.pause = icon_button("pause", "", on_click=self._pause, tooltip="Pause / resume (F11)")
        self.pause.setFixedWidth(40)
        bottom.addWidget(self.pill, 1)
        bottom.addWidget(self.pause)
        lay.addLayout(bottom)
        self.hide()

    def _pause(self) -> None:
        self.engine.toggle_pause()

    def refresh(self) -> None:
        if not self.isVisible():
            return
        st = self.engine.state
        self.mode.setText(MODE_LABELS.get(st.mode or "", "IDLE").upper())
        self.pill.set_state(st.pill_text, st.pill_kind)
        elapsed_h = max(
            1e-6, (self.engine.ledger.stats.readings and 1) or 1
        )  # placeholder until ledger has time
        self.gems.setText(f"{self.engine.ledger.stats.session_delta / elapsed_h:,.0f} gems")

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _e: QMouseEvent) -> None:  # noqa: N802 - Qt override
        self._drag = None
