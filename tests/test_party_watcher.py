"""Banner identification against the fixtures (KICKOFF §14) and a simulated scan/join."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np
import pytest

from jamcrammer.core.config import HuntConfig, InputConfig
from jamcrammer.core.events import EventBus
from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import load_profile
from jamcrammer.core.store import Store
from jamcrammer.modes.party_watcher import (
    Banner,
    BannerLibrary,
    PanelAnalyzer,
    PartyWatcher,
    rotation_report,
    slug,
)
from jamcrammer.platform.input import InputController
from jamcrammer.platform.window import Rect, WindowInfo

PATHS = Paths()
FIXTURE_CROP = PATHS.root / "tests/fixtures/my_parties_2026-10-26.png"
FIXTURE_FULL = PATHS.root / "tests/fixtures/my_parties_full_2026-10-26.png"


def _analyzer(ocr: Callable[[np.ndarray], str] | None = None) -> PanelAnalyzer:
    return PanelAnalyzer(
        load_profile("parties", PATHS),
        BannerLibrary(PATHS.assets / "banners"),
        paths=PATHS,
        ocr=ocr,
    )


def _load(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert img is not None
    return img


def test_fixture_crop_identifies_pinned_banners_and_go_now() -> None:
    """The spec's fixture test: Adventure Base Camp, Bitter Sweets, Haunted Forest Party, GO NOW."""
    an = _analyzer(ocr=lambda _c: "")  # pHash only: no OCR dependence
    title, banners = an.analyze(_load(FIXTURE_CROP))
    assert title is not None
    labels = [b.label for b in banners]
    assert labels == ["Adventure Base Camp", "Bitter Sweets", "Haunted Forest Party"]
    assert [b.is_slot for b in banners] == [False, False, True]
    go = banners[2].go_now_box
    assert go is not None
    gx, gy, gw, _gh = go
    # GO NOW sits on the right of the party slot (fixture: ~445-620 x, ~460-505 y)
    assert 430 <= gx <= 470 and 450 <= gy <= 475 and 150 <= gw <= 200
    assert all(b.method == "phash" for b in banners)


def test_full_frame_identifies_banners_including_spooky() -> None:
    an = _analyzer(ocr=lambda _c: "")
    title, banners = an.analyze(_load(FIXTURE_FULL))
    assert title is not None
    assert [b.label for b in banners] == ["Adventure Base Camp", "Bitter Sweets", "Spooky Party"]
    assert banners[2].is_slot and banners[2].go_now_point is not None
    px, py = banners[2].go_now_point
    assert 1110 <= px <= 1160 and 715 <= py <= 750  # stage space
    assert an.close_x_point(_load(FIXTURE_FULL)) is not None
    cx, cy = an.close_x_point(_load(FIXTURE_FULL)) or (0, 0)
    assert 1220 <= cx <= 1265 and 315 <= cy <= 355


def test_unknown_banner_falls_back_to_ocr_fuzzy_and_target_detection() -> None:
    an = _analyzer(ocr=lambda c: "BEST GUeSS GAMe" if c.shape[1] < 500 else "")  # slot art only
    img = _load(FIXTURE_FULL)
    # Scribble over the slot art so pHash cannot recognise Spooky Party.
    img[640:730, 715:1020] = (30, 30, 30)
    cv2.putText(
        img, "Best Guess Game", (730, 700), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3
    )
    _, banners = an.analyze(img)
    slot = banners[2]
    assert slot.is_slot
    assert slot.method == "ocr" and slot.label == "Best Guess Game"
    assert an.is_target(slot)
    assert not an.is_target(banners[0])


def test_real_ocr_on_pinned_banner_mentions_known_words() -> None:
    an = _analyzer()
    _, banners = an.analyze(_load(FIXTURE_FULL))
    text = banners[1].ocr_text.lower()  # Bitter Sweets: plain bold font
    if not text:
        pytest.skip("winocr returned nothing")
    assert "bitter" in text or "sweets" in text


class FakeWorld:
    """Panel opens on the party hat, closes on the X, GO NOW changes the room."""

    def __init__(self, an: PanelAnalyzer) -> None:
        self.an = an
        self.room = (
            _load(PATHS.root / "captures/panel_probe/0_before.png")
            if (PATHS.root / "captures/panel_probe/0_before.png").exists()
            else np.full((1080, 1920, 3), 120, dtype=np.uint8)
        )
        self.panel = _load(FIXTURE_FULL)
        self.open = False
        self.joined = False
        self.clicks: list[tuple[int, int]] = []
        self.hat = an.profile.point("party_hat_point")
        self.x = an.profile.point("close_x_point")

    def grab(self) -> np.ndarray:
        if self.joined:
            return np.roll(self.room, 300, axis=0)
        return self.panel if self.open else self.room

    def click(self, cx: int, cy: int, **_: object) -> bool:
        self.clicks.append((cx, cy))
        if abs(cx - self.hat[0]) < 20 and abs(cy - self.hat[1]) < 20:
            self.open = True
        elif abs(cx - self.x[0]) < 25 and abs(cy - self.x[1]) < 25:
            self.open = False
        elif self.open and 1047 <= cx <= 1222 and 710 <= cy <= 755:
            self.open = False
            self.joined = True
        return True


def test_scan_cycle_records_sightings(tmp_path: Path) -> None:
    an = _analyzer(ocr=lambda _c: "")
    world = FakeWorld(an)
    store = Store(tmp_path / "t.db")
    bus = EventBus()
    events: list[str] = []
    bus.subscribe("*", lambda e: events.append(e.type))
    watcher = PartyWatcher(
        an,
        HuntConfig(panel_settle_s=0, panel_poll_s=0),
        world,
        world.grab,
        store=store,
        bus=bus,
        unknown_dir=tmp_path / "unk",
        sleep=lambda _s: None,
    )
    res = watcher.scan()
    assert res.opened and res.closed and not res.errors
    assert res.labels == ["Adventure Base Camp", "Bitter Sweets", "Spooky Party"]
    assert res.target is None
    assert not world.open
    rows = rotation_report(store)
    assert [r["label"] for r in rows] == ["Adventure Base Camp", "Bitter Sweets", "Spooky Party"]
    res2 = watcher.scan()
    assert res2.opened and len(rotation_report(store)) == 3  # merged, not duplicated
    assert "parties.scan" in events
    store.close()


def test_join_clicks_go_now_and_confirms_arrival() -> None:
    an = _analyzer(ocr=lambda _c: "")
    world = FakeWorld(an)
    watcher = PartyWatcher(
        an,
        HuntConfig(panel_settle_s=0, panel_poll_s=0, arrival_timeout_s=1.0),
        world,
        world.grab,
        sleep=lambda _s: None,
    )
    assert watcher.open_panel() is not None
    _, banners = an.analyze(world.grab())
    res = watcher.join(
        banners[2],
        room_changed=lambda im: bool(np.abs(im.astype(int) - world.room.astype(int)).mean() > 10),
    )
    assert res.clicked and res.arrived
    assert world.joined


def test_input_controller_satisfies_clicker_bounds() -> None:
    win = WindowInfo(
        1,
        "Animal Jam Classic",
        "Chrome_WidgetWin_1",
        1,
        "AJ Classic.exe",
        Rect(0, 0, 1920, 1080),
        Rect(0, 0, 1920, 1080),
        96,
    )
    ctl = InputController(lambda: win, InputConfig(), dry_run=True)
    assert ctl.click(690, 50) is True


def test_slug() -> None:
    assert slug("Best Guess Game!") == "best_guess_game"
    assert Banner(0, (0, 0, 1, 1), False, None).go_now_point is None
