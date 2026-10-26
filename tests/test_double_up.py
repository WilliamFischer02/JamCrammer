"""Grid pairing on synthetic boards and the Double Up farmer against the simulated game."""

from __future__ import annotations

import random

from jamcrammer.core.config import DoubleUpConfig
from jamcrammer.core.events import EventBus
from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import Profile, load_profile
from jamcrammer.modes.arcade.double_up import DoubleUpFarmer
from jamcrammer.vision.grid import pair_board
from tests.synthetic.double_up import (
    END_GEMS_ROI,
    GEOMETRY,
    PLAY_AGAIN_POINT,
    DoubleUpWorld,
    render_board,
)


def _layout(seed: int) -> list[int]:
    rng = random.Random(seed)
    layout = [s for s in range(8) for _ in range(2)]
    rng.shuffle(layout)
    return layout


def test_pairing_on_synthetic_boards() -> None:
    for seed in range(5):
        layout = _layout(seed)
        board = render_board(layout, set(range(16)), set())
        pairing = pair_board(board, GEOMETRY)
        assert len(pairing.pairs) == 8
        for a, b in pairing.pairs:
            assert layout[a] == layout[b], (seed, pairing.pairs)
        assert pairing.confidence >= 0.8


def _profile() -> Profile:
    prof = load_profile("double_up", Paths())
    prof.set_value("grid_origin", list(GEOMETRY.origin))
    prof.set_value("grid_pitch", list(GEOMETRY.pitch))
    prof.set_value("tile_size", list(GEOMETRY.tile_size))
    prof.set_value("board_roi", [560, 220, 820, 640])
    prof.set_value("play_again_point", list(PLAY_AGAIN_POINT))
    prof.set_value("end_screen_gems_roi", list(END_GEMS_ROI))
    return prof


class Sim:
    """Glue: the farmer's grab/click/sleep/clock drive the world's virtual time."""

    def __init__(self, world: DoubleUpWorld) -> None:
        self.world = world
        self.t = 0.0

    def grab(self):  # type: ignore[no-untyped-def]
        self.world.tick(0.0)
        return self.world.image()

    def click(self, x: int, y: int) -> bool:
        return self.world.click(x, y)

    def sleep(self, s: float) -> None:
        self.t += s
        self.world.tick(s)

    def clock(self) -> float:
        return self.t

    def ocr(self, img):  # type: ignore[no-untyped-def]
        return (
            f"You earned {24 + 20 if self.world.mismatches == 0 else 24} gems!"
            if self.world.ended
            else ""
        )


def _farmer(
    world: DoubleUpWorld, cfg: DoubleUpConfig | None = None
) -> tuple[DoubleUpFarmer, Sim, EventBus]:
    sim = Sim(world)
    bus = EventBus()
    cfg = cfg or DoubleUpConfig(preview_s=1.0, burst_fps=10, poll_s=0.1, settle_s=0.05)
    farmer = DoubleUpFarmer(
        _profile(), cfg, sim, sim.grab, bus=bus, ocr=sim.ocr, clock=sim.clock, sleep=sim.sleep
    )
    return farmer, sim, bus


def test_farmer_finishes_a_round_perfectly_from_the_preview() -> None:
    world = DoubleUpWorld(seed=3, preview_grabs=8)
    farmer, _sim, bus = _farmer(world)
    events: list[str] = []
    bus.subscribe("*", lambda e: events.append(e.type))
    res = farmer.play_round()
    assert res.pairs_found == 8 and res.mismatches == 0, res
    assert res.strategy == "pairing" and res.confidence >= 0.8
    assert world.rounds_finished == 1 and world.mismatches == 0
    assert res.gems_reported == 44
    assert "double_up.pairs" in events and "double_up.round" in events
    assert farmer.stats.perfect == 1 and farmer.stats.gems == 44
    # Play Again was clicked: a new round is live
    assert not world.ended and world.grabs == 0


def test_farmer_falls_back_to_reveal_and_remember_without_preview() -> None:
    world = DoubleUpWorld(seed=4, preview_grabs=0)  # board hidden from the first frame
    farmer, _sim, _bus = _farmer(world)
    res = farmer.play_round()
    assert res.strategy == "remember"
    assert res.pairs_found == 8, res
    assert world.rounds_finished == 1
    assert res.duration_s < 60.0  # must finish inside the game's 60 s limit


def test_profile_gate_refuses_uncalibrated_profile() -> None:
    import pytest

    prof = load_profile("double_up", Paths())
    with pytest.raises(ValueError, match="missing"):
        DoubleUpFarmer(
            prof,
            DoubleUpConfig(),
            Sim(DoubleUpWorld()),
            lambda: render_board(_layout(0), set(), set()),
        )
