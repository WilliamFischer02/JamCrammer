"""Application config (pydantic), loaded from config.yaml with config.example.yaml as fallback.

Every tunable number in the app lives here or in a mode profile. Code never hard-codes them.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from jamcrammer.core.paths import Paths

Direction = Literal["left", "right", "up", "down"]


class Level(StrEnum):
    """Event / notification levels (KICKOFF §7.5)."""

    INFO = "INFO"  # log only
    NOTICE = "NOTICE"  # toast
    HIGH = "HIGH"  # toast + repeating sound + push

    @property
    def rank(self) -> int:
        return {Level.INFO: 0, Level.NOTICE: 1, Level.HIGH: 2}[self]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AntiAfkConfig(StrictModel):
    tap_ms: int = Field(200, ge=20, le=2000)
    gap_ms: int = Field(300, ge=0, le=5000)
    settle_ms: int = Field(400, ge=0, le=5000)
    pattern: list[tuple[Direction, Direction]] = [
        ("left", "right"),
        ("up", "down"),
        ("right", "left"),
        ("down", "up"),
    ]


class NotifyLevels(StrictModel):
    toast: Level = Level.NOTICE
    sound: Level = Level.HIGH
    push: Level = Level.HIGH


class NotifyConfig(StrictModel):
    toast: bool = True
    sound: bool = True
    ntfy_topic: str = ""
    ntfy_server: str = "https://ntfy.sh"
    discord_webhook: str = ""
    levels: NotifyLevels = NotifyLevels()
    sound_repeats: int = Field(3, ge=1, le=20)
    sound_interval_s: float = Field(1.5, ge=0.1, le=30)
    timeout_s: float = Field(10.0, gt=0)


class WatchdogConfig(StrictModel):
    fps: float = Field(2.0, gt=0, le=30)
    clear_timeout_s: float = Field(3.0, gt=0)
    ocr_interval_s: float = Field(5.0, gt=0)
    max_retries: int = Field(1, ge=0, le=5)
    unknown_cooldown_s: float = Field(60.0, ge=0)
    unknown_min_ticks: int = Field(3, ge=1)
    baseline_refresh_s: float = Field(30.0, gt=0)


class DriftConfig(StrictModel):
    threshold: float = Field(18.0, gt=0, le=255)
    downscale: tuple[int, int] = (64, 36)


class DoubleUpConfig(StrictModel):
    preview_s: float = Field(3.0, gt=0)
    burst_fps: float = Field(10.0, gt=0, le=60)
    pair_confidence_min: float = Field(0.6, ge=0, le=1)
    flip_ms: int = Field(400, ge=0)
    settle_s: float = Field(0.15, ge=0)
    click_gap_s: float = Field(0.08, ge=0)
    poll_s: float = Field(0.25, gt=0)
    board_timeout_s: float = Field(20.0, gt=0)
    hide_timeout_s: float = Field(5.0, gt=0)
    end_timeout_s: float = Field(10.0, gt=0)
    change_threshold: float = Field(18.0, gt=0)
    same_face_distance: float = Field(0.35, gt=0)
    preview_diversity: float = Field(0.5, gt=0)
    hidden_uniformity: float = Field(0.25, gt=0)
    tile_min_std: float = Field(8.0, ge=0)
    end_screen_keywords: list[str] = ["earned", "gems", "play again"]


class BestGuessConfig(StrictModel):
    fps: float = Field(5.0, gt=0, le=30)
    commit_after_s: float = Field(3.0, ge=0)
    lock_seconds: int = Field(5, ge=0)
    verify_after_s: float = Field(1.0, ge=0)
    heartbeat_s: float = Field(180.0, gt=0)
    unknown_pause_s: float = Field(30.0, gt=0)
    fact_min_chars: int = Field(8, ge=1)
    question_s: float = Field(10.0, gt=0)
    waiting_keywords: list[str] = ["get ready", "next round", "waiting", "starting"]
    ocr_upscale: float = Field(2.0, ge=1.0)
    reveal_min_fill: float = Field(0.03, ge=0, le=1)
    reveal_ratio: float = Field(3.0, ge=1)
    score_upscale: float = Field(3.0, ge=1.0)
    # the parchment banner must cover this much of fact_roi, else we are not in the arena
    banner_min_parchment: float = Field(0.3, ge=0, le=1)
    parchment_hsv: tuple[tuple[int, int, int], tuple[int, int, int]] = ((0, 0, 200), (180, 60, 255))
    # the gem-stack icon in arena_anchor_roi is ~58% saturated colour in the arena, <8% elsewhere
    anchor_min_fill: float = Field(0.25, ge=0, le=1)
    # movement: a held arrow key walks the animal to the wall of that side (never clicks a player)
    move_method: Literal["keys", "click"] = "keys"
    move_hold_s: float = Field(3.0, gt=0)
    move_verify_after_s: float = Field(0.8, ge=0)
    own_name: str = "Bingusthewizard"
    own_tag_upscale: float = Field(1.5, ge=1.0)
    own_tag_fuzz: int = Field(80, ge=50, le=100)
    # crowd heuristic: white name tags per half of the floor
    crowd_min_ratio: float = Field(1.25, ge=1.0)
    # the floor itself: lime green left (H34-40 S~190), purple right (H100-130); the rest is crowd
    green_floor_hsv: tuple[tuple[int, int, int], tuple[int, int, int]] = (
        (28, 90, 60),
        (62, 255, 255),
    )
    purple_floor_hsv: tuple[tuple[int, int, int], tuple[int, int, int]] = (
        (95, 35, 40),
        (165, 255, 255),
    )
    tag_max_sat: int = Field(40, ge=0, le=255)
    tag_min_val: int = Field(215, ge=0, le=255)
    tag_min_w: int = Field(120, ge=1)
    tag_max_w: int = Field(420, ge=1)
    tag_min_h: int = Field(22, ge=1)
    tag_max_h: int = Field(60, ge=1)
    anchor_hsv: tuple[tuple[int, int, int], tuple[int, int, int]] = ((0, 120, 120), (180, 255, 255))
    dance_motion_min: float = Field(12.0, ge=0)
    # measured on the 2026-10-26 recording: True! green H40-50 V~217, False! orange H0-10 V~194,
    # question text brown H10-25 but V~95 (the V floor keeps it out)
    true_hsv: tuple[tuple[int, int, int], tuple[int, int, int]] = ((36, 110, 150), (58, 255, 255))
    false_hsv: tuple[tuple[int, int, int], tuple[int, int, int]] = ((0, 120, 150), (14, 255, 255))


class PlannerConfig(StrictModel):
    """Den -> Jamaa Derby until Best Guess is due -> Best Guess (KICKOFF 9.4 variant)."""

    demo: bool = True  # show the party menu / tray round trip before farming
    leave_lead_s: float = Field(90.0, ge=0)  # leave the Derby this long before Best Guess is due
    eta_default_min: int = Field(45, ge=0)  # when neither the tray nor the user gave a time
    tray_settle_s: float = Field(1.2, ge=0)
    panel_settle_s: float = Field(1.0, ge=0)
    games_settle_s: float = Field(1.5, ge=0)
    den_timeout_s: float = Field(20.0, gt=0)
    derby_timeout_s: float = Field(20.0, gt=0)
    hunt_interval_s: float = Field(20.0, gt=0)  # My Parties re-check cadence once Best Guess is due
    max_attempts: int = Field(2, ge=1)


class RouteConfig(StrictModel):
    """Route farmer (modes/adventures/replay.py): replays a recorded adventure run and keeps it
    on course with the minimap dot, scene alignment and click-target matching."""

    target: str = "mystery_below"  # Learn target whose recorded run is the route
    session: str = ""  # a session folder name; "" = the longest finished run of the target
    tick_s: float = Field(0.05, gt=0)  # control loop period
    # building the route
    patch_px: int = Field(120, ge=16)  # anchor patch around every click
    thumb_w: int = Field(64, ge=8)  # localisation thumbnails
    thumb_h: int = Field(36, ge=8)
    align_scale: float = Field(0.25, gt=0, le=1)  # scene alignment works on this downscale
    highpass_sigma: float = Field(6.0, gt=0)  # removes the cave's smooth darkness vignette
    repeat_px: int = Field(40, ge=0)  # clicks this close ...
    repeat_window_s: float = Field(3.0, gt=0)  # ... and this close in time are one repeat group
    post_frame_s: float = Field(0.8, ge=0)  # the frame this long after a group shows its result
    objective_fuzzy: int = Field(80, ge=0, le=100)  # banner text equality
    objective_min_hits: int = Field(2, ge=1)  # OCR snapshots that must agree on a new banner
    # clicks
    patch_radius_px: int = Field(180, ge=0)  # search for a click's patch this far around
    patch_min_score: float = Field(0.6, ge=0, le=1)
    patch_accept_px: int = Field(60, ge=0)  # a match further than this from the predicted
    # point is not trusted (measured: 38% of recorded targets re-found within 40 px)
    click_wait_s: float = Field(3.0, ge=0)  # wait this long for a target to appear
    item_check_s: float = Field(1.0, ge=0)  # an item pickup / use must show within this
    item_change_min: float = Field(6.0, ge=0)  # mean abs change of the item slots that counts
    items_still_px: float = Field(30.0, ge=0)  # ... measured only while the view held still
    item_retries: int = Field(2, ge=0)
    repeat_extra_max: int = Field(4, ge=0)  # extra clicks on a mirror / rock until it matches
    post_min_sim: float = Field(0.6, ge=-1, le=1)  # local similarity to the recorded result
    drag_steps: int = Field(12, ge=1)
    dialog_retries: int = Field(2, ge=0)
    # staying on course
    align_every_s: float = Field(0.5, gt=0)
    scene_min_response: float = Field(0.06, ge=0)  # phase correlation peak that counts
    drift_scene_px: int = Field(90, ge=0)  # scene offset that needs a correction
    drift_dot_px: float = Field(2.5, ge=0)  # minimap dot distance for a gross correction
    # (1 minimap px is ~70 scene px on The Mystery Below; the scene does the fine work)
    warp_window_s: float = Field(1.5, ge=0)  # timing drift: search this far for the frame
    servo_tol_scene_px: int = Field(40, ge=0)
    servo_tol_dot_px: float = Field(1.5, ge=0)
    servo_burst_min_s: float = Field(0.12, gt=0)
    servo_burst_max_s: float = Field(0.6, gt=0)
    servo_timeout_s: float = Field(12.0, gt=0)
    servo_no_progress: int = Field(3, ge=1)  # bursts without progress before a sidestep
    walk_px_s: float = Field(260.0, gt=0)  # scene speed while walking (route estimate wins)
    dot_px_s: float = Field(6.0, gt=0)  # minimap dot speed while walking (route estimate wins)
    stuck_window_s: float = Field(0.9, gt=0)  # a held key and no motion this long = stuck
    stuck_min_px: float = Field(6.0, ge=0)  # scene motion below this = not moving
    stuck_dot_px: float = Field(1.5, ge=0)  # dot motion below this = not moving
    lookahead_s: float = Field(1.0, ge=0)  # recover toward where the route is this soon
    fix_match_s: float = Field(4.0, ge=0)  # a stored fix applies within this of its route time
    # sync and help
    objective_every_s: float = Field(2.0, gt=0)
    behind_grace_s: float = Field(45.0, ge=0)  # banner still old this long past its change
    retry_back_s: float = Field(60.0, ge=0)  # a segment retry starts this far before the change
    segment_retries: int = Field(2, ge=0)  # then it asks for help
    resync_window_s: float = Field(120.0, ge=0)  # localise within this of the route time
    resync_min_sim: float = Field(0.55, ge=-1, le=1)
    start_min_sim: float = Field(0.6, ge=-1, le=1)  # start mid-route only on a clear match
    start_look_s: float = Field(1.0, ge=0)  # look this long for the minimap dot at Start:
    # none means we are outside the adventure (a den, a room), so the route starts from the top
    dot_weight: float = Field(0.02, ge=0)  # localisation: score lost per minimap px of distance
    resume_idle_s: float = Field(2.5, gt=0)  # William's hands off this long: continue
    scene_hold_sim: float = Field(0.2, ge=-1, le=1)  # live vs recorded thumbnail below this:
    # the scene is not there yet (loading, the drop to the next level): hold the route clock
    scene_hold_max_s: float = Field(45.0, gt=0)  # then re-localise, else ask for help
    # after William steers: look a little behind the route clock and up to a minute ahead,
    # the minimap dot first (measured 60-70% right; a full-route search was 35%)
    resume_back_s: float = Field(10.0, ge=0)
    resume_ahead_s: float = Field(60.0, ge=0)
    resume_time_scale_s: float = Field(20.0, gt=0)  # one dot px of score per this many seconds
    resume_dot_cap: float = Field(20.0, gt=0)  # dot distances beyond this all count the same
    resume_dot_px: float = Field(1.5, ge=0)  # a dot this close makes the resume point sure
    supervised: bool = False  # William watching: run although the replay gate is locked
    loop: bool = False  # start the route again after it ends (switch animals by hand)


class LevelConfig(StrictModel):
    """Level model farmer (modes/adventures/level.py, level_runner.py): the recorded runs become
    a map in minimap coordinates (floor, crystals, fungi, the door) and the bot plans on it:
    menus by state, the moth chamber by spots, the rest by route replay for now."""

    enabled: bool = True
    sessions: list[str] = []  # Learn session folders the level is built from; [] = every
    # finished run of the target (the newest is the reference plan)
    spot_merge_px: float = Field(5.0, ge=0)  # events this close on the minimap are one spot
    floor_min_v: int = Field(28, ge=0, le=255)  # minimap pixels at least this bright are floor
    floor_min_frames: int = Field(3, ge=1)  # ... in at least this many sampled frames (a zoomed
    # minimap or a flash is bright everywhere for a moment)
    floor_dilate_px: int = Field(1, ge=0)  # the recorded dot track widens the floor by this
    use_click_window_s: float = Field(2.5, ge=0)  # a counter change this soon after a world
    # click: that click did it (its target is the crystal / fungus)
    # navigation on the floor mask
    nav_tol_px: float = Field(2.0, gt=0)  # arrived when the dot is this close (minimap px)
    nav_lookahead_px: float = Field(4.0, gt=0)  # steer at the path point this far ahead
    nav_stuck_s: float = Field(1.0, gt=0)  # keys held and the dot still this long: blocked
    nav_stuck_px: float = Field(1.0, ge=0)
    nav_timeout_s: float = Field(60.0, gt=0)
    nav_max_blocks: int = Field(8, ge=0)  # blocked cells learned in one go_to before giving up
    nav_sidestep_s: float = Field(0.5, gt=0)  # stuck: step across the heading this long
    # first (a crystal, a rock, an NPC in the way), then learn a wall
    screen_walk_px: int = Field(900, ge=0)  # a target blob this close to the animal is
    # walked at by screen, no minimap plan (crystal to crystal along a path)
    nav_dot_wait_s: float = Field(4.0, gt=0)  # no dot (a dialog, loading) this long: fail
    nav_snap_px: int = Field(8, ge=0)  # a target off the floor snaps to floor this close
    # (no path at all: walk straight at the target and let the walls be learned)
    # acting on what is on screen
    reach_px: int = Field(420, ge=0)  # a blob this close to the animal can be clicked
    act_verify_s: float = Field(2.5, gt=0)  # the item counter must change within this
    act_retries: int = Field(2, ge=0)
    approach_px: float = Field(3.0, gt=0)  # go this close (minimap px) to a spot, then look
    walk_toward_s: float = Field(1.2, gt=0)  # a blob out of reach: walk at it this long
    pin_near_px: int = Field(300, ge=0)  # the paw pin shows once the fungus is this close
    pin_stop_px: int = Field(50, ge=0)  # ... approach the fungus to this close, no further
    pin_looks: int = Field(8, ge=1)  # looks (approach a little, look again) for the pin
    pin_search_px: int = Field(450, ge=0)  # a pin this close to the animal is the one
    # lighting a crystal, for certain
    stock_read_s: float = Field(1.5, gt=0)  # read the counter this long for a number
    drop_reach_px: int = Field(260, ge=0)  # walk up until the crystal is this close
    lit_verify_s: float = Field(4.0, gt=0)  # the lit signal must show within this
    glow_new_px: int = Field(3000, ge=0)  # newly bright pixels near the crystal = lit
    glow_radius_px: int = Field(200, ge=0)
    courage_radius_px: int = Field(350, ge=0)  # the Courage text pops this close
    unlit_wait_s: float = Field(8.0, ge=0)  # the next crystal turns unlit as the moth comes
    light_attempts: int = Field(3, ge=1)  # drops at one crystal before asking for help
    pred_tol_px: int = Field(600, ge=0)  # a target must be this close to where the map
    # says the spot is (spots are good to 3-5 minimap px, 210-350 scene px)
    phase_wait_s: float = Field(90.0, gt=0)  # an act waits this long for its banner phase
    act_lead_s: float = Field(3.0, ge=0)  # an act's phase is the banner this long before its
    # click: the walk to it began then (the pick banner itself only comes as you reach the
    # fungus, so the first act must not wait for it)
    # (the door after the first moth, the next room) before asking for help
    smash_min_clicks: int = Field(4, ge=1)  # repeated clicks in the recording that count as
    # a smash (a cracked rock, a purple crystal; fewer is a walk-click)
    smash_clicks: int = Field(18, ge=1)  # clicks on a rock / purple crystal at most
    smash_gap_s: float = Field(0.25, gt=0)
    smash_quiet: int = Field(3, ge=1)  # clicks without a Courage pop after a hit: it is gone
    smash_probe: int = Field(6, ge=1)  # clicks without any Courage pop: nothing there
    bad_drop_px: int = Field(120, ge=0)  # a drop that left the crystal dark: no target
    # this close to it again in this act (it was Cosmo, a decoy, the floor)
    room_hold_s: float = Field(1.5, gt=0)  # a room must read as one this long before the
    # globe is clicked (a panel sliding away reads as a room for a moment)
    list_clicks: int = Field(3, ge=1)  # scroll-arrow clicks per look at the adventure list
    menu_settle_s: float = Field(0.8, ge=0)  # after a menu click, before looking again
    look_retries: int = Field(3, ge=0)  # looks (walk, look again) for the crystal at a spot
    inventory_every_s: float = Field(1.0, gt=0)
    banner_every_s: float = Field(2.0, gt=0)
    resume_idle_s: float = Field(2.5, gt=0)  # William's hands off this long: continue
    # the menus
    menu_step_s: float = Field(4.0, gt=0)  # each menu click must show its panel within this
    menu_retries: int = Field(2, ge=0)
    load_max_s: float = Field(60.0, gt=0)  # PLAY to the first banner
    join: bool = True  # Join an Adventure (as William recorded); False: Host
    hard: bool = True  # Hard is required: the second half of the level needs it (William,
    # 2026-10-30); Normal is only here for a deliberate experiment


class WorldConfig(StrictModel):
    """The world map of an adventure (modes/adventures/world.py, docs/MAPPING.md): one
    occupancy grid and one scene mosaic per area, in scene px anchored to the minimap, plus
    the things in it; the localiser (localize.py) fuses the minimap dot with scene
    odometry; the pilot's probes find walls by walking into them."""

    grid_px: int = Field(35, ge=5)  # scene px per occupancy cell (half a minimap px)
    mosaic_scale: float = Field(0.2, gt=0, le=1)  # the mosaic is kept at this downscale
    vignette_px: int = Field(480, ge=0)  # the lit disc around the animal that is pasted in
    mosaic_min_v: int = Field(24, ge=0, le=255)  # darker pixels never overwrite the mosaic
    merge_px: float = Field(120.0, ge=0)  # observations of one kind this close are one object
    # odometry: phase correlation between consecutive frames (sight.scene_shift)
    odom_scale: float = Field(0.25, gt=0, le=1)
    odom_sigma: float = Field(6.0, gt=0)
    odom_min_response: float = Field(0.05, ge=0)  # a weaker peak is no measurement
    odom_max_px_s: float = Field(700.0, gt=0)  # faster than the animal walks: a bad peak
    # the minimap dot: absolute, quantised to a pixel (~70 scene px), a little behind
    dot_gain: float = Field(0.15, ge=0, le=1)  # pull toward the dot per update
    dot_reset_px: float = Field(250.0, ge=0)  # this far off the dot ...
    dot_reset_n: int = Field(4, ge=1)  # ... this many updates in a row: the dot wins outright
    dot_rejoin_px: float = Field(400.0, ge=0)  # between commands, a dot this far from the
    # estimate means someone moved the animal: start from the dot again (the dot lags a
    # walk by up to a second, ~400 px)
    # probing: hold a direction until the scene stops moving
    probe_tick_s: float = Field(0.1, gt=0)
    probe_max_s: float = Field(2.5, gt=0)
    stuck_px: float = Field(12.0, ge=0)  # moved less than this in a tick while a key is held
    stuck_ticks: int = Field(3, ge=1)  # ... this many ticks running: a collision
    unknown_cost: float = Field(3.0, ge=1)  # A* cost of an unknown cell (floor 1, walked 0.8)


class AdventureConfig(StrictModel):
    """Adventure bots (The Forgotten Desert, The Mystery Below): invite bubbles in Jamaa rooms."""

    invite_keywords: list[str] = ["forgotten desert"]  # adventure names to join
    invite_join_words: list[str] = ["join", "join me"]  # the bubble's clickable header
    invite_fuzzy: int = Field(75, ge=0, le=100)  # partial_ratio for (occluded) name fragments
    invite_min_chars: int = Field(4, ge=1)  # shorter OCR fragments never count as a name match
    invite_line_gap: float = Field(1.5, ge=0)  # lines of one bubble are at most this far apart
    invite_crop_up: float = Field(3.0, ge=0)  # look this many name-line heights above for "Join"
    invite_crop_side: float = Field(4.0, ge=0)  # ... and this many to each side
    invite_upscale: float = Field(2.0, gt=0)  # OCR upscale of the bubble crop
    route: RouteConfig = RouteConfig()
    level: LevelConfig = LevelConfig()
    world: WorldConfig = WorldConfig()


class OpportunistConfig(StrictModel):
    entry_timeout_s: float = Field(8.0, gt=0)
    exit_timeout_s: float = Field(5.0, gt=0)
    settle_s: float = Field(0.5, ge=0)
    poll_s: float = Field(0.25, gt=0)
    max_entry_failures: int = Field(2, ge=1)


class DerbyConfig(StrictModel):
    fps: float = Field(20.0, gt=0, le=60)
    jump_key: str = "space"
    jump_hold_ms: int = Field(40, ge=1)
    boosts_per_race: int = Field(3, ge=0)
    boost_gap_s: float = Field(4.0, ge=0)
    boost_after_jump_s: float = Field(0.6, ge=0)
    boost_earliest_s: float = Field(2.0, ge=0)
    band_min_width_px: int = Field(40, ge=1)
    band_fill: float = Field(0.5, gt=0, le=1)
    # dark green: tall grass columns fill 0.5-0.9 (stripes), hedge ~0.9, checkered finish <= 0.66
    hedge_seed_fill: float = Field(0.62, gt=0, le=1)
    hedge_grow_fill: float = Field(0.4, gt=0, le=1)
    hedge_min_width_px: int = Field(150, ge=1)  # checker columns are ~130 px
    hedge_mean_fill: float = Field(0.55, gt=0, le=1)  # checker runs average ~0.45
    band_pass_px: int = Field(120, ge=0)  # a band this far past the hooves is behind us
    band_merge_gap_px: int = Field(300, ge=0)  # same-kind pieces closer than this are one band
    speed_alpha: float = Field(0.3, gt=0, le=1)
    speed_max_dt_s: float = Field(0.3, gt=0)
    speed_max_step_px: int = Field(400, ge=1)
    speed_min_baseline_s: float = Field(0.1, gt=0)  # measure speed over at least this long
    speed_min_px_s: float = Field(700.0, gt=0)
    speed_max_px_s: float = Field(1800.0, gt=0)
    track_hold_s: float = Field(0.4, ge=0)  # predict a hidden band's edge this long
    track_stuck_px: int = Field(30, ge=0)  # observed edge this far right of the prediction = hidden
    track_new_band_px: int = Field(150, ge=0)
    jump_retry_s: float = Field(0.25, ge=0)
    jump_retry_min_obs: int = Field(2, ge=1)  # fresh frames needed before judging a press
    period_alpha: float = Field(0.2, gt=0, le=1)
    period_max_s: float = Field(0.5, gt=0)
    jump_rise_px: int = Field(20, ge=1)
    results_settle_s: float = Field(1.0, ge=0)
    unknown_pause_s: float = Field(30.0, gt=0)
    ocr_upscale: float = Field(2.0, ge=1.0)
    column_px: int = Field(320, ge=1)  # one obstacle "column"; bands are 1, 2 or 3 columns
    # a band ending this close to the strip's right edge may run past it (live 2026-10-29:
    # 3-column hedges ended at x 1750-1807 at the press, 680-770 of their 815 px visible) ...
    clip_margin_px: int = Field(80, ge=0)
    clip_tolerance_px: int = Field(40, ge=0)  # ... and is the narrowest class this close
    boost_speed_ratio: float = Field(1.15, gt=0)  # scroll faster than this x nominal = boosted
    boost_effect_s: float = Field(3.0, ge=0)  # ... or a carrot was clicked this recently
    press_match_s: float = Field(
        1.2, ge=0
    )  # a press this long before the edge passes belongs to it
    hit_speed_ratio: float = Field(
        0.5, gt=0, le=1
    )  # passing band slower than this x nominal = stall
    hit_min_obs: int = Field(2, ge=1)
    hit_window_s: float = Field(1.3, gt=0)
    learn_enabled: bool = True
    # Tapping through a landing (William holds Space so the next jump fires on touchdown)
    rejump_hold_ms: int = Field(12, ge=1)  # each tap of the landing train
    rejump_after_land_s: float = Field(0.15, ge=0)  # keep tapping past the predicted landing
    rejump_max_s: float = Field(0.9, gt=0)  # never longer: no accidental second jump
    airtime_default_s: float = Field(0.8, gt=0)  # press to landing until one is measured
    airtime_min_s: float = Field(0.4, gt=0)  # plausible measured airtimes ...
    airtime_max_s: float = Field(1.6, gt=0)  # ... anything else is a misread
    airtime_alpha: float = Field(0.2, gt=0, le=1)
    ground_alpha: float = Field(0.2, gt=0, le=1)  # the horse's ground row, smoothed
    # A one/two-column band with little ground before the next: jump it a bit earlier
    tight_gap_px: int = Field(400, ge=0)
    tight_extra_px: float = Field(40.0, ge=0)
    # Carrots kept back to get out of a stumble's slow zone (William's habit)
    boost_reserve: int = Field(1, ge=0)
    boost_reserve_release_s: float = Field(28.0, ge=0)  # unused by then: clear stretch
    boost_recover: bool = True
    same_band_px: int = Field(150, ge=0)  # a band this close to where our last press's
    # band should be now is that band: never pressed for twice (early leads outlast the
    # cooldown while the band is still ahead of the hooves)
    learn_air_margin_s: float = Field(0.1, ge=0)  # stumble this soon before touchdown = late
    learn_lead_step_px: float = Field(15.0, ge=0)
    learn_lead_max_px: float = Field(160.0, ge=0)
    learn_early_px: float = Field(120.0, ge=0)
    # colours measured on the 2026-10-26 recording (OpenCV HSV, H in 0..180)
    hay_hsv: tuple[tuple[int, int, int], tuple[int, int, int]] = ((18, 110, 150), (35, 255, 255))
    mud_hsv: tuple[tuple[int, int, int], tuple[int, int, int]] = ((6, 50, 40), (22, 255, 185))
    water_hsv: tuple[tuple[int, int, int], tuple[int, int, int]] = ((72, 60, 80), (105, 255, 255))
    hedge_hsv: tuple[tuple[int, int, int], tuple[int, int, int]] = ((40, 90, 85), (66, 255, 170))
    grass_hsv: tuple[tuple[int, int, int], tuple[int, int, int]] = ((40, 60, 150), (70, 255, 255))
    horse_box_max_fill: float = Field(
        0.9, gt=0, le=1
    )  # scenery share above which no horse is visible
    horse_min_px: int = Field(300, ge=1)
    beige_hsv: tuple[tuple[int, int, int], tuple[int, int, int]] = ((15, 0, 190), (35, 130, 255))
    results_min_fill: float = Field(0.35, gt=0, le=1)
    white_hsv: tuple[tuple[int, int, int], tuple[int, int, int]] = ((0, 0, 230), (180, 40, 255))
    startline_col_fill: float = Field(0.5, gt=0, le=1)
    startline_min_cols: float = Field(0.3, gt=0, le=1)
    progress_hsv: tuple[tuple[int, int, int], tuple[int, int, int]] = (
        (40, 90, 140),
        (70, 255, 255),
    )
    progress_min_fill: float = Field(0.4, gt=0, le=1)
    carrot_hsv: tuple[tuple[int, int, int], tuple[int, int, int]] = ((8, 150, 180), (22, 255, 255))
    carrot_min_fill: float = Field(0.05, gt=0, le=1)
    highlight_hsv: tuple[tuple[int, int, int], tuple[int, int, int]] = (
        (20, 150, 200),
        (35, 255, 255),
    )
    highlight_row_fill: float = Field(0.5, gt=0, le=1)  # the frame's long top/bottom lines
    highlight_min_rows: int = Field(6, ge=1)
    highlight_max_height_px: int = Field(90, ge=1)


class GemBallConfig(StrictModel):
    hold_min_ms: int = Field(60, ge=1)
    hold_max_ms: int = Field(1200, ge=1)
    hold_step_ms: int = Field(20, ge=5)
    repeats: int = Field(3, ge=1)
    min_repeats: int = Field(2, ge=1)
    exploit_rate: float = Field(0.7, ge=0, le=1)
    min_balls: int = Field(6, ge=1)
    settle_s: float = Field(4.0, ge=0)


class PilotConfig(StrictModel):
    """Claude at the controls (jamcrammer/pilot.py, docs/PILOT.md): input only when William
    has switched it on; clicks never on the chat field or the log-out button."""

    enabled: bool = False  # William's switch: pilot input allowed
    forbidden_rois: list[list[int]] = [[760, 990, 600, 90], [1840, 985, 80, 95]]  # chat, log out
    max_hold_s: float = Field(3.0, gt=0)  # a movement key held at most this long per command
    yield_s: float = Field(2.0, ge=0)  # William's own input this recent: his controls, refuse
    max_goto_s: float = Field(60.0, gt=0)  # one goto walks at most this long
    area: str = "moths"  # the level area whose floor goto plans on
    budget_min: float = Field(45.0, gt=0)  # a pilot session sends input at most this long
    serve_poll_s: float = Field(0.05, gt=0)  # the server looks for a command file this often
    serve_timeout_s: float = Field(90.0, gt=0)  # a client waits this long for the server's answer


class SwoopyConfig(StrictModel):
    fps: float = Field(60.0, gt=0, le=120)
    flap_key: str = "space"
    flap_hold_ms: int = Field(20, ge=1)
    kp: float = Field(1.5, ge=0)
    kd: float = Field(0.05, ge=0)
    flap_threshold: float = Field(25.0)
    target_offset_px: float = Field(0.0)
    min_flap_interval_s: float = Field(0.12, ge=0)
    default_target_y: float = Field(150.0)
    min_bird_px: int = Field(10, ge=1)
    bird_lead_px: int = Field(12, ge=0)
    pipe_col_min: float = Field(0.2, ge=0, le=1)
    pipe_probe_px: int = Field(6, ge=1)
    gap_reset_px: float = Field(60.0, ge=0)
    restart_s: float = Field(1.0, ge=0)


ArcadeGame = Literal["double_up", "jamaa_derby", "gem_ball", "swoopy_eagle"]


class AnalysisConfig(StrictModel):
    duplicate_distance: int = Field(2, ge=0, le=64)
    transition_distance: int = Field(14, ge=0, le=64)
    cluster_distance: float = Field(1.6, gt=0)
    min_cluster: int = Field(3, ge=1)
    samples_per_state: int = Field(4, ge=1, le=12)
    ocr: bool = True
    ocr_upscale: float = Field(1.5, ge=1.0, le=4.0)
    max_regions: int = Field(20, ge=1)


class HuntConfig(StrictModel):
    panel_timeout_s: float = Field(4.0, gt=0)
    panel_poll_s: float = Field(0.25, ge=0)
    panel_settle_s: float = Field(0.4, ge=0)
    close_timeout_s: float = Field(3.0, gt=0)
    join_retry_s: float = Field(30.0, gt=0)
    join_max_retries: int = Field(10, ge=0)
    arrival_timeout_s: float = Field(8.0, gt=0)
    sighting_merge_gap_s: float = Field(900.0, gt=0)
    antiafk_each_cycle: bool = True


class ActivityTestConfig(StrictModel):
    phase_minutes: float = Field(8.0, gt=0)
    movement_interval_s: float = Field(60.0, gt=0)
    panel_interval_s: float = Field(60.0, gt=0)


class HotkeyConfig(StrictModel):
    kill: str = "f12"
    pause: str = "f11"
    toggle_mode: str = "f9"
    learn_mark: str = "f6"
    learn_correct: str = "f7"
    learn_wrong: str = "f8"


class LearnConfig(StrictModel):
    keyframe_threshold: int = Field(12, ge=0, le=64)
    ring_fps: float = Field(5.0, gt=0, le=60)
    ring_window_s: float = Field(5.0, gt=0, le=60)
    heartbeat_s: float = Field(30.0, gt=0)
    jpeg_quality: int = Field(90, ge=10, le=100)
    phash_width: int = Field(256, ge=32, le=1920)
    ocr_keyframes: bool = True


KeyMethod = Literal["scancode", "vk"]


class InputConfig(StrictModel):
    click_hold_ms: int = Field(60, ge=1, le=1000)
    cursor_settle_ms: int = Field(30, ge=0, le=1000)
    key_method: KeyMethod = "scancode"
    postcondition_retries: int = Field(1, ge=0, le=5)
    foreground_timeout_s: float = Field(2.0, gt=0)
    history_len: int = Field(200, ge=10)


UnknownFactPolicy = Literal["crowd", "learned_prior", "fixed_true", "fixed_false", "skip"]
AnswerMode = Literal["learn_only", "bank_policy", "coin_flip"]
CaptureBackend = Literal["auto", "wgc", "mss"]


class AppConfig(StrictModel):
    """Top-level config. Field names mirror config.example.yaml exactly."""

    window_title_match: str = "Animal Jam Classic"
    window_process_name: str = "AJ Classic.exe"
    capture_backend: CaptureBackend = "auto"
    fps: float = Field(4.0, gt=0, le=60)

    hunt_interval_s: float = Field(300.0, gt=0)
    opportunist_check_s: float = Field(300.0, gt=0)
    resume_after_idle_s: float = Field(120.0, ge=0)
    heartbeat_s: float = Field(180.0, gt=0)

    antiafk: AntiAfkConfig = AntiAfkConfig()

    auto_join: bool = True
    start_farming_after_join: bool = True
    unknown_fact_policy: UnknownFactPolicy = "crowd"
    answer_mode: AnswerMode = "learn_only"
    fuzzy_threshold: int = Field(90, ge=0, le=100)

    notify: NotifyConfig = NotifyConfig()
    hotkeys: HotkeyConfig = HotkeyConfig()
    dry_run: bool = False
    presence_slack_s: float = Field(0.5, ge=0)
    input: InputConfig = InputConfig()
    learn: LearnConfig = LearnConfig()
    watchdog: WatchdogConfig = WatchdogConfig()
    drift: DriftConfig = DriftConfig()
    activity_test: ActivityTestConfig = ActivityTestConfig()
    hunt: HuntConfig = HuntConfig()
    analysis: AnalysisConfig = AnalysisConfig()
    double_up: DoubleUpConfig = DoubleUpConfig()
    bestguess: BestGuessConfig = BestGuessConfig()
    opportunist: OpportunistConfig = OpportunistConfig()
    planner: PlannerConfig = PlannerConfig()
    adventures: AdventureConfig = AdventureConfig()
    derby: DerbyConfig = DerbyConfig()
    gem_ball: GemBallConfig = GemBallConfig()
    swoopy: SwoopyConfig = SwoopyConfig()
    pilot: PilotConfig = PilotConfig()
    arcade_game: ArcadeGame = "double_up"

    @classmethod
    def from_yaml(cls, path: Path) -> AppConfig:
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        if not isinstance(raw, dict):
            msg = f"{path}: top level must be a mapping"
            raise ValueError(msg)
        return cls.model_validate(raw)


def load_config(paths: Paths | None = None, override: Path | None = None) -> AppConfig:
    """Load config.yaml, falling back to config.example.yaml, falling back to defaults."""
    paths = paths or Paths()
    candidates = [override] if override else [paths.config_file, paths.config_example]
    for candidate in candidates:
        if candidate is not None and candidate.exists():
            return AppConfig.from_yaml(candidate)
    return AppConfig()
