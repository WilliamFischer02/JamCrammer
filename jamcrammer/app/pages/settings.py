"""Settings: window/capture, timing, anti-AFK, hotkeys, Learn; save to config.yaml."""

from __future__ import annotations

from typing import Any

import yaml
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QFormLayout, QLabel, QLineEdit, QSpinBox

from jamcrammer.app import theme
from jamcrammer.app.engine import Engine
from jamcrammer.app.widgets import Card, Page, icon_button, row


class SettingsPage(Page):
    key = "settings"
    title = "Settings"
    subtitle = "Everything in config.yaml. Changes apply live where possible; Save writes the file."
    icon = "settings"

    def __init__(self, engine: Engine, window: Any) -> None:
        super().__init__()
        self.engine = engine
        cfg = engine.cfg

        win = Card("Window and capture")
        f = QFormLayout()
        self.title_edit = QLineEdit(cfg.window_title_match)
        self.title_edit.textChanged.connect(lambda v: setattr(cfg, "window_title_match", v))
        f.addRow("Window title", self.title_edit)
        self.proc = QLineEdit(cfg.window_process_name)
        self.proc.textChanged.connect(lambda v: setattr(cfg, "window_process_name", v))
        f.addRow("Process name", self.proc)
        backend = QComboBox()
        for b in ("auto", "wgc", "mss"):
            backend.addItem(b)
        backend.setCurrentText(cfg.capture_backend)
        backend.currentTextChanged.connect(lambda v: setattr(cfg, "capture_backend", v))
        f.addRow("Capture backend", backend)
        fps = QDoubleSpinBox()
        fps.setRange(1, 30)
        fps.setValue(cfg.fps)
        fps.valueChanged.connect(lambda v: setattr(cfg, "fps", float(v)))
        f.addRow("Capture fps", fps)
        self.window_info = QLabel("")
        self.window_info.setObjectName("CardHelp")
        f.addRow("Detected window", self.window_info)
        win.add_layout(f)
        self.content.addWidget(win)

        timing = Card("Timing (seconds)")
        f2 = QFormLayout()
        for attr, label, lo, hi in (
            ("hunt_interval_s", "Hunt interval", 30, 3600),
            ("opportunist_check_s", "Opportunist check", 60, 3600),
            ("resume_after_idle_s", "Resume after user idle", 0, 3600),
            ("heartbeat_s", "In-game heartbeat", 30, 600),
        ):
            box = QDoubleSpinBox()
            box.setRange(lo, hi)
            box.setValue(getattr(cfg, attr))
            box.valueChanged.connect(lambda v, a=attr: setattr(cfg, a, float(v)))
            f2.addRow(label, box)
        timing.add_layout(f2)
        self.content.addWidget(timing)

        afk = Card("Anti-AFK")
        f3 = QFormLayout()
        for attr, label in (
            ("tap_ms", "Tap (ms)"),
            ("gap_ms", "Gap (ms)"),
            ("settle_ms", "Settle before drift check (ms)"),
        ):
            ibox = QSpinBox()
            ibox.setRange(0, 5000)
            ibox.setValue(getattr(cfg.antiafk, attr))
            ibox.valueChanged.connect(lambda v, a=attr: setattr(cfg.antiafk, a, int(v)))
            f3.addRow(label, ibox)
        f3.addRow("Pattern", QLabel(" → ".join(f"({a},{b})" for a, b in cfg.antiafk.pattern)))
        afk.add_layout(f3)
        self.content.addWidget(afk)

        keys = Card("Hotkeys", "Global; take effect after restart.")
        f4 = QFormLayout()
        for attr, label in (
            ("kill", "Kill"),
            ("pause", "Pause / resume"),
            ("toggle_mode", "Start / stop mode"),
            ("learn_mark", "Learn: mark"),
            ("learn_correct", "Learn: correct"),
            ("learn_wrong", "Learn: wrong"),
        ):
            e = QLineEdit(getattr(cfg.hotkeys, attr))
            e.textChanged.connect(lambda v, a=attr: setattr(cfg.hotkeys, a, v.strip()))
            f4.addRow(label, e)
        keys.add_layout(f4)
        self.content.addWidget(keys)

        learn = Card("Learn")
        f5 = QFormLayout()
        for attr, label, lo, hi in (
            ("keyframe_threshold", "Keyframe pHash threshold", 0, 64),
            ("ring_fps", "Ring buffer fps", 1, 60),
            ("ring_window_s", "Ring window (s)", 1, 60),
            ("heartbeat_s", "Heartbeat keyframe (s)", 1, 600),
        ):
            box = QDoubleSpinBox()
            box.setRange(lo, hi)
            box.setValue(float(getattr(cfg.learn, attr)))
            box.valueChanged.connect(
                lambda v, a=attr: setattr(
                    cfg.learn, a, int(v) if a == "keyframe_threshold" else float(v)
                )
            )
            f5.addRow(label, box)
        learn.add_layout(f5)
        self.content.addWidget(learn)

        self.saved = QLabel("")
        self.saved.setObjectName("CardHelp")
        self.content.addLayout(
            row(
                icon_button(
                    "check",
                    "Save config.yaml",
                    kind="primary",
                    color=theme.PALETTE.bg,
                    on_click=self.save,
                ),
                self.saved,
                stretch_last=True,
            )
        )
        self.finish()

    def save(self) -> None:
        path = self.engine.paths.config_file
        with path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(self.engine.cfg.model_dump(mode="json"), fh, sort_keys=False)
        self.saved.setText(f"Saved {path}")
        self.engine.emit("config.saved", path=str(path))

    def on_tick(self) -> None:
        info = self.engine.state.window or self.engine.tracker.info
        if info is None:
            self.window_info.setText("not found")
        else:
            w, h = info.client_size
            self.window_info.setText(
                f"{info.process_name} · client {w}x{h} at ({info.client_rect.left},{info.client_rect.top}) · DPI {info.dpi}"
            )
