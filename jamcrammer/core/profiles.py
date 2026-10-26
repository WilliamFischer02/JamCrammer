"""Per-mode UI profiles: points, ROIs, colors, templates, numbers, lists.

``profiles/<name>.yaml`` (gitignored, real) overrides ``profiles/<name>.example.yaml`` (committed).
Every field carries a status so the GUI and the live-mode gate can show what is still missing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from jamcrammer.core.paths import Paths

FieldType = Literal["point", "roi", "color", "template", "number", "fraction_box", "list", "text"]
FieldStatus = Literal["calibrated", "proposed", "uncalibrated"]

PROFILE_VERSION = 1


class ProfileError(RuntimeError):
    pass


class ProfileField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: FieldType
    value: Any = None
    status: FieldStatus = "uncalibrated"
    note: str = ""
    confidence: float | None = Field(None, ge=0.0, le=1.0)

    @property
    def usable(self) -> bool:
        return self.status != "uncalibrated" and self.value is not None


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = PROFILE_VERSION
    name: str
    fields: dict[str, ProfileField] = Field(default_factory=dict)
    source_path: Path | None = Field(default=None, exclude=True)

    # --- access ---------------------------------------------------------------------

    def field(self, key: str) -> ProfileField:
        try:
            return self.fields[key]
        except KeyError as exc:
            msg = f"profile {self.name!r} has no field {key!r}"
            raise ProfileError(msg) from exc

    def has(self, key: str) -> bool:
        return key in self.fields and self.fields[key].usable

    def status(self, key: str) -> FieldStatus:
        return self.fields[key].status if key in self.fields else "uncalibrated"

    def point(self, key: str) -> tuple[int, int]:
        v = self._value(key, "point")
        return (int(v[0]), int(v[1]))

    def roi(self, key: str) -> tuple[int, int, int, int]:
        """(x, y, w, h) in client pixels."""
        v = self._value(key, "roi")
        return (int(v[0]), int(v[1]), int(v[2]), int(v[3]))

    def fraction_box(self, key: str) -> tuple[float, float, float, float]:
        """(x0, y0, x1, y1) as fractions of the client size."""
        v = self._value(key, "fraction_box")
        return (float(v[0]), float(v[1]), float(v[2]), float(v[3]))

    def color(self, key: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
        """HSV (lo, hi) in OpenCV ranges: H 0-180, S/V 0-255."""
        v = self._value(key, "color")
        lo, hi = v
        return ((int(lo[0]), int(lo[1]), int(lo[2])), (int(hi[0]), int(hi[1]), int(hi[2])))

    def number(self, key: str) -> float:
        return float(self._value(key, "number"))

    def items(self, key: str) -> list[Any]:
        return list(self._value(key, "list"))

    def text(self, key: str) -> str:
        return str(self._value(key, "text"))

    def template_path(self, key: str, paths: Paths | None = None) -> Path:
        rel = str(self._value(key, "template"))
        base = (paths or Paths()).assets
        return base / rel

    def _value(self, key: str, expected: FieldType) -> Any:
        f = self.field(key)
        if f.type != expected:
            msg = f"profile {self.name!r} field {key!r} is {f.type}, not {expected}"
            raise ProfileError(msg)
        if not f.usable:
            msg = f"profile {self.name!r} field {key!r} is uncalibrated"
            raise ProfileError(msg)
        return f.value

    # --- completeness -----------------------------------------------------------------

    def missing(self, required: list[str]) -> list[str]:
        return [k for k in required if not self.has(k)]

    def set_value(
        self,
        key: str,
        value: Any,
        status: FieldStatus = "calibrated",
        confidence: float | None = None,
        note: str | None = None,
    ) -> None:
        f = self.field(key)
        f.value = value
        f.status = status
        f.confidence = confidence
        if note is not None:
            f.note = note

    # --- io ---------------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, path: Path) -> Profile:
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        prof = cls.model_validate(raw)
        prof.source_path = path
        return prof

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = self.model_dump(mode="json")
        with path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(doc, fh, sort_keys=False, allow_unicode=True)


def load_profile(name: str, paths: Paths | None = None) -> Profile:
    """``profiles/<name>.yaml`` if present, else the committed ``.example.yaml``."""
    paths = paths or Paths()
    for candidate in (paths.profiles / f"{name}.yaml", paths.profiles / f"{name}.example.yaml"):
        if candidate.exists():
            return Profile.from_yaml(candidate)
    msg = f"no profile named {name!r} under {paths.profiles}"
    raise ProfileError(msg)
