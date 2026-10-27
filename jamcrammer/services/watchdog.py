"""Popup Watchdog (KICKOFF §7.1). Runs on every capture tick and preempts the active mode.

Detection order per tick:
1. calibrated templates (still-playing / logged-out / Play Now / per-mode popups);
2. saturated-green button candidates in the center box, confirmed by OCR keywords;
3. OCR keywords alone (logged out, Play Now) on a slower cadence;
4. unknown modal: the center box differs strongly from the room baseline while the frame border
   does not (a room change would move everything).

Handling: still-playing → click the green button and confirm it is gone within the timeout
(retry once, then alert and pause). Logged out / Play Now → stop everything, HIGH alert,
screenshot. Unknown modal → screenshot, alert, pause clicking. Never guess-click.
"""

from __future__ import annotations

import collections
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
import numpy.typing as npt

from jamcrammer.core.config import Level, WatchdogConfig
from jamcrammer.core.events import EventBus
from jamcrammer.core.logging import get_logger
from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import Profile
from jamcrammer.core.safety import InputLease
from jamcrammer.platform.capture import Frame
from jamcrammer.vision.ocr import contains_any, ocr_image

log = get_logger("watchdog")

BgrImage = npt.NDArray[np.uint8]
Box = tuple[int, int, int, int]  # x, y, w, h (client pixels)

KIND_STILL_PLAYING = "still_playing"
KIND_LOGGED_OUT = "logged_out"
KIND_PLAY_NOW = "play_now"
KIND_UNKNOWN = "unknown_modal"
FATAL_KINDS = frozenset({KIND_LOGGED_OUT, KIND_PLAY_NOW})
CLICKABLE_KINDS = frozenset({KIND_STILL_PLAYING})
POPUP_OCR_UPSCALE = 1.5
MORPH_KERNEL = 5
BORDER_FRACTION = 0.08  # outer ring of the frame used as the "did the room change?" reference
SECONDS_PER_HOUR = 3600.0


@dataclass(frozen=True)
class Detection:
    kind: str
    confidence: float
    click_point: tuple[int, int] | None
    bbox: Box | None
    text: str = ""
    via: str = ""


class Clicker(Protocol):
    def click(self, cx: int, cy: int) -> bool: ...


TextFn = Callable[[BgrImage], str]


def _fraction_box_to_px(box: tuple[float, float, float, float], w: int, h: int) -> Box:
    x0, y0, x1, y1 = box
    px0, py0 = int(x0 * w), int(y0 * h)
    return (px0, py0, int(x1 * w) - px0, int(y1 * h) - py0)


def green_button_candidates(
    image: BgrImage,
    center: Box,
    *,
    hsv_lo: tuple[int, int, int],
    hsv_hi: tuple[int, int, int],
    min_area: float,
    max_area: float,
    min_aspect: float,
    max_aspect: float,
) -> list[Box]:
    """Button-shaped saturated-green blobs inside ``center``; boxes in full-frame pixels."""
    x, y, w, h = center
    sub = image[y : y + h, x : x + w]
    hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(hsv_lo, dtype=np.uint8), np.array(hsv_hi, dtype=np.uint8))
    kernel = np.ones((MORPH_KERNEL, MORPH_KERNEL), np.uint8)
    mask = np.asarray(cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel), dtype=np.uint8)
    n, _labels, stats, _cent = cv2.connectedComponentsWithStats(mask)
    out: list[tuple[int, Box]] = []
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        bw = int(stats[i, cv2.CC_STAT_WIDTH])
        bh = int(stats[i, cv2.CC_STAT_HEIGHT])
        if area < min_area or area > max_area or bh == 0:
            continue
        aspect = bw / bh
        if aspect < min_aspect or aspect > max_aspect:
            continue
        fill = area / float(bw * bh)
        if fill < 0.5:  # buttons are solid; foliage is ragged
            continue
        out.append(
            (
                area,
                (x + int(stats[i, cv2.CC_STAT_LEFT]), y + int(stats[i, cv2.CC_STAT_TOP]), bw, bh),
            )
        )
    out.sort(reverse=True)
    return [b for _, b in out]


def match_template(
    image: BgrImage, template: BgrImage, threshold: float
) -> tuple[float, Box | None]:
    if template.shape[0] > image.shape[0] or template.shape[1] > image.shape[1]:
        return (0.0, None)
    res = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    if max_val < threshold:
        return (float(max_val), None)
    th, tw = template.shape[:2]
    return (float(max_val), (int(max_loc[0]), int(max_loc[1]), tw, th))


class PopupDetector:
    """Stateless per-frame detection, driven by the ``shared`` profile."""

    def __init__(
        self,
        profile: Profile,
        text_fn: TextFn | None = None,
        paths: Paths | None = None,
        extra_templates: dict[str, Path] | None = None,
    ) -> None:
        self.profile = profile
        self._text_fn: TextFn = text_fn or (
            lambda img: ocr_image(img, upscale=POPUP_OCR_UPSCALE).text
        )
        self._paths = paths or Paths()
        self._templates: dict[str, BgrImage] = {}
        for kind, key in (
            (KIND_STILL_PLAYING, "still_playing_template"),
            (KIND_LOGGED_OUT, "logged_out_template"),
            (KIND_PLAY_NOW, "play_now_template"),
        ):
            if profile.has(key):
                self._load_template(kind, profile.template_path(key, self._paths))
        for kind, path in (extra_templates or {}).items():
            self._load_template(kind, path)
        self.last_text = ""

    def _load_template(self, kind: str, path: Path) -> None:
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            log.warning("template for %s missing at %s", kind, path)
            return
        self._templates[kind] = np.asarray(img, dtype=np.uint8)

    def center_box(self, image: BgrImage) -> Box:
        h, w = image.shape[:2]
        return _fraction_box_to_px(self.profile.fraction_box("popup_center_box"), w, h)

    def detect(
        self,
        image: BgrImage,
        baseline: BgrImage | None = None,
        run_ocr: bool = True,
    ) -> Detection | None:
        p = self.profile
        center = self.center_box(image)
        x, y, w, h = center
        sub = image[y : y + h, x : x + w]

        # 1. templates
        if self._templates:
            thr = p.number("template_match_threshold")
            for kind, tpl in self._templates.items():
                score, box = match_template(image, tpl, thr)
                if box is not None:
                    bx, by, bw, bh = box
                    return Detection(kind, score, (bx + bw // 2, by + bh // 2), box, via="template")

        # 2. green button + keywords
        lo, hi = p.color("popup_green_hsv")
        buttons = green_button_candidates(
            image,
            center,
            hsv_lo=lo,
            hsv_hi=hi,
            min_area=p.number("popup_green_min_area"),
            max_area=p.number("popup_green_max_area"),
            min_aspect=p.number("popup_green_min_aspect"),
            max_aspect=p.number("popup_green_max_aspect"),
        )
        text = ""
        if buttons or run_ocr:
            text = self._text_fn(sub)
            self.last_text = text
        if buttons and text:
            kw = contains_any(text, p.items("still_playing_keywords"))
            if kw:
                bx, by, bw, bh = buttons[0]
                return Detection(
                    KIND_STILL_PLAYING,
                    0.9,
                    (bx + bw // 2, by + bh // 2),
                    buttons[0],
                    text,
                    "green+ocr",
                )

        # 3. keywords alone
        if text:
            if contains_any(text, p.items("logged_out_keywords")):
                return Detection(KIND_LOGGED_OUT, 0.8, None, center, text, "ocr")
            if contains_any(text, p.items("play_now_keywords")):
                return Detection(KIND_PLAY_NOW, 0.7, None, center, text, "ocr")

        # 4. unknown modal vs baseline
        if baseline is not None and baseline.shape == image.shape:
            center_diff, border_diff = _center_vs_border(image, baseline, center)
            ratio_ok = center_diff >= p.number("modal_center_border_ratio") * max(border_diff, 1.0)
            if (
                center_diff >= p.number("modal_center_diff_min")
                and border_diff <= p.number("modal_border_diff_max")
                and ratio_ok
            ):
                conf = min(1.0, center_diff / (2 * p.number("modal_center_diff_min")))
                return Detection(KIND_UNKNOWN, conf, None, center, text, "baseline")
        return None


def _center_vs_border(image: BgrImage, baseline: BgrImage, center: Box) -> tuple[float, float]:
    x, y, w, h = center
    diff = cv2.absdiff(image, baseline)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    center_diff = float(gray[y : y + h, x : x + w].mean())
    fh, fw = gray.shape[:2]
    bw, bh = int(fw * BORDER_FRACTION), int(fh * BORDER_FRACTION)
    ring = np.concatenate(
        [gray[:bh, :].ravel(), gray[-bh:, :].ravel(), gray[:, :bw].ravel(), gray[:, -bw:].ravel()]
    )
    return center_diff, float(ring.mean())


@dataclass
class WatchdogStats:
    still_playing_seen: int = 0
    cleared: int = 0
    clear_failures: int = 0
    fatal: int = 0
    unknown: int = 0
    last_detection_ts: float = 0.0
    recent_popups: collections.deque[float] = field(
        default_factory=lambda: collections.deque(maxlen=200)
    )

    def popups_last_hour(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        return sum(1 for t in self.recent_popups if now - t <= SECONDS_PER_HOUR)


@dataclass
class _Pending:
    detection: Detection
    deadline: float
    retries: int = 0


class Watchdog:
    def __init__(
        self,
        cfg: WatchdogConfig,
        detector: PopupDetector,
        clicker: Clicker,
        *,
        bus: EventBus | None = None,
        lease: InputLease | None = None,
        on_fatal: Callable[[Detection], None] | None = None,
        on_pause: Callable[[str], None] | None = None,
        screenshot_dir: Path | None = None,
        unknown_dir: Path | None = None,
        antiafk_active: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.cfg = cfg
        self.detector = detector
        self._click = clicker
        self._bus = bus
        self._lease = lease
        self._on_fatal = on_fatal
        self._on_pause = on_pause
        self._shots = screenshot_dir
        self._unknown_dir = unknown_dir or screenshot_dir
        self._antiafk_active = antiafk_active or (lambda: False)
        self._clock = clock
        self.stats = WatchdogStats()
        self.baseline: BgrImage | None = None
        self._pending: _Pending | None = None
        self._last_ocr_ts = 0.0
        self._unknown_until = 0.0
        self._unknown_streak = 0
        self._baseline_ts = 0.0
        self.paused = False

    def set_baseline(self, image: BgrImage) -> None:
        self.baseline = image.copy()
        self._baseline_ts = self._clock()

    def resume(self) -> None:
        """Clear the paused state (the user or the GUI checked the screen)."""
        self.paused = False
        self._unknown_until = 0.0

    # --- main entry --------------------------------------------------------------------

    def tick(self, frame: Frame, known_screen: bool = False) -> Detection | None:
        """``known_screen``: the running mode recognises this screen (a game arena, a track),
        so the centre-vs-border "unknown modal" heuristic is skipped - a mini-game canvas
        looks exactly like a dialog to it. Templates and OCR keywords still run."""
        now = self._clock()
        image = frame.image
        run_ocr = now - self._last_ocr_ts >= self.cfg.ocr_interval_s or self._pending is not None
        if run_ocr:
            self._last_ocr_ts = now
        baseline = None if known_screen else self.baseline
        det = self.detector.detect(image, baseline, run_ocr=run_ocr)
        if known_screen:
            self._unknown_streak = 0

        if self._pending is not None:
            self._continue_pending(det, image, now)
            return det
        if det is None:
            self._unknown_streak = 0
            if now - self._baseline_ts >= self.cfg.baseline_refresh_s:
                self.set_baseline(image)  # rolling baseline while the screen is quiet
            return None
        if det.kind == KIND_UNKNOWN:
            self._unknown_streak += 1
            if self._unknown_streak < self.cfg.unknown_min_ticks:
                return det  # one odd frame is not a dialog; wait for persistence
        else:
            self._unknown_streak = 0
        self.stats.last_detection_ts = now
        if self.paused and det.kind not in FATAL_KINDS:
            return det  # paused after a failure or an unknown dialog: no more clicking
        if det.kind in CLICKABLE_KINDS or (
            det.click_point is not None and det.kind not in FATAL_KINDS and det.kind != KIND_UNKNOWN
        ):
            self._handle_clickable(det, image, now)
        elif det.kind in FATAL_KINDS:
            self._handle_fatal(det, image)
        elif det.kind == KIND_UNKNOWN:
            self._handle_unknown(det, image, now)
        return det

    # --- handlers ----------------------------------------------------------------------

    def _handle_clickable(self, det: Detection, image: BgrImage, now: float) -> None:
        self.stats.still_playing_seen += 1
        self.stats.recent_popups.append(now)
        self._emit(
            "watchdog.popup_detected",
            Level.NOTICE,
            kind=det.kind,
            via=det.via,
            text=det.text[:120],
            screenshot_path=self._save(image, det.kind),
        )
        if self._antiafk_active():
            self._emit(
                "watchdog.antiafk_not_registering",
                Level.HIGH,
                message=(
                    "An idle popup appeared while Anti-AFK was running: "
                    "its input is not counting as activity."
                ),
            )
        self._click_pending(det, now)

    def _click_pending(self, det: Detection, now: float) -> None:
        assert det.click_point is not None
        if self._lease is not None:
            self._lease.preempt("watchdog")
        ok = self._click.click(*det.click_point)
        self._emit("watchdog.click", kind=det.kind, point=list(det.click_point), ok=ok)
        retries = self._pending.retries if self._pending else 0
        self._pending = _Pending(det, now + self.cfg.clear_timeout_s, retries)

    def _continue_pending(self, det: Detection | None, image: BgrImage, now: float) -> None:
        assert self._pending is not None
        pend = self._pending
        if det is None or det.kind != pend.detection.kind:
            self._pending = None
            self.stats.cleared += 1
            if self._lease is not None:
                self._lease.release("watchdog")
            self._emit("watchdog.popup_cleared", kind=pend.detection.kind, retries=pend.retries)
            return
        if now < pend.deadline:
            return
        if pend.retries < self.cfg.max_retries:
            pend.retries += 1
            self._emit("watchdog.retry", Level.NOTICE, kind=det.kind, attempt=pend.retries)
            self._click_pending(det, now)
            return
        self._pending = None
        self.stats.clear_failures += 1
        if self._lease is not None:
            self._lease.release("watchdog")
        self._emit(
            "watchdog.popup_not_cleared",
            Level.HIGH,
            kind=det.kind,
            screenshot_path=self._save(image, f"{det.kind}_stuck"),
            message=f"The {det.kind} popup did not go away after clicking; pausing.",
        )
        self._pause("popup_not_cleared")

    def _handle_fatal(self, det: Detection, image: BgrImage) -> None:
        self.stats.fatal += 1
        shot = self._save(image, det.kind)
        self._emit(
            "watchdog.fatal",
            Level.HIGH,
            kind=det.kind,
            text=det.text[:120],
            screenshot_path=shot,
            message=f"{det.kind.replace('_', ' ')} detected; all modes stopped. No auto-login.",
        )
        if self._on_fatal is not None:
            self._on_fatal(det)

    def _handle_unknown(self, det: Detection, image: BgrImage, now: float) -> None:
        if now < self._unknown_until:
            return
        self._unknown_until = now + self.cfg.unknown_cooldown_s
        self.stats.unknown += 1
        shot = self._save(image, "unknown", directory=self._unknown_dir)
        self._emit(
            "watchdog.unknown_modal",
            Level.HIGH,
            confidence=round(det.confidence, 2),
            text=det.text[:120],
            screenshot_path=shot,
            message="Unknown dialog in the center of the screen; clicking paused. Please check.",
        )
        self._pause("unknown_modal")

    # --- plumbing ----------------------------------------------------------------------

    def _pause(self, reason: str) -> None:
        self.paused = True
        if self._on_pause is not None:
            self._on_pause(reason)

    def _save(self, image: BgrImage, tag: str, directory: Path | None = None) -> Path | None:
        d = directory or self._shots
        if d is None:
            return None
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{time.strftime('%Y%m%d_%H%M%S')}_{tag}.png"
        cv2.imwrite(str(path), image)
        return path

    def _emit(
        self,
        event_type: str,
        level: Level = Level.INFO,
        screenshot_path: Path | None = None,
        **p: object,
    ) -> None:
        if self._bus is not None:
            self._bus.emit(
                event_type, source="watchdog", level=level, screenshot_path=screenshot_path, **p
            )
