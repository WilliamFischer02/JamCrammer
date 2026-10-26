"""Opportunist against a combined simulation: Double Up rounds, My Parties scans, re-entry."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from jamcrammer.core.config import AppConfig, DoubleUpConfig, HuntConfig, OpportunistConfig
from jamcrammer.core.events import Event, EventBus
from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import Profile, load_profile
from jamcrammer.core.store import Store
from jamcrammer.modes.arcade.double_up import DoubleUpFarmer
from jamcrammer.modes.bestguess.hunter import Hunter
from jamcrammer.modes.opportunist import ArcadeEntry, Opportunist, overhead_summary
from jamcrammer.modes.party_watcher import BannerLibrary, PanelAnalyzer, PartyWatcher
from jamcrammer.platform.capture import Frame
from jamcrammer.platform.window import Rect
from tests.synthetic.double_up import END_GEMS_ROI, GEOMETRY, PLAY_AGAIN_POINT, DoubleUpWorld
from tests.test_party_watcher import FIXTURE_FULL, _load

PATHS = Paths()
EXIT_POINT = (1850, 60)
MENU_POINT = (790, 50)
TILE_POINT = (700, 500)


def _double_up_profile() -> Profile:
    prof = load_profile("double_up", PATHS)
    prof.set_value("grid_origin", list(GEOMETRY.origin))
    prof.set_value("grid_pitch", list(GEOMETRY.pitch))
    prof.set_value("tile_size", list(GEOMETRY.tile_size))
    prof.set_value("board_roi", [560, 220, 820, 640])
    prof.set_value("play_again_point", list(PLAY_AGAIN_POINT))
    prof.set_value("end_screen_gems_roi", list(END_GEMS_ROI))
    prof.set_value("exit_point", list(EXIT_POINT))
    prof.set_value("games_menu_point", list(MENU_POINT))
    prof.set_value("game_tile_point", list(TILE_POINT))
    return prof


class Lobby:
    """Three screens: the arcade game (DoubleUpWorld), the lobby room, the My Parties panel."""

    def __init__(self, best_guess_after_scans: int | None = None, entry_works: bool = True) -> None:
        self.game = DoubleUpWorld(seed=2, preview_grabs=6)
        self.room = np.full((1080, 1920, 3), (110, 90, 60), dtype=np.uint8)
        panel = _load(FIXTURE_FULL)
        self.panel_plain = panel
        bg = panel.copy()
        bg[670:750, 712:1035] = (30, 30, 30)
        cv2.putText(
            bg, "Best Guess Game", (730, 720), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3
        )
        self.panel_bg = bg
        self.in_game = True
        self.panel_open = False
        self.menu_open = False
        self.joined = False
        self.scans = 0
        self.best_guess_after_scans = best_guess_after_scans
        self.entry_works = entry_works
        self.t = 0.0
        self.parties = load_profile("parties", PATHS)
        self.hat = self.parties.point("party_hat_point")
        self.x = self.parties.point("close_x_point")

    def best_guess_showing(self) -> bool:
        return self.best_guess_after_scans is not None and self.scans >= self.best_guess_after_scans

    def grab(self) -> np.ndarray:
        self.game.tick(0.0)
        if self.joined:
            return np.roll(self.room, 300, axis=0)
        if self.in_game:
            return self.game.image()
        if self.panel_open:
            return self.panel_bg if self.best_guess_showing() else self.panel_plain
        return self.room

    def click(self, cx: int, cy: int) -> bool:
        if self.in_game:
            if abs(cx - EXIT_POINT[0]) < 30 and abs(cy - EXIT_POINT[1]) < 30:
                self.in_game = False
                return True
            return self.game.click(cx, cy)
        if self.panel_open:
            if abs(cx - self.x[0]) < 25 and abs(cy - self.x[1]) < 25:
                self.panel_open = False
            elif 1047 <= cx <= 1222 and 710 <= cy <= 755 and self.best_guess_showing():
                self.panel_open = False
                self.joined = True
            return True
        if abs(cx - self.hat[0]) < 20 and abs(cy - self.hat[1]) < 20:
            self.panel_open = True
            self.scans += 1
        elif abs(cx - MENU_POINT[0]) < 20 and abs(cy - MENU_POINT[1]) < 20:
            self.menu_open = True
        elif self.menu_open and abs(cx - TILE_POINT[0]) < 30 and abs(cy - TILE_POINT[1]) < 30:
            self.menu_open = False
            if self.entry_works:
                self.in_game = True
                self.game.new_round()
        return True

    def sleep(self, s: float) -> None:
        self.t += s
        self.game.tick(s)

    def clock(self) -> float:
        return self.t

    def ocr(self, img: np.ndarray) -> str:
        return "You earned 44 gems!" if self.in_game and self.game.ended else ""

    def frame(self) -> Frame:
        return Frame(
            image=self.grab(), ts=self.t, seq=1, client_rect=Rect(0, 0, 1920, 1080), backend="sim"
        )


def _build(
    lobby: Lobby, tmp_path: Path, check_s: float = 30.0
) -> tuple[Opportunist, Store, list[str]]:
    store = Store(tmp_path / "t.db")
    bus = EventBus()
    events: list[str] = []
    bus.subscribe("*", lambda e: events.append(e.type))

    def persist(e: Event) -> None:
        store.add_event(e)

    bus.subscribe("*", persist)
    cfg = AppConfig()
    cfg.hunt.antiafk_each_cycle = False
    du_cfg = DoubleUpConfig(preview_s=1.0, burst_fps=10, poll_s=0.1, settle_s=0.05)
    farmer = DoubleUpFarmer(
        _double_up_profile(),
        du_cfg,
        lobby,
        lobby.grab,
        store=store,
        bus=bus,
        ocr=lobby.ocr,
        clock=lobby.clock,
        sleep=lobby.sleep,
    )
    analyzer = PanelAnalyzer(
        load_profile("parties", PATHS),
        BannerLibrary(PATHS.assets / "banners"),
        paths=PATHS,
        ocr=lambda c: (
            "Best Guess Game" if (c.shape[1] < 500 and lobby.best_guess_showing()) else ""
        ),
    )
    watcher = PartyWatcher(
        analyzer,
        HuntConfig(panel_settle_s=0, panel_poll_s=0, arrival_timeout_s=1.0),
        lobby,
        lobby.grab,
        store=store,
        bus=bus,
        clock=lobby.clock,
        sleep=lambda _s: None,
    )
    hunter = Hunter(
        cfg, watcher, lobby.frame, bus=bus, no_join=False, clock=lobby.clock, sleep=lobby.sleep
    )
    entry = ArcadeEntry(
        _double_up_profile(),
        OpportunistConfig(poll_s=0.1, settle_s=0.05),
        lobby,
        lobby.grab,
        farmer.board_visible,
        clock=lobby.clock,
        sleep=lobby.sleep,
    )
    opp = Opportunist(
        OpportunistConfig(), check_s, farmer, hunter, entry, store=store, bus=bus, clock=lobby.clock
    )
    return opp, store, events


def test_checks_at_round_boundary_and_reenters(tmp_path: Path) -> None:
    lobby = Lobby(best_guess_after_scans=None)
    opp, store, events = _build(lobby, tmp_path, check_s=30.0)
    opp.run(lambda: lobby.t > 140.0)
    assert opp.stats.rounds >= 3
    assert opp.stats.checks >= 2 and lobby.scans == opp.stats.checks
    assert not opp.stats.hopped and not opp.stats.fell_back
    assert lobby.in_game  # re-entered after every check
    assert events.count("opportunist.check") == opp.stats.checks
    summary = overhead_summary(store)
    assert summary["checks"] == opp.stats.checks and summary["mean_s"] > 0
    store.close()


def test_hops_to_best_guess_when_it_appears(tmp_path: Path) -> None:
    lobby = Lobby(best_guess_after_scans=2)
    opp, store, events = _build(lobby, tmp_path, check_s=20.0)
    opp.run(lambda: lobby.t > 300.0)
    assert opp.stats.hopped and lobby.joined
    assert "hunter.best_guess_found" in events and "opportunist.hopped" in events
    assert opp.stats.checks == 2
    store.close()


def test_falls_back_to_hunt_after_two_failed_reentries(tmp_path: Path) -> None:
    lobby = Lobby(best_guess_after_scans=None, entry_works=False)
    opp, store, events = _build(lobby, tmp_path, check_s=20.0)
    opp.run(lambda: lobby.t > 400.0)
    assert opp.stats.fell_back and opp.stats.entry_failures >= 2
    assert "opportunist.fallback" in events
    assert opp.state == "HUNT_FALLBACK"
    store.close()


def test_entry_requires_calibrated_route() -> None:
    prof = load_profile("double_up", PATHS)
    lobby = Lobby()
    with pytest.raises(ValueError, match="entry route"):
        ArcadeEntry(prof, OpportunistConfig(), lobby, lobby.grab, lambda im: True)
