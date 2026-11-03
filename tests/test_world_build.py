"""The offline map rebuild (modes/adventures/world_build.py) on a synthetic cave: frames
cut from a known texture under a moving light, kept with noisy poses, must come back
registered to the pixel, with the animal, the HUD and a passing moth left out."""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

from jamcrammer.core.config import WorldConfig
from jamcrammer.core.profiles import Profile
from jamcrammer.modes.adventures import sight
from jamcrammer.modes.adventures.world import ObsStore, World
from jamcrammer.modes.adventures.world_build import load_frames, rebuild

W, H = 1920, 1080
ANCHOR = (1005, 500)
BOX = (875, 335, 260, 200)  # the animal and its tag on screen
HUD = [[0, 935, 1920, 145], [0, 0, 90, 1080], [1830, 0, 90, 1080]]
LIGHT = 420  # the lit radius of the synthetic lamp (fully dark past 1.4x)
POSE_NOISE = 45  # px of error on the kept poses
SEED = 7


def _texture(w: int, h: int, rng: np.random.Generator) -> np.ndarray:
    """A cave-like texture: coloured blobs of several sizes on a dark grey rock."""
    img = np.full((h, w, 3), 70, np.uint8)
    for _ in range(900):
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        r = int(rng.integers(6, 60))
        col = tuple(int(v) for v in rng.integers(40, 230, 3))
        cv2.circle(img, (x, y), r, col, -1)
    noise = rng.normal(0, 12, (h, w, 3))
    return np.asarray(np.clip(img.astype(np.float32) + noise, 0, 255), np.uint8)


def _frame(tex: np.ndarray, origin: tuple[int, int], moth: tuple[int, int] | None) -> np.ndarray:
    ox, oy = origin
    crop = tex[oy : oy + H, ox : ox + W].astype(np.float32)
    yy, xx = np.mgrid[0:H, 0:W]
    r = np.hypot(xx - ANCHOR[0], yy - ANCHOR[1])
    gain = np.clip(1.4 - r / LIGHT, 0.0, 1.0)[..., None]
    out = (crop * gain).astype(np.uint8)
    cv2.rectangle(
        out, (BOX[0], BOX[1]), (BOX[0] + BOX[2] - 1, BOX[1] + BOX[3] - 1), (40, 40, 220), -1
    )
    for x, y, w, h in HUD:
        cv2.rectangle(out, (x, y), (x + w, y + h), (200, 120, 30), -1)
    if moth is not None:
        cv2.circle(out, moth, 22, (255, 255, 255), -1)
    return np.asarray(out, np.uint8)


def _walk() -> list[tuple[int, int]]:
    """Frame origins: a lawnmower over the texture with revisits (loop closures)."""
    xs = list(range(0, 1500, 260))
    ys = list(range(0, 1100, 240))
    path = []
    for k, y in enumerate(ys):
        row = xs if k % 2 == 0 else xs[::-1]
        path += [(x, y) for x in row]
    path += [(xs[2], ys[1]), (xs[1], ys[2])]  # back over the middle
    return path


def _build(tmp: Path) -> tuple[World, Path, np.ndarray, list[tuple[int, int]]]:
    rng = np.random.default_rng(SEED)
    world = World("synthetic", 70.0, ANCHOR, (0, 0, 50, 32), WorldConfig())
    world.area("moths")
    folder = tmp / "world"
    world.save(folder)
    tex = _texture(*world.size, rng)
    store = ObsStore(tmp / "obs")
    truth = _walk()
    for k, (ox, oy) in enumerate(truth):
        moth = (int(rng.integers(300, W - 300)), int(rng.integers(200, 700))) if k % 2 else None
        frame = _frame(tex, (ox, oy), moth)
        nx, ny = rng.uniform(-POSE_NOISE, POSE_NOISE, 2)
        pos = (ox + ANCHOR[0] + float(nx), oy + ANCHOR[1] + float(ny))
        store.add("moths", 1000.0 + k, frame, pos=pos, anchor=ANCHOR, box=BOX, tag="hold", dot=pos)
    store.flush()
    return world, folder, tex, truth


def test_rebuild_registers_to_the_pixel_and_drops_transients(tmp_path: Path) -> None:
    _world, folder, tex, truth = _build(tmp_path)
    # the synthetic lamp: gain 1 to 0.4*LIGHT, then linear to 0 at 1.4*LIGHT
    lamp = [[0.4 * LIGHT, 1.0], [0.8 * LIGHT, 0.6], [1.1 * LIGHT, 0.3]]
    cfg = WorldConfig(light_px=LIGHT, light_gain=lamp)
    stats = rebuild(folder, [tmp_path / "obs"], cfg, area="moths", hud_rois=HUD)
    assert stats["frames"] == len(truth)
    assert stats["frames_linked"] == len(truth)
    assert stats["residual_px_median"] is not None and stats["residual_px_median"] <= 1.0
    # the solved origins: every pair of frames agrees with the truth to a px
    frames = load_frames([tmp_path / "obs"], "moths")
    import json

    doc = json.loads((folder / "build.json").read_text(encoding="utf-8"))
    solved = {Path(o["file"]).name: tuple(o["origin"]) for o in doc["origins"]}
    worst = 0.0
    for a in frames:
        for b in frames:
            sa, sb = solved[a.path.name], solved[b.path.name]
            ta, tb = truth[a.idx], truth[b.idx]
            worst = max(
                worst, math.dist((sa[0] - sb[0], sa[1] - sb[1]), (ta[0] - tb[0], ta[1] - tb[1]))
            )
    assert worst <= 1.0, worst
    # the picture: where the tiles are lit they are the texture (one global offset allowed);
    # compared high-passed, so the lamp's dimming does not count, and against the texture
    # moved by 3 px it is clearly worse: the composite is aligned to the pixel
    rebuilt = World.load(folder, cfg)
    assert rebuilt is not None
    rebuilt.attach_tiles(folder / "tiles")
    out = tmp_path / "export.png"
    box = rebuilt.tiles.export("moths", out)
    assert box is not None
    raw = cv2.imread(str(out))
    assert raw is not None
    img = np.asarray(raw, np.uint8)
    first = solved[frames[0].path.name]
    off = (int(first[0] - truth[0][0]), int(first[1] - truth[0][1]))
    lit = img.max(axis=2) > 0
    ncc = _ncc(img, lit, tex, (box[0] - off[0], box[1] - off[1]))
    ncc3 = _ncc(img, lit, tex, (box[0] - off[0] + 3, box[1] - off[1]))
    assert ncc > 0.85, ncc
    assert ncc3 < ncc - 0.1, (ncc, ncc3)
    # no animal box colour, no HUD bar, no moth white in the picture
    box_colour = np.all(np.abs(img.astype(int) - np.array([40, 40, 220])) <= 2, axis=2)
    assert np.count_nonzero(box_colour) < 50, np.count_nonzero(box_colour)
    # 13 moths of ~1500 px flew through: at most one survives (where only one or two frames
    # saw a spot, the nearest sample wins and a moth can be it)
    white = img.min(axis=2) > 250
    ys, xs = np.nonzero(white)
    tx, ty = xs + box[0] - off[0], ys + box[1] - off[1]
    ok = (tx >= 0) & (ty >= 0) & (tx < tex.shape[1]) & (ty < tex.shape[0])
    moth = np.count_nonzero(tex[ty[ok], tx[ok]].min(axis=1) < 200)  # white where the cave is not
    assert moth < 2000, moth


def _ncc(img: np.ndarray, lit: np.ndarray, tex: np.ndarray, at: tuple[int, int]) -> float:
    """Normalised correlation of the high-passed grey picture with the texture when the
    picture's top-left sits at ``at`` on the texture (the common part only)."""
    h, w = img.shape[:2]
    x0, y0 = at
    ix0, iy0 = max(0, -x0), max(0, -y0)
    ix1, iy1 = min(w, tex.shape[1] - x0), min(h, tex.shape[0] - y0)
    assert ix1 > ix0 and iy1 > iy0
    a = cv2.cvtColor(img[iy0:iy1, ix0:ix1], cv2.COLOR_BGR2GRAY).astype(np.float32)
    b = cv2.cvtColor(tex[iy0 + y0 : iy1 + y0, ix0 + x0 : ix1 + x0], cv2.COLOR_BGR2GRAY)
    b = b.astype(np.float32)
    a = a - cv2.GaussianBlur(a, (0, 0), 6)
    b = b - cv2.GaussianBlur(b, (0, 0), 6)
    inner = cv2.erode(lit[iy0:iy1, ix0:ix1].astype(np.uint8), np.ones((15, 15), np.uint8)) > 0
    va, vb = a[inner], b[inner]
    va, vb = va - va.mean(), vb - vb.mean()
    return float(va @ vb / (np.linalg.norm(va) * np.linalg.norm(vb)))


def test_obs_store_round_trip(tmp_path: Path) -> None:
    store = ObsStore(tmp_path / "obs")
    frame = np.zeros((H, W, 3), np.uint8)
    o = store.add("moths", 5.0, frame, pos=(10.0, 20.0), anchor=(1005.0, 500.0), box=BOX, tag="t")
    assert o is not None
    store.flush()
    again = ObsStore.load(tmp_path / "obs")
    assert len(again) == 1
    assert again[0].pos == (10.0, 20.0) and again[0].box == BOX and again[0].tag == "t"
    assert (tmp_path / "obs" / again[0].file).exists()


def test_player_on_screen_by_the_tag() -> None:
    prof = Profile(
        name="adventure",
        fields={
            "nametag_template": {"type": "template", "value": "x.png", "status": "calibrated"},
            "nametag_min": {"type": "number", "value": 0.7, "status": "calibrated"},
            "nametag_box": {"type": "roi", "value": [-130, -165, 260, 200], "status": "calibrated"},
            "player_from_tag": {"type": "point", "value": [5, -25], "status": "calibrated"},
            "avatar_xy": {"type": "point", "value": [1005, 500], "status": "calibrated"},
            "avatar_roi": {"type": "roi", "value": [860, 360, 300, 300], "status": "proposed"},
        },
    )
    rng = np.random.default_rng(1)
    tag = rng.integers(0, 255, (34, 210, 3), dtype=np.uint8)
    frame = np.zeros((H, W, 3), np.uint8)
    frame[700:734, 400:610] = tag  # the animal stands low left: the camera stopped at an edge
    point, box, score = sight.player_on_screen(frame, prof, tag)
    assert score > 0.95
    assert point == (505 + 5, 717 - 25)
    assert box == (505 - 130, 717 - 165, 260, 200)
    none_point, none_box, none_score = sight.player_on_screen(np.zeros_like(frame), prof, tag)
    assert none_score == 0.0 and none_point == (1005, 500) and none_box == (860, 360, 300, 300)
