"""Review wizard (KICKOFF §11.4): States → ROIs → Click targets → Answers → Replay check →
Accept (promote the proposed profile, seed the answer bank)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from jamcrammer.app import theme
from jamcrammer.app.engine import Engine
from jamcrammer.app.widgets import Chip, icon_button, row
from jamcrammer.vision.ocr import normalize_text

PREVIEW_W = 720
OVERLAY = 2


def _pix(image: np.ndarray, width: int) -> QPixmap:
    h = int(width * image.shape[0] / image.shape[1])
    small = cv2.resize(image, (width, h), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    return QPixmap.fromImage(
        QImage(
            rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888
        ).copy()
    )


def _page(title: str, body: str) -> tuple[QWizardPage, QVBoxLayout]:
    page = QWizardPage()
    lay = QVBoxLayout(page)
    t = QLabel(title)
    t.setObjectName("WizardTitle")
    b = QLabel(body)
    b.setWordWrap(True)
    b.setObjectName("PageSubtitle")
    lay.addWidget(t)
    lay.addWidget(b)
    return page, lay


class ReviewWizard(QWizard):
    def __init__(self, engine: Engine, session_dir: Path) -> None:
        super().__init__()
        self.engine = engine
        self.session_dir = session_dir
        self.setWindowTitle(f"Review · {session_dir.name}")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.resize(1000, 720)
        self.analysis: dict[str, Any] = json.loads(
            (session_dir / "analysis" / "result.json").read_text(encoding="utf-8")
        )
        self.target = str(self.analysis.get("target", "custom"))
        self._build_states()
        self._build_rois()
        self._build_clicks()
        self._build_answers()
        self._build_replay()
        self._build_accept()

    def _sample_frame(self) -> np.ndarray | None:
        frames = sorted((self.session_dir / "frames").glob("*.png"))
        if not frames:
            return None
        img = cv2.imread(str(frames[len(frames) // 2]))
        return None if img is None else np.asarray(img, dtype=np.uint8)

    def _build_states(self) -> None:
        page, lay = _page(
            "1 · States",
            f"{len(self.analysis['states'])} candidate screen states found by clustering. Cycle: {' → '.join(str(c) for c in self.analysis['cycle']) or 'not found'}",
        )
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        grid = QGridLayout(inner)
        for i, s in enumerate(self.analysis["states"]):
            cell = QVBoxLayout()
            img = cv2.imread(str(self.session_dir / s["representative"]))
            lab = QLabel()
            if img is not None:
                lab.setPixmap(_pix(np.asarray(img, dtype=np.uint8), 300))
            cell.addWidget(lab)
            cell.addWidget(
                QLabel(
                    f"state {s['id']} · {s['frames']} frames · median {s['median_duration_s']} s · {' '.join(s.get('tags', []))}"
                )
            )
            w = QWidget()
            w.setLayout(cell)
            grid.addWidget(w, i // 3, i % 3)
        scroll.setWidget(inner)
        lay.addWidget(scroll)
        self.addPage(page)

    def _build_rois(self) -> None:
        page, lay = _page(
            "2 · Regions", "Proposed ROIs drawn on a sample frame (adjust later in Calibration)."
        )
        img = self._sample_frame()
        lab = QLabel("no frames")
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if img is not None:
            img = img.copy()
            for key, v in self.analysis["proposal"].items():
                val = v["value"]
                if isinstance(val, list) and len(val) == 4:
                    x, y, w, h = (int(a) for a in val)
                    cv2.rectangle(img, (x, y), (x + w, y + h), (255, 107, 155), OVERLAY)
                    cv2.putText(
                        img,
                        f"{key} ({v['confidence']})",
                        (x, max(16, y - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (255, 255, 255),
                        2,
                        cv2.LINE_AA,
                    )
            lab.setPixmap(_pix(img, PREVIEW_W))
        lay.addWidget(lab)
        lay.addWidget(QLabel("Missing: " + (", ".join(self.analysis["missing"]) or "nothing")))
        self.addPage(page)

    def _build_clicks(self) -> None:
        page, lay = _page(
            "3 · Click targets", "Where you clicked, per state, and the proposed target points."
        )
        img = self._sample_frame()
        lab = QLabel()
        if img is not None:
            img = img.copy()
            for key, v in self.analysis["proposal"].items():
                val = v["value"]
                if isinstance(val, list) and len(val) == 2:
                    x, y = int(val[0]), int(val[1])
                    cv2.circle(img, (x, y), 18, (138, 208, 47), OVERLAY + 1)
                    cv2.putText(
                        img,
                        key,
                        (x + 24, y + 6),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.8,
                        (255, 255, 255),
                        2,
                        cv2.LINE_AA,
                    )
            lab.setPixmap(_pix(img, PREVIEW_W))
        lay.addWidget(lab)
        lay.addWidget(
            QLabel(f"clicks per state: {self.analysis['zones'].get('clicks_by_state', {})}")
        )
        self.addPage(page)

    def _build_answers(self) -> None:
        page, lay = _page(
            "4 · Answers", "Facts seeded from your F7/F8 markers. Uncheck a row to skip it."
        )
        self.answers = QTableWidget(len(self.analysis["answers"]), 3)
        self.answers.setHorizontalHeaderLabels(["Fact", "Answer", "Confidence"])
        self.answers.horizontalHeader().setStretchLastSection(True)
        for i, a in enumerate(self.analysis["answers"]):
            item = QTableWidgetItem(a["display"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.answers.setItem(i, 0, item)
            self.answers.setItem(i, 1, QTableWidgetItem(a["answer"]))
            self.answers.setItem(i, 2, QTableWidgetItem(str(a["confidence"])))
        lay.addWidget(self.answers)
        self.addPage(page)

    def _build_replay(self) -> None:
        page, lay = _page(
            "5 · Replay check",
            "Runs the state classifier over the session and compares with the analysis labels (self-consistency until the mode's own detectors exist).",
        )
        self.replay_out = QPlainTextEdit()
        self.replay_out.setReadOnly(True)
        self.replay_chip = Chip("not run", "neutral")
        lay.addLayout(
            row(
                icon_button("refresh", "Run replay", on_click=self._run_replay),
                self.replay_chip,
                stretch_last=True,
            )
        )
        lay.addWidget(self.replay_out)
        self.addPage(page)

    def _run_replay(self) -> None:
        from jamcrammer.replay.harness import NearestStateDetector, format_timeline, replay

        try:
            res = replay(self.session_dir, NearestStateDetector(self.session_dir))
        except Exception as exc:
            self.replay_out.setPlainText(f"replay failed: {exc}")
            return
        self.replay_out.setPlainText(format_timeline(res))
        ok = res.accuracy >= 0.95
        self.replay_chip.setText(f"{res.accuracy:.1%}")
        self.replay_chip.set_status("calibrated" if ok else "uncalibrated")

    def _build_accept(self) -> None:
        page, lay = _page(
            "6 · Accept",
            f"Promote profiles/{self.target}.proposed.yaml to the active profile and write the checked answers into the answer bank.",
        )
        self.accept_note = QLabel(
            f"Proposed fields: {', '.join(self.analysis['proposal']) or 'none'}"
        )
        self.accept_note.setWordWrap(True)
        lay.addWidget(self.accept_note)
        lay.addStretch(1)
        self.addPage(page)

    def accept(self) -> None:
        proposed = self.engine.paths.profiles / f"{self.target}.proposed.yaml"
        if proposed.exists():
            shutil.copy2(proposed, self.engine.paths.profiles / f"{self.target}.yaml")
            self.engine.reload_profiles()
        n = 0
        for i, a in enumerate(self.analysis["answers"]):
            item = self.answers.item(i, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                self.engine.store.upsert_fact(
                    normalize_text(a["display"]), a["display"], a["answer"], "learn-session"
                )
                n += 1
        self.engine.emit(
            "learn.accepted",
            session=self.session_dir.name,
            target=self.target,
            answers=n,
            message=f"Profile {self.target} promoted; {n} answers seeded",
        )
        super().accept()


__all__ = ["ReviewWizard", "theme"]
