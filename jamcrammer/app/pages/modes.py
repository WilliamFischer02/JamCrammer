"""Bot pages: a run panel at the top (one status line, Start / Stop / Pause, the bot's own
stat tiles and chart), then its settings in cards, then the profile checklist.

Each bot shows what matters to it: Best Guess its answers and accuracy, the Arcade its
races and gems, the Adventure its crystals and helps, the Opportunist its checks and hops."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any, cast

import yaml
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
from jamcrammer.app.engine import MODE_LABELS, Engine
from jamcrammer.app.widgets import Card, Chip, Meter, Page, RunPanel, StatTile, icon_button, row
from jamcrammer.core.gate import gate_status
from jamcrammer.modes.situation import FARMER_MODES

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
FIELD_WIDTH = 160
COMBO_WIDTH = 260
MODE_ICONS = {
    "hunt": "bestguess",
    "farm": "arcade",
    "adventure": "adventure",
    "opportunist": "opportunist",
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
    box.setMinimumWidth(FIELD_WIDTH)
    box.valueChanged.connect(lambda v: setter(float(v)))
    return box


def _form() -> QFormLayout:
    """A settings form whose fields keep their natural width (no full-width spin boxes)."""
    form = QFormLayout()
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
    form.setHorizontalSpacing(theme.PAD_MD)
    return form


def _save_config(engine: Engine) -> str:
    try:
        with engine.paths.config_file.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(engine.cfg.model_dump(mode="json"), fh, sort_keys=False)
    except OSError as exc:
        return f"could not save config.yaml: {exc}"
    return ""


class ModePage(Page):
    """A bot page. Subclasses fill ``build_stats`` (tiles and a chart for the run panel),
    ``update_stats`` (every tick) and ``build_settings`` (the cards below)."""

    mode = "hunt"
    gate_mode = "hunt"

    def __init__(self, engine: Engine, window: Any) -> None:
        super().__init__()
        self.engine = engine
        self.window_ref = window
        self.panel = RunPanel(MODE_LABELS[self.mode], self.mode, MODE_ICONS.get(self.mode, "bot"))
        self.panel.start_clicked.connect(self.start)
        self.panel.stop_clicked.connect(engine.stop_mode)
        self.panel.pause_clicked.connect(engine.toggle_pause)
        self.panel.dry.setChecked(engine.state.dry_run)
        self.panel.dry_toggled.connect(engine.set_dry_run)
        if self.mode in FARMER_MODES:
            # "Hunt Best Guess" (William, 2026-11-03): persisted per bot in config.yaml
            self.panel.hunt.setVisible(True)
            self.panel.hunt.setChecked(engine.cfg.hunt_best_guess.for_mode(self.mode))
            self.panel.hunt_toggled.connect(self._set_hunt_best_guess)
        self.content.addWidget(self.panel)
        self.build_stats()
        self.build_settings()
        self.checklist = Card("Profile", "What the live run needs. Missing fields: Calibration.")
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
                    "Calibrate",
                    kind="ghost",
                    on_click=lambda: window.show_page_key("calibration"),
                ),
                stretch_last=True,
            )
        )
        self.content.addWidget(self.checklist)
        self.finish()
        self.refresh_checklist()

    # --- hooks ---------------------------------------------------------------------------

    def build_stats(self) -> None:
        pass

    def build_settings(self) -> None:
        pass

    def update_stats(self) -> None:
        pass

    def status_line(self) -> tuple[str, str]:
        """(one line, detail) for the run panel while this bot runs or rests."""
        st = self.engine.state
        if st.mode == self.mode:
            detail = st.mode_detail
            if st.handoff:
                detail = f"{st.handoff} · {detail}" if detail else st.handoff
            return (st.mode_state.replace("_", " ").title(), detail)
        if st.mode:
            return (f"{MODE_LABELS.get(st.mode, st.mode)} is running", "one bot at a time")
        allowed, why = self.engine.start_allowed(self.mode)
        if not allowed:
            # "Return to Den to enable this Farmer (where you are)"
            line, _, rest = why.partition(" (")
            return (line, rest.rstrip(")"))
        if st.last_error:
            return ("Stopped", st.last_error)
        where = f"in {st.situation_room}" if st.situation_room else st.situation
        return ("Ready", where or "")

    # --- actions -------------------------------------------------------------------------

    def start(self) -> None:
        self.engine.selected_mode = self.mode
        if not self.engine.start_mode(self.mode):
            self.panel.set_status(
                "Could not start", self.engine.state.last_error or "a bot is running"
            )

    def _set_hunt_best_guess(self, on: bool) -> None:
        self.engine.cfg.hunt_best_guess.set_mode(self.mode, on)
        self.engine.emit("config.changed", key=f"hunt_best_guess.{self.mode}", value=on)
        err = _save_config(self.engine)
        if err:
            self.gate_line.setText(err)

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
        gate_mode = {
            "hunt": "hunt",
            "farm": self.engine.cfg.arcade_game,
            "opportunist": "double_up",
        }.get(self.mode, self.gate_mode)
        self.gate_line.setText(
            "Gate: " + gate_status(gate_mode, self.engine.paths, self.engine._profiles).summary()
        )

    def on_show(self) -> None:
        self.refresh_checklist()
        if not self.engine.state.capture_running:
            # the den gate needs live frames (read-only); quiet when the game is not running
            self.engine.start_capture(quiet=True)
        self.on_tick()

    def on_tick(self) -> None:
        st = self.engine.state
        running = st.mode == self.mode
        allowed, _why = self.engine.start_allowed(self.mode)
        self.panel.set_running(
            running, st.paused, can_start=st.mode is None and not st.killed and allowed
        )
        self.panel.pill.set_state(
            st.mode_state if running else ("BUSY" if st.mode else "IDLE"),
            st.pill_kind if running else "idle",
        )
        line, detail = self.status_line()
        self.panel.set_status(line, detail)
        if self.panel.dry.isChecked() != st.dry_run:
            self.panel.dry.setChecked(st.dry_run)
        self.update_stats()


# --- Best Guess ------------------------------------------------------------------------------


class BestGuessPage(ModePage):
    key = "hunt"
    mode = "hunt"
    title = "Best Guess"
    subtitle = (
        "Farms the Best Guess arena with the answer bank. From the den: reads when it is due, "
        "waits (or races the Derby), joins on time."
    )
    icon = "bestguess"

    def build_stats(self) -> None:
        c = theme.PALETTE.bestguess
        self.t_correct = StatTile("Correct", c, spark=False, size="md")
        self.t_rounds = StatTile("Rounds", c, spark=False, size="md")
        self.t_gems = StatTile("Gems", theme.PALETTE.arcade, spark=False, size="md")
        self.t_bank = StatTile("Facts known", theme.PALETTE.tool, spark=False, size="md")
        self.t_eta = StatTile("Best Guess in", theme.PALETTE.planner, spark=False, size="md")
        self.panel.add_stats([self.t_eta, self.t_correct, self.t_rounds, self.t_gems, self.t_bank])
        self.m_eta = Meter("Until Best Guess", "planner")
        self.m_acc = Meter("Accuracy", "hunt")
        self.m_streak = Meter("Streak toward 4x", "hunt", unit="", maximum=4)
        self.panel.add_chart(self.m_eta)
        self.panel.add_chart(self.m_acc)
        self.panel.add_chart(self.m_streak)
        self._bank_ts = 0.0
        self._bank_n = 0
        self._eta_full = 0.0
        self._reading = False
        self._read_result: tuple[int | None, str] | None = None

    def update_stats(self) -> None:
        self._update_eta()
        s = self.engine.state.stats.get("bestguess", {})
        correct, answered = int(s.get("correct", 0)), int(s.get("answered", 0))
        self.t_correct.set_value(correct)
        self.t_rounds.set_value(int(s.get("rounds", 0)))
        self.t_gems.set_value(int(s.get("gems_est", 0)))
        acc = 100.0 * correct / answered if answered else 0.0
        self.m_acc.set_value(acc, f"{acc:.0f}%  ({correct} of {answered})")
        mult = int(s.get("multiplier", 1))
        self.m_streak.set_value(mult, f"x{mult} · streak {int(s.get('streak', 0))}")
        if time.time() - self._bank_ts > 10:
            self._bank_ts = time.time()
            try:
                rows = self.engine.store.query("SELECT COUNT(*) AS n FROM bg_facts")
                self._bank_n = int(rows[0]["n"]) if rows else 0
            except Exception:
                pass
        self.t_bank.set_value(self._bank_n)

    def _update_eta(self) -> None:
        """The countdown to Best Guess (read from the party tray, or typed on the Den
        Planner page): the bot leaves the Derby ``leave_lead_s`` before it."""
        from jamcrammer.modes.planner import format_eta

        st = self.engine.state
        eta = st.bg_eta_s()
        if eta is None:
            self.t_eta.set_value("unknown")
            self.m_eta.set_value(0, "not read yet")
            return
        self._eta_full = max(self._eta_full, eta)
        self.t_eta.set_value(format_eta(eta) if eta > 0 else "due now")
        share = 100.0 * (1.0 - min(1.0, max(0.0, eta) / self._eta_full)) if self._eta_full else 0.0
        lead = self.engine.cfg.planner.leave_lead_s
        note = f" · {st.bg_eta_note}" if st.bg_eta_note else ""
        self.m_eta.set_value(
            share, f"{format_eta(max(0.0, eta))} · leaves the Derby {lead:.0f} s before{note}"
        )

    def read_time(self) -> None:
        """Open My Parties and its tray, read when Best Guess is due, close them."""
        if self._reading or self.engine.state.mode is not None:
            return
        self._reading = True
        self._read_result = None
        self.panel.set_status(
            "Reading the party tray", "My Parties, then the Upcoming Parties tray"
        )

        def work() -> None:
            try:
                self._read_result = self.engine.read_eta_from_game()
            finally:
                self._reading = False

        threading.Thread(target=work, name="bestguess-read-eta", daemon=True).start()

    def status_line(self) -> tuple[str, str]:
        st = self.engine.state
        if self._reading:
            return ("Reading the party tray", "My Parties, then the Upcoming Parties tray")
        if self._read_result is not None and st.mode is None:
            eta, note = self._read_result
            return ("Best Guess time read" if eta is not None else "Could not read the time", note)
        if st.mode == self.mode and st.mode_state == "PLANNER":
            return (f"Plan: {st.mode_detail}", "tray, den, farm until due, then Best Guess")
        if st.mode == self.mode:
            s = st.stats.get("bestguess", {})
            fact = str(s.get("last_fact", ""))[:70]
            decision = str(s.get("last_decision", ""))
            if fact:
                return (f"{st.mode_state.title()}: {fact}", decision)
        return super().status_line()

    def build_settings(self) -> None:
        cfg = self.engine.cfg
        hunt = Card("Hunting")
        hunt.add_layout(
            row(
                icon_button("timer", "Read time", kind="ghost", on_click=self.read_time),
                stretch_last=True,
            )
        )
        form = _form()
        form.addRow(
            "Scan every (s)",
            _spin(
                cfg.hunt_interval_s,
                30,
                3600,
                30,
                lambda v: setattr(cfg, "hunt_interval_s", v),
                "How often My Parties is scanned",
            ),
        )
        form.addRow(
            "",
            _bind_bool(
                self.engine,
                lambda: cfg.auto_join,
                lambda v: setattr(cfg, "auto_join", v),
                "Join when it appears",
                "Click GO NOW; otherwise alert only",
            ),
        )
        form.addRow(
            "",
            _bind_bool(
                self.engine,
                lambda: cfg.start_farming_after_join,
                lambda v: setattr(cfg, "start_farming_after_join", v),
                "Answer after joining",
                "Learn-only until the replay gate passes",
            ),
        )
        form.addRow(
            "",
            _bind_bool(
                self.engine,
                lambda: cfg.hunt.antiafk_each_cycle,
                lambda v: setattr(cfg.hunt, "antiafk_each_cycle", v),
                "Move before every scan",
                "A cancelling movement pair keeps the session alive",
            ),
        )
        form.addRow(
            "",
            _bind_bool(
                self.engine,
                lambda: cfg.hunt.derby_while_waiting,
                lambda v: setattr(cfg.hunt, "derby_while_waiting", v),
                "Race in the Derby until Best Guess is due",
                "Start in your den: read the party tray, race the Derby (PRO), come back and "
                "join on time; unchecked, wait in the den with the keepalive instead",
            ),
        )
        hunt.add_layout(form)
        self.content.addWidget(hunt)

        brain = Card("Answering")
        form2 = _form()
        policy = QComboBox()
        policy.setMinimumWidth(COMBO_WIDTH)
        for p in ("crowd", "learned_prior", "fixed_true", "fixed_false", "skip"):
            policy.addItem(p)
        policy.setCurrentText(cfg.unknown_fact_policy)
        policy.currentTextChanged.connect(lambda v: setattr(cfg, "unknown_fact_policy", v))
        form2.addRow("Unknown facts", policy)
        mode = QComboBox()
        mode.setMinimumWidth(COMBO_WIDTH)
        for m in ("learn_only", "bank_policy", "coin_flip"):
            mode.addItem(m)
        mode.setCurrentText(cfg.answer_mode)
        mode.currentTextChanged.connect(self._set_answer_mode)
        form2.addRow("Answer mode", mode)
        form2.addRow(
            "Match threshold",
            _spin(
                cfg.fuzzy_threshold,
                50,
                100,
                1,
                lambda v: setattr(cfg, "fuzzy_threshold", int(v)),
                "Fuzzy match of a fact against the bank",
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
                "Re-click the current side after this long idle",
            ),
        )
        brain.add_layout(form2)
        self.content.addWidget(brain)

    def _set_answer_mode(self, value: str) -> None:
        self.engine.cfg.answer_mode = cast(Any, value)
        err = _save_config(self.engine)
        if err:
            self.gate_line.setText(err)
            return
        self.refresh_checklist()


# --- Arcade ----------------------------------------------------------------------------------


class ArcadePage(ModePage):
    key = "farm"
    mode = "farm"
    title = "Arcade"
    subtitle = (
        "Farms a mini-game on repeat from your den: Jamaa Derby or Double Up. "
        "Hunt Best Guess: joins Best Guess on time and hands over."
    )
    icon = "arcade"
    GAMES = ("jamaa_derby", "double_up", "gem_ball", "swoopy_eagle")
    GAME_LABELS = {
        "jamaa_derby": "Jamaa Derby",
        "double_up": "Double Up",
        "gem_ball": "Gem Ball (experimental)",
        "swoopy_eagle": "Swoopy Eagle (experimental)",
    }

    def build_stats(self) -> None:
        c = theme.PALETTE.arcade
        self.t_gems = StatTile("Gems", c, spark=False, size="md")
        self.t_rounds = StatTile("Races", c, spark=False, size="md")
        self.t_wins = StatTile("Wins", theme.PALETTE.ok, spark=False, size="md")
        self.t_hits = StatTile("Stumbles", theme.PALETTE.danger, spark=False, size="md")
        self.panel.add_stats([self.t_gems, self.t_rounds, self.t_wins, self.t_hits])
        self.m_win = Meter("Win rate", "farm")
        self.m_clean = Meter("Clean jumps", "farm")
        self.panel.add_chart(self.m_win)
        self.panel.add_chart(self.m_clean)

    def update_stats(self) -> None:
        s = self.engine.state.stats.get("farm", {})
        game = self.engine.cfg.arcade_game
        if game == "jamaa_derby":
            races, wins = int(s.get("races", 0)), int(s.get("first_places", 0))
            jumps, hits = int(s.get("jumps", 0)), int(s.get("hits", 0))
            self.t_rounds.caption.setText("RACES")
            self.t_wins.caption.setText("WINS")
            self.t_hits.caption.setText("STUMBLES")
            self.t_rounds.set_value(races)
            self.t_wins.set_value(wins)
            self.t_hits.set_value(hits)
            rate = 100.0 * wins / races if races else 0.0
            self.m_win.label.setText("WIN RATE")
            self.m_win.set_value(rate, f"{rate:.0f}%  ({wins} of {races})")
            clean = 100.0 * (jumps - hits) / jumps if jumps else 0.0
            self.m_clean.label.setText("CLEAN JUMPS")
            self.m_clean.set_value(clean, f"{clean:.0f}%  ({jumps} jumps)")
        else:
            rounds, perfect = int(s.get("rounds", 0)), int(s.get("perfect", 0))
            self.t_rounds.caption.setText("ROUNDS")
            self.t_wins.caption.setText("PERFECT")
            self.t_hits.caption.setText("MISMATCHES")
            self.t_rounds.set_value(rounds)
            self.t_wins.set_value(perfect)
            self.t_hits.set_value(int(s.get("mismatches", 0)))
            rate = 100.0 * perfect / rounds if rounds else 0.0
            self.m_win.label.setText("PERFECT ROUNDS")
            self.m_win.set_value(rate, f"{rate:.0f}%")
            self.m_clean.label.setText("PAIRING CONFIDENCE")
            conf = float(s.get("confidence", 0.0)) * 100.0
            self.m_clean.set_value(conf, f"{conf:.0f}%")
        self.t_gems.set_value(int(s.get("gems", s.get("gems_reported", 0)) or 0))

    def status_line(self) -> tuple[str, str]:
        st = self.engine.state
        if st.mode == self.mode and st.mode_state != "PLANNER":
            game = self.GAME_LABELS.get(self.engine.cfg.arcade_game, self.engine.cfg.arcade_game)
            return (f"{game}: {st.mode_state.title()}", st.mode_detail)
        if st.mode == self.mode:
            return (f"Plan: {st.mode_detail}", "tray, den, farm until due, then Best Guess")
        return super().status_line()

    def build_settings(self) -> None:
        card = Card("Game")
        form = _form()
        game = QComboBox()
        game.setMinimumWidth(COMBO_WIDTH)
        for g in self.GAMES:
            game.addItem(self.GAME_LABELS[g], g)
        current = self.engine.cfg.arcade_game
        game.setCurrentIndex(max(0, self.GAMES.index(current)) if current in self.GAMES else 0)
        game.currentIndexChanged.connect(lambda i: self._set_game(str(game.itemData(i))))
        form.addRow("Game", game)
        card.add_layout(form)
        self.content.addWidget(card)

    def _set_game(self, game: str) -> None:
        self.engine.cfg.arcade_game = cast(Any, game)
        err = _save_config(self.engine)
        if err:
            self.gate_line.setText(err)
            return
        self.refresh_checklist()
        self.update_stats()


# --- Opportunist -----------------------------------------------------------------------------


class OpportunistPage(ModePage):
    key = "opportunist"
    mode = "opportunist"
    title = "Opportunist"
    subtitle = "Farms the arcade and hops to Best Guess the moment it appears."
    icon = "opportunist"

    def build_stats(self) -> None:
        c = theme.PALETTE.opportunist
        self.t_rounds = StatTile("Arcade rounds", theme.PALETTE.arcade, spark=False, size="md")
        self.t_checks = StatTile("Party checks", c, spark=False, size="md")
        self.t_overhead = StatTile("Overhead", c, unit=" s", spark=False, size="md")
        self.t_hopped = StatTile("Hopped", theme.PALETTE.bestguess, spark=False, size="md")
        self.panel.add_stats([self.t_rounds, self.t_checks, self.t_overhead, self.t_hopped])
        self.m_next = Meter("Next party check", "opportunist")
        self.panel.add_chart(self.m_next)

    def update_stats(self) -> None:
        s = self.engine.state.stats.get("opportunist", {})
        self.t_rounds.set_value(int(s.get("rounds", 0)))
        self.t_checks.set_value(int(s.get("checks", 0)))
        self.t_overhead.set_value(float(s.get("mean_overhead_s", 0.0)))
        self.t_hopped.set_value("yes" if s.get("hopped") else "no")
        period = max(1.0, float(self.engine.cfg.opportunist_check_s))
        since = float(s.get("since_check_s", 0.0))
        self.m_next.set_value(100.0 * min(1.0, since / period), f"{max(0.0, period - since):.0f} s")

    def build_settings(self) -> None:
        cfg = self.engine.cfg
        card = Card("Checks")
        form = _form()
        form.addRow(
            "Check every (s)",
            _spin(
                cfg.opportunist_check_s,
                60,
                1800,
                30,
                lambda v: setattr(cfg, "opportunist_check_s", v),
                "Checked at the next round boundary, never mid-round",
            ),
        )
        card.add_layout(form)
        self.content.addWidget(card)


__all__ = ["ArcadePage", "BestGuessPage", "OpportunistPage", "theme"]
