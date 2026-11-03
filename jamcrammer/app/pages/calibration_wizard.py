"""The guided Calibration wizard panel (Qt): per profile, tell the user which game screen to
open, capture and check it, then walk every drawn field of that screen one at a time with a
plain-English name and explanation, the current value, a status chip, 'Can be a range',
Confirm / Adjust / Skip and Back / Next under a step-k-of-n progress bar. The canvas beside it
is driven through ``WizardHost`` (the page): the edited field glows, its siblings are dim.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from jamcrammer.app import theme
from jamcrammer.app.engine import Engine
from jamcrammer.app.pages.calibration_flow import (
    BgrImage,
    CheckResult,
    Step,
    WizardModel,
    capture_path,
    capture_stamp,
    describe_value,
    field_hint,
    field_state,
    load_capture,
    run_check,
    save_capture,
)
from jamcrammer.app.widgets import Chip, StepProgress, icon_button, row, section
from jamcrammer.core.profiles import Profile, ProfileField

TOOL_FOR_TYPE = {"color": "color", "template": "template"}
STATE_CHIP = {"calibrated": "calibrated", "proposed": "proposed", "missing": "uncalibrated"}
VALUE_COL_MIN_PX = 160
STATUS_COL_PX = 110


class WizardHost(Protocol):
    """What the wizard needs from the Calibration page."""

    engine: Engine

    def profile(self) -> Profile: ...

    def profile_name(self) -> str: ...

    def show_image(self, image: BgrImage | None) -> None: ...

    def set_tool(self, tool: str) -> None: ...

    def set_active_field(self, key: str | None, siblings: tuple[str, ...]) -> None: ...

    def edit_value(self, key: str) -> None: ...

    def save_profile(self) -> None: ...

    def live_frame(self) -> BgrImage | None: ...


def thumbnail(image: BgrImage, width: int, dpr: float) -> QPixmap:
    """A crisp downscale (never an upscale) at the device pixel ratio."""
    px_w = min(image.shape[1], int(width * dpr))
    px_h = max(1, int(px_w * image.shape[0] / image.shape[1]))
    small = cv2.resize(image, (px_w, px_h), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    pm = QPixmap.fromImage(
        QImage(
            rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888
        ).copy()
    )
    pm.setDevicePixelRatio(dpr)
    return pm


def _wrapped(text: str = "", name: str | None = None) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    if name:
        lab.setObjectName(name)
    return lab


class ScreenStepView(QWidget):
    """'Open this screen in the game', Capture / Retake, the cheap check, the thumbnail."""

    def __init__(self, panel: WizardPanel) -> None:
        super().__init__()
        self.panel = panel
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(theme.PAD_SM)
        lay.addWidget(section("In the game"))
        self.instruction = _wrapped()
        lay.addWidget(self.instruction)
        self.fields_line = _wrapped("", "CardHelp")
        lay.addWidget(self.fields_line)
        lay.addWidget(section("Screenshot"))
        self.capture_chip = Chip("not captured", "uncalibrated")
        self.stamp = QLabel("")
        self.stamp.setObjectName("StatusDetail")
        lay.addLayout(row(self.capture_chip, self.stamp, stretch_last=True))
        self.thumb = QLabel()
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.thumb)
        self.capture_btn = icon_button(
            "camera", "Capture", kind="accent", accent="tool", on_click=self.panel.capture
        )
        self.retake_btn = icon_button(
            "refresh", "Retake", kind="ghost", on_click=self.panel.capture
        )
        self.check_btn = icon_button(
            "scan", "Check live", kind="ghost", on_click=self.panel.check_live
        )
        lay.addLayout(row(self.capture_btn, self.retake_btn, self.check_btn, stretch_last=True))
        lay.addWidget(section("Does it look right?"))
        self.check_chip = Chip("confirm by eye", "neutral")
        self.check_text = _wrapped("", "StatusDetail")
        lay.addLayout(row(self.check_chip, self.check_text, stretch_last=True))

    def show(self, step: Step, prof: Profile, path: Path, image: BgrImage | None) -> None:  # type: ignore[override]
        self.instruction.setText(step.screen.instruction)
        states = [field_state(prof.field(k)) for k in step.fields if k in prof.fields]
        n = len(states)
        if n:
            parts = [
                f"{states.count(s)} {s}"
                for s in ("calibrated", "proposed", "missing")
                if s in states
            ]
            self.fields_line.setText(
                f"{n} field{'s' if n != 1 else ''} on this screen: " + ", ".join(parts) + "."
            )
        else:
            self.fields_line.setText("No fields are drawn on this screen.")
        stamp = capture_stamp(path)
        if stamp:
            self.capture_chip.setText("captured")
            self.capture_chip.set_status("calibrated")
            self.stamp.setText(f"captured {stamp} · {path.name}")
        else:
            self.capture_chip.setText("not captured")
            self.capture_chip.set_status("uncalibrated")
            self.stamp.setText("the live screen is shown until you capture")
        self.retake_btn.setVisible(stamp is not None)
        self.capture_btn.setVisible(stamp is None)
        if image is not None:
            dpr = float(self.devicePixelRatioF())
            self.thumb.setPixmap(thumbnail(image, theme.CAPTURE_PREVIEW_W, dpr))
        else:
            self.thumb.clear()

    def show_check(self, result: CheckResult, source: str) -> None:
        if result.ok is None:
            self.check_chip.setText("confirm by eye")
            self.check_chip.set_status("neutral")
        elif result.ok:
            self.check_chip.setText("looks right")
            self.check_chip.set_status("calibrated")
        else:
            self.check_chip.setText("not this screen")
            self.check_chip.set_status("uncalibrated")
        self.check_text.setText(f"{source}: {result.text}")


class FieldStepView(QWidget):
    """One field: its name, what it is for, the value, its status, the range flag, Adjust."""

    def __init__(self, panel: WizardPanel) -> None:
        super().__init__()
        self.panel = panel
        self.key = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(theme.PAD_SM)
        self.name = _wrapped("", "StatusLine")
        lay.addWidget(self.name)
        self.ident = _wrapped("", "CardHelp")
        lay.addWidget(self.ident)
        lay.addWidget(section("What it captures"))
        self.help = _wrapped()
        lay.addWidget(self.help)
        lay.addWidget(section("Current value"))
        self.status_chip = Chip("missing", "uncalibrated")
        self.value = QLabel("")
        self.value.setObjectName("MeterValue")
        self.value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addLayout(row(self.status_chip, self.value, stretch_last=True))
        self.hint = _wrapped("", "CardHelp")
        lay.addWidget(self.hint)
        self.warning = _wrapped()  # no object name: the amber accent rule must win
        self.warning.setProperty("accent", "adventure")  # torch amber = the warning tone
        lay.addWidget(self.warning)
        self.range_box = QCheckBox("Can be a range")
        self.range_box.setToolTip(
            "This region is a range of possible positions, not one spot: stretch the box over "
            "every position the element can take. The detector then searches inside it."
        )
        self.range_box.toggled.connect(self._range_toggled)
        lay.addWidget(self.range_box)
        self.adjust_btn = icon_button("crosshair", "Adjust", kind="ghost", on_click=self._adjust)
        self.edit_btn = icon_button("tool", "Type value", kind="ghost", on_click=self._edit)
        lay.addLayout(row(self.adjust_btn, self.edit_btn, stretch_last=True))

    def show(self, key: str, f: ProfileField, captured: bool) -> None:  # type: ignore[override]
        self.key = key
        self.name.setText(f.display_label(key))
        self.ident.setText(f"{self.panel.host.profile_name()}.{key} · {f.type}")
        self.help.setText(f.help or f.note or "No description yet.")
        state = field_state(f)
        self.status_chip.setText(state)
        self.status_chip.set_status(STATE_CHIP[state])
        self.value.setText(describe_value(f))
        self.hint.setText(field_hint(f))
        self.warning.setText(
            "" if captured else "This screen has not been captured yet: go Back and capture it."
        )
        self.warning.setVisible(not captured)
        self.range_box.blockSignals(True)
        self.range_box.setChecked(f.range)
        self.range_box.blockSignals(False)
        self.range_box.setVisible(f.type == "roi")
        self.adjust_btn.setVisible(f.visual or f.type == "number")

    def _range_toggled(self, on: bool) -> None:
        if self.key:
            self.panel.host.profile().set_range(self.key, on)
            self.panel.host.save_profile()
            self.panel.refresh()

    def _adjust(self) -> None:
        f = self.panel.host.profile().field(self.key)
        self.panel.host.set_tool(TOOL_FOR_TYPE.get(f.type, "select"))

    def _edit(self) -> None:
        self.panel.host.edit_value(self.key)


class NumbersStepView(QWidget):
    """The profile's numbers, lists and text in one table; double-click edits."""

    def __init__(self, panel: WizardPanel) -> None:
        super().__init__()
        self.panel = panel
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(theme.PAD_SM)
        self.instruction = _wrapped()
        lay.addWidget(self.instruction)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Field", "Value", "Status"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setMinimumSectionSize(VALUE_COL_MIN_PX // 2)
        self.table.setColumnWidth(2, STATUS_COL_PX)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemDoubleClicked.connect(self._edit)
        lay.addWidget(self.table)

    def show(self, step: Step, prof: Profile) -> None:  # type: ignore[override]
        self.instruction.setText(step.screen.instruction)
        self.table.setRowCount(0)
        for key in step.fields:
            f = prof.field(key)
            r = self.table.rowCount()
            self.table.insertRow(r)
            name = QTableWidgetItem(f.display_label(key))
            name.setToolTip(f"{key}\n{f.help or f.note}")
            name.setData(Qt.ItemDataRole.UserRole, key)
            self.table.setItem(r, 0, name)
            val = QTableWidgetItem(describe_value(f))
            val.setToolTip(str(f.value))
            self.table.setItem(r, 1, val)
            state = field_state(f)
            self.table.setCellWidget(r, 2, Chip(state, STATE_CHIP[state]))
        self.table.setMinimumHeight(
            min(theme.WIZARD_PANEL_MIN_W, (self.table.rowCount() + 2) * self.table.rowHeight(0))
            if self.table.rowCount()
            else 0
        )

    def _edit(self, item: QTableWidgetItem) -> None:
        name = self.table.item(item.row(), 0)
        if name is not None:
            self.panel.host.edit_value(str(name.data(Qt.ItemDataRole.UserRole)))


class WizardPanel(QWidget):
    def __init__(self, host: WizardHost) -> None:
        super().__init__()
        self.host = host
        self.model: WizardModel | None = None
        self._shown_capture: Path | None = None
        self._capture_image: BgrImage | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(theme.PAD_SM)
        self.progress = StepProgress("tool")
        lay.addWidget(self.progress)
        head = QHBoxLayout()
        self.screen_title = QLabel("")
        self.screen_title.setObjectName("CardTitle")
        self.kind_chip = Chip("screen", "neutral")
        head.addWidget(self.screen_title)
        head.addStretch(1)
        head.addWidget(self.kind_chip)
        lay.addLayout(head)
        self.stack = QStackedWidget()
        self.screen_view = ScreenStepView(self)
        self.field_view = FieldStepView(self)
        self.numbers_view = NumbersStepView(self)
        for v in (self.screen_view, self.field_view, self.numbers_view):
            self.stack.addWidget(v)
        lay.addWidget(self.stack)
        lay.addStretch(1)
        nav = QHBoxLayout()
        nav.setSpacing(theme.PAD_SM)
        self.back_btn = icon_button("chevron-left", "Back", kind="ghost", on_click=self.back)
        self.skip_btn = icon_button("arrow-right", "Skip", kind="ghost", on_click=self.skip)
        self.confirm_btn = icon_button(
            "check", "Confirm", kind="accent", accent="tool", on_click=self.confirm
        )
        self.next_btn = icon_button(
            "chevron-right", "Next", kind="accent", accent="tool", on_click=self.next
        )
        nav.addWidget(self.back_btn)
        nav.addStretch(1)
        nav.addWidget(self.skip_btn)
        nav.addWidget(self.confirm_btn)
        nav.addWidget(self.next_btn)
        lay.addLayout(nav)
        self.summary = _wrapped("", "CardHelp")
        lay.addWidget(self.summary)

    # --- model -----------------------------------------------------------------------------

    def load(self, profile_name: str) -> None:
        self.model = WizardModel.for_profile(profile_name, self.host.profile().fields)
        self._shown_capture = None
        self._capture_image = None
        self._show()

    def _path(self, step: Step) -> Path:
        return capture_path(self.host.engine.paths, self.host.profile_name(), step.screen.key)

    def _show(self) -> None:
        if self.model is None:
            return
        step = self.model.step
        k, n = self.model.progress
        self.progress.set_step(k, n, step.screen.title)
        self.screen_title.setText(step.screen.title)
        self.kind_chip.setText(
            {"screen": "screen", "field": "field", "numbers": "numbers"}[step.kind]
        )
        prof = self.host.profile()
        path = self._path(step)
        captured = step.kind != "numbers" and path.exists()
        self._show_canvas(step, path)
        if step.kind == "screen":
            self.screen_view.show(step, prof, path, self._capture_image)
            self.stack.setCurrentWidget(self.screen_view)
            self.host.set_active_field(None, step.fields)
            self._auto_check(step)
        elif step.kind == "field":
            self.field_view.show(step.field, prof.field(step.field), captured)
            self.stack.setCurrentWidget(self.field_view)
            self.host.set_active_field(step.field, self.model.siblings(step))
            self.host.set_tool(TOOL_FOR_TYPE.get(prof.field(step.field).type, "select"))
        else:
            self.numbers_view.show(step, prof)
            self.stack.setCurrentWidget(self.numbers_view)
            self.host.set_active_field(None, ())
        self.back_btn.setEnabled(self.model.can_back)
        self.skip_btn.setVisible(step.kind == "field")
        self.confirm_btn.setVisible(step.kind == "field")
        self.next_btn.setVisible(step.kind != "field")
        self.next_btn.setText("Next" if self.model.can_next else "Finish")
        s = self.model.summary()
        self.summary.setText(
            f"{s['confirmed']} confirmed · {s['skipped']} skipped · {s['remaining']} to go "
            f"of {s['fields']} fields"
        )

    def _show_canvas(self, step: Step, path: Path) -> None:
        if step.kind == "numbers":
            return
        if path != self._shown_capture:
            self._shown_capture = path
            self._capture_image = load_capture(path)
            self.host.show_image(
                self._capture_image if self._capture_image is not None else self.host.live_frame()
            )

    def _auto_check(self, step: Step) -> None:
        if self._capture_image is not None:
            self.screen_view.show_check(self._run(step, self._capture_image), "capture")
        else:
            self.screen_view.show_check(
                CheckResult(None, "capture the screen, or check the live one"), "no capture"
            )

    def _run(self, step: Step, image: BgrImage) -> CheckResult:
        return run_check(
            step.screen.check,
            image,
            self.host.profile_name(),
            self.host.engine.profile,
            paths=self.host.engine.paths,
        )

    # --- actions ---------------------------------------------------------------------------

    def capture(self) -> None:
        if self.model is None:
            return
        frame = self.host.live_frame()
        if frame is None:
            self.screen_view.show_check(
                CheckResult(None, "no frame: is Animal Jam Classic open?"), "capture"
            )
            return
        step = self.model.step
        path = save_capture(self._path(step), np.ascontiguousarray(frame))
        self._shown_capture = None  # force a reload
        self._show_canvas(step, path)
        self.screen_view.show(step, self.host.profile(), path, self._capture_image)
        self._auto_check(step)
        self.host.engine.emit(
            "calibration.captured",
            profile=self.host.profile_name(),
            screen=step.screen.key,
            message=f"Captured {path.relative_to(self.host.engine.paths.root)}",
        )

    def check_live(self) -> None:
        if self.model is None:
            return
        frame = self.host.live_frame()
        if frame is None:
            self.screen_view.show_check(CheckResult(None, "no live frame"), "live")
            return
        self.screen_view.show_check(self._run(self.model.step, frame), "live screen")

    def confirm(self) -> None:
        if self.model is None:
            return
        step = self.model.step
        if step.kind == "field":
            f = self.host.profile().field(step.field)
            if f.value is not None:
                f.status = "calibrated"
                self.host.save_profile()
        self.model.confirm()
        self._show()

    def skip(self) -> None:
        if self.model is not None:
            self.model.skip()
            self._show()

    def next(self) -> None:
        if self.model is None:
            return
        if not self.model.can_next:
            self.host.save_profile()
            s = self.model.summary()
            self.summary.setText(
                f"Done: {s['confirmed']} confirmed, {s['skipped']} skipped, "
                f"{s['remaining']} untouched. Profile saved."
            )
            return
        self.model.next()
        self._show()

    def back(self) -> None:
        if self.model is not None:
            self.model.back()
            self._show()

    def refresh(self) -> None:
        """The host changed the current field's value: redraw the field view."""
        if self.model is None or self.model.step.kind != "field":
            return
        step = self.model.step
        self.field_view.show(
            step.field, self.host.profile().field(step.field), self._path(step).exists()
        )

    @property
    def current_field(self) -> str | None:
        if self.model is None or self.model.step.kind != "field":
            return None
        return self.model.step.field

    def buttons(self) -> list[QPushButton]:
        return [self.back_btn, self.skip_btn, self.confirm_btn, self.next_btn]
