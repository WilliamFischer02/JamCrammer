"""Repository-relative paths. Everything runtime lives under the repo root."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


def repo_root() -> Path:
    """The project root (parent of the ``jamcrammer`` package)."""
    return Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class Paths:
    """All well-known directories and files, derived from one root."""

    root: Path = field(default_factory=repo_root)

    @property
    def config_file(self) -> Path:
        return self.root / "config.yaml"

    @property
    def config_example(self) -> Path:
        return self.root / "config.example.yaml"

    @property
    def profiles(self) -> Path:
        return self.root / "profiles"

    @property
    def assets(self) -> Path:
        return self.root / "assets"

    @property
    def inbox(self) -> Path:
        return self.root / "inbox"

    @property
    def captures(self) -> Path:
        return self.root / "captures"

    @property
    def sessions(self) -> Path:
        return self.captures / "sessions"

    @property
    def unknown(self) -> Path:
        return self.captures / "unknown"

    @property
    def unknown_popups(self) -> Path:
        return self.captures / "unknown_popups"

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def logs(self) -> Path:
        return self.data / "logs"

    @property
    def db(self) -> Path:
        return self.data / "jamcrammer.db"

    def ensure_runtime_dirs(self) -> None:
        for d in (self.sessions, self.unknown, self.unknown_popups, self.logs):
            d.mkdir(parents=True, exist_ok=True)
