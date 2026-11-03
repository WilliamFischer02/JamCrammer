"""Adventure page: The Mystery Below from the level model and the route replay
(modes/adventures/, docs/MYSTERY_BELOW.md). The run panel shows the crystals lit, the picks,
the helps and how far along the plan it is."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import yaml
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QLabel

from jamcrammer.app import theme
from jamcrammer.app.pages.modes import ModePage, _bind_bool, _form
from jamcrammer.app.widgets import Card, Chip, Meter, StatTile, icon_button, row
from jamcrammer.core.gate import REQUIRED_FIELDS as GATE_FIELDS
from jamcrammer.core.gate import gate_status
from jamcrammer.modes.adventures.route import latest_session, list_sessions

ADVENTURES = (
    ("mystery_below", "The Mystery Below"),
    ("forgotten_desert", "The Forgotten Desert (not ready)"),
)
AUTO = ""  # session combo entry: the longest finished run
FIXES_SHOWN = 8
REFRESH_S = 5
CP_PER_CRYSTAL = 25
CP_PER_SMASH = 2
RUN_MINUTES = 22.0  # William's recorded pace, for the run-time meter


class AdventuresPage(ModePage):
    key = "adventure"
    mode = "adventure"
    title = "Adventure"
    subtitle = "Runs The Mystery Below on its own. Steer any time; it continues where you leave it."
    icon = "adventure"

    # --- stats ---------------------------------------------------------------------------

    def build_stats(self) -> None:
        c = theme.PALETTE.adventure
        self.t_lit = StatTile("Crystals lit", c, spark=False, size="md")
        self.t_picked = StatTile("Blossoms picked", theme.PALETTE.ok, spark=False, size="md")
        self.t_cp = StatTile("Courage (est.)", c, size="md")
        self.t_helps = StatTile("Helps", theme.PALETTE.danger, spark=False, size="md")
        self.panel.add_stats([self.t_lit, self.t_picked, self.t_cp, self.t_helps])
        self.m_plan = Meter("Plan", "adventure")
        self.m_time = Meter("Run time", "adventure", unit=" min")
        self.panel.add_chart(self.m_plan)
        self.panel.add_chart(self.m_time)
        self._run_started = 0.0

    def update_stats(self) -> None:
        st = self.engine.state
        runner = self.engine._mode_obj
        s = st.stats.get("adventure_level") or st.stats.get("adventure") or {}
        if runner is not None and type(runner).__name__ == "LevelRunner":
            s = runner.stats.as_dict()
            k, n = int(getattr(runner, "k", 0)), len(getattr(runner, "acts", []))
            self.m_plan.set_value(100.0 * k / n if n else 0.0, f"act {k} of {n}")
        elif runner is not None and type(runner).__name__ == "RouteRunner":
            r = runner.route
            self.m_plan.set_value(
                100.0 * runner.rt / r.duration if r.duration else 0.0,
                f"route {runner.rt:.0f} of {r.duration:.0f} s",
            )
        lit, picked = int(s.get("lit", 0)), int(s.get("picked", 0))
        self.t_lit.set_value(lit)
        self.t_picked.set_value(picked)
        self.t_helps.set_value(int(s.get("helps", 0)))
        self.t_cp.set_value(lit * CP_PER_CRYSTAL + int(s.get("smashed", 0)) * CP_PER_SMASH)
        if st.mode == self.mode:
            if not self._run_started:
                self._run_started = time.time()
            minutes = (time.time() - self._run_started) / 60.0
            self.m_time.set_value(
                100.0 * min(1.0, minutes / RUN_MINUTES), f"{minutes:.0f} of ~{RUN_MINUTES:.0f} min"
            )
        else:
            self._run_started = 0.0

    def status_line(self) -> tuple[str, str]:
        st = self.engine.state
        runner = self.engine._mode_obj
        if st.mode == self.mode and runner is not None and type(runner).__name__ == "LevelRunner":
            phase = str(getattr(runner, "phase", "") or "").replace("_", " ")
            return (f"{runner.state.title()}: {phase or 'starting'}", st.mode_detail)
        return super().status_line()

    # --- layout --------------------------------------------------------------------------

    def build_settings(self) -> None:
        rc = self.engine.cfg.adventures.route
        self._busy = False
        self._loading = False
        self._note: str | None = None
        self._ticks = 0

        card = Card("Adventure")
        form = _form()
        self.adv = QComboBox()
        self.adv.setMinimumWidth(360)
        for key, label in ADVENTURES:
            self.adv.addItem(label, key)
        keys = [k for k, _ in ADVENTURES]
        self.adv.setCurrentIndex(keys.index(rc.target) if rc.target in keys else 0)
        self.adv.currentIndexChanged.connect(lambda i: self._set_target(str(self.adv.itemData(i))))
        form.addRow("Adventure", self.adv)
        self.session = QComboBox()
        self.session.setMinimumWidth(360)
        self.session.currentIndexChanged.connect(self._set_session)
        form.addRow("Recorded run", self.session)
        self.route_line = QLabel("")
        self.route_line.setObjectName("CardHelp")
        self.route_line.setWordWrap(True)
        form.addRow("", self.route_line)
        card.add_layout(form)
        self.rebuild_btn = icon_button("refresh", "Rebuild", kind="ghost", on_click=self._rebuild)
        card.add_layout(
            row(
                self.rebuild_btn,
                icon_button(
                    "learn",
                    "Record a run",
                    kind="ghost",
                    on_click=lambda: self.window_ref.show_page_key("learn"),
                ),
                icon_button(
                    "map",
                    "Open the map",
                    kind="ghost",
                    on_click=lambda: self.window_ref.show_page_key("mapping"),
                ),
                stretch_last=True,
            )
        )
        card.add(
            _bind_bool(
                self.engine,
                lambda: rc.supervised,
                lambda v: self._save("supervised", v),
                "Supervised: run with the gate locked (you watch and steer)",
                "The gate measures how well it can tell where it is on the route.",
            )
        )
        card.add(
            _bind_bool(
                self.engine,
                lambda: rc.loop,
                lambda v: self._save("loop", v),
                "Loop: start again when a run ends",
                "Off: one run, then it stops in your den.",
            )
        )
        self.start_note = QLabel("")
        self.start_note.setObjectName("CardHelp")
        self.start_note.setWordWrap(True)
        card.add(self.start_note)
        self.content.addWidget(card)

        fx = Card(
            "Learned", "Your corrections and its own; the latest one near a spot is tried first."
        )
        self.fixes = QLabel("")
        self.fixes.setObjectName("CardHelp")
        self.fixes.setWordWrap(True)
        fx.add(self.fixes)
        fx.add_layout(
            row(
                icon_button("logs", "Open log", kind="ghost", on_click=self._open_fixes),
                stretch_last=True,
            )
        )
        self.content.addWidget(fx)

        self._load_sessions()
        self._show_fixes()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    # --- gate ----------------------------------------------------------------------------

    def refresh_checklist(self) -> None:
        while self.check_rows.rowCount():
            self.check_rows.removeRow(0)
        rc = self.engine.cfg.adventures.route
        for profile_name, field in GATE_FIELDS.get(rc.target, []):
            try:
                status = self.engine.profile(profile_name).status(field)
            except Exception:
                status = "uncalibrated"
            self.check_rows.addRow(QLabel(f"{profile_name}.{field}"), Chip(status, status))
        gate = gate_status(rc.target, self.engine.paths, self.engine._profiles)
        extra = ""
        if not gate.unlocked:
            extra = " · supervised runs allowed" if rc.supervised else " · Start needs Supervised"
        self.gate_line.setText("Gate: " + gate.summary() + extra)

    # --- settings ------------------------------------------------------------------------

    def _save(self, field: str, value: Any) -> None:
        setattr(self.engine.cfg.adventures.route, field, value)
        try:
            with self.engine.paths.config_file.open("w", encoding="utf-8") as fh:
                yaml.safe_dump(self.engine.cfg.model_dump(mode="json"), fh, sort_keys=False)
        except OSError as exc:
            self.start_note.setText(f"could not save config.yaml: {exc}")
        self.refresh_checklist()

    def _set_target(self, target: str) -> None:
        self._save("target", target)
        self._save("session", AUTO)
        self._load_sessions()

    def _set_session(self, index: int) -> None:
        if index < 0 or self._loading:
            return
        name = str(self.session.itemData(index))
        if name != self.engine.cfg.adventures.route.session:
            self._save("session", name)
        self._show_route()

    def _load_sessions(self) -> None:
        rc = self.engine.cfg.adventures.route
        self._loading = True
        self.session.clear()
        self.session.addItem("Longest finished run", AUTO)
        for info in list_sessions(self.engine.paths.sessions, rc.target):
            state = "finished" if info.finished else "unfinished"
            self.session.addItem(
                f"{info.path.name} · {info.minutes:.0f} min · {state}", info.path.name
            )
        names = [str(self.session.itemData(i)) for i in range(self.session.count())]
        self.session.setCurrentIndex(names.index(rc.session) if rc.session in names else 0)
        self._loading = False
        self._show_route()

    def _selected(self) -> Path | None:
        rc = self.engine.cfg.adventures.route
        sessions = self.engine.paths.sessions
        if rc.session:
            return sessions / rc.session
        return latest_session(sessions, rc.target)

    def _show_route(self) -> None:
        session = self._selected()
        if session is None or not session.exists():
            self.route_line.setText(f"No recorded run on this machine. {self._level_note()}")
            self.rebuild_btn.setEnabled(False)
            return
        from jamcrammer.modes.adventures.route import session_info

        info = session_info(session)
        self.rebuild_btn.setEnabled(not self._busy)
        if info.route_current:
            self.route_line.setText(
                f"{session.name}: {info.route_steps} steps, {info.route_objectives} objectives, "
                f"{info.minutes:.0f} min. {self._level_note()}"
            )
        else:
            self.route_line.setText(
                f"{session.name}: route not built yet; Start builds it, or Rebuild."
            )

    def _level_note(self) -> str:
        from jamcrammer.modes.adventures.level import Level, level_dir

        target = self.engine.cfg.adventures.route.target
        lvl = Level.load(level_dir(self.engine.paths.profiles, target))
        if lvl is None:
            return "No level model yet."
        acts = sum(len(v) for v in lvl.plan.values())
        return f"Level model: {len(lvl.spots)} spots, {acts} acts from {len(lvl.sessions)} runs; {self._economy_note()}"

    def _economy_note(self) -> str:
        from jamcrammer.economy import YIELDS, rate_from_runs

        key = f"{self.engine.cfg.adventures.route.target}_hard"
        if key not in YIELDS:
            return ""
        y = YIELDS[key]
        r = rate_from_runs(key, runs=1, minutes_total=y.minutes)
        return f"a run is ~{y.cp_typical:,} CP in {y.minutes:.0f} min (~{r.cp_per_hour:,.0f} CP/h)."

    # --- actions -------------------------------------------------------------------------

    def _rebuild(self) -> None:
        session = self._selected()
        if session is None or self._busy:
            return
        self._busy = True
        self.rebuild_btn.setEnabled(False)
        self.route_line.setText(f"building the route from {session.name} (a few minutes)")

        def work() -> None:
            try:
                self._note = self.engine.rebuild_route(session)
            except Exception as exc:
                self._note = f"route build failed: {exc}"
            self._busy = False

        threading.Thread(target=work, name="adventure-route-build", daemon=True).start()

    def _fixes_path(self) -> Path:
        return (
            self.engine.paths.data
            / "adventures"
            / f"{self.engine.cfg.adventures.route.target}_fixes.jsonl"
        )

    def _open_fixes(self) -> None:
        path = self._fixes_path()
        if path.exists():
            os.startfile(path)
        else:
            self.fixes.setText("nothing learned yet: the log appears after the first correction")

    def _show_fixes(self) -> None:
        path = self._fixes_path()
        if not path.exists():
            self.fixes.setText("nothing learned yet")
            return
        lines = path.read_text(encoding="utf-8").splitlines()[-FIXES_SHOWN:]
        out = []
        for line in reversed(lines):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            after = f" -> {e['rt_after']:.0f}s" if e.get("rt_after") is not None else ""
            why = f" · {e['why']}" if e.get("why") else ""
            out.append(
                f"{str(e.get('logged', ''))[11:19]}  route {float(e.get('rt', 0)):.0f}s{after}  "
                f"{e.get('reason', '?')}  {'worked' if e.get('ok', True) else 'did not work'}  "
                f"{len(e.get('inputs', []))} inputs{why}"
            )
        self.fixes.setText("\n".join(out) or "nothing learned yet")

    # --- refresh -------------------------------------------------------------------------

    def on_show(self) -> None:
        super().on_show()
        self._load_sessions()
        self._show_fixes()

    def _tick(self) -> None:
        self._ticks += 1
        if self._note is not None:
            note, self._note = self._note, None
            self._show_route()
            self.route_line.setText(self.route_line.text() + f"\nlast build: {note}")
            self.refresh_checklist()
        if self._ticks % REFRESH_S == 0:
            self._show_fixes()
