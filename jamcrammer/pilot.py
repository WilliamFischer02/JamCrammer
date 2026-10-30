"""The pilot: Claude at the controls, one command at a time (docs/PILOT.md).

Each ``jamcrammer pilot <command>`` is its own short process: it finds the AJC window, takes a
frame, acts through the same InputController the bots use (inside the client only), takes
another frame and reports what changed, as JSON for the one reading it. A pilot session is
an ordinary Learn recording (``learn --stop-file``) started by ``pilot start`` and ended by
``pilot stop``, so everything done at the controls can be analysed by the same route and
level builders as William's own play, plus a ``pilot_log.jsonl`` of the commands and notes.

Guardrails, none of them optional:
- input only with ``pilot.enabled: true`` in config.yaml (William's switch); ``look`` always;
- clicks only inside the client, never on the chat field or the log-out button
  (``forbidden_rois``), never a drag that starts or ends there;
- keys from a short list (arrows, space, escape), held at most ``max_hold_s``;
- a refusal when William's own input was seen within ``yield_s`` (he has the controls);
- every command and its outcome written down.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt

from jamcrammer.core.config import AppConfig, PilotConfig
from jamcrammer.core.profiles import Profile
from jamcrammer.modes.adventures import objects, sight
from jamcrammer.modes.adventures.inventory import DigitBank, read_counts
from jamcrammer.modes.adventures.level import Level, level_dir
from jamcrammer.modes.adventures.opening import classify
from jamcrammer.vision.ocr import OcrResult

BgrImage = npt.NDArray[np.uint8]
ALLOWED_KEYS = frozenset({"up", "down", "left", "right", "space", "escape"})
CONTACT_WIDTH = 960  # the frame I read, halved
MAX_OCR_LINES = 40


class PilotRefusedError(RuntimeError):
    """A command the guardrails do not allow."""


@dataclass
class Seen:
    """What one frame shows, for the pilot's eyes."""

    ts: float
    frame_path: str
    contact_path: str
    window: dict[str, Any]
    menu: str
    banner: str
    phase: str
    dot: list[float] | None
    counts: list[int | None]
    dialog_x: list[int] | None
    crystals: list[dict[str, Any]]
    fungi: list[dict[str, Any]]
    pin: list[int] | None
    ocr: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def contact(frame: BgrImage) -> BgrImage:
    h, w = frame.shape[:2]
    scale = CONTACT_WIDTH / w
    return np.asarray(
        cv2.resize(frame, (CONTACT_WIDTH, int(h * scale)), interpolation=cv2.INTER_AREA),
        dtype=np.uint8,
    )


def summarize(
    frame: BgrImage,
    prof: Profile,
    lvl: Level | None,
    ocr: OcrResult,
    banner: str,
    *,
    digits: DigitBank | None,
    dialog_x: BgrImage | None,
    window: dict[str, Any],
    frame_path: str,
    contact_path: str,
) -> Seen:
    """Everything the detectors say about a frame."""
    menu = classify(frame, ocr, lvl, prof).state if lvl is not None else ""
    phase = ""
    if lvl is not None:
        p = lvl.phase_of(banner)
        phase = p.key if p is not None else ""
    counts = read_counts(frame, prof, lambda _c: "", digits=digits)
    dot = sight.minimap_dot(frame, prof)
    x = None
    if dialog_x is not None:
        pt = sight.find_dialog_x(frame, dialog_x, prof.number("dialog_x_min"))
        x = [pt[0], pt[1]] if pt is not None else None
    pin = objects.find_marker(frame, prof)
    lines = [{"text": ln.text, "box": list(ln.box)} for ln in ocr.lines[:MAX_OCR_LINES] if ln.text]
    return Seen(
        ts=time.time(),
        frame_path=frame_path,
        contact_path=contact_path,
        window=window,
        menu=menu,
        banner=banner,
        phase=phase,
        dot=[round(dot[0], 1), round(dot[1], 1)] if dot else None,
        counts=[counts.blossoms, counts.boomseeds],
        dialog_x=x,
        crystals=[asdict(b) for b in objects.crystals(frame, prof)[:12]],
        fungi=[asdict(b) for b in objects.fungi(frame, prof)[:8]],
        pin=[pin.x, pin.y] if pin else None,
        ocr=lines,
    )


# --- guardrails -----------------------------------------------------------------------


def check_point(cfg: PilotConfig, x: int, y: int) -> None:
    for roi in cfg.forbidden_rois:
        rx, ry, rw, rh = (int(v) for v in roi)
        if rx <= x < rx + rw and ry <= y < ry + rh:
            msg = f"({x},{y}) is in a forbidden area {list(roi)} (chat, log out)"
            raise PilotRefusedError(msg)


def check_key(cfg: PilotConfig, key: str, hold_s: float) -> None:
    if key.lower() not in ALLOWED_KEYS:
        msg = f"key {key!r} is not on the pilot's list {sorted(ALLOWED_KEYS)}"
        raise PilotRefusedError(msg)
    if hold_s > cfg.max_hold_s or hold_s <= 0:
        msg = f"hold {hold_s} s is outside (0, {cfg.max_hold_s}]"
        raise PilotRefusedError(msg)


def check_enabled(cfg: PilotConfig) -> None:
    if not cfg.enabled:
        msg = "pilot input is off: set pilot.enabled: true in config.yaml (William's switch)"
        raise PilotRefusedError(msg)


def check_yield(cfg: PilotConfig, seconds_since_user_input: float) -> None:
    if seconds_since_user_input < cfg.yield_s:
        msg = (
            f"William used the keyboard or mouse {seconds_since_user_input:.1f} s ago: his controls"
        )
        raise PilotRefusedError(msg)


# --- the session ----------------------------------------------------------------------


@dataclass
class PilotSession:
    """The active pilot session: where the recording and the command log live."""

    session_dir: Path
    stop_file: Path
    target: str
    started_ts: float

    @property
    def log_path(self) -> Path:
        return self.session_dir / "pilot_log.jsonl"

    def log(self, record: dict[str, Any]) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), **record}) + "\n")

    def save(self, pointer: Path) -> None:
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text(
            json.dumps(
                {
                    "session_dir": str(self.session_dir),
                    "stop_file": str(self.stop_file),
                    "target": self.target,
                    "started_ts": self.started_ts,
                }
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, pointer: Path) -> PilotSession | None:
        if not pointer.exists():
            return None
        doc = json.loads(pointer.read_text(encoding="utf-8"))
        return cls(
            Path(doc["session_dir"]), Path(doc["stop_file"]), doc["target"], doc["started_ts"]
        )


def pointer_path(data_dir: Path) -> Path:
    return data_dir / "pilot" / "current.json"


def load_level(cfg: AppConfig, profiles_dir: Path) -> Level | None:
    return Level.load(level_dir(profiles_dir, cfg.adventures.route.target))
