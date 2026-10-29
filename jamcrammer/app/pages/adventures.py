"""Adventures page: route farmers that replay one of William's recorded adventure runs and keep
it on course (modes/adventures/replay.py, docs/ADVENTURES.md)."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

import yaml
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel

from jamcrammer.app.engine import MODES
from jamcrammer.app.pages.modes import ModePage, _bind_bool
from jamcrammer.app.widgets import Card, Chip, icon_button, row
from jamcrammer.core.gate import REQUIRED_FIELDS as GATE_FIELDS
from jamcrammer.core.gate import gate_status
from jamcrammer.modes.adventures.route import latest_session, list_sessions

ADVENTURES = (
    ("mystery_below", "The Mystery Below"),
    ("forgotten_desert", "The Forgotten Desert (route farmer not suited yet)"),
)
AUTO = ""  # session combo entry: the longest finished run
FIXES_SHOWN = 8
REFRESH_S = 5  # the fixes list and run list are re-read this often (ticks are 1 s)


class AdventuresPage(ModePage):
    key = "adventure"
    mode = "adventure"
    title = "Adventures"
    subtitle = (
        "Replays one of your recorded adventure runs and keeps it on course with the minimap "
        "dot, the scene and the objective banner. Steer any time: it lets go at once, then "
        "continues where you leave it, and keeps what you did as a fix for next time."
    )
    icon = "adventure"

    # --- layout --------------------------------------------------------------------------

    def build_settings(self) -> None:
        rc = self.engine.cfg.adventures.route
        self._busy = False
        self._loading = False
        self._note: str | None = None
        self._ticks = 0

        card = Card("Adventure", "Which adventure the farmer runs (saved to config.yaml).")
        form = QFormLayout()
        self.adv = QComboBox()
        for key, label in ADVENTURES:
            self.adv.addItem(label, key)
        keys = [k for k, _ in ADVENTURES]
        self.adv.setCurrentIndex(keys.index(rc.target) if rc.target in keys else 0)
        self.adv.currentIndexChanged.connect(lambda i: self._set_target(str(self.adv.itemData(i))))
        form.addRow("Adventure", self.adv)
        card.add_layout(form)
        self.content.addWidget(card)

        rcard = Card(
            "Recorded run",
            "The Learn session the route is built from. Record more runs in Learn with the "
            "adventure's target, starting in your den, on different animals.",
        )
        rform = QFormLayout()
        self.session = QComboBox()
        self.session.currentIndexChanged.connect(self._set_session)
        rform.addRow("Run", self.session)
        self.route_line = QLabel("")
        self.route_line.setObjectName("CardHelp")
        self.route_line.setWordWrap(True)
        rform.addRow("Route", self.route_line)
        rcard.add_layout(rform)
        self.rebuild_btn = icon_button("refresh", "Rebuild route", on_click=self._rebuild)
        rcard.add_layout(
            row(
                self.rebuild_btn,
                icon_button(
                    "learn", "Record a run", on_click=lambda: self.window_ref.show_page_key("learn")
                ),
                stretch_last=True,
            )
        )
        self.content.addWidget(rcard)

        run = Card("Run", "Start from your den, any room, or anywhere in the adventure.")
        run.add(
            _bind_bool(
                self.engine,
                lambda: rc.supervised,
                lambda v: self._save("supervised", v),
                "Supervised: run although the replay gate is locked (you watch and steer)",
                "Your call: the gate measures how well it can tell where it is on the route.",
            )
        )
        run.add(
            _bind_bool(
                self.engine,
                lambda: rc.loop,
                lambda v: self._save("loop", v),
                "Loop: start the route again when it ends (switch animals by hand)",
                "Off: one run, then it stops in your den.",
            )
        )
        run.add_layout(
            row(
                icon_button("play", "Start adventure", kind="ok", on_click=self._start),
                icon_button("stop", "Stop", kind="danger", on_click=self.engine.stop_mode),
                stretch_last=True,
            )
        )
        self.start_note = QLabel("")
        self.start_note.setObjectName("CardHelp")
        self.start_note.setWordWrap(True)
        run.add(self.start_note)
        self.content.addWidget(run)

        live = Card("Live", "Route time, the objective it is on, and its corrections so far.")
        self.live = QLabel("idle")
        self.live.setWordWrap(True)
        live.add(self.live)
        self.content.addWidget(live)

        fx = Card(
            "What it learned",
            "Your corrections and its own. The latest one that worked near a spot is tried "
            "first the next time it gets stuck there.",
        )
        self.fixes = QLabel("")
        self.fixes.setObjectName("CardHelp")
        self.fixes.setWordWrap(True)
        fx.add(self.fixes)
        fx.add_layout(
            row(
                icon_button("logs", "Open the fixes log", on_click=self._open_fixes),
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
            extra = (
                " · supervised runs allowed"
                if rc.supervised
                else " · Start refuses until it unlocks or Supervised is on"
            )
        self.gate_line.setText("Live-mode gate: " + gate.summary() + extra)

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
        self.session.addItem("Longest finished run (automatic)", AUTO)
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
            self.route_line.setText("no recorded run yet: record one in Learn")
            self.rebuild_btn.setEnabled(False)
            return
        from jamcrammer.modes.adventures.route import session_info

        info = session_info(session)
        self.rebuild_btn.setEnabled(not self._busy)
        if info.route_current:
            self.route_line.setText(
                f"{session.name}: {info.route_steps} steps ({info.route_clicks} clicks), "
                f"{info.route_objectives} objectives, {info.minutes:.0f} min"
            )
        else:
            self.route_line.setText(
                f"{session.name}: route not built yet. Start builds it first (a few minutes for "
                "a long run), or press Rebuild route now."
            )

    # --- actions -------------------------------------------------------------------------

    def _rebuild(self) -> None:
        session = self._selected()
        if session is None or self._busy:
            return
        self._busy = True
        self.rebuild_btn.setEnabled(False)
        self.route_line.setText(f"building the route from {session.name}... (a few minutes)")

        def work() -> None:
            try:
                self._note = self.engine.rebuild_route(session)
            except Exception as exc:
                self._note = f"route build failed: {exc}"
            self._busy = False

        threading.Thread(target=work, name="adventure-route-build", daemon=True).start()

    def _start(self) -> None:
        bar = getattr(self.window_ref, "bar", None)
        if bar is not None:
            bar.mode.setCurrentIndex(MODES.index("adventure"))
        ok = self.engine.start_mode("adventure")
        self.start_note.setText(
            "started: AJC comes to the front"
            if ok
            else (self.engine.state.last_error or "a mode is running")
        )

    def _fixes_path(self) -> Path:
        return (
            self.engine.paths.data
            / "adventures"
            / f"{self.engine.cfg.adventures.route.target}_fixes.jsonl"
        )

    def _open_fixes(self) -> None:
        path = self._fixes_path()
        if path.exists():
            os.startfile(path)  # opens the log in his default editor
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
        runner = self.engine._mode_obj
        st = self.engine.state
        if runner is not None and type(runner).__name__ == "RouteRunner":
            r = runner.route
            k = r.objective_index_at(runner.rt)
            obj = r.objectives[k].text if k >= 0 else "opening the adventure"
            s = runner.stats
            self.live.setText(
                f"{runner.state} · route {runner.rt:.0f} / {r.duration:.0f} s · {obj}\n"
                f"clicks {s.clicks} ({s.unverified} unverified, {s.retries} retries, "
                f"{s.extra_clicks} extra) · dialogs {s.dialogs} · course fixes {s.course_fixes} · "
                f"timing {s.warps} · stuck {s.stuck} · your turns {s.user_turns} · help {s.helps}"
            )
        elif st.mode == "adventure":
            self.live.setText(f"{st.mode_state} · {st.mode_detail}")
        else:
            last = st.stats.get("adventure")
            self.live.setText(f"idle · last run: {last}" if last else "idle")
        if self._ticks % REFRESH_S == 0:
            self._show_fixes()
