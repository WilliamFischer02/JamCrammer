"""Den Planner page: when is Best Guess due, read it from the game, run the planner."""

from __future__ import annotations

import threading

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QSpinBox

from jamcrammer.app import theme
from jamcrammer.app.engine import Engine
from jamcrammer.app.pages.modes import ModePage, _form
from jamcrammer.app.widgets import Card, Meter, StatTile, icon_button, row
from jamcrammer.modes.planner import format_eta

PLAN_MAX_S = 2 * 3600.0  # the countdown meter's full scale


class PlannerPage(ModePage):
    key = "planner"
    mode = "planner"
    title = "Den Planner"
    subtitle = "Derby in your den until Best Guess is due, then Best Guess."
    icon = "home"

    def build_stats(self) -> None:
        c = theme.PALETTE.planner
        self.t_eta = StatTile("Best Guess in", c, spark=False, size="md")
        self.t_phase = StatTile("Phase", c, spark=False, size="md")
        self.t_gems = StatTile("Gems", theme.PALETTE.arcade, spark=False, size="md")
        self.panel.add_stats([self.t_eta, self.t_phase, self.t_gems])
        self.m_eta = Meter("Countdown", "planner")
        self.panel.add_chart(self.m_eta)

    def update_stats(self) -> None:
        st = self.engine.state
        eta = st.bg_eta_s()
        if eta is None:
            self.t_eta.set_value("not set")
            self.m_eta.set_value(0, "–")
        else:
            self.t_eta.set_value(format_eta(eta) if eta > 0 else "due now")
            self.m_eta.set_value(
                100.0 * (1.0 - min(1.0, max(0.0, eta) / PLAN_MAX_S)), format_eta(max(0.0, eta))
            )
        self.t_phase.set_value(st.mode_state.title() if st.mode == self.mode else "idle")
        self.t_gems.set_value(int(self.engine.ledger.stats.session_delta))

    def build_settings(self) -> None:
        eng: Engine = self.engine
        self._pending: tuple[int | None, str] | None = None
        card = Card(
            "Time until Best Guess", "Type what the party tray shows, or read it from the game."
        )
        form = _form()
        self.hours = QSpinBox()
        self.hours.setRange(0, 12)
        self.minutes = QSpinBox()
        self.minutes.setRange(0, 59)
        eta = eng.state.bg_eta_s()
        if eta is not None and eta > 0:
            self.hours.setValue(int(eta // 3600))
            self.minutes.setValue(int(eta % 3600 // 60))
        form.addRow("Hours", self.hours)
        form.addRow("Minutes", self.minutes)
        self.note = QLabel("")
        self.note.setObjectName("CardHelp")
        self.note.setWordWrap(True)
        form.addRow("", self.note)
        card.add_layout(form)
        card.add_layout(
            row(
                icon_button(
                    "timer", "Set timer", kind="accent", accent="planner", on_click=self._set_timer
                ),
                icon_button(
                    "parties", "Read from game", kind="ghost", on_click=self._read_from_game
                ),
                stretch_last=True,
            )
        )
        self.content.addWidget(card)
        plan = Card("What Start does")
        plan.add(
            QLabel(
                "1. Reads the Best Guess time from the party tray.\n"
                "2. Goes to your den and opens Jamaa Derby (PRO).\n"
                "3. Races until the timer is within the lead, then leaves at a leaderboard.\n"
                "4. Watches My Parties, joins Best Guess and farms it until you stop."
            )
        )
        self.content.addWidget(plan)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    def _set_timer(self) -> None:
        seconds = self.hours.value() * 3600 + self.minutes.value() * 60
        self.engine.set_best_guess_eta(seconds, "typed")
        self._tick()

    def _read_from_game(self) -> None:
        self.note.setText("reading the party tray...")

        def work() -> None:
            eta, note = self.engine.read_eta_from_game()
            self._pending = (eta, note)

        threading.Thread(target=work, name="planner-read-eta", daemon=True).start()

    def _tick(self) -> None:
        pending = getattr(self, "_pending", None)
        if pending is not None:
            eta, note = pending
            self._pending = None
            self.note.setText(note)
            if eta is not None:
                self.hours.setValue(int(eta // 3600))
                self.minutes.setValue(int(eta % 3600 // 60))
        if self.engine.state.bg_eta_note and not self.note.text():
            self.note.setText(self.engine.state.bg_eta_note)
