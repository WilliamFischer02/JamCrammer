"""Jamaa Derby Learn sessions: William's own jumps measured against the bands.

The farmer's detector runs on every keyframe of a session (screen, bands, horse top). Bands
scroll left at one speed, so they are tracked from frame to frame and each band's edges are
placed in time between sightings; that puts the edge at the exact instant of every Space press
(the key log is wall-clock, like the frames). Per press: the band it was for, its kind and
width class, where its edge was relative to the hooves (``jump_trigger_x``; positive = still
ahead), the clear ground behind the previous band and ahead of the next one, the scroll speed,
and whether the scroll stalled while the band passed (a stumble). Every band seen is counted
per kind and width class, and a few crops of each go to ``analysis/derby_bands/`` so the
classification can be checked by eye.

    .venv\\Scripts\\python -m jamcrammer.learn.analyze.derby_timing <session_dir> [...]
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from jamcrammer.core.config import DerbyConfig
from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import Profile, load_profile
from jamcrammer.learn.recorder import read_meta
from jamcrammer.modes.arcade.derby import Band, DerbyDetector, Screen
from jamcrammer.modes.arcade.derby_learn import width_class
from jamcrammer.vision.stage import StageMap

EDGE_MARGIN_PX = 6  # a band edge this close to the strip's side is clipped, not real
MATCH_SLACK_PX = 90  # tracking: a band may sit this far from where the scroll speed puts it
STALL_TO_BOOST = 1.5  # tracking accepts a move up to this times the current speed (boosts)
MIN_FIT_POINTS = 2  # sightings of an unclipped edge needed to place it in time
AIRBORNE_RISE_PX = 25  # horse top this far above its ground row = in the air
AIRTIME_MAX_S = 2.0  # a longer "airtime" is two jumps or a misread
GROUND_PERCENTILE = 75  # the horse's ground row: it spends most frames on the ground
TRACK_REACH_S = 1.5  # a press may fall this far outside a band's sightings
CROPS_PER_CLASS = 6
CROP_PAD_PX = 30  # grass kept either side of a band crop
JUMP_KEYS = {"space"}


@dataclass
class Sighting:
    ts: float
    left: int
    right: int
    left_clipped: bool
    right_clipped: bool
    frame: str


@dataclass
class Track:
    tid: int
    kind: str
    sightings: list[Sighting] = field(default_factory=list)

    @property
    def last(self) -> Sighting:
        return self.sightings[-1]

    def width(self) -> int | None:
        full = [s.right - s.left for s in self.sightings if not (s.left_clipped or s.right_clipped)]
        return int(statistics.median(full)) if full else None

    def _points(self, edge: str) -> list[tuple[float, int]]:
        return [
            (s.ts, s.left if edge == "left" else s.right)
            for s in self.sightings
            if not (s.left_clipped if edge == "left" else s.right_clipped)
        ]

    def _at(self, ts: float, edge: str) -> float | None:
        """Piecewise-linear between sightings (a stumble makes the motion uneven), linear
        extrapolation from the two nearest sightings outside them."""
        pts = self._points(edge)
        if len(pts) < MIN_FIT_POINTS:
            return None
        t = [q[0] for q in pts]
        x = [float(q[1]) for q in pts]
        if t[0] <= ts <= t[-1]:
            return float(np.interp(ts, t, x))
        (ta, xa), (tb, xb) = (pts[0], pts[1]) if ts < t[0] else (pts[-2], pts[-1])
        return xb + (xb - xa) / (tb - ta) * (ts - tb) if tb > ta else float(xb)

    def edge_at(self, ts: float, edge: str = "left") -> float | None:
        x = self._at(ts, edge)
        if x is not None:
            return x
        other = self._at(ts, "right" if edge == "left" else "left")
        w = self.width()
        if other is None or w is None:
            return None
        return other - w if edge == "left" else other + w

    def speed(self) -> float | None:
        """Median of the per-frame speeds while the band moved (stalls excluded)."""
        pts = self._points("left") or self._points("right")
        vs = [(a[1] - b[1]) / (b[0] - a[0]) for a, b in pairwise(pts) if b[0] > a[0]]
        vs = [v for v in vs if v > 0]
        return float(statistics.median(vs)) if vs else None


@dataclass
class Press:
    ts: float
    race: int
    kind: str | None = None
    wclass: str | None = None
    width: int | None = None
    edge_rel: float | None = None  # band's left edge minus the hooves at the press; + = ahead
    gap_prev: float | None = None  # clear ground between the previous band and this one
    gap_next: float | None = None  # clear ground between this band and the next
    next_kind: str | None = None
    next_wclass: str | None = None
    speed: int | None = None
    since_prev_press_s: float | None = None
    airtime_s: float | None = None
    stumbled: bool = False
    band_id: int | None = None


@dataclass
class Scan:
    """Everything read from a session's keyframes."""

    tracks: list[Track] = field(default_factory=list)
    screens: list[tuple[float, str]] = field(default_factory=list)
    horse: list[tuple[float, int | None]] = field(default_factory=list)
    speeds: list[tuple[float, float]] = field(default_factory=list)  # scroll px/s between frames
    races: list[tuple[float, int]] = field(default_factory=list)  # (ts, race number)

    def race_at(self, ts: float) -> int:
        n = 0
        for t, rc in self.races:
            if t > ts:
                break
            n = rc
        return n


def _stage(image: np.ndarray) -> np.ndarray:
    h, w = image.shape[:2]
    if (w, h) == (1920, 1080):
        return image
    return StageMap(w, h).normalize(image)


def _ts(r: dict[str, Any]) -> float:
    return float(r["ts"])


def _match(band: Band, tracks: list[Track], shift: float, strip_x0: int) -> Track | None:
    """The track this band continues: same kind, moved left by anything from a full stall (a
    stumble) to a little more than the scroll speed predicts (``shift`` px)."""
    best: tuple[float, Track] | None = None
    for tr in tracks:
        if tr.kind != band.kind:
            continue
        s = tr.last
        if not (band.left <= strip_x0 + EDGE_MARGIN_PX or s.left_clipped):
            moved = s.left - band.left
        else:
            moved = s.right - band.right
        if not (-MATCH_SLACK_PX / 3 <= moved <= shift * STALL_TO_BOOST + MATCH_SLACK_PX):
            continue
        err = min(abs(moved - shift), abs(moved))
        if best is None or err < best[0]:
            best = (err, tr)
    return best[1] if best else None


class _Tracker:
    def __init__(self, prof: Profile, cfg: DerbyConfig) -> None:
        sx, _sy, sw, _sh = prof.roi("strip_roi")
        self.x0, self.x1 = sx, sx + sw
        self.nominal = prof.number("scroll_px_s")
        self.cfg = cfg
        self.speed = self.nominal
        self.active: list[Track] = []
        self.prev_ts: float | None = None

    def reset(self) -> None:
        self.active, self.prev_ts = [], None

    def feed(self, scan: Scan, ts: float, bands: Iterable[Band], frame: str) -> None:
        dt = ts - self.prev_ts if self.prev_ts is not None else 0.0
        moved: list[float] = []
        nxt: list[Track] = []
        for b in bands:
            sig = Sighting(
                ts,
                b.left,
                b.right,
                b.left <= self.x0 + EDGE_MARGIN_PX,
                b.right >= self.x1 - EDGE_MARGIN_PX,
                frame,
            )
            tr = _match(b, self.active, self.speed * dt, self.x0) if dt > 0 else None
            if tr is None or tr in nxt:
                tr = Track(len(scan.tracks), b.kind)
                scan.tracks.append(tr)
            elif not (sig.left_clipped or tr.last.left_clipped):
                moved.append((tr.last.left - sig.left) / dt)
            elif not (sig.right_clipped or tr.last.right_clipped):
                moved.append((tr.last.right - sig.right) / dt)
            tr.sightings.append(sig)
            nxt.append(tr)
        if moved:
            v = float(statistics.median(moved))
            scan.speeds.append((ts, v))
            if v > self.nominal * self.cfg.hit_speed_ratio:
                self.speed = v
        self.active, self.prev_ts = nxt, ts


def scan_session(session: Path, prof: Profile, cfg: DerbyConfig) -> Scan:
    det = DerbyDetector(prof, cfg)
    tracker = _Tracker(prof, cfg)
    frames = sorted((r for r in read_meta(session) if r.get("t") == "keyframe"), key=_ts)
    scan = Scan()
    race, prev = 0, None
    for r in frames:
        img = cv2.imread(str(session / r["file"]))
        if img is None:
            continue
        obs = det.observe(_stage(img))
        ts = float(r["ts"])
        scan.screens.append((ts, str(obs.screen)))
        if obs.screen == Screen.RACING and prev != Screen.RACING:
            race += 1
            tracker.reset()
        prev = obs.screen
        scan.races.append((ts, race))
        if obs.screen == Screen.RACING:
            scan.horse.append((ts, obs.horse_top))
            tracker.feed(scan, ts, obs.bands, r["file"])
    return scan


def stalled_bands(scan: Scan, front: int, nominal: float, cfg: DerbyConfig) -> set[int]:
    """Bands at the hooves while the scroll stalled (speed well under nominal): stumbles."""
    out: set[int] = set()
    for ts, v in scan.speeds:
        if v >= cfg.hit_speed_ratio * nominal:
            continue
        for tr in scan.tracks:
            left, right = tr.edge_at(ts, "left"), tr.edge_at(ts, "right")
            if left is not None and right is not None and left <= front < right + cfg.band_pass_px:
                out.add(tr.tid)
    return out


def _bands_at(scan: Scan, ts: float) -> list[tuple[float, float, Track]]:
    at = []
    for tr in scan.tracks:
        if not (tr.sightings[0].ts - TRACK_REACH_S <= ts <= tr.last.ts + TRACK_REACH_S):
            continue
        left, right = tr.edge_at(ts, "left"), tr.edge_at(ts, "right")
        if left is not None and right is not None:
            at.append((left, right, tr))
    return at


def _airtime(horse: list[tuple[float, int | None]], ts: float, ground: float) -> float | None:
    top = ground - AIRBORNE_RISE_PX
    up = next((t for t, h in horse if t >= ts and h is not None and h < top), None)
    if up is None:
        return None
    down = next((t for t, h in horse if t > up and h is not None and h >= top), None)
    return round(down - ts, 3) if down is not None and down - ts < AIRTIME_MAX_S else None


def _fill_band(
    p: Press, ahead: list[tuple[float, float, Track]], front: int, cfg: DerbyConfig
) -> None:
    left, right, tr = ahead[0]
    w = tr.width() or int(right - left)
    p.kind, p.width, p.wclass = tr.kind, w, width_class(w, cfg.column_px)
    p.edge_rel = round(left - front, 1)
    sp = tr.speed()
    p.speed = round(sp) if sp else None
    p.band_id = tr.tid
    if len(ahead) > 1:
        nl, nr, ntr = ahead[1]
        p.gap_next = round(nl - right, 1)
        p.next_kind = ntr.kind
        p.next_wclass = width_class(ntr.width() or int(nr - nl), cfg.column_px)


def measure_presses(
    scan: Scan, keys: list[float], front: int, stalled: set[int], cfg: DerbyConfig
) -> list[Press]:
    ground_rows = [h for _t, h in scan.horse if h is not None]
    ground = float(np.percentile(ground_rows, GROUND_PERCENTILE)) if ground_rows else None
    presses: list[Press] = []
    last: float | None = None
    for ts in keys:
        p = Press(ts=ts, race=scan.race_at(ts))
        p.since_prev_press_s = round(ts - last, 3) if last is not None else None
        last = ts
        at = _bands_at(scan, ts)
        ahead = sorted((a for a in at if a[1] > front), key=lambda a: a[0])
        behind = sorted((a for a in at if a[1] <= front), key=lambda a: -a[1])
        if ahead:
            _fill_band(p, ahead, front, cfg)
            p.stumbled = p.band_id in stalled
            if behind:
                p.gap_prev = round(ahead[0][0] - behind[0][1], 1)
        if ground is not None:
            p.airtime_s = _airtime(scan.horse, ts, ground)
        presses.append(p)
    return presses


def save_crops(session: Path, scan: Scan, prof: Profile, cfg: DerbyConfig) -> Counter[str]:
    """Count every band per kind:width class; keep a few strip crops of each to check by eye."""
    census: Counter[str] = Counter()
    saved: Counter[str] = Counter()
    out = session / "analysis" / "derby_bands"
    out.mkdir(parents=True, exist_ok=True)
    _sx, sy, _sw, sh = prof.roi("strip_roi")
    for tr in scan.tracks:
        w = tr.width()
        if w is None or len(tr.sightings) < MIN_FIT_POINTS:
            continue
        key = f"{tr.kind}:{width_class(w, cfg.column_px)}"
        census[key] += 1
        if saved[key] >= CROPS_PER_CLASS:
            continue
        full = next(s for s in tr.sightings if not (s.left_clipped or s.right_clipped))
        img = cv2.imread(str(session / full.frame))
        if img is None:
            continue
        x0 = max(0, full.left - CROP_PAD_PX)
        crop_img = _stage(img)[sy : sy + sh, x0 : full.right + CROP_PAD_PX]
        name = f"{key.replace(':', '_')}_{tr.tid:04d}_w{w}.png"
        cv2.imwrite(str(out / name), crop_img)
        saved[key] += 1
    return census


def _quartiles(xs: list[float]) -> dict[str, float] | None:
    if not xs:
        return None
    a = np.array(xs, dtype=float)
    return {
        "n": len(xs),
        "p25": round(float(np.percentile(a, 25)), 1),
        "median": round(float(np.median(a)), 1),
        "p75": round(float(np.percentile(a, 75)), 1),
    }


def _by_class(presses: list[Press]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[Press]] = defaultdict(list)
    for p in presses:
        if p.kind and p.wclass:
            groups[f"{p.kind}:{p.wclass}"].append(p)
    out: dict[str, dict[str, Any]] = {}
    for key, ps in sorted(groups.items()):
        ok = [p.edge_rel for p in ps if not p.stumbled and p.edge_rel is not None]
        bad = [p.edge_rel for p in ps if p.stumbled and p.edge_rel is not None]
        out[key] = {
            "presses": len(ps),
            "stumbles": len(bad),
            "edge_rel_cleared": _quartiles(ok),
            "edge_rel_stumbled": _quartiles(bad),
        }
    return out


def _jump_keys(session: Path) -> list[float]:
    return sorted(
        float(r["ts"])
        for r in read_meta(session)
        if r.get("t") == "input"
        and r.get("kind") == "key"
        and r.get("event") == "press"
        and r.get("key") in JUMP_KEYS
    )


def analyze_session(
    session: Path, paths: Paths | None = None, profile: Profile | None = None
) -> dict[str, Any]:
    cfg = DerbyConfig()
    prof = profile or load_profile("jamaa_derby", paths or Paths())
    front = int(prof.number("jump_trigger_x"))
    nominal = prof.number("scroll_px_s")
    scan = scan_session(session, prof, cfg)
    stalled = stalled_bands(scan, front, nominal, cfg)
    presses = measure_presses(scan, _jump_keys(session), front, stalled, cfg)
    census = save_crops(session, scan, prof, cfg)
    air = [p.airtime_s for p in presses if p.airtime_s]
    speeds = [v for _t, v in scan.speeds]
    report = {
        "session": session.name,
        "frames": len(scan.screens),
        "races": max((rc for _t, rc in scan.races), default=0),
        "presses": len(presses),
        "stumbles": sum(p.stumbled for p in presses),
        "unmatched_presses": sum(p.kind is None for p in presses),
        "hooves_x": front,
        "speed_median": round(statistics.median(speeds)) if speeds else None,
        "airtime_s": _quartiles(air),
        "census": dict(sorted(census.items())),
        "by_class": _by_class(presses),
        "screens": dict(Counter(s for _t, s in scan.screens)),
    }
    out = session / "analysis"
    (out / "derby_timing.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    rows = "".join(json.dumps(asdict(p)) + "\n" for p in presses)
    (out / "derby_presses.jsonl").write_text(rows, encoding="utf-8")
    return report


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        print(json.dumps(analyze_session(Path(arg)), indent=1))
