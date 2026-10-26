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


class DoubleUpConfig(StrictModel):
    preview_s: float = Field(3.0, gt=0)
    burst_fps: float = Field(10.0, gt=0, le=60)
    pair_confidence_min: float = Field(0.6, ge=0, le=1)
    flip_ms: int = Field(400, ge=0)
    settle_s: float = Field(0.15, ge=0)
    click_gap_s: float = Field(0.08, ge=0)
    poll_s: float = Field(0.25, gt=0)
    board_timeout_s: float = Field(20.0, gt=0)
    hide_timeout_s: float = Field(5.0, gt=0)
    end_timeout_s: float = Field(10.0, gt=0)
    change_threshold: float = Field(18.0, gt=0)
    same_face_distance: float = Field(0.35, gt=0)
    preview_diversity: float = Field(0.5, gt=0)
    hidden_uniformity: float = Field(0.25, gt=0)
    tile_min_std: float = Field(8.0, ge=0)
    end_screen_keywords: list[str] = ["earned", "gems", "play again"]


class BestGuessConfig(StrictModel):
    fps: float = Field(5.0, gt=0, le=30)
    commit_after_s: float = Field(3.0, ge=0)
    lock_seconds: int = Field(5, ge=0)
    verify_after_s: float = Field(1.0, ge=0)
    heartbeat_s: float = Field(180.0, gt=0)
    unknown_pause_s: float = Field(30.0, gt=0)
    fact_min_chars: int = Field(8, ge=1)
    question_s: float = Field(10.0, gt=0)
    waiting_keywords: list[str] = ["get ready", "next round", "waiting", "starting"]
    ocr_upscale: float = Field(2.0, ge=1.0)
    reveal_min_fill: float = Field(0.15, ge=0, le=1)
    reveal_min_margin: float = Field(0.4, ge=0, le=1)
    dance_motion_min: float = Field(12.0, ge=0)
    true_hsv: tuple[tuple[int, int, int], tuple[int, int, int]] = ((40, 90, 90), (85, 255, 255))
    false_hsv: tuple[tuple[int, int, int], tuple[int, int, int]] = ((130, 60, 60), (165, 255, 255))


class OpportunistConfig(StrictModel):
    entry_timeout_s: float = Field(8.0, gt=0)
    exit_timeout_s: float = Field(5.0, gt=0)
    settle_s: float = Field(0.5, ge=0)
    poll_s: float = Field(0.25, gt=0)
    max_entry_failures: int = Field(2, ge=1)


class DerbyConfig(StrictModel):
    fps: float = Field(20.0, gt=0, le=60)
    jump_key: str = "space"
    jump_hold_ms: int = Field(40, ge=1)
    boosts_per_race: int = Field(3, ge=0)
    boost_after_jump_s: float = Field(0.6, ge=0)
    pixel_diff_min: float = Field(30.0, gt=0)
    column_fraction_min: float = Field(0.1, gt=0, le=1)
    obstacle_min_px: int = Field(20, ge=1)
    background_alpha: float = Field(0.05, gt=0, le=1)
    background_warmup_frames: int = Field(3, ge=1)
    race_timeout_s: float = Field(90.0, gt=0)
    results_settle_s: float = Field(0.5, ge=0)


class GemBallConfig(StrictModel):
    hold_min_ms: int = Field(60, ge=1)
    hold_max_ms: int = Field(1200, ge=1)
    hold_step_ms: int = Field(20, ge=5)
    repeats: int = Field(3, ge=1)
    min_repeats: int = Field(2, ge=1)
    exploit_rate: float = Field(0.7, ge=0, le=1)
    min_balls: int = Field(6, ge=1)
    settle_s: float = Field(4.0, ge=0)


class SwoopyConfig(StrictModel):
    fps: float = Field(60.0, gt=0, le=120)
    flap_key: str = "space"
    flap_hold_ms: int = Field(20, ge=1)
    kp: float = Field(1.5, ge=0)
    kd: float = Field(0.05, ge=0)
    flap_threshold: float = Field(25.0)
    target_offset_px: float = Field(0.0)
    min_flap_interval_s: float = Field(0.12, ge=0)
    default_target_y: float = Field(150.0)
    min_bird_px: int = Field(10, ge=1)
    bird_lead_px: int = Field(12, ge=0)
    pipe_col_min: float = Field(0.2, ge=0, le=1)
    pipe_probe_px: int = Field(6, ge=1)
    gap_reset_px: float = Field(60.0, ge=0)
    restart_s: float = Field(1.0, ge=0)


ArcadeGame = Literal["double_up", "jamaa_derby", "gem_ball", "swoopy_eagle"]


class AnalysisConfig(StrictModel):
    duplicate_distance: int = Field(2, ge=0, le=64)
    transition_distance: int = Field(14, ge=0, le=64)
    cluster_distance: float = Field(1.6, gt=0)
    min_cluster: int = Field(3, ge=1)
    samples_per_state: int = Field(4, ge=1, le=12)
    ocr: bool = True
    ocr_upscale: float = Field(1.5, ge=1.0, le=4.0)
    max_regions: int = Field(20, ge=1)


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
    analysis: AnalysisConfig = AnalysisConfig()
    double_up: DoubleUpConfig = DoubleUpConfig()
    bestguess: BestGuessConfig = BestGuessConfig()
    opportunist: OpportunistConfig = OpportunistConfig()
    derby: DerbyConfig = DerbyConfig()
    gem_ball: GemBallConfig = GemBallConfig()
    swoopy: SwoopyConfig = SwoopyConfig()
    arcade_game: ArcadeGame = "double_up"

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
