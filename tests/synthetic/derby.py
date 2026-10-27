"""Synthetic Jamaa Derby in the real layout (2026-10-26 recording): a green field scrolling
right-to-left under a brown horse pinned at the top lane, full-height obstacle bands (hay,
mud, water, hedge), a checkered finish stretch, a HUD with three carrots and a progress bar,
a white start line while waiting, and a beige "Next Race In N" leaderboard with a yellow
frame around our row. Only the band's leading edge matters: the horse must be airborne when
the edge reaches its hooves."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import cv2
import numpy as np
import numpy.typing as npt

BgrImage = npt.NDArray[np.uint8]

W, H = 1920, 1080
STRIP_ROI = (100, 400, 1720, 500)  # stops before the right wooden frame
HORSE_FRONT_X = 940
JUMP_TRIGGER_X = 800  # hooves
HORSE_BOX = (700, 60, 260, 360)
BOOST_POINT = (960, 975)
BOOST_BOX = (840, 945, 240, 60)
RESULTS_BOX = (300, 150, 1300, 750)
RESULTS_ROWS = (400, 190, 560, 540)
FIRST_ROW_CY = 52  # within RESULTS_ROWS
ROW_PITCH = 86
STARTLINE_ROI = (860, 400, 80, 500)
PROGRESS_ROI = (300, 1042, 1300, 16)
AIR_S = 0.75
BAND_SPECS = {"hay": (350, 700), "mud": (316, 640), "water": (278, 560), "hedge": (200, 200)}


def _bgr(h: int, s: int, v: int) -> tuple[int, int, int]:
    px = np.array([[[h, s, v]]], dtype=np.uint8)
    b, g, r = cv2.cvtColor(px, cv2.COLOR_HSV2BGR)[0, 0]
    return (int(b), int(g), int(r))


GRASS = _bgr(54, 136, 198)
CHECK_DARK = _bgr(54, 150, 139)
COLOURS = {
    "hay": _bgr(24, 163, 249),
    "mud": _bgr(17, 113, 108),
    "water": _bgr(84, 157, 162),
    "hedge": _bgr(51, 141, 140),
}
HORSE = _bgr(16, 134, 101)
BEIGE = _bgr(25, 90, 235)
WHITE = (255, 255, 255)
PROGRESS = _bgr(55, 200, 220)
CARROT = _bgr(15, 220, 240)
GREY = (150, 150, 150)
HIGHLIGHT = _bgr(28, 230, 250)
FRAME = (40, 90, 140)


@dataclass
class Band:
    kind: str
    x: float  # left edge, stage px
    width: int
    judged: bool = False
    hit: bool = False


@dataclass
class DerbyWorld:
    seed: int = 1
    speed: float = 830.0
    boost_speed: float = 1150.0
    race_len_px: float = 36000.0
    wait_s: float = 3.0
    results_s: float = 5.0
    t: float = 0.0
    phase: str = "WAITING"
    phase_t0: float = 0.0
    bands: list[Band] = field(default_factory=list)
    dist: float = 0.0
    airborne_until: float = -1.0
    boost_until: float = -1.0
    stall_until: float = -1.0
    boosts_left: int = 3
    hits: int = 0
    races: int = 0
    places: list[int] = field(default_factory=list)
    jumps: int = 0
    keys: list[tuple[float, str]] = field(default_factory=list)
    clicks: list[tuple[float, int, int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)
        self._layout()

    def _layout(self) -> None:
        self.bands = []
        x = 2500.0
        while x < self.race_len_px - 4000:
            kind = self._rng.choice(list(BAND_SPECS))
            width = self._rng.choice(BAND_SPECS[kind])
            self.bands.append(Band(kind, x, width))
            x += width + self._rng.uniform(700, 1400)
        self.dist = 0.0
        self.airborne_until = -1.0
        self.boost_until = -1.0
        self.boosts_left = 3
        self.hits = 0

    # --- simulation -------------------------------------------------------------------------------

    def tick(self, dt: float) -> None:
        if dt <= 0:
            return
        steps = max(1, int(dt / 0.01))
        sub = dt / steps
        for _ in range(steps):
            self.t += sub
            if self.phase == "WAITING":
                if self.t - self.phase_t0 >= self.wait_s:
                    self.phase, self.phase_t0 = "RACING", self.t
            elif self.phase == "RACING":
                v = self.boost_speed if self.t < self.boost_until else self.speed
                if self.t < self.stall_until:
                    v = 0.0
                self.dist += v * sub
                for b in self.bands:
                    b.x -= v * sub
                    if not b.judged and b.x <= JUMP_TRIGGER_X:
                        b.judged = True
                        if self.t >= self.airborne_until:
                            b.hit = True
                            self.hits += 1
                            self.stall_until = self.t + 0.6  # stumble: the track stops scrolling
                if self.dist >= self.race_len_px:
                    self.races += 1
                    self.places.append(1 if self.hits == 0 else min(6, 1 + self.hits))
                    self.phase, self.phase_t0 = "RESULTS", self.t
            elif self.phase == "RESULTS" and self.t - self.phase_t0 >= self.results_s:
                self._layout()
                self.phase, self.phase_t0 = "WAITING", self.t

    def key_tap(self, key: str, hold_ms: int) -> None:
        self.keys.append((self.t, key))
        if key == "space" and self.phase == "RACING" and self.t >= self.airborne_until:
            self.airborne_until = self.t + AIR_S
            self.jumps += 1

    def click(self, x: int, y: int) -> bool:
        self.clicks.append((self.t, x, y))
        if (
            self.phase == "RACING"
            and abs(x - BOOST_POINT[0]) < 60
            and abs(y - BOOST_POINT[1]) < 40
            and self.boosts_left > 0
            and self.t >= self.boost_until
        ):
            self.boosts_left -= 1
            self.boost_until = self.t + 3.0
        return True

    # --- rendering --------------------------------------------------------------------------------

    def image(self) -> BgrImage:
        img = np.full((H, W, 3), GRASS, dtype=np.uint8)
        cv2.rectangle(img, (0, 0), (100, H), FRAME, -1)
        cv2.rectangle(img, (1820, 0), (W, H), FRAME, -1)
        if self.phase == "RACING":
            self._draw_track(img)
        elif self.phase == "WAITING":
            cv2.rectangle(img, (880, 100), (930, 920), WHITE, -1)
        self._draw_horse(img, airborne=self.phase == "RACING" and self.t < self.airborne_until)
        self._draw_hud(img)
        if self.phase == "RESULTS":
            self._draw_results(img)
        return img

    def _draw_track(self, img: BgrImage) -> None:
        if self.dist >= self.race_len_px - 8000:  # checkered finish stretch
            for yy in range(100, 920, 130):
                for xx in range(100, 1820, 130):
                    if ((xx // 130) + (yy // 130)) % 2 == 0:
                        cv2.rectangle(img, (xx, yy), (xx + 100, yy + 100), CHECK_DARK, -1)
        for b in self.bands:
            if -b.width < b.x < 1820:
                x0 = int(max(100, b.x))
                x1 = int(min(1820, b.x + b.width))
                cv2.rectangle(img, (x0, 100), (x1, 920), COLOURS[b.kind], -1)
                if b.kind == "hedge":  # tall-grass texture: lighter patches, never full columns
                    for i, xx in enumerate(range(x0, x1, 24)):
                        yy = 100 + (i * 173) % 600
                        cv2.rectangle(img, (xx, yy), (xx + 12, yy + 200), GRASS, -1)

    def _draw_horse(self, img: BgrImage, airborne: bool) -> None:
        lift = 60 if airborne else 0
        cv2.rectangle(img, (720, 180 - lift), (940, 340 - lift), HORSE, -1)

    def _draw_hud(self, img: BgrImage) -> None:
        cv2.rectangle(img, (500, 930), (1420, 1030), (90, 150, 200), -1)
        bx, by, bw, bh = BOOST_BOX
        cv2.rectangle(img, (bx, by), (bx + bw, by + bh), (200, 140, 60), -1)
        for i in range(3):
            colour = CARROT if (i < self.boosts_left and self.phase == "RACING") else GREY
            cv2.rectangle(img, (bx + 20 + i * 75, by + 10), (bx + 70 + i * 75, by + 50), colour, -1)
        px, py, pw, ph = PROGRESS_ROI
        cv2.rectangle(img, (px, py), (px + pw, py + ph), PROGRESS, -1)

    def _draw_results(self, img: BgrImage) -> None:
        x, y, w, h = RESULTS_BOX
        cv2.rectangle(img, (x, y), (x + w, y + h), BEIGE, -1)
        rx, ry, _rw, _rh = RESULTS_ROWS
        place = self.places[-1] if self.places else 1
        for i in range(6):
            cy = ry + FIRST_ROW_CY + i * ROW_PITCH
            if i == place - 1:
                cv2.rectangle(img, (rx + 10, cy - 28), (rx + 540, cy + 28), HIGHLIGHT, 6)
            cv2.putText(
                img,
                f"{i + 1}th rider {[100, 75, 50, 40, 30, 25][i]} Gems",
                (rx + 40, cy + 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.9,
                (60, 50, 40),
                2,
                cv2.LINE_AA,
            )
