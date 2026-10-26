"""OCR via Windows OCR (winocr) with preprocessing, plus text normalization helpers."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt

BgrImage = npt.NDArray[np.uint8]

DEFAULT_LANG = "en"
# Common OCR confusions in stylized game fonts, applied to lowercase text.
CONFUSIONS: tuple[tuple[str, str], ...] = (
    ("0", "o"),  # digits inside words are almost always letters in facts
    ("1", "l"),
    ("|", "l"),
    ("5", "s"),
)
DIGIT_CONFUSIONS: dict[str, str] = {
    "o": "0",
    "O": "0",
    "l": "1",
    "I": "1",
    "|": "1",
    "S": "5",
    "B": "8",
}
_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")
_NUM_TOKEN = re.compile(r"[0-9OoIlS|,]+")


@dataclass(frozen=True)
class OcrWord:
    text: str
    box: tuple[int, int, int, int]  # x, y, w, h relative to the image given to OCR


@dataclass(frozen=True)
class OcrLine:
    text: str
    words: tuple[OcrWord, ...]

    @property
    def box(self) -> tuple[int, int, int, int]:
        if not self.words:
            return (0, 0, 0, 0)
        x0 = min(w.box[0] for w in self.words)
        y0 = min(w.box[1] for w in self.words)
        x1 = max(w.box[0] + w.box[2] for w in self.words)
        y1 = max(w.box[1] + w.box[3] for w in self.words)
        return (x0, y0, x1 - x0, y1 - y0)


@dataclass(frozen=True)
class OcrResult:
    text: str
    lines: tuple[OcrLine, ...] = ()
    ms: float = 0.0
    error: str = ""
    scale: float = 1.0  # upscale factor applied before OCR (boxes are in scaled pixels)

    def boxes_in_source(self) -> list[tuple[str, tuple[int, int, int, int]]]:
        """Line text with boxes mapped back to the un-scaled source image."""
        out = []
        for line in self.lines:
            x, y, w, h = line.box
            s = self.scale
            out.append((line.text, (int(x / s), int(y / s), int(w / s), int(h / s))))
        return out

    @property
    def lowered(self) -> str:
        return self.text.lower()


def preprocess(
    image_bgr: BgrImage, upscale: float = 1.0, threshold: int | None = None, invert: bool = False
) -> BgrImage:
    """Upscale (cubic), optional binarize/invert. Returns a 3-channel image for winocr."""
    img = image_bgr
    if upscale != 1.0:
        img = np.asarray(
            cv2.resize(img, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC),
            dtype=np.uint8,
        )
    if threshold is not None or invert:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if threshold is not None:
            _, gray = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
        if invert:
            gray = cv2.bitwise_not(gray)
        img = np.asarray(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), dtype=np.uint8)
    return np.ascontiguousarray(img)


def _parse_winocr(raw: dict[str, Any]) -> tuple[str, tuple[OcrLine, ...]]:
    lines: list[OcrLine] = []
    for line in raw.get("lines", []):
        words = tuple(
            OcrWord(
                text=str(w.get("text", "")),
                box=(
                    int(w.get("bounding_rect", {}).get("x", 0)),
                    int(w.get("bounding_rect", {}).get("y", 0)),
                    int(w.get("bounding_rect", {}).get("width", 0)),
                    int(w.get("bounding_rect", {}).get("height", 0)),
                ),
            )
            for w in line.get("words", [])
        )
        lines.append(OcrLine(text=str(line.get("text", "")), words=words))
    return str(raw.get("text", "")), tuple(lines)


def ocr_image(
    image_bgr: BgrImage,
    lang: str = DEFAULT_LANG,
    upscale: float = 1.0,
    threshold: int | None = None,
    invert: bool = False,
) -> OcrResult:
    """Run Windows OCR. Never raises; errors land in ``OcrResult.error``."""
    t0 = time.perf_counter()
    try:
        import winocr

        prepared = preprocess(image_bgr, upscale=upscale, threshold=threshold, invert=invert)
        raw = winocr.recognize_cv2_sync(prepared, lang)
        text, lines = _parse_winocr(raw)
        return OcrResult(
            text=text, lines=lines, ms=(time.perf_counter() - t0) * 1000.0, scale=upscale
        )
    except Exception as exc:
        return OcrResult(
            text="",
            ms=(time.perf_counter() - t0) * 1000.0,
            error=f"{type(exc).__name__}: {exc}",
            scale=upscale,
        )


def crop(image_bgr: BgrImage, roi: tuple[int, int, int, int]) -> BgrImage:
    x, y, w, h = roi
    h_img, w_img = image_bgr.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(w_img, x + w), min(h_img, y + h)
    return np.ascontiguousarray(image_bgr[y0:y1, x0:x1])


def normalize_text(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace, fix common OCR confusions."""
    s = text.lower()
    for wrong, right in CONFUSIONS:
        # only replace inside alphabetic context: "l0ve" -> "love", keep standalone numbers
        s = re.sub(rf"(?<=[a-z]){re.escape(wrong)}(?=[a-z])", right, s)
    s = _PUNCT.sub(" ", s)
    return _WS.sub(" ", s).strip()


def parse_int(text: str) -> int | None:
    """First integer in ``text`` ('Gems: 1,234' -> 1234), tolerating O/l/I/S confusions."""
    for m in _NUM_TOKEN.finditer(text):
        token = m.group(0)
        if not any(ch.isdigit() for ch in token):
            continue  # a word like "no" or "Is", not a number with confusions
        fixed = "".join(DIGIT_CONFUSIONS.get(ch, ch) for ch in token).replace(",", "")
        if fixed.isdigit():
            return int(fixed)
    return None


def contains_any(text: str, keywords: list[Any]) -> str | None:
    """Return the first keyword present in ``text`` (case-insensitive), else None."""
    low = text.lower()
    for kw in keywords:
        if str(kw).lower() in low:
            return str(kw)
    return None
