"""Party Watcher (KICKOFF 8.1 steps 1-7): open My Parties, segment and identify banners,
log sightings, close the panel. Pure vision in ``analyze``; input only in ``scan``/``join``.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import cv2
import imagehash
import numpy as np
import numpy.typing as npt
from rapidfuzz import fuzz

from jamcrammer.core.config import HuntConfig, Level
from jamcrammer.core.events import EventBus
from jamcrammer.core.logging import get_logger
from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import Profile
from jamcrammer.core.store import Store
from jamcrammer.vision.ocr import contains_any, normalize_text, ocr_image

log = get_logger("parties")

BgrImage = npt.NDArray[np.uint8]
Box = tuple[int, int, int, int]
GrabFn = Callable[[], BgrImage]


class Clicker(Protocol):
    def click(self, cx: int, cy: int) -> bool: ...


CANON_SIZE = (320, 100)
PHASH_SIZE = 8
GO_NOW_MARGIN = 8
RING_CLOSE_KERNEL = 3
RING_INSET = 3
MIN_ROW_WIDTH_FRACTION = 0.5
_SLUG = re.compile(r"[^a-z0-9]+")


def slug(label: str) -> str:
    return _SLUG.sub("_", label.lower()).strip("_")


@dataclass
class Banner:
    index: int
    box: Box
    is_slot: bool
    go_now_box: Box | None
    label: str | None = None
    banner_id: str | None = None
    method: str = ""
    score: float = 0.0
    ocr_text: str = ""
    crop: BgrImage | None = field(default=None, repr=False)

    @property
    def go_now_point(self) -> tuple[int, int] | None:
        if self.go_now_box is None:
            return None
        x, y, w, h = self.go_now_box
        return (x + w // 2, y + h // 2)


@dataclass
class ScanResult:
    ts: float
    opened: bool = False
    closed: bool = False
    panel_box: Box | None = None
    banners: list[Banner] = field(default_factory=list)
    target: Banner | None = None
    errors: list[str] = field(default_factory=list)
    unknown_paths: list[Path] = field(default_factory=list)

    @property
    def labels(self) -> list[str]:
        return [b.label or "?" for b in self.banners]


# --- vision helpers --------------------------------------------------------------------------


def multiscale_match(
    image: BgrImage, template: BgrImage, scales: list[float], threshold: float
) -> tuple[float, Box | None, float]:
    """Best TM_CCOEFF_NORMED match over scales: (score, box, scale)."""
    best: tuple[float, Box | None, float] = (0.0, None, 1.0)
    for s in scales:
        tpl = (
            template
            if s == 1.0
            else cv2.resize(template, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        )
        th, tw = tpl.shape[:2]
        if th >= image.shape[0] or tw >= image.shape[1]:
            continue
        res = cv2.matchTemplate(image, tpl, cv2.TM_CCOEFF_NORMED)
        _, mv, _, ml = cv2.minMaxLoc(res)
        if mv > best[0]:
            best = (float(mv), (int(ml[0]), int(ml[1]), tw, th), float(s))
    if best[0] < threshold:
        return (best[0], None, best[2])
    return best


def dark_bands(
    gray_strip: npt.NDArray[np.uint8], dark_max: float, fraction: float
) -> list[tuple[int, int]]:
    """Rows of a grayscale strip where ≥ ``fraction`` of pixels are darker than ``dark_max``."""
    dark = (gray_strip < dark_max).mean(axis=1)
    bands: list[list[int]] = []
    for y in np.flatnonzero(dark > fraction):
        yi = int(y)
        if bands and yi - bands[-1][1] <= 2:
            bands[-1][1] = yi
        else:
            bands.append([yi, yi])
    return [(a, b) for a, b in bands]


def _overlaps(a: Box, b: Box) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def _contains(outer: Box, inner: Box, tolerance: int = 4) -> bool:
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    return (
        ox - tolerance <= ix
        and oy - tolerance <= iy
        and ix + iw <= ox + ow + tolerance
        and iy + ih <= oy + oh + tolerance
        and (iw * ih) < (ow * oh)
    )


def canonical(crop: BgrImage) -> BgrImage:
    return np.asarray(cv2.resize(crop, CANON_SIZE, interpolation=cv2.INTER_AREA), dtype=np.uint8)


def phash(crop: BgrImage) -> imagehash.ImageHash:
    from PIL import Image

    gray = cv2.cvtColor(canonical(crop), cv2.COLOR_BGR2GRAY)
    return imagehash.phash(Image.fromarray(gray), hash_size=PHASH_SIZE)


class BannerLibrary:
    """``assets/banners/<id>.png`` → perceptual hashes."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.hashes: dict[str, imagehash.ImageHash] = {}
        self.reload()

    def reload(self) -> None:
        self.hashes.clear()
        if not self.directory.exists():
            return
        for path in sorted(self.directory.glob("*.png")):
            img = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if img is not None:
                self.hashes[path.stem] = phash(np.asarray(img, dtype=np.uint8))

    def match(self, crop: BgrImage) -> tuple[str | None, int, int]:
        """(best id, its distance, runner-up distance). 64 = no library."""
        if not self.hashes:
            return (None, 64, 64)
        h = phash(crop)
        ranked = sorted((int(h - bh), bid) for bid, bh in self.hashes.items())
        second = ranked[1][0] if len(ranked) > 1 else 64
        return (ranked[0][1], ranked[0][0], second)

    def add(self, banner_id: str, crop: BgrImage) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{banner_id}.png"
        cv2.imwrite(str(path), crop)
        self.hashes[banner_id] = phash(crop)
        return path


# --- the analyzer -----------------------------------------------------------------------------


class PanelAnalyzer:
    """Locate the panel and identify its banners on a single frame (no input)."""

    def __init__(
        self,
        profile: Profile,
        library: BannerLibrary,
        paths: Paths | None = None,
        ocr: Callable[[BgrImage], str] | None = None,
    ) -> None:
        self.profile = profile
        self.library = library
        self._paths = paths or Paths()
        self._ocr = ocr or self._default_ocr
        self._title = self._load("title_template")
        self._go_now = self._load("go_now_template")
        self._close_x = self._load("close_x_template")

    def _load(self, key: str) -> BgrImage:
        path = self.profile.template_path(key, self._paths)
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            msg = f"template {key} missing at {path}"
            raise FileNotFoundError(msg)
        return np.asarray(img, dtype=np.uint8)

    def _default_ocr(self, crop: BgrImage) -> str:
        return ocr_image(crop, upscale=self.profile.number("ocr_upscale")).text

    @property
    def scales(self) -> list[float]:
        return [float(s) for s in self.profile.items("template_scales")]

    def find_title(self, image: BgrImage) -> tuple[Box | None, float]:
        thr = self.profile.number("template_threshold")
        rx, ry, rw, rh = self.profile.roi("title_search_roi")
        h, w = image.shape[:2]
        sub = image[max(0, ry) : min(h, ry + rh), max(0, rx) : min(w, rx + rw)]
        _score, box, scale = (0.0, None, 1.0)
        if sub.size:
            _score, box, scale = multiscale_match(sub, self._title, self.scales, thr)
            if box is not None:
                box = (box[0] + max(0, rx), box[1] + max(0, ry), box[2], box[3])
        if box is None:
            _score, box, scale = multiscale_match(image, self._title, self.scales, thr)
        return (box, scale)

    def is_open(self, image: BgrImage) -> Box | None:
        return self.find_title(image)[0]

    def close_x_point(self, image: BgrImage, title: Box | None = None) -> tuple[int, int] | None:
        thr = self.profile.number("template_threshold")
        _, box, _ = multiscale_match(image, self._close_x, self.scales, thr)
        if box is not None:
            return (box[0] + box[2] // 2, box[1] + box[3] // 2)
        if self.profile.has("close_x_point"):
            return self.profile.point("close_x_point")
        return None

    def banner_region(self, image: BgrImage, title: Box) -> Box:
        tx, ty, tw, th = title
        c0, c1 = self.profile.items("panel_column_rel")
        r0, r1 = self.profile.items("panel_rows_rel")
        h, w = image.shape[:2]
        x0 = max(0, int(tx + float(c0) * tw))
        x1 = min(w, int(tx + float(c1) * tw))
        y0 = max(0, int(ty + float(r0) * th))
        y1 = min(h, int(ty + float(r1) * th))
        return (x0, y0, x1 - x0, y1 - y0)

    def segment_rows(self, image: BgrImage, title: Box, dark_max: float | None = None) -> list[Box]:
        """Banner boxes inside the panel: each banner has a dark border ring, so the connected
        components of the dark mask whose bbox spans most of the column are the banners. Dark
        artwork touching a ring merges into that ring's component without changing its bbox."""
        rx, ry, rw, rh = self.banner_region(image, title)
        region = image[ry : ry + rh, rx : rx + rw]
        gray = np.asarray(cv2.cvtColor(region, cv2.COLOR_BGR2GRAY), dtype=np.uint8)
        if dark_max is None:
            dark_max = self.profile.number("border_dark_max")
        mask = np.asarray((gray < dark_max).astype(np.uint8) * 255, dtype=np.uint8)
        kernel = np.ones((RING_CLOSE_KERNEL, RING_CLOSE_KERNEL), np.uint8)
        mask = np.asarray(cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel), dtype=np.uint8)
        n, _labels, stats, _cent = cv2.connectedComponentsWithStats(mask)
        hmin, hmax = (float(v) * title[3] for v in self.profile.items("banner_height_rel"))
        boxes: list[Box] = []
        for i in range(1, n):
            x, y, w, h = (
                int(stats[i, k])
                for k in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP, cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT)
            )
            if w >= rw * MIN_ROW_WIDTH_FRACTION and hmin <= h <= hmax:
                boxes.append((rx + x, ry + y, w, h))
        boxes.sort(key=lambda b: b[1])
        rows: list[Box] = []
        for b in boxes:
            # drop a box that contains an already-kept smaller box (slot frame around a banner)
            if any(_contains(b, kept) for kept in rows):
                continue
            rows = [kept for kept in rows if not _contains(kept, b)]
            rows.append(b)
        rows.sort(key=lambda b: b[1])
        # shrink inside the border ring so the art crop excludes the dark edge
        return [
            (x + RING_INSET, y + RING_INSET, max(1, w - 2 * RING_INSET), max(1, h - 2 * RING_INSET))
            for x, y, w, h in rows
        ]

    def find_go_now_buttons(self, image: BgrImage, title: Box) -> list[Box]:
        """Every GO NOW button inside the banner region (supports N party slots)."""
        rx, ry, rw, rh = self.banner_region(image, title)
        sub = image[ry : ry + rh, rx : rx + rw]
        thr = self.profile.number("go_now_threshold")
        found: list[tuple[float, Box]] = []
        for scale in self.scales:
            tpl = (
                self._go_now
                if scale == 1.0
                else cv2.resize(
                    self._go_now, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA
                )
            )
            th, tw = tpl.shape[:2]
            if th >= sub.shape[0] or tw >= sub.shape[1]:
                continue
            res = cv2.matchTemplate(sub, tpl, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(res >= thr)
            for y, x in zip(ys.tolist(), xs.tolist(), strict=True):
                found.append((float(res[y, x]), (rx + int(x), ry + int(y), tw, th)))
        found.sort(key=lambda t: t[0], reverse=True)
        kept: list[Box] = []
        for _score, box in found:
            if all(not _overlaps(box, k) for k in kept):
                kept.append(box)
        return kept

    def find_go_now(self, image: BgrImage, row: Box) -> Box | None:
        """GO NOW inside one row (kept for callers that already have a row)."""
        x, y, w, h = row
        sub = image[y : y + h, x : x + w]
        _, box, _ = multiscale_match(
            sub, self._go_now, self.scales, self.profile.number("go_now_threshold")
        )
        if box is None:
            return None
        return (x + box[0], y + box[1], box[2], box[3])

    def identify(self, banner: Banner) -> None:
        assert banner.crop is not None
        bid, dist, second = self.library.match(banner.crop)
        if (
            bid is not None
            and dist <= self.profile.number("phash_max_distance")
            and second - dist >= self.profile.number("phash_min_margin")
        ):
            banner.banner_id, banner.method, banner.score = bid, "phash", float(dist)
            banner.label = self._label_for(bid)
        text = self._ocr(banner.crop)
        banner.ocr_text = text
        if banner.label is None and text:
            label, score = self.fuzzy_label(text)
            if label is not None:
                banner.label, banner.banner_id, banner.method, banner.score = (
                    label,
                    slug(label),
                    "ocr",
                    score,
                )

    def _label_for(self, banner_id: str) -> str:
        for label in self.profile.items("known_labels"):
            if slug(str(label)) == banner_id:
                return str(label)
        return banner_id.replace("_", " ").title()

    def fuzzy_label(self, text: str) -> tuple[str | None, float]:
        norm = normalize_text(text)
        thr = self.profile.number("label_fuzzy_threshold")
        best: tuple[str | None, float] = (None, 0.0)
        for label in self.profile.items("known_labels"):
            s = float(fuzz.partial_ratio(normalize_text(str(label)), norm))
            if s > best[1]:
                best = (str(label), s)
        return best if best[1] >= thr else (None, best[1])

    def is_target(self, banner: Banner) -> bool:
        kws = self.profile.items("target_keywords")
        if banner.banner_id and any(slug(str(k)) in banner.banner_id for k in kws):
            return True
        if banner.label and contains_any(banner.label, kws):
            return True
        norm = normalize_text(banner.ocr_text)
        thr = self.profile.number("label_fuzzy_threshold")
        return (
            any(fuzz.partial_ratio(normalize_text(str(k)), norm) >= thr for k in kws)
            if norm
            else False
        )

    def analyze(self, image: BgrImage) -> tuple[Box | None, list[Banner]]:
        title, _ = self.find_title(image)
        if title is None:
            return (None, [])
        pinned = self.segment_rows(image, title)
        light = self.segment_rows(image, title, self.profile.number("slot_border_dark_max"))
        buttons = self.find_go_now_buttons(image, title)
        region = self.banner_region(image, title)
        typical_h = int(np.median([r[3] for r in pinned])) if pinned else int(title[3] * 2.3)
        slot_rows: list[tuple[Box, Box]] = []
        for button in buttons:
            gx, gy, gw, gh = button
            gcy = gy + gh // 2
            row = next((r for r in light if r[1] <= gcy <= r[1] + r[3]), None)
            if row is None:
                row = next((r for r in pinned if r[1] <= gcy <= r[1] + r[3]), None)
            if row is None:
                rx, _, rw, _ = region
                y1 = gy + gh + GO_NOW_MARGIN
                row = (rx + RING_INSET, y1 - typical_h, rw - 2 * RING_INSET, typical_h)
            x, y, w, h = row
            x1 = max(x + w, gx + gw)
            slot_rows.append(((x, y, x1 - x, h), button))
        entries: list[tuple[Box, Box | None]] = [(r, None) for r in pinned]
        for row, button in slot_rows:
            entries = [(r, b) for r, b in entries if not _overlaps(r, row)]
            entries.append((row, button))
        entries.sort(key=lambda e: e[0][1])
        banners: list[Banner] = []
        for index, (row_box, go_box) in enumerate(entries):
            x, y, w, h = row_box
            art_w = (go_box[0] - GO_NOW_MARGIN - x) if go_box is not None else w
            crop = np.ascontiguousarray(image[y : y + h, x : x + max(1, art_w)])
            b = Banner(
                index=index, box=row_box, is_slot=go_box is not None, go_now_box=go_box, crop=crop
            )
            self.identify(b)
            banners.append(b)
        return (title, banners)


# --- the watcher (input + store) ---------------------------------------------------------------


@dataclass
class JoinResult:
    clicked: bool
    arrived: bool
    waits: int = 0
    error: str = ""


class PartyWatcher:
    def __init__(
        self,
        analyzer: PanelAnalyzer,
        cfg: HuntConfig,
        inp: Clicker,
        grab: GrabFn,
        *,
        store: Store | None = None,
        bus: EventBus | None = None,
        unknown_dir: Path | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.analyzer = analyzer
        self.cfg = cfg
        self._input = inp
        self._grab = grab
        self._store = store
        self._bus = bus
        self._unknown_dir = unknown_dir
        self._clock = clock
        self._sleep = sleep
        self.scans = 0

    # --- panel control ---------------------------------------------------------------------

    def _wait_for(self, predicate: Callable[[BgrImage], bool], timeout_s: float) -> BgrImage | None:
        deadline = self._clock() + timeout_s
        while True:
            image = self._grab()
            if predicate(image):
                return image
            if self._clock() >= deadline:
                return None
            self._sleep(self.cfg.panel_poll_s)

    def ensure_closed(self) -> bool:
        image = self._grab()
        title = self.analyzer.is_open(image)
        if title is None:
            return True
        point = self.analyzer.close_x_point(image, title)
        if point is None:
            return False
        for _ in range(2):
            self._input.click(*point)
            if (
                self._wait_for(
                    lambda im: self.analyzer.is_open(im) is None, self.cfg.close_timeout_s
                )
                is not None
            ):
                return True
        self._emit("parties.close_failed", Level.NOTICE)
        return False

    def open_panel(self) -> BgrImage | None:
        hat = self.analyzer.profile.point("party_hat_point")
        for attempt in range(2):
            self._input.click(*hat)
            image = self._wait_for(
                lambda im: self.analyzer.is_open(im) is not None, self.cfg.panel_timeout_s
            )
            if image is not None:
                return image
            self._emit("parties.open_retry", Level.NOTICE, attempt=attempt + 1)
        return None

    # --- scanning ----------------------------------------------------------------------------

    def scan(self) -> ScanResult:
        result = ScanResult(ts=self._clock())
        self.scans += 1
        if not self.ensure_closed():
            result.errors.append("panel would not close before scan")
            return result
        image = self.open_panel()
        if image is None:
            result.errors.append("panel did not open")
            self._emit("parties.open_failed", Level.NOTICE)
            return result
        result.opened = True
        self._sleep(self.cfg.panel_settle_s)
        image = self._grab()
        result.panel_box, result.banners = self.analyzer.analyze(image)
        self.record(result)
        result.closed = self.ensure_closed()
        if not result.closed:
            result.errors.append("panel did not close after scan")
        return result

    def record(self, result: ScanResult) -> None:
        for b in result.banners:
            if self.analyzer.is_target(b):
                result.target = b
            if b.label is None:
                path = self._save_unknown(b, result.ts)
                if path is not None:
                    result.unknown_paths.append(path)
                self._emit(
                    "parties.unknown_banner",
                    Level.NOTICE,
                    index=b.index,
                    slot=b.is_slot,
                    ocr=b.ocr_text[:80],
                    screenshot_path=path,
                )
                continue
            if self._store is not None:
                self._store.record_sighting(
                    b.banner_id or slug(b.label),
                    b.label,
                    b.index,
                    result.ts,
                    self.cfg.sighting_merge_gap_s,
                )
        self._emit(
            "parties.scan",
            labels=result.labels,
            slots=[b.index for b in result.banners if b.is_slot],
            target=result.target.label if result.target else None,
        )

    def _save_unknown(self, b: Banner, ts: float) -> Path | None:
        if self._unknown_dir is None or b.crop is None:
            return None
        self._unknown_dir.mkdir(parents=True, exist_ok=True)
        tag = slug(b.ocr_text)[:30] or "banner"
        path = (
            self._unknown_dir
            / f"{time.strftime('%Y%m%d_%H%M%S', time.localtime(ts))}_slot{b.index}_{tag}.png"
        )
        cv2.imwrite(str(path), b.crop)
        return path

    # --- joining -----------------------------------------------------------------------------

    def join(self, banner: Banner, room_changed: Callable[[BgrImage], bool]) -> JoinResult:
        """Click GO NOW (panel must be open), handle the 'wait' popup, confirm arrival."""
        point = banner.go_now_point
        if point is None:
            return JoinResult(False, False, error="banner has no GO NOW button")
        waits = 0
        for attempt in range(self.cfg.join_max_retries + 1):
            self._input.click(*point)
            self._emit("parties.join_click", attempt=attempt, point=list(point))
            arrived = self._wait_for(
                lambda im: self.analyzer.is_open(im) is None and room_changed(im),
                self.cfg.arrival_timeout_s,
            )
            if arrived is not None:
                return JoinResult(True, True, waits)
            image = self._grab()
            text = ocr_image(image, upscale=1.0).text
            if contains_any(text, self.analyzer.profile.items("wait_popup_keywords")):
                waits += 1
                self._emit("parties.join_wait", Level.NOTICE, attempt=attempt, text=text[:80])
                self._sleep(self.cfg.join_retry_s)
                if self.analyzer.is_open(self._grab()) is None and not self.open_panel():
                    return JoinResult(True, False, waits, "panel lost while waiting")
                continue
            if self.analyzer.is_open(image) is None:
                return JoinResult(True, False, waits, "panel closed but room unchanged")
        return JoinResult(True, False, waits, "gave up after retries")

    def _emit(
        self,
        event_type: str,
        level: Level = Level.INFO,
        screenshot_path: Path | None = None,
        **p: Any,
    ) -> None:
        if self._bus is not None:
            self._bus.emit(
                event_type, source="parties", level=level, screenshot_path=screenshot_path, **p
            )


def rotation_report(store: Store, since_ts: float | None = None) -> list[dict[str, Any]]:
    """Sightings ordered by first_seen with durations; the raw material for the rotation log."""
    rows = store.query(
        "SELECT banner_id, label, slot_index, first_seen, last_seen FROM party_sightings"
        " WHERE first_seen >= ? ORDER BY first_seen",
        (since_ts or 0.0,),
    )
    out = []
    for r in rows:
        out.append(
            {
                "banner_id": r["banner_id"],
                "label": r["label"],
                "slot": r["slot_index"],
                "first_seen": r["first_seen"],
                "last_seen": r["last_seen"],
                "duration_min": round((r["last_seen"] - r["first_seen"]) / 60.0, 1),
            }
        )
    return out
