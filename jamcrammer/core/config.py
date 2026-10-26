"""Application config (pydantic), loaded from config.yaml with config.example.yaml as fallback.

Every tunable number in the app lives here or in a mode profile. Code never hard-codes them.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from jamcrammer.core.paths import Paths

Direction = Literal["left", "right", "up", "down"]


class Level(StrEnum):
    """Event / notification levels (KICKOFF §7.5)."""

    INFO = "INFO"  # log only
    NOTICE = "NOTICE"  # toast
    HIGH = "HIGH"  # toast + repeating sound + push

    @property
    def rank(self) -> int:
        return {Level.INFO: 0, Level.NOTICE: 1, Level.HIGH: 2}[self]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AntiAfkConfig(StrictModel):
    tap_ms: int = Field(200, ge=20, le=2000)
    gap_ms: int = Field(300, ge=0, le=5000)
    settle_ms: int = Field(400, ge=0, le=5000)
    pattern: list[tuple[Direction, Direction]] = [
        ("left", "right"),
        ("up", "down"),
        ("right", "left"),
        ("down", "up"),
    ]


class NotifyLevels(StrictModel):
    toast: Level = Level.NOTICE
    sound: Level = Level.HIGH
    push: Level = Level.HIGH


class NotifyConfig(StrictModel):
    toast: bool = True
    sound: bool = True
    ntfy_topic: str = ""
    ntfy_server: str = "https://ntfy.sh"
    discord_webhook: str = ""
    levels: NotifyLevels = NotifyLevels()
    sound_repeats: int = Field(3, ge=1, le=20)
    sound_interval_s: float = Field(1.5, ge=0.1, le=30)
    timeout_s: float = Field(10.0, gt=0)


class WatchdogConfig(StrictModel):
    fps: float = Field(2.0, gt=0, le=30)
    clear_timeout_s: float = Field(3.0, gt=0)
    ocr_interval_s: float = Field(5.0, gt=0)
    max_retries: int = Field(1, ge=0, le=5)
    unknown_cooldown_s: float = Field(60.0, ge=0)
    unknown_min_ticks: int = Field(3, ge=1)
    baseline_refresh_s: float = Field(30.0, gt=0)


class DriftConfig(StrictModel):
    threshold: float = Field(18.0, gt=0, le=255)
    downscale: tuple[int, int] = (64, 36)


class HuntConfig(StrictModel):
    panel_timeout_s: float = Field(4.0, gt=0)
    panel_poll_s: float = Field(0.25, ge=0)
    panel_settle_s: float = Field(0.4, ge=0)
    close_timeout_s: float = Field(3.0, gt=0)
    join_retry_s: float = Field(30.0, gt=0)
    join_max_retries: int = Field(10, ge=0)
    arrival_timeout_s: float = Field(8.0, gt=0)
    sighting_merge_gap_s: float = Field(900.0, gt=0)
    antiafk_each_cycle: bool = True


class ActivityTestConfig(StrictModel):
    phase_minutes: float = Field(8.0, gt=0)
    movement_interval_s: float = Field(60.0, gt=0)
    panel_interval_s: float = Field(60.0, gt=0)


class HotkeyConfig(StrictModel):
    kill: str = "f12"
    pause: str = "f11"
    toggle_mode: str = "f9"
    learn_mark: str = "f6"
    learn_correct: str = "f7"
    learn_wrong: str = "f8"


class LearnConfig(StrictModel):
    keyframe_threshold: int = Field(12, ge=0, le=64)
    ring_fps: float = Field(5.0, gt=0, le=60)
    ring_window_s: float = Field(5.0, gt=0, le=60)
    heartbeat_s: float = Field(30.0, gt=0)
    jpeg_quality: int = Field(90, ge=10, le=100)
    phash_width: int = Field(256, ge=32, le=1920)
    ocr_keyframes: bool = True


KeyMethod = Literal["scancode", "vk"]


class InputConfig(StrictModel):
    click_hold_ms: int = Field(60, ge=1, le=1000)
    cursor_settle_ms: int = Field(30, ge=0, le=1000)
    key_method: KeyMethod = "scancode"
    postcondition_retries: int = Field(1, ge=0, le=5)
    foreground_timeout_s: float = Field(2.0, gt=0)
    history_len: int = Field(200, ge=10)


UnknownFactPolicy = Literal["learned_prior", "fixed_true", "fixed_false", "skip"]
AnswerMode = Literal["learn_only", "bank_policy", "coin_flip"]
CaptureBackend = Literal["auto", "wgc", "mss"]


class AppConfig(StrictModel):
    """Top-level config. Field names mirror config.example.yaml exactly."""

    window_title_match: str = "Animal Jam Classic"
    window_process_name: str = "AJ Classic.exe"
    capture_backend: CaptureBackend = "auto"
    fps: float = Field(4.0, gt=0, le=60)

    hunt_interval_s: float = Field(300.0, gt=0)
    opportunist_check_s: float = Field(300.0, gt=0)
    resume_after_idle_s: float = Field(120.0, ge=0)
    heartbeat_s: float = Field(180.0, gt=0)

    antiafk: AntiAfkConfig = AntiAfkConfig()

    auto_join: bool = True
    start_farming_after_join: bool = True
    unknown_fact_policy: UnknownFactPolicy = "learned_prior"
    answer_mode: AnswerMode = "learn_only"
    fuzzy_threshold: int = Field(90, ge=0, le=100)

    notify: NotifyConfig = NotifyConfig()
    hotkeys: HotkeyConfig = HotkeyConfig()
    dry_run: bool = False
    presence_slack_s: float = Field(0.5, ge=0)
    input: InputConfig = InputConfig()
    learn: LearnConfig = LearnConfig()
    watchdog: WatchdogConfig = WatchdogConfig()
    drift: DriftConfig = DriftConfig()
    activity_test: ActivityTestConfig = ActivityTestConfig()
    hunt: HuntConfig = HuntConfig()

    @classmethod
    def from_yaml(cls, path: Path) -> AppConfig:
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        if not isinstance(raw, dict):
            msg = f"{path}: top level must be a mapping"
            raise ValueError(msg)
        return cls.model_validate(raw)


def load_config(paths: Paths | None = None, override: Path | None = None) -> AppConfig:
    """Load config.yaml, falling back to config.example.yaml, falling back to defaults."""
    paths = paths or Paths()
    candidates = [override] if override else [paths.config_file, paths.config_example]
    for candidate in candidates:
        if candidate is not None and candidate.exists():
            return AppConfig.from_yaml(candidate)
    return AppConfig()
