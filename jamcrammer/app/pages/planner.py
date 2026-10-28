"""Den Planner page: when is Best Guess due, read it from the game, run the planner."""

from __future__ import annotations

import threading

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFormLayout, QLabel, QSpinBox

from jamcrammer.app.engine import Engine
from jamcrammer.app.pages.modes import ModePage
from jamcrammer.app.widgets import Card, icon_button, row
from jamcrammer.modes.planner import format_eta


class PlannerPage(ModePage):
    key = "planner"
    mode = "planner"
    title = "Den Planner"
    subtitle = (
        "From the den: Jamaa Derby (PRO) on repeat until Best Guess is due, then leave, "
        "find Best Guess in My Parties and farm it."
    )
    icon = "opportunist"

    def build_settings(self) -> None:
        eng: Engine = self.engine
        self._pending: tuple[int | None, str] | None = None
        card = Card(
            "Time until Best Guess", "Type what the party tray shows, or read it from the game."
        )
        form = QFormLayout()
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
        self.countdown = QLabel("not set")
        self.countdown.setObjectName("CardHelp")
        form.addRow("Countdown", self.countdown)
        self.note = QLabel("")
        self.note.setObjectName("CardHelp")
        self.note.setWordWrap(True)
        form.addRow("", self.note)
        card.add_layout(form)
        card.add_layout(
            row(
                icon_button("play", "Set timer", kind="ok", on_click=self._set_timer),
                icon_button("parties", "Read from game", on_click=self._read_from_game),
                stretch_last=True,
            )
        )
        self.content.addWidget(card)
        plan = Card("What Start does", "")
        plan.add(
            QLabel(
                "1. Opens My Parties and the Upcoming Parties tray, reads the Best Guess time.\n"
                "2. Goes to the den if the room name is not your den.\n"
                "3. Opens the games menu, scrolls halfway, opens Jamaa Derby, clicks PRO.\n"
                "4. Farms the Derby; leaves at a leaderboard once the timer is within the lead.\n"
                "5. Re-checks My Parties every few seconds, joins Best Guess, farms it until you stop."
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
        eta_s = self.engine.state.bg_eta_s()
        if eta_s is None:
            self.countdown.setText("not set")
        elif eta_s <= 0:
            self.countdown.setText("Best Guess is due now")
        else:
            self.countdown.setText(f"Best Guess in {format_eta(eta_s)}")
        if self.engine.state.bg_eta_note and not self.note.text():
            self.note.setText(self.engine.state.bg_eta_note)
