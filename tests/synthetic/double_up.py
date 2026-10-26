"""Synthetic Double Up: a 4x4 memory board rendered as a stage frame, plus a simulated world
that reacts to clicks the way the game does (preview, flip, match, hide, end screen)."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import cv2
import numpy as np
import numpy.typing as npt

from jamcrammer.vision.grid import GridGeometry

BgrImage = npt.NDArray[np.uint8]

W, H = 1920, 1080
GEOMETRY = GridGeometry(origin=(660, 300), pitch=(200, 160), shape=(4, 4), tile_size=(170, 130))
HIDDEN_COLOR = (170, 110, 40)  # blue-ish back (BGR)
SYMBOL_COLORS = [
    (60, 60, 230),
    (60, 200, 60),
    (230, 100, 60),
    (40, 200, 230),
    (200, 60, 200),
    (60, 230, 230),
    (120, 120, 120),
    (30, 140, 255),
]


def draw_symbol(img: BgrImage, box: tuple[int, int, int, int], symbol: int) -> None:
    x, y, w, h = box
    color = SYMBOL_COLORS[symbol]
    cx, cy = x + w // 2, y + h // 2
    kind = symbol % 4
    if kind == 0:
        cv2.circle(img, (cx, cy), min(w, h) // 3, color, -1)
    elif kind == 1:
        cv2.rectangle(img, (cx - w // 4, cy - h // 4), (cx + w // 4, cy + h // 4), color, -1)
    elif kind == 2:
        pts = np.array(
            [[cx, cy - h // 3], [cx - w // 3, cy + h // 3], [cx + w // 3, cy + h // 3]], np.int32
        )
        cv2.fillPoly(img, [pts], color)
    else:
        cv2.ellipse(img, (cx, cy), (w // 3, h // 5), 30, 0, 360, color, -1)
    cv2.putText(
        img,
        str(symbol),
        (x + 8, y + 24),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )


def render_board(
    layout: list[int], revealed: set[int], matched: set[int], geometry: GridGeometry = GEOMETRY
) -> BgrImage:
    img = np.full((H, W, 3), (70, 60, 50), dtype=np.uint8)
    cv2.putText(
        img, "Double Up", (820, 120), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (255, 255, 255), 4, cv2.LINE_AA
    )
    for i, symbol in enumerate(layout):
        x, y, w, h = geometry.box(i)
        if i in revealed or i in matched:
            cv2.rectangle(img, (x, y), (x + w, y + h), (235, 235, 235), -1)
            draw_symbol(img, (x, y, w, h), symbol)
            if i in matched:
                cv2.rectangle(img, (x, y), (x + w, y + h), (60, 200, 60), 6)
        else:
            cv2.rectangle(img, (x, y), (x + w, y + h), HIDDEN_COLOR, -1)
            cv2.putText(
                img,
                "?",
                (x + w // 2 - 15, y + h // 2 + 18),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.6,
                (255, 255, 255),
                3,
                cv2.LINE_AA,
            )
    return img


def render_end_screen(gems: int) -> BgrImage:
    img = np.full((H, W, 3), (70, 60, 50), dtype=np.uint8)
    cv2.putText(
        img,
        f"You earned {gems} gems!",
        (560, 420),
        cv2.FONT_HERSHEY_SIMPLEX,
        2.2,
        (255, 255, 255),
        5,
        cv2.LINE_AA,
    )
    cv2.rectangle(img, (810, 620), (1110, 700), (60, 200, 60), -1)
    cv2.putText(
        img, "PLAY AGAIN", (840, 672), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (0, 0, 0), 3, cv2.LINE_AA
    )
    return img


PLAY_AGAIN_POINT = (960, 660)
END_GEMS_ROI = (520, 340, 900, 120)


@dataclass
class DoubleUpWorld:
    """Game simulation. ``preview_grabs`` frames show the whole board, then it hides."""

    seed: int = 1
    preview_grabs: int = 6
    flip_ms: int = 400
    shuffle_each_round: bool = True
    layout: list[int] = field(default_factory=list)
    revealed: list[int] = field(default_factory=list)
    matched: set[int] = field(default_factory=set)
    grabs: int = 0
    clicks: list[tuple[int, int]] = field(default_factory=list)
    mismatches: int = 0
    rounds_finished: int = 0
    ended: bool = False
    now: float = 0.0
    _hide_at: float | None = None

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)
        self.new_round()

    def new_round(self) -> None:
        self.layout = [s for s in range(8) for _ in range(2)]
        if self.shuffle_each_round or not self.rounds_finished:
            self._rng.shuffle(self.layout)
        self.revealed, self.matched, self.grabs, self.ended = [], set(), 0, False
        self._hide_at = None

    def tick(self, dt: float) -> None:
        self.now += dt
        if self._hide_at is not None and self.now >= self._hide_at:
            self.revealed = []
            self._hide_at = None

    def image(self) -> BgrImage:
        if self.ended:
            return render_end_screen(3 * 8 + 20 if self.mismatches == 0 else 3 * 8)
        self.grabs += 1
        if self.grabs <= self.preview_grabs:
            return render_board(self.layout, set(range(16)), set())
        return render_board(self.layout, set(self.revealed), self.matched)

    def click(self, x: int, y: int) -> bool:
        self.clicks.append((x, y))
        if self.ended:
            if abs(x - PLAY_AGAIN_POINT[0]) < 150 and abs(y - PLAY_AGAIN_POINT[1]) < 40:
                self.new_round()
            return True
        if self.grabs <= self.preview_grabs:
            return True  # clicks during the preview are ignored by the game
        if self._hide_at is not None:  # mismatch still showing: the game hides it first
            self.revealed = []
            self._hide_at = None
        for i in range(16):
            bx, by, bw, bh = GEOMETRY.box(i)
            if bx <= x < bx + bw and by <= y < by + bh:
                if i in self.matched or i in self.revealed:
                    return True
                self.revealed.append(i)
                if len(self.revealed) == 2:
                    a, b = self.revealed
                    if self.layout[a] == self.layout[b]:
                        self.matched |= {a, b}
                        self.revealed = []
                        if len(self.matched) == 16:
                            self.ended = True
                            self.rounds_finished += 1
                    else:
                        self.mismatches += 1
                        self._hide_at = self.now + self.flip_ms / 1000.0
                return True
        return True
