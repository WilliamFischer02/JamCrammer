"""Synthetic Jamaa Derby: a scrolling lane with obstacles, a horse that jumps on Space, carrot
boosts, and a results screen. Place is 1st when nothing was hit."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import cv2
import numpy as np
import numpy.typing as npt

BgrImage = npt.NDArray[np.uint8]

W, H = 1920, 1080
LANE_ROI = (200, 500, 1500, 200)  # x, y, w, h (stage)
HORSE_X = 150  # within the lane
BOOST_POINT = (1700, 950)
PLACE_ROI = (700, 300, 520, 120)
GEMS_ROI = (700, 440, 520, 100)
RACE_AGAIN = (960, 800)


@dataclass
class Ob:
    x: float  # left edge within the lane
    width: int
    hit: bool = False
    cleared: bool = False


@dataclass
class DerbyWorld:
    seed: int = 1
    speed: float = 220.0  # px per second
    boost_speed: float = 320.0
    race_len_s: float = 20.0
    jump_air_s: float = 0.55
    fps_hint: float = 20.0
    t: float = 0.0
    obstacles: list[Ob] = field(default_factory=list)
    airborne_until: float = -1.0
    boost_until: float = -1.0
    boosts_left: int = 3
    hits: int = 0
    racing: bool = True
    races: int = 0
    places: list[int] = field(default_factory=list)
    jumps: int = 0
    keys: list[tuple[float, str]] = field(default_factory=list)
    clicks: list[tuple[float, int, int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)
        self.new_race()

    def new_race(self) -> None:
        self.t = 0.0
        self.obstacles = []
        x = 900.0
        while x < self.speed * self.race_len_s + 1400:
            self.obstacles.append(Ob(x, self._rng.choice([60, 70, 150])))
            x += self._rng.uniform(380, 650)
        self.airborne_until = -1.0
        self.boost_until = -1.0
        self.boosts_left = 3
        self.hits = 0
        self.racing = True
        self.race_t0 = 0.0
        self.dist = 0.0

    def tick(self, dt: float) -> None:
        if not self.racing or dt <= 0:
            return
        steps = max(1, int(dt / 0.01))
        sub = dt / steps
        for _ in range(steps):
            v = self.boost_speed if self.t < self.boost_until else self.speed
            self.t += sub
            self.dist += v * sub
            for ob in self.obstacles:
                ob.x -= v * sub
                front = HORSE_X + 60
                if not ob.hit and not ob.cleared and ob.x <= front and ob.x + ob.width >= HORSE_X:
                    if self.t < self.airborne_until:
                        ob.cleared = True
                    else:
                        ob.hit = True
                        self.hits += 1
                        self.speed_penalty()
            if self.dist >= self.speed * self.race_len_s:
                self.racing = False
                self.races += 1
                self.places.append(1 if self.hits == 0 else min(6, 1 + self.hits))

    def speed_penalty(self) -> None:
        self.t += 0.4  # a stumble costs time

    def image(self) -> BgrImage:
        img = np.full((H, W, 3), (60, 120, 60), dtype=np.uint8)
        lx, ly, lw, lh = LANE_ROI
        if self.racing:
            cv2.rectangle(img, (lx, ly), (lx + lw, ly + lh), (90, 150, 190), -1)  # sand lane
            cv2.rectangle(
                img,
                (lx + HORSE_X, ly + lh - 90),
                (lx + HORSE_X + 60, ly + lh - 20),
                (40, 40, 140),
                -1,
            )
            for ob in self.obstacles:
                if -ob.width < ob.x < lw:
                    x0 = lx + int(max(0, ob.x))
                    x1 = lx + int(min(lw, ob.x + ob.width))
                    cv2.rectangle(img, (x0, ly + lh - 70), (x1, ly + lh - 10), (30, 60, 120), -1)
            cv2.circle(img, BOOST_POINT, 36, (40, 120, 240), -1)
        else:
            place = self.places[-1]
            words = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", 6: "6th"}[place]
            cv2.putText(
                img,
                f"You finished {words}!",
                (PLACE_ROI[0] + 20, PLACE_ROI[1] + 80),
                cv2.FONT_HERSHEY_SIMPLEX,
                2.0,
                (255, 255, 255),
                4,
                cv2.LINE_AA,
            )
            gems = {1: 100, 2: 75, 3: 50, 4: 40, 5: 30, 6: 25}[place]
            cv2.putText(
                img,
                f"Gems {gems}",
                (GEMS_ROI[0] + 20, GEMS_ROI[1] + 70),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.8,
                (255, 255, 255),
                4,
                cv2.LINE_AA,
            )
            cv2.rectangle(
                img,
                (RACE_AGAIN[0] - 150, RACE_AGAIN[1] - 40),
                (RACE_AGAIN[0] + 150, RACE_AGAIN[1] + 40),
                (60, 200, 60),
                -1,
            )
        return img

    def key_tap(self, key: str, hold_ms: int) -> None:
        self.keys.append((self.t, key))
        if key == "space" and self.racing and self.t >= self.airborne_until:
            self.airborne_until = self.t + self.jump_air_s
            self.jumps += 1

    def click(self, x: int, y: int) -> bool:
        self.clicks.append((self.t, x, y))
        if (
            self.racing
            and abs(x - BOOST_POINT[0]) < 40
            and abs(y - BOOST_POINT[1]) < 40
            and self.boosts_left > 0
            and self.t >= self.boost_until
        ):
            self.boosts_left -= 1
            self.boost_until = self.t + 3.0
        elif not self.racing and abs(x - RACE_AGAIN[0]) < 150 and abs(y - RACE_AGAIN[1]) < 40:
            self.new_race()
        return True
