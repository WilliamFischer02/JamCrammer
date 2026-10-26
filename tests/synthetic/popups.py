"""Draw AJC-like rooms and popups for watchdog tests."""

from __future__ import annotations

import cv2
import numpy as np
import numpy.typing as npt

BgrImage = npt.NDArray[np.uint8]

W, H = 1920, 1080
FOLIAGE_GREEN = (40, 180, 60)  # BGR, inside the profile's HSV range on purpose
BUTTON_GREEN = (60, 205, 70)
PANEL_BEIGE = (190, 225, 245)
PANEL_BORDER = (60, 110, 160)
TEXT_DARK = (40, 40, 60)


def room_frame(seed: int = 1) -> BgrImage:
    """A textured 'room': gradient sky/sand, foliage blobs (green!), a HUD band."""
    rng = np.random.default_rng(seed)
    img = np.zeros((H, W, 3), dtype=np.uint8)
    for yy in range(H):
        t = yy / H
        img[yy, :, :] = (int(200 - 120 * t), int(180 - 40 * t), int(120 + 100 * t))
    noise = rng.integers(0, 25, size=(H, W, 1), dtype=np.uint8)
    img = np.asarray(cv2.add(img, np.repeat(noise, 3, axis=2)), dtype=np.uint8)
    # foliage: ragged green blobs (should NOT look like buttons)
    for _ in range(12):
        cx, cy = int(rng.integers(100, W - 100)), int(rng.integers(150, H - 250))
        for _ in range(6):
            r = int(rng.integers(12, 40))
            cv2.circle(
                img,
                (cx + int(rng.integers(-40, 40)), cy + int(rng.integers(-30, 30))),
                r,
                FOLIAGE_GREEN,
                -1,
            )
    # HUD bands
    cv2.rectangle(img, (0, 0), (W, 100), (90, 60, 30), -1)
    cv2.rectangle(img, (0, 900), (W, H), (90, 60, 30), -1)
    return img


def draw_modal(
    base: BgrImage,
    text: str,
    button_text: str | None = "YES",
    size: tuple[int, int] = (620, 300),
) -> tuple[BgrImage, tuple[int, int, int, int] | None]:
    """Centered dialog; returns (image, button bbox or None)."""
    img = base.copy()
    pw, ph = size
    x0, y0 = (W - pw) // 2, (H - ph) // 2
    cv2.rectangle(img, (x0, y0), (x0 + pw, y0 + ph), PANEL_BEIGE, -1)
    cv2.rectangle(img, (x0, y0), (x0 + pw, y0 + ph), PANEL_BORDER, 6)
    cv2.putText(
        img, text, (x0 + 40, y0 + 110), cv2.FONT_HERSHEY_SIMPLEX, 1.3, TEXT_DARK, 3, cv2.LINE_AA
    )
    button = None
    if button_text is not None:
        bw, bh = 220, 70
        bx, by = x0 + (pw - bw) // 2, y0 + ph - bh - 40
        cv2.rectangle(img, (bx, by), (bx + bw, by + bh), BUTTON_GREEN, -1)
        cv2.putText(
            img,
            button_text,
            (bx + 60, by + 48),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.2,
            (255, 255, 255),
            3,
            cv2.LINE_AA,
        )
        button = (bx, by, bw, bh)
    return img, button


def still_playing_popup(base: BgrImage) -> tuple[BgrImage, tuple[int, int, int, int]]:
    img, button = draw_modal(base, "Are you still playing?", "YES")
    assert button is not None
    return img, button


def logged_out_popup(base: BgrImage) -> BgrImage:
    img, _ = draw_modal(base, "You have been logged out", None)
    return img


def unknown_popup(base: BgrImage) -> BgrImage:
    img, _ = draw_modal(base, "Wild Jammer wants to trade!", None)
    return img
