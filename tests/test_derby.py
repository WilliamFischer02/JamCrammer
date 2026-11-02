"""Jamaa Derby farmer against the synthetic track (real layout) and real-frame fixtures."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from jamcrammer.core.config import DerbyConfig
from jamcrammer.core.events import EventBus
from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import Profile, ProfileField
from jamcrammer.core.store import Store
from jamcrammer.modes.arcade.derby import (
    Band,
    DerbyDetector,
    DerbyFarmer,
    Screen,
    detect_bands,
    fill,
    highlighted_row,
    horse_top,
)
from jamcrammer.vision.ocr import crop
from tests.synthetic import derby as synth

FIX = Path(__file__).parent / "fixtures" / "derby"
CFG = DerbyConfig()


def _profile() -> Profile:
    # the committed example, never this machine's calibration (profiles/jamaa_derby.yaml)
    prof = Profile.from_yaml(Paths().profiles / "jamaa_derby.example.yaml")
    values = {
        "strip_roi": list(synth.STRIP_ROI),
        "horse_front_x": synth.HORSE_FRONT_X,
        "jump_trigger_x": synth.JUMP_TRIGGER_X,
        "horse_box_roi": list(synth.HORSE_BOX),
        "boost_button_point": list(synth.BOOST_POINT),
        "boost_box_roi": list(synth.BOOST_BOX),
        "results_box_roi": list(synth.RESULTS_BOX),
        "results_rows_roi": list(synth.RESULTS_ROWS),
        "results_first_row_cy": synth.FIRST_ROW_CY,
        "results_row_pitch": synth.ROW_PITCH,
        "startline_roi": list(synth.STARTLINE_ROI),
        "progress_bar_roi": list(synth.PROGRESS_ROI),
        "scroll_px_s": 830,
        "jump_lead_s": 0.08,
        "jump_lead_px": 0,
        "jump_cooldown_s": 0.5,
        "boost_clear_px": 500,
    }
    for key, value in values.items():
        prof.set_value(key, value, status="calibrated")
    return prof


class Sim:
    def __init__(self, world: synth.DerbyWorld) -> None:
        self.world = world

    def grab(self) -> np.ndarray:
        return self.world.image()

    def key_tap(self, key: str, hold_ms: int) -> None:
        self.world.key_tap(key, hold_ms)

    def click(self, x: int, y: int) -> bool:
        return self.world.click(x, y)

    def sleep(self, s: float) -> None:
        self.world.tick(s)

    def clock(self) -> float:
        return self.world.t

    def ocr(self, img: np.ndarray) -> str:
        w = self.world
        if w.phase != "RESULTS" or not w.places:
            return ""
        place = w.places[-1]
        gems = [100, 75, 50, 40, 30, 25][place - 1]
        if img.shape[1] < 300:
            return f"{gems} Gems"
        return f"{place}{['st', 'nd', 'rd', 'th', 'th', 'th'][place - 1]} rider"


def test_synthetic_screens_and_bands() -> None:
    world = synth.DerbyWorld(seed=5)
    det = DerbyDetector(_profile(), CFG)
    assert det.screen(world.image()) == Screen.WAITING
    world.tick(3.2)
    for _ in range(200):  # scroll until a whole band sits inside the strip
        if any(b.x >= 900 and b.x + b.width <= 1700 for b in world.bands):
            break
        world.tick(0.05)
    obs = det.observe(world.image())
    assert obs.screen == Screen.RACING and obs.carrots_left
    kinds = {b.kind for b in obs.bands}
    assert kinds and kinds <= {"hay", "mud", "water", "hedge"}
    inside = [b for b in obs.bands if b.left > 110 and b.right < 1810]  # not clipped by the strip
    assert inside
    for b in inside:  # edges within a few px of the world's bands
        match = min(world.bands, key=lambda wb: abs(wb.x - b.left))
        assert abs(match.x - b.left) <= 6 and abs(match.width - b.width) <= 12, (b, match)
    world.dist = world.race_len_px + 1
    world.tick(0.02)
    assert det.screen(world.image()) == Screen.RESULTS


def test_farmer_wins_at_least_90_percent(tmp_path: Path) -> None:
    world = synth.DerbyWorld(seed=3)
    sim = Sim(world)
    store = Store(tmp_path / "t.db")
    bus = EventBus()
    events: list[str] = []
    bus.subscribe("*", lambda e: events.append(e.type))
    farmer = DerbyFarmer(
        _profile(),
        DerbyConfig(fps=20.0),
        sim,
        sim.grab,
        store=store,
        bus=bus,
        ocr=sim.ocr,
        clock=sim.clock,
        sleep=sim.sleep,
    )
    races = 10
    farmer.run(lambda: farmer.stats.races >= races)
    assert farmer.stats.races == races
    assert farmer.stats.first_place_rate >= 0.9, (world.places, farmer.stats.as_dict())
    assert farmer.stats.jumps > 0 and farmer.stats.pro
    assert "derby.pro_unlocked" in events and events.count("derby.race") == races
    rows = store.query("SELECT gems_reported, notes FROM arcade_rounds WHERE game='jamaa_derby'")
    assert len(rows) == races and all(r["gems_reported"] in (100, 75, 50, 40, 30, 25) for r in rows)
    assert farmer.stats.last is not None and farmer.stats.last.boosts == 3
    assert not farmer.paused_reason


def test_uncalibrated_profile_refused() -> None:
    prof = _profile()
    prof.set_value("strip_roi", None, status="uncalibrated")
    with pytest.raises(ValueError, match="missing"):
        DerbyFarmer(
            prof, DerbyConfig(), Sim(synth.DerbyWorld()), lambda: synth.DerbyWorld().image()
        )


# --- real frames (2026-10-26 recording, stage-space crops) ------------------------------------


def _fx(name: str) -> np.ndarray:
    img = cv2.imread(str(FIX / name))
    assert img is not None, name
    return np.asarray(img, dtype=np.uint8)


def _kinds(name: str) -> list[tuple[str, int, int]]:
    strip = _fx(name)[:, :1720]  # the live strip stops before the right wooden frame
    return [(b.kind, b.left, b.right) for b in detect_bands(strip, 100, CFG)]


def test_real_bands_by_colour() -> None:
    hay = _kinds("strip_36s.png")
    assert [k for k, *_ in hay] == ["hay"] and 1180 <= hay[0][1] <= 1250, hay
    water = _kinds("strip_40s.png")
    assert [k for k, *_ in water] == ["water"] and 600 <= water[0][1] <= 660, water
    hedge = _kinds("strip_44s.png")
    assert "hedge" in [k for k, *_ in hedge] and all(
        k != "water" or b - a >= 40 for k, a, b in hedge
    ), hedge
    tall = _kinds("strip_52s.png")
    assert [k for k, *_ in tall if k == "hedge"] == ["hedge"], tall
    hedge_band = next(
        b for b in detect_bands(_fx("strip_52s.png")[:, :1720], 100, CFG) if b.kind == "hedge"
    )
    assert hedge_band.width >= 180, hedge_band  # stripes closed by hysteresis
    mud = _kinds("strip_56s.png")
    assert [k for k, *_ in mud] == ["mud", "hay"], mud


def test_checkered_finish_is_not_a_hedge() -> None:
    assert _kinds("strip_76s.png") == []


def test_real_screen_anchors() -> None:
    assert fill(_fx("progress_8s.png"), CFG.progress_hsv) >= CFG.progress_min_fill
    assert fill(_fx("progress_results_96s.png"), CFG.progress_hsv) >= CFG.progress_min_fill
    assert fill(_fx("results_box_96s.png"), CFG.beige_hsv) >= CFG.results_min_fill
    line = cv2.cvtColor(_fx("startline_8s.png"), cv2.COLOR_BGR2HSV)
    lo, hi = CFG.white_hsv
    cols = (cv2.inRange(line, np.array(lo, np.uint8), np.array(hi, np.uint8)) > 0).mean(axis=0)
    assert (cols >= CFG.startline_col_fill).mean() >= CFG.startline_min_cols
    none = cv2.cvtColor(_fx("startline_none_36s.png"), cv2.COLOR_BGR2HSV)
    cols = (cv2.inRange(none, np.array(lo, np.uint8), np.array(hi, np.uint8)) > 0).mean(axis=0)
    assert (cols >= CFG.startline_col_fill).mean() < CFG.startline_min_cols
    assert fill(_fx("boost_box_36s.png"), CFG.carrot_hsv) >= CFG.carrot_min_fill
    assert fill(_fx("boost_box_8s.png"), CFG.carrot_hsv) < CFG.carrot_min_fill  # grey before GO


def test_real_results_row_and_horse() -> None:
    box = _fx("results_box_96s.png")
    rows = crop(box, (100, 40, 560, 540))  # results_rows_roi relative to results_box_roi
    cy = highlighted_row(rows, CFG)
    assert cy is not None and 40 <= cy <= 64, cy  # first row: 1st place
    ground = horse_top(_fx("horse_box_36s.png"), CFG)
    assert ground is not None and 100 <= ground <= 160, ground
    # at 64 s a hay band fills the box: nothing of the horse can be told apart -> unknown
    assert horse_top(_fx("horse_box_64s.png"), CFG, hay_overlap=True) is None


def test_stumbles_are_detected_and_learned(tmp_path: Path) -> None:
    """Pressing late (after the edge reached the hooves, inside the window the farmer still
    acts in) hits bands; the stall after each hit is recognised, the late press is matched to
    the passing band and the learner moves that class's lead earlier, persisted to the
    learned file. (At -260 px the farmer never pressed at all; the test only passed while
    every stalled band was re-judged.)"""
    world = synth.DerbyWorld(seed=4)
    sim = Sim(world)
    prof = _profile()
    prof.set_value("jump_lead_px", -100)  # press after the edge has passed the hooves
    prof.set_value("jump_lead_px_narrow", 0)
    bus = EventBus()
    events: list[str] = []
    bus.subscribe("*", lambda e: events.append(e.type))
    learned = tmp_path / "derby_learned.json"
    farmer = DerbyFarmer(
        prof,
        DerbyConfig(fps=20.0),
        sim,
        sim.grab,
        bus=bus,
        ocr=sim.ocr,
        clock=sim.clock,
        sleep=sim.sleep,
        learned_path=learned,
        hits_dir=tmp_path / "hits",
    )
    farmer.run(lambda: farmer.stats.races >= 1)
    assert world.hits > 0 and farmer.stats.hits > 0, (world.hits, farmer.stats.as_dict())
    assert "derby.hit" in events and "derby.learned" in events
    assert learned.exists() and any(v > 0 for v in farmer.learned.lead_px.values())
    assert list((tmp_path / "hits").glob("*.png"))


def test_a_stumble_is_judged_once_however_long_the_stall(tmp_path: Path) -> None:
    """Live 2026-10-29: the stalled band stays at the hooves for 1-3 s after a stumble; it was
    re-opened and re-judged every tick (48 hits and 5-10 lead steps for ~6 stumbles)."""
    world = synth.DerbyWorld(seed=4, stall_s=2.0)
    sim = Sim(world)
    prof = _profile()
    prof.set_value("jump_lead_px", -260)
    prof.set_value("jump_lead_px_narrow", 0)
    farmer = DerbyFarmer(
        prof,
        DerbyConfig(fps=20.0),
        sim,
        sim.grab,
        ocr=sim.ocr,
        clock=sim.clock,
        sleep=sim.sleep,
        learned_path=tmp_path / "derby_learned.json",
    )
    farmer.run(lambda: farmer.stats.races >= 1)
    assert world.hits > 0 and farmer.stats.hits == world.hits, (world.hits, farmer.stats.hits)


def _farmer(
    world: synth.DerbyWorld, prof: Profile, tmp_path: Path, events: list[Any]
) -> DerbyFarmer:
    sim = Sim(world)
    bus = EventBus()
    bus.subscribe("*", events.append)
    return DerbyFarmer(
        prof,
        DerbyConfig(fps=20.0),
        sim,
        sim.grab,
        bus=bus,
        ocr=sim.ocr,
        clock=sim.clock,
        sleep=sim.sleep,
        learned_path=tmp_path / "derby_learned.json",
    )


def test_tight_pair_is_cleared_by_tapping_through_the_landing(tmp_path: Path) -> None:
    """William holds Space through a landing so the next jump fires on touchdown. Here the
    second band is due while the horse is still up from the first: a single press would be
    swallowed in the air; the tap train fires the jump as the hooves land."""
    world = synth.DerbyWorld(seed=1, race_len_px=9000)
    world.bands = [synth.Band("hay", 3000.0, 350), synth.Band("mud", 3000.0 + 350 + 250, 316)]
    prof = _profile()
    prof.set_value("jump_lead_px", 60)  # jump the first band well before its edge ...
    prof.set_value("jump_lead_px_narrow", 0)
    events: list[Any] = []
    farmer = _farmer(world, prof, tmp_path, events)
    farmer.cfg.boosts_per_race = 0
    farmer.cfg.tight_extra_px = 0  # isolate the tap train
    farmer.run(lambda: farmer.stats.races >= 1)
    types = [e.type for e in events]
    assert world.hits == 0, (world.hits, world.keys)
    assert "derby.rejump" in types
    taps = next(e.payload["taps"] for e in events if e.type == "derby.rejump")
    assert taps >= 1


def test_per_class_lead_from_the_profile_scales_with_speed(tmp_path: Path) -> None:
    prof = _profile()
    for key, value in (("jump_lead_px_hay_1col", 390), ("jump_lead_ref_px_s", 845)):
        prof.fields[key] = ProfileField(type="number", value=value, status="calibrated")
    farmer = _farmer(synth.DerbyWorld(seed=1), prof, tmp_path, [])
    band = Band("hay", 1200, 1200 + 330)
    farmer._speed = 845.0
    assert farmer._lead_for(band, 0.0) == (390.0, 0.0)
    farmer._speed = 845.0 / 2
    assert farmer._lead_for(band, 0.0)[0] == 195.0
    tight = Band("mud", band.right + 200, band.right + 520)
    assert farmer._lead_for(band, 0.0, tight)[0] == 195.0 + farmer.cfg.tight_extra_px
    # a class without its own value falls back to the old rule
    assert farmer._lead_for(Band("water", 1200, 1200 + 330), 0.0)[0] == prof.number(
        "jump_lead_px"
    ) + prof.number("jump_lead_px_narrow")


def test_reserve_carrot_gets_the_horse_out_of_a_stumble(tmp_path: Path) -> None:
    """One carrot is kept back on clear stretches and clicked the moment a stumble is seen."""
    world = synth.DerbyWorld(seed=4, stall_s=1.5)
    prof = _profile()
    prof.set_value("jump_lead_px", -100)  # late presses: stumbles
    prof.set_value("jump_lead_px_narrow", 0)
    events: list[Any] = []
    farmer = _farmer(world, prof, tmp_path, events)
    farmer.cfg.learn_enabled = False
    farmer.run(lambda: farmer.stats.races >= 1)
    reasons = [e.payload["reason"] for e in events if e.type == "derby.boost"]
    assert "recover" in reasons, reasons
    first_hit = next(i for i, e in enumerate(events) if e.type == "derby.hit")
    clear_before_hit = [
        e for e in events[:first_hit] if e.type == "derby.boost" and e.payload["reason"] == "clear"
    ]
    assert len(clear_before_hit) <= farmer.cfg.boosts_per_race - farmer.cfg.boost_reserve


def test_an_early_lead_presses_once_per_band(tmp_path: Path) -> None:
    """13:43 live run: with William's ~400 px leads the band was still ahead of the hooves when
    the cooldown ran out, so it was pressed for again (a tap train that re-jumped on landing)."""
    world = synth.DerbyWorld(seed=1, race_len_px=14000)
    world.bands = [synth.Band("hay", 3000.0 + i * 2000, 350) for i in range(5)]
    prof = _profile()
    for key, value in (("jump_lead_px_hay_1col", 400), ("jump_lead_ref_px_s", 830)):
        prof.fields[key] = ProfileField(type="number", value=value, status="calibrated")
    events: list[Any] = []
    farmer = _farmer(world, prof, tmp_path, events)
    farmer.cfg.boosts_per_race = 0
    farmer.run(lambda: farmer.stats.races >= 1)
    presses = [k for _t, k in world.keys if k == "space"]
    assert world.hits == 0 and world.jumps == 5, (world.hits, world.jumps)
    assert len(presses) <= 5 + farmer.stats.as_dict().get("retries", 0), presses
    assert not [e for e in events if e.type == "derby.rejump"]


def test_race_clock_starts_at_go_and_stumbles_are_judged_by_touchdown(tmp_path: Path) -> None:
    from jamcrammer.modes.arcade.derby import RaceResult
    from jamcrammer.modes.arcade.derby_learn import BandRecord

    assert DerbyFarmer._race_t(25.0, RaceResult(started_ts=0.0, go_ts=20.0)) == 5.0
    assert DerbyFarmer._race_t(25.0, RaceResult(started_ts=0.0)) == 25.0
    prof = _profile()
    prof.fields["jump_lead_px_mud_1col"] = ProfileField(
        type="number", value=350, status="calibrated"
    )
    farmer = _farmer(synth.DerbyWorld(seed=1), prof, tmp_path, [])
    farmer.airtime_s = 0.9
    rec = BandRecord(10.4, "mud", 330, "1col", 1100, 10.0, 845.0, False, outcome="hit")
    assert farmer._verdict(rec, 10.5) == "late"  # stalled while it should still be in the air
    assert farmer._verdict(rec, 11.0) == "early"  # stalled after touchdown: landed in the band
    rec.kind = "water"  # no per-class lead: the offset rule decides
    assert farmer._verdict(rec, 11.0) is None


def _class_lead(prof: Profile, **leads: float) -> None:
    for key, value in {**leads, "jump_lead_ref_px_s": 830}.items():
        name = key if key.startswith("jump_") else f"jump_lead_px_{key}"
        prof.fields[name] = ProfileField(type="number", value=value, status="calibrated")


def test_a_band_running_off_the_strip_gets_its_full_width_class(tmp_path: Path) -> None:
    """Live 2026-10-29: 3-column hedges (815 px) showed 680-770 px at the press, still running
    past the strip's right edge, and got the 2-column lead: 37% of them landed in the band."""
    from jamcrammer.modes.arcade.derby_learn import BandRecord

    farmer = _farmer(synth.DerbyWorld(seed=1), _profile(), tmp_path, [])
    sx, _sy, sw, _sh = synth.STRIP_ROI
    edge = sx + sw - 10  # visible right end, near the strip's edge
    hedge = Band("hedge", edge - 730, edge)
    assert farmer._wclass(Band("hedge", 900, 1630)) == "2col"  # whole: as measured
    assert farmer._wclass(hedge) == "3col"  # no records yet: 640 px per two columns
    hay = Band("hay", edge - 356, edge)  # a 372 px single column with its end cut off
    assert farmer._wclass(hay) == "1col"
    for kind, wclass, width in (("hedge", "2col", 505), ("hedge", "3col", 815)):
        for _ in range(3):
            rec = BandRecord(0.0, kind, width, wclass, None, None, 830.0, False, outcome="cleared")
            farmer.learned.history.append(rec.as_dict())
    assert farmer._wclass(Band("hedge", edge - 560, edge)) == "3col"  # 505 px cannot cover it
    assert farmer._wclass(Band("hedge", edge - 520, edge)) == "2col"


def test_a_stumble_on_the_next_band_is_not_blamed_on_the_one_before(tmp_path: Path) -> None:
    """Live 2026-10-29: the first band of a pair was still on record when the horse stumbled
    on the second, so the stall was charged to the first as "landed in it" and its lead moved
    later every time (single-column leads drifted 150 px late)."""
    world = synth.DerbyWorld(seed=1, race_len_px=9000)
    world.bands = [synth.Band("hay", 3000.0, 350), synth.Band("mud", 3000.0 + 350 + 400, 316)]
    prof = _profile()
    _class_lead(prof, hay_1col=60, mud_1col=-400)  # the mud band is never pressed for
    events: list[Any] = []
    farmer = _farmer(world, prof, tmp_path, events)
    farmer.cfg.boosts_per_race = 0
    farmer.run(lambda: farmer.stats.races >= 1)
    hits = [e.payload for e in events if e.type == "derby.hit"]
    assert world.hits == 1 and [h["kind"] for h in hits] == ["mud"], hits
    assert hits[0]["press_edge"] is None
    assert farmer.learned.lead_for("hay", "1col") == 0.0
    judged = [(r["kind"], r["outcome"]) for r in farmer.learned.history]
    assert judged == [("hay", "cleared"), ("mud", "hit")], judged


def test_a_landing_bound_stumble_moves_the_band_before_it_earlier(tmp_path: Path) -> None:
    """William: in a tight pair, jump the single column early so the horse lands in time for
    the next. A stumble on a band jumped by tapping through a landing moves the tight lead of
    the band whose landing that was."""
    from jamcrammer.modes.arcade.derby_learn import BandRecord, LearnedDerby

    cfg = DerbyConfig()
    learned = LearnedDerby(path=tmp_path / "l.json")
    first = BandRecord(10.0, "mud", 328, "1col", 1150, 9.6, 845.0, False, outcome="cleared")
    first.tight = True
    second = BandRecord(10.9, "hedge", 225, "1col", 900, 10.6, 845.0, False, outcome="hit")
    second.rejump = True
    note = learned.note(second, cfg, 785, ((0, 0, 0), (1, 1, 1)), prev=first)
    assert note.startswith("landing-bound: lead tight:mud:1col +15px")
    assert learned.tight_lead_for("mud", "1col") == cfg.learn_lead_step_px
    assert learned.lead_for("mud", "1col") == 0.0 and learned.lead_for("hedge", "1col") == 0.0
    # the tight lead is added only when the next band is close
    prof = _profile()
    _class_lead(prof, mud_1col=300)
    farmer = _farmer(synth.DerbyWorld(seed=1), prof, tmp_path, [])
    farmer.learned = learned
    farmer._speed = 830.0
    band = Band("mud", 1100, 1428)
    close = Band("hedge", band.right + 200, band.right + 425)
    alone = farmer._lead_for(band, 0.0)[0]
    assert alone == 300.0
    tight = farmer._lead_for(band, 0.0, close)[0]
    assert tight == 300.0 + farmer.cfg.tight_extra_px + cfg.learn_lead_step_px


def test_leads_from_the_old_learner_are_archived_and_dropped(tmp_path: Path) -> None:
    import json

    from jamcrammer.modes.arcade.derby_learn import LearnedDerby

    path = tmp_path / "derby_learned.json"
    doc = {
        "lead_px": {"hedge:3col": -160.0, "mud:1col": -150.0},
        "ranges": {"hedge": [[40, 90, 85], [66, 255, 172]]},
        "airtime_s": 1.02,
        "history": [{"kind": "hedge", "width": 815, "wclass": "3col"}],
    }
    path.write_text(json.dumps(doc), encoding="utf-8")
    learned = LearnedDerby.load(path)
    assert learned.lead_px == {} and "hedge:3col -160" in learned.reset_note
    assert learned.ranges == doc["ranges"] and learned.airtime_s == 1.02
    assert len(learned.history) == 1
    archived = list(tmp_path.glob("derby_learned_v1_*.json"))
    assert len(archived) == 1 and json.loads(archived[0].read_text())["lead_px"] == doc["lead_px"]
    again = LearnedDerby.load(path)  # saved as the new version: nothing to drop twice
    assert again.reset_note == "" and len(list(tmp_path.glob("derby_learned_v1_*.json"))) == 1


def test_only_a_plain_press_times_the_air(tmp_path: Path) -> None:
    farmer = _farmer(synth.DerbyWorld(seed=1), _profile(), tmp_path, [])
    farmer.airtime_s = 1.0
    farmer._last_jump = 10.0
    farmer._air_sample = False  # a train's predicted takeoff, or a retry
    farmer._on_landed(10.5)
    assert farmer.airtime_s == 1.0
    farmer._air_sample = True
    farmer._on_landed(11.2)
    assert farmer.airtime_s > 1.0 and not farmer._air_sample
