"""Live-mode gate (KICKOFF §8.3): a mode may send real input only when every required profile
field is usable and the replay harness reached the accuracy threshold on at least one
recorded session of that target. The GUI shows exactly what is missing."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import Profile, ProfileError, load_profile

REPLAY_THRESHOLD = 0.95
REQUIRED_FIELDS: dict[str, list[tuple[str, str]]] = {
    "hunt": [
        ("parties", "party_hat_point"),
        ("parties", "close_x_point"),
        ("parties", "title_template"),
        ("parties", "go_now_template"),
    ],
    "bestguess": [
        ("bestguess", "fact_roi"),
        ("bestguess", "true_point"),
        ("bestguess", "false_point"),
        ("bestguess", "reveal_roi"),
    ],
    "double_up": [
        ("double_up", "grid_origin"),
        ("double_up", "grid_pitch"),
        ("double_up", "tile_size"),
        ("double_up", "play_again_point"),
        ("double_up", "end_screen_gems_roi"),
    ],
}
REPLAY_TARGET = {"bestguess": "bestguess", "double_up": "double_up", "hunt": None}


@dataclass
class ReplayRecord:
    session: str
    accuracy: float
    frames: int


@dataclass
class GateStatus:
    mode: str
    missing: list[str] = field(default_factory=list)
    statuses: dict[str, str] = field(default_factory=dict)  # "profile.field" -> status
    best_replay: ReplayRecord | None = None
    needs_replay: bool = True
    threshold: float = REPLAY_THRESHOLD

    @property
    def replay_ok(self) -> bool:
        return not self.needs_replay or (
            self.best_replay is not None and self.best_replay.accuracy >= self.threshold
        )

    @property
    def unlocked(self) -> bool:
        return not self.missing and self.replay_ok

    def summary(self) -> str:
        if self.unlocked:
            rep = (
                f" · replay {self.best_replay.accuracy:.1%} on {self.best_replay.session}"
                if self.best_replay
                else ""
            )
            return f"UNLOCKED{rep}"
        parts = []
        if self.missing:
            parts.append(f"missing {', '.join(self.missing)}")
        if self.needs_replay and not self.replay_ok:
            if self.best_replay is None:
                parts.append(f"no replay ≥{self.threshold:.0%} yet")
            else:
                parts.append(
                    f"best replay {self.best_replay.accuracy:.1%} < {self.threshold:.0%} "
                    f"({self.best_replay.session})"
                )
        return "LOCKED: " + "; ".join(parts)


def best_replay(paths: Paths, target: str) -> ReplayRecord | None:
    """Scan captures/sessions/*_<target>*/analysis/replay.json for the best accuracy."""
    best: ReplayRecord | None = None
    if not paths.sessions.exists():
        return None
    for session in paths.sessions.iterdir():
        if target not in session.name:
            continue
        rec_path = session / "analysis" / "replay.json"
        if not rec_path.exists():
            continue
        try:
            doc = json.loads(rec_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rec = ReplayRecord(session.name, float(doc.get("accuracy", 0.0)), int(doc.get("frames", 0)))
        if rec.frames and (best is None or rec.accuracy > best.accuracy):
            best = rec
    return best


def gate_status(
    mode: str,
    paths: Paths | None = None,
    profiles: dict[str, Profile] | None = None,
    threshold: float = REPLAY_THRESHOLD,
) -> GateStatus:
    paths = paths or Paths()
    status = GateStatus(mode=mode, threshold=threshold)
    cache: dict[str, Profile] = dict(profiles or {})
    for profile_name, key in REQUIRED_FIELDS.get(mode, []):
        try:
            prof = cache.get(profile_name) or load_profile(profile_name, paths)
            cache[profile_name] = prof
            st = prof.status(key)
        except ProfileError:
            st = "uncalibrated"
        status.statuses[f"{profile_name}.{key}"] = st
        if st == "uncalibrated":
            status.missing.append(f"{profile_name}.{key}")
    target = REPLAY_TARGET.get(mode)
    status.needs_replay = target is not None
    if target is not None:
        status.best_replay = best_replay(paths, target)
    return status


def write_replay_record(session_dir: Path, accuracy: float, frames: int, detector: str) -> Path:
    out = session_dir / "analysis" / "replay.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"accuracy": accuracy, "frames": frames, "detector": detector}, indent=2),
        encoding="utf-8",
    )
    return out
