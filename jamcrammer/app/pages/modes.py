"""Mode pages: every toggle for the mode in titled cards, plus a profile-completeness checklist."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QWidget,
)

from jamcrammer.app import theme
from jamcrammer.app.engine import Engine
from jamcrammer.app.widgets import Card, Chip, Page, icon_button, row
from jamcrammer.core.gate import gate_status

REQUIRED_FIELDS: dict[str, list[tuple[str, str]]] = {
    "hunt": [
        ("parties", "party_hat_point"),
        ("parties", "close_x_point"),
        ("parties", "title_template"),
        ("parties", "go_now_template"),
        ("shared", "popup_green_hsv"),
    ],
    "farm": [
        ("double_up", "grid_origin"),
        ("double_up", "tile_size"),
        ("double_up", "play_again_point"),
        ("double_up", "end_screen_gems_roi"),
    ],
    "opportunist": [("parties", "party_hat_point"), ("double_up", "grid_origin")],
}


def _bind_bool(
    engine: Engine, getter: Callable[[], bool], setter: Callable[[bool], None], text: str, tip: str
) -> QCheckBox:
    cb = QCheckBox(text)
    cb.setChecked(getter())
    cb.setToolTip(tip)

    def on_toggle(v: bool) -> None:
        setter(v)
        engine.emit("config.changed", key=text, value=v)

    cb.toggled.connect(on_toggle)
    return cb


def _spin(  # noqa: PLR0917 - small positional helper
    value: float,
    lo: float,
    hi: float,
    step: float,
    setter: Callable[[float], None],
    tip: str,
    integer: bool = False,
) -> QWidget:
    box: QSpinBox | QDoubleSpinBox = QSpinBox() if integer else QDoubleSpinBox()
    box.setRange(int(lo) if integer else lo, int(hi) if integer else hi)  # type: ignore[arg-type]
    box.setSingleStep(int(step) if integer else step)  # type: ignore[arg-type]
    box.setValue(int(value) if integer else value)  # type: ignore[arg-type]
    box.setToolTip(tip)
    box.valueChanged.connect(lambda v: setter(float(v)))
    return box


class ModePage(Page):
    mode = "hunt"

    def __init__(self, engine: Engine, window: Any) -> None:
        super().__init__()
        self.engine = engine
        self.window_ref = window
        self.build_settings()
        self.checklist = Card(
            "Profile completeness",
            "Fields the live mode needs. Calibrate missing ones in Calibration.",
        )
        self.check_rows = QFormLayout()
        self.checklist.add_layout(self.check_rows)
        self.gate_line = QLabel("")
        self.gate_line.setObjectName("CardHelp")
        self.gate_line.setWordWrap(True)
        self.checklist.add(self.gate_line)
        self.checklist.add_layout(
            row(
                icon_button(
                    "calibration",
                    "Go to Calibration",
                    on_click=lambda: window.show_page_key("calibration"),
                ),
                stretch_last=True,
            )
        )
        self.content.addWidget(self.checklist)
        self.finish()
        self.refresh_checklist()

    def build_settings(self) -> None:
        pass

    def refresh_checklist(self) -> None:
        while self.check_rows.rowCount():
            self.check_rows.removeRow(0)
        for profile_name, field in REQUIRED_FIELDS.get(self.mode, []):
            try:
                prof = self.engine.profile(profile_name)
                status = prof.status(field)
            except Exception:
                status = "uncalibrated"
            self.check_rows.addRow(QLabel(f"{profile_name}.{field}"), Chip(status, status))
        gate_mode = {"hunt": "hunt", "farm": "double_up", "opportunist": "double_up"}[self.mode]
        self.gate_line.setText(
            "Live-mode gate: "
            + gate_status(gate_mode, self.engine.paths, self.engine._profiles).summary()
        )

    def on_show(self) -> None:
        self.refresh_checklist()

    def on_tick(self) -> None:
        pass


class BestGuessPage(ModePage):
    key = "hunt"
    mode = "hunt"
    title = "Best Guess"
    subtitle = "Hunt the Best Guess Game party, join it, farm it with the answer bank."
    icon = "bestguess"

    def build_settings(self) -> None:
        cfg = self.engine.cfg
        hunt = Card("Hunter (Phases A/B)")
        form = QFormLayout()
        form.addRow(
            "Scan interval (s)",
            _spin(
                cfg.hunt_interval_s,
                30,
                3600,
                30,
                lambda v: setattr(cfg, "hunt_interval_s", v),
                "How often My Parties is opened and scanned",
            ),
        )
        form.addRow(
            "",
            _bind_bool(
                self.engine,
                lambda: cfg.auto_join,
                lambda v: setattr(cfg, "auto_join", v),
                "Auto-join when Best Guess appears",
                "Click GO NOW automatically; otherwise alert only",
            ),
        )
        form.addRow(
            "",
            _bind_bool(
                self.engine,
                lambda: cfg.start_farming_after_join,
                lambda v: setattr(cfg, "start_farming_after_join", v),
                "Start farming after join",
                "Phase C; Learn-only until the replay gate passes",
            ),
        )
        form.addRow(
            "",
            _bind_bool(
                self.engine,
                lambda: cfg.hunt.antiafk_each_cycle,
                lambda v: setattr(cfg.hunt, "antiafk_each_cycle", v),
                "Anti-AFK pair before every scan",
                "A cancelling movement pair keeps the session alive",
            ),
        )
        hunt.add_layout(form)
        self.content.addWidget(hunt)

        brain = Card(
            "Farmer brain (Phase C)", "Live farming stays locked until the replay gate hits 95%."
        )
        form2 = QFormLayout()
        policy = QComboBox()
        for p in ("learned_prior", "fixed_true", "fixed_false", "skip"):
            policy.addItem(p)
        policy.setCurrentText(cfg.unknown_fact_policy)
        policy.currentTextChanged.connect(lambda v: setattr(cfg, "unknown_fact_policy", v))
        form2.addRow("Unknown-fact policy", policy)
        mode = QComboBox()
        for m in ("learn_only", "bank_policy", "coin_flip"):
            mode.addItem(m)
        mode.setCurrentText(cfg.answer_mode)
        mode.currentTextChanged.connect(lambda v: setattr(cfg, "answer_mode", v))
        form2.addRow("Answer mode", mode)
        form2.addRow(
            "Fuzzy threshold",
            _spin(
                cfg.fuzzy_threshold,
                50,
                100,
                1,
                lambda v: setattr(cfg, "fuzzy_threshold", int(v)),
                "rapidfuzz token_set_ratio minimum",
                integer=True,
            ),
        )
        form2.addRow(
            "Heartbeat (s)",
            _spin(
                cfg.heartbeat_s,
                30,
                600,
                10,
                lambda v: setattr(cfg, "heartbeat_s", v),
                "Re-click the current side after this long without input",
            ),
        )
        gate = QLabel("Live-mode gate: LOCKED (no recorded session has passed replay ≥95%)")
        gate.setObjectName("CardHelp")
        form2.addRow("", gate)
        brain.add_layout(form2)
        self.content.addWidget(brain)


class ArcadePage(ModePage):
    key = "farm"
    mode = "farm"
    title = "Arcade"
    subtitle = "Farm the most profitable always-available mini-game. Primary target: Double Up."
    icon = "arcade"

    def build_settings(self) -> None:
        card = Card("Double Up", "Arrives in M7 after a Learn session of 2-3 manual rounds.")
        form = QFormLayout()
        game = QComboBox()
        for g in (
            "double_up",
            "jamaa_derby",
            "gem_ball (experimental)",
            "swoopy_eagle (experimental)",
        ):
            game.addItem(g)
        form.addRow("Game", game)
        form.addRow("Target perfect rounds", QLabel("≥ 98% (8/8, no mismatches)"))
        form.addRow("Preview burst capture", QLabel("≥ 10 fps during the 3 s preview"))
        card.add_layout(form)
        self.content.addWidget(card)
        act = Card("Activity", "Set from the Activity Test result (KICKOFF 7.3).")
        act.add(
            QLabel(
                "Insert a lobby movement pair every N rounds if in-game clicks don't count as activity."
            )
        )
        self.content.addWidget(act)


class OpportunistPage(ModePage):
    key = "opportunist"
    mode = "opportunist"
    title = "Opportunist"
    subtitle = "Farm the arcade, peek at My Parties every few minutes, hop to Best Guess the moment it appears."
    icon = "opportunist"

    def build_settings(self) -> None:
        cfg = self.engine.cfg
        card = Card("Orchestrator")
        form = QFormLayout()
        form.addRow(
            "Check interval (s)",
            _spin(
                cfg.opportunist_check_s,
                60,
                1800,
                30,
                lambda v: setattr(cfg, "opportunist_check_s", v),
                "Checked at the next round boundary, never mid-round",
            ),
        )
        form.addRow("Overhead", QLabel("Measured per check and shown in Profit Lab."))
        form.addRow("Fallback", QLabel("If re-entry fails twice → plain Hunt mode + alert."))
        card.add_layout(form)
        self.content.addWidget(card)


__all__ = ["ArcadePage", "BestGuessPage", "OpportunistPage", "theme"]
