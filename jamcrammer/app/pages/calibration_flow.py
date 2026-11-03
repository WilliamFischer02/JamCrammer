"""The guided calibration flow, without Qt: which game screen each profile field lives on,
what to tell the user to do in the game, how a captured screen is checked, and the step model
the wizard walks (screen -> its fields, one at a time -> the numbers). Unit-tested on its own.

Captured screens are kept under ``captures/calibration/<profile>/<screen>.png`` and reused
until the user retakes them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
import numpy.typing as npt

from jamcrammer.core.paths import Paths
from jamcrammer.core.profiles import Profile, ProfileField

BgrImage = npt.NDArray[np.uint8]
CheckKind = Literal["confirm", "ocr", "template", "derby", "bestguess", "dialog_x", "minimap_dot"]
FieldState = Literal["calibrated", "proposed", "missing"]
StepKind = Literal["screen", "field", "numbers"]
Decision = Literal["confirmed", "skipped"]

CAPTURE_DIR = "calibration"
STAMP_FMT = "%Y-%m-%d %H:%M"
OCR_UPSCALE = 2.0
DEFAULT_SCALES = (1.0,)
ROI_DEFAULT_SIZE = (120, 48)  # a fresh ROI placed by a double-click


@dataclass(frozen=True)
class ScreenCheck:
    """A cheap test that the captured screen is the one asked for. ``profile`` names the
    profile holding the keys (empty = the wizard's own profile)."""

    kind: CheckKind
    expect: str = ""  # a word the OCR must read, or the Derby screen name
    roi_key: str = ""  # roi / fraction_box searched or read
    template_key: str = ""
    threshold_key: str = ""
    profile: str = ""


@dataclass(frozen=True)
class ScreenSpec:
    key: str
    title: str
    instruction: str
    fields: tuple[str, ...]
    check: ScreenCheck | None = None


@dataclass(frozen=True)
class CheckResult:
    ok: bool | None  # None = nothing cheap to test; ask the user
    text: str


@dataclass(frozen=True)
class Step:
    kind: StepKind
    screen: ScreenSpec
    field: str = ""  # field steps only
    fields: tuple[str, ...] = ()  # the screen's drawn fields (siblings) or the numbers


CONFIRM = ScreenCheck("confirm")

PROFILE_TITLES: dict[str, str] = {
    "parties": "My Parties",
    "navigation": "Navigation: den, games menu, party tray",
    "shared": "Shared HUD and popups",
    "bestguess": "Best Guess",
    "jamaa_derby": "Jamaa Derby",
    "double_up": "Double Up",
    "adventure": "Adventures (The Mystery Below)",
    "gem_ball": "Gem Ball",
    "swoopy_eagle": "Swoopy Eagle",
}

PROFILE_SCREENS: dict[str, tuple[ScreenSpec, ...]] = {
    "parties": (
        ScreenSpec(
            "room",
            "Any room, no panel open",
            "Stand in any room (your den is fine) with the full HUD visible and nothing open.",
            ("party_hat_point",),
        ),
        ScreenSpec(
            "my_parties",
            "My Parties panel",
            "Open My Parties: click the party hat (balloon) icon at the top of the HUD. Leave "
            "the panel open with its banners showing.",
            (
                "title_template",
                "title_search_roi",
                "close_x_point",
                "close_x_template",
                "go_now_template",
            ),
            ScreenCheck(
                "template",
                template_key="title_template",
                roi_key="title_search_roi",
                threshold_key="template_threshold",
            ),
        ),
    ),
    "navigation": (
        ScreenSpec(
            "den",
            "Your den",
            "Stand in your den: click the hut icon at the bottom right of the HUD and wait "
            'until the room title at the top right reads "...\'s Den".',
            ("den_button_point", "room_name_roi", "games_icon_point"),
            ScreenCheck("ocr", expect="den", roi_key="room_name_roi"),
        ),
        ScreenSpec(
            "games_menu",
            "Games menu, list at the top",
            "Click the controller icon in the top bar so the games menu is open, with its "
            "list scrolled to the very top.",
            (
                "games_close_point",
                "games_scroll_up_point",
                "games_scroll_down_point",
                "scroll_thumb_top_point",
            ),
        ),
        ScreenSpec(
            "games_menu_scrolled",
            "Games menu, Jamaa Derby in view",
            "Scroll the games list (the blue down arrow) until the Jamaa Derby tile is visible.",
            ("derby_tile_template", "derby_tile_fallback_point", "scroll_mid_point"),
            ScreenCheck(
                "template", template_key="derby_tile_template", threshold_key="template_threshold"
            ),
        ),
        ScreenSpec(
            "parties_tray",
            "Upcoming Parties tray",
            "Open My Parties from the party hat, then click the clock-and-hat button on the "
            "panel's right edge so the Upcoming Parties tray slides out.",
            ("tray_toggle_point", "tray_close_point", "tray_list_roi", "tray_banner_template"),
            ScreenCheck(
                "template",
                template_key="tray_banner_template",
                roi_key="tray_list_roi",
                threshold_key="template_threshold",
            ),
        ),
    ),
    "shared": (
        ScreenSpec(
            "room",
            "Any room with the HUD",
            "Stand in any room with the full HUD visible and no panel open.",
            (
                "hud_top_band",
                "hud_bottom_band",
                "animal_center_box",
                "party_hat_point",
                "gem_counter_roi",
                "popup_center_box",
            ),
        ),
        ScreenSpec(
            "still_playing",
            "'Are you still playing?' popup",
            "Leave the game untouched for about 6.5 minutes until the 'Are you still playing?' "
            "popup with its green button appears, then capture before clicking it (you have "
            "30 seconds). Skip this screen if you would rather not wait.",
            ("popup_green_hsv", "still_playing_template"),
            ScreenCheck("ocr", expect="still", roi_key="popup_center_box"),
        ),
        ScreenSpec(
            "logged_out",
            "'Logged out' dialog",
            "If the still-playing popup is left alone for 30 seconds the logged-out dialog "
            "follows. Capture it, then click OK.",
            ("logged_out_template",),
            ScreenCheck("ocr", expect="logged", roi_key="popup_center_box"),
        ),
        ScreenSpec(
            "play_now",
            "Play Now screen",
            "After the logged-out dialog's OK the Play Now screen shows. Capture it, then log "
            "back in.",
            ("play_now_template",),
            ScreenCheck("ocr", expect="play", roi_key="popup_center_box"),
        ),
    ),
    "bestguess": (
        ScreenSpec(
            "arena",
            "Best Guess arena, question open",
            "Join the Best Guess Game (My Parties, GO NOW on its banner) and capture while a "
            "fact is written on the parchment banner at the top.",
            (
                "fact_roi",
                "reveal_word_roi",
                "reveal_roi",
                "arena_anchor_roi",
                "score_roi",
                "multiplier_roi",
                "countdown_roi",
                "gem_counter_roi",
                "true_point",
                "false_point",
                "floor_roi",
                "own_animal_roi",
            ),
            ScreenCheck("bestguess"),
        ),
        ScreenSpec(
            "round_complete",
            "ROUND COMPLETE scroll",
            "Stay in the arena until the round ends and the ROUND COMPLETE scroll shows the "
            "correct count and the gems earned.",
            ("round_complete_roi",),
        ),
    ),
    "jamaa_derby": (
        ScreenSpec(
            "start_line",
            "Start line",
            "Enter a Jamaa Derby race (games menu, Jamaa Derby, PLAY) and capture while the "
            "horses wait at the white start line.",
            (
                "startline_roi",
                "horse_box_roi",
                "player_marker_hsv",
                "horse_front_x",
                "jump_trigger_x",
            ),
            ScreenCheck("derby", expect="WAITING"),
        ),
        ScreenSpec(
            "racing",
            "Mid-race",
            "Capture during the race, with an obstacle band in view on the track.",
            ("strip_roi", "boost_button_point", "boost_box_roi", "progress_bar_roi"),
            ScreenCheck("derby", expect="RACING"),
        ),
        ScreenSpec(
            "leaderboard",
            "'Next Race In' leaderboard",
            "Finish a race and capture the leaderboard parchment that counts down to the next "
            "race.",
            ("results_box_roi", "results_rows_roi", "results_close_x_point"),
            ScreenCheck("derby", expect="RESULTS"),
        ),
        ScreenSpec(
            "leave_dialog",
            "'Leave game?' dialog",
            "Click the X at the top right of the race screen so the 'Leave game?' dialog "
            "shows. Capture it; do not confirm unless you want to leave.",
            ("close_x_point", "leave_confirm_point"),
        ),
    ),
    "double_up": (
        ScreenSpec(
            "room",
            "Any room with the HUD",
            "Stand in any room with the HUD visible and nothing open.",
            ("games_menu_point",),
        ),
        ScreenSpec(
            "games_menu",
            "Games menu, Double Up in view",
            "Open the games menu (controller icon) and scroll until the Double Up tile shows.",
            ("game_tile_point",),
        ),
        ScreenSpec(
            "board",
            "Double Up board, face up",
            "Start Double Up and capture during the first seconds, while all 16 tiles are "
            "shown face up.",
            ("board_roi", "grid_origin", "grid_pitch", "tile_size"),
        ),
        ScreenSpec(
            "end_screen",
            "Double Up end screen",
            "Finish a round (or let the 60 seconds run out) so the end screen with Play Again "
            "and the gems earned is shown.",
            ("play_again_point", "end_screen_gems_roi", "exit_point"),
        ),
    ),
    "adventure": (
        ScreenSpec(
            "room",
            "Any room with the bottom bar",
            "Stand in your den or any room with the bottom bar visible.",
            ("menu_globe",),
        ),
        ScreenSpec(
            "world_map",
            "WORLD MAP",
            "Click the globe in the bottom bar so the WORLD MAP is open.",
            ("menu_adventures",),
        ),
        ScreenSpec(
            "adventure_list",
            "Adventure list",
            "Click Adventures on the WORLD MAP so the list of adventures is shown.",
            ("menu_list_roi", "menu_list_down", "menu_entry_template"),
        ),
        ScreenSpec(
            "adventure_dialog",
            "Adventure dialog",
            "Click The Mystery Below in the list so its dialog with Join and Host is open.",
            ("menu_join", "menu_host"),
        ),
        ScreenSpec(
            "difficulty",
            "Difficulty dialog",
            "Click Join so the Difficulty Mode dialog with Normal and Hard is open.",
            ("menu_normal", "menu_hard"),
        ),
        ScreenSpec(
            "lobby",
            "Adventure lobby",
            "Pick a difficulty so the lobby panel with its PLAY button is open.",
            ("menu_play",),
        ),
        ScreenSpec(
            "in_level",
            "Inside the adventure",
            "Press PLAY and capture once you stand in the first cave with the objective "
            "banner, the minimap and the item slots on screen.",
            (
                "banner_roi",
                "minimap_roi",
                "minimap_exclude_roi",
                "dot_hsv",
                "items_roi",
                "avatar_roi",
                "avatar_xy",
                "item_slot_1",
                "item_slot_2",
                "nametag_template",
                "count_digits_template",
            ),
            ScreenCheck("minimap_dot"),
        ),
        ScreenSpec(
            "dialog",
            "An in-adventure dialog",
            "Walk up to Cosmo so his dialog with the blue X is open.",
            ("dialog_x_template", "dialog_x_search_roi"),
            ScreenCheck("dialog_x"),
        ),
        ScreenSpec(
            "crystals",
            "Crystals in view",
            "Stand where an unlit crystal and, if you can, a lit one and a decoy are on screen.",
            (
                "crystal_unlit_hsv",
                "crystal_lit_hsv",
                "crystal_decoy_hsv",
                "crystal_decoy_red_hsv",
                "courage_template",
                "courage_mask_hsv",
            ),
        ),
        ScreenSpec(
            "fungus",
            "A glow fungus with its paw pin",
            "Stand next to a glow fungus so the white paw pin floats over it.",
            ("fungus_hsv", "marker_white_hsv", "marker_yellow_hsv", "marker_orange_hsv"),
        ),
    ),
    "gem_ball": (
        ScreenSpec(
            "machine",
            "Gem Ball machine",
            "Walk to a Gem Ball machine in the Sol Arcade and start it.",
            ("launch_point", "gem_counter_roi"),
        ),
    ),
    "swoopy_eagle": (
        ScreenSpec(
            "playing",
            "Swoopy Eagle in play",
            "Start Swoopy Eagle and capture while the eagle flies between pipes.",
            ("play_roi", "bird_hsv", "pipe_hsv"),
        ),
        ScreenSpec(
            "crash",
            "Crash screen",
            "Crash into a pipe so the game-over screen shows.",
            ("crash_template",),
        ),
    ),
}

# Drawn-type fields whose values are relative to something else; they are not drawn.
NOT_DRAWN: dict[str, frozenset[str]] = {"adventure": frozenset({"nametag_box", "player_from_tag"})}

OTHER_SCREEN = ScreenSpec(
    "other",
    "Any screen that shows these",
    "These fields have no screen of their own yet. Capture any screen where they are visible.",
    (),
)
NUMBERS_SCREEN = ScreenSpec(
    "numbers",
    "Numbers, lists and text",
    "No screenshot needed. These are thresholds, timings and word lists; review them and "
    "double-click one to change it.",
    (),
)


# --- fields ---------------------------------------------------------------------------------


def field_state(f: ProfileField) -> FieldState:
    if f.value is None or f.status == "uncalibrated":
        return "missing"
    return "proposed" if f.status == "proposed" else "calibrated"


def is_drawn(profile_name: str, key: str, f: ProfileField) -> bool:
    """Shown on the canvas: points, ROIs, colours, templates, fraction boxes and the
    numbers that are an x position (``*_x``)."""
    if key in NOT_DRAWN.get(profile_name, frozenset()):
        return False
    return f.visual or (f.type == "number" and key.endswith("_x"))


def _describe_roi(f: ProfileField) -> str:
    x, y, w, h = (int(n) for n in f.value)
    return f"x {x}, y {y}, {w} x {h} px" + ("  (range)" if f.range else "")


def _describe_color(f: ProfileField) -> str:
    lo, hi = f.value
    return f"HSV {lo[0]}-{hi[0]} / {lo[1]}-{hi[1]} / {lo[2]}-{hi[2]}"


_DESCRIBE: dict[str, Callable[[ProfileField], str]] = {
    "roi": _describe_roi,
    "point": lambda f: f"({int(f.value[0])}, {int(f.value[1])})",
    "color": _describe_color,
    "fraction_box": lambda f: "fractions " + ", ".join(f"{float(n):.2f}" for n in f.value),
    "list": lambda f: f"{len(f.value)} items",
}


def describe_value(f: ProfileField) -> str:
    if f.value is None:
        return "not set"
    fn = _DESCRIBE.get(f.type)
    return fn(f) if fn is not None else str(f.value)


def field_hint(f: ProfileField) -> str:
    """What to do on the canvas to set this kind of field."""
    return {
        "point": "Double-click the spot on the screenshot.",
        "roi": "Drag the box or its corner handles; double-click to place a fresh box.",
        "color": "Choose Sample colour, then click a pixel of that colour.",
        "template": "Choose Crop template, then drag a tight box around the element.",
        "fraction_box": "Drag the box; it is stored as fractions of the screen.",
        "number": "Double-click the x position on the screenshot.",
    }.get(f.type, "Double-click the value to edit it.")


# --- steps ----------------------------------------------------------------------------------


def screens_for(profile_name: str, fields: Mapping[str, ProfileField]) -> list[ScreenSpec]:
    """The profile's screens with only the fields it really has, plus an 'other' screen for
    drawn fields no screen claims."""
    specs = PROFILE_SCREENS.get(profile_name, ())
    out: list[ScreenSpec] = []
    claimed: set[str] = set()
    for spec in specs:
        present = tuple(
            k for k in spec.fields if k in fields and is_drawn(profile_name, k, fields[k])
        )
        claimed.update(spec.fields)
        out.append(ScreenSpec(spec.key, spec.title, spec.instruction, present, spec.check))
    rest = tuple(k for k, f in fields.items() if k not in claimed and is_drawn(profile_name, k, f))
    if rest:
        out.append(ScreenSpec(OTHER_SCREEN.key, OTHER_SCREEN.title, OTHER_SCREEN.instruction, rest))
    return out


def numbers_for(profile_name: str, fields: Mapping[str, ProfileField]) -> tuple[str, ...]:
    return tuple(k for k, f in fields.items() if not is_drawn(profile_name, k, f))


def build_steps(profile_name: str, fields: Mapping[str, ProfileField]) -> list[Step]:
    steps: list[Step] = []
    for spec in screens_for(profile_name, fields):
        steps.append(Step("screen", spec, fields=spec.fields))
        steps.extend(Step("field", spec, field=k, fields=spec.fields) for k in spec.fields)
    numbers = numbers_for(profile_name, fields)
    if numbers:
        steps.append(Step("numbers", NUMBERS_SCREEN, fields=numbers))
    return steps


@dataclass
class WizardModel:
    """Where the user is in the walk and what they decided per field."""

    profile_name: str
    steps: list[Step]
    index: int = 0
    decisions: dict[str, Decision] = field(default_factory=dict)

    @classmethod
    def for_profile(cls, profile_name: str, fields: Mapping[str, ProfileField]) -> WizardModel:
        return cls(profile_name, build_steps(profile_name, fields))

    @property
    def step(self) -> Step:
        return self.steps[self.index]

    @property
    def progress(self) -> tuple[int, int]:
        return (self.index + 1, len(self.steps))

    @property
    def can_back(self) -> bool:
        return self.index > 0

    @property
    def can_next(self) -> bool:
        return self.index < len(self.steps) - 1

    def next(self) -> Step:
        if self.can_next:
            self.index += 1
        return self.step

    def back(self) -> Step:
        if self.can_back:
            self.index -= 1
        return self.step

    def confirm(self) -> Step:
        if self.step.kind == "field":
            self.decisions[self.step.field] = "confirmed"
        return self.next()

    def skip(self) -> Step:
        if self.step.kind == "field":
            self.decisions[self.step.field] = "skipped"
        return self.next()

    def go_to_screen(self, key: str) -> Step:
        for i, s in enumerate(self.steps):
            if s.kind == "screen" and s.screen.key == key:
                self.index = i
                break
        return self.step

    def siblings(self, step: Step | None = None) -> tuple[str, ...]:
        """The other drawn fields of the step's screen (drawn dim)."""
        s = step or self.step
        return tuple(k for k in s.fields if k != s.field)

    def summary(self) -> dict[str, int]:
        total = sum(1 for s in self.steps if s.kind == "field")
        confirmed = sum(1 for d in self.decisions.values() if d == "confirmed")
        skipped = sum(1 for d in self.decisions.values() if d == "skipped")
        return {
            "fields": total,
            "confirmed": confirmed,
            "skipped": skipped,
            "remaining": total - confirmed - skipped,
        }


# --- captured screens -----------------------------------------------------------------------


def capture_path(paths: Paths, profile_name: str, screen_key: str) -> Path:
    return paths.captures / CAPTURE_DIR / profile_name / f"{screen_key}.png"


def capture_stamp(path: Path) -> str | None:
    """'captured 2026-11-03 14:02' for an existing capture, else None."""
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime).strftime(STAMP_FMT)


def save_capture(path: Path, image: BgrImage) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), image)
    return path


def load_capture(path: Path) -> BgrImage | None:
    if not path.exists():
        return None
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    return None if img is None else np.asarray(img, dtype=np.uint8)


# --- screen checks --------------------------------------------------------------------------

ProfileOf = Callable[[str], Profile]
OcrFn = Callable[[BgrImage], str]


def _roi_of(prof: Profile, key: str, image: BgrImage) -> tuple[int, int, int, int] | None:
    if not prof.has(key):
        return None
    f = prof.field(key)
    if f.type == "fraction_box":
        h, w = image.shape[:2]
        x0, y0, x1, y1 = prof.fraction_box(key)
        return (int(x0 * w), int(y0 * h), int((x1 - x0) * w), int((y1 - y0) * h))
    return prof.roi(key) if f.type == "roi" else None


def _crop(image: BgrImage, roi: tuple[int, int, int, int] | None) -> BgrImage:
    if roi is None:
        return image
    x, y, w, h = roi
    return image[max(0, y) : y + h, max(0, x) : x + w]


def _check_ocr(check: ScreenCheck, image: BgrImage, prof: Profile, ocr: OcrFn) -> CheckResult:
    text = ocr(_crop(image, _roi_of(prof, check.roi_key, image))).strip()
    ok = check.expect.lower() in text.lower()
    shown = text[:40] or "nothing"
    return CheckResult(ok, f"read '{shown}'" + ("" if ok else f"; expected '{check.expect}'"))


def _check_template(
    check: ScreenCheck, image: BgrImage, prof: Profile, paths: Paths
) -> CheckResult:
    from jamcrammer.modes.party_watcher import multiscale_match

    if not prof.has(check.template_key):
        return CheckResult(None, "no template cropped yet; confirm the screen by eye")
    tpl = cv2.imread(str(prof.template_path(check.template_key, paths)), cv2.IMREAD_COLOR)
    if tpl is None:
        return CheckResult(None, "template file missing; confirm the screen by eye")
    thr = prof.number(check.threshold_key) if prof.has(check.threshold_key) else 0.8
    scales = (
        [float(s) for s in prof.items("template_scales")]
        if prof.has("template_scales")
        else list(DEFAULT_SCALES)
    )
    sub = _crop(image, _roi_of(prof, check.roi_key, image)) if check.roi_key else image
    score, box, _ = multiscale_match(sub, np.asarray(tpl, dtype=np.uint8), scales, thr)
    if box is None and sub is not image:
        score, box, _ = multiscale_match(image, np.asarray(tpl, dtype=np.uint8), scales, thr)
    label = prof.field(check.template_key).display_label(check.template_key)
    return CheckResult(box is not None, f"{label} match {score:.2f} (needs {thr:.2f})")


def _check_derby(check: ScreenCheck, image: BgrImage, prof: Profile) -> CheckResult:
    from jamcrammer.core.config import DerbyConfig
    from jamcrammer.modes.arcade.derby import REQUIRED, DerbyDetector

    if prof.missing(list(REQUIRED)):
        return CheckResult(None, "Derby boxes not all set yet; confirm the screen by eye")
    seen = str(DerbyDetector(prof, DerbyConfig()).screen(image))
    return CheckResult(seen == check.expect, f"looks like {seen}; expected {check.expect}")


def _check_bestguess(image: BgrImage, prof: Profile) -> CheckResult:
    from jamcrammer.core.config import BestGuessConfig
    from jamcrammer.modes.bestguess.state import in_arena

    if not (prof.has("fact_roi") or prof.has("arena_anchor_roi")):
        return CheckResult(None, "banner and arena marker not set yet; confirm by eye")
    ok = in_arena(image, prof, BestGuessConfig())
    return CheckResult(ok, "parchment and gem icon found" if ok else "not seen as the arena")


def _check_dialog_x(image: BgrImage, prof: Profile, paths: Paths) -> CheckResult:
    from jamcrammer.modes.adventures.sight import find_dialog_x

    if not (prof.has("dialog_x_template") and prof.has("dialog_x_min")):
        return CheckResult(None, "dialog X template not set yet; confirm by eye")
    tpl = cv2.imread(str(prof.template_path("dialog_x_template", paths)), cv2.IMREAD_COLOR)
    if tpl is None:
        return CheckResult(None, "dialog X template file missing; confirm by eye")
    roi = prof.roi("dialog_x_search_roi") if prof.has("dialog_x_search_roi") else None
    at = find_dialog_x(image, np.asarray(tpl, dtype=np.uint8), prof.number("dialog_x_min"), roi)
    return CheckResult(at is not None, f"dialog X at {at}" if at else "no dialog X found")


def _check_minimap_dot(image: BgrImage, prof: Profile) -> CheckResult:
    from jamcrammer.modes.adventures.sight import minimap_dot

    needed = ("minimap_roi", "dot_hsv", "minimap_exclude_roi", "dot_area")
    if prof.missing(list(needed)):
        return CheckResult(None, "minimap and dot colour not set yet; confirm by eye")
    at = minimap_dot(image, prof)
    return CheckResult(at is not None, "player dot found on the minimap" if at else "no dot")


def run_check(
    check: ScreenCheck | None,
    image: BgrImage,
    profile_name: str,
    profile_of: ProfileOf,
    *,
    paths: Paths,
    ocr: OcrFn | None = None,
) -> CheckResult:
    """Evaluate a screen check on ``image``. Never raises: a broken profile or a missing
    template degrades to 'confirm by eye'."""
    if check is None or check.kind == "confirm":
        return CheckResult(None, "No cheap check for this screen: confirm it looks right.")
    runners: dict[str, Callable[[Profile], CheckResult]] = {
        "ocr": lambda prof: _check_ocr(check, image, prof, ocr or _default_ocr),
        "template": lambda prof: _check_template(check, image, prof, paths),
        "derby": lambda prof: _check_derby(check, image, prof),
        "bestguess": lambda prof: _check_bestguess(image, prof),
        "dialog_x": lambda prof: _check_dialog_x(image, prof, paths),
        "minimap_dot": lambda prof: _check_minimap_dot(image, prof),
    }
    try:
        return runners[check.kind](profile_of(check.profile or profile_name))
    except Exception as exc:  # a check is advisory, never fatal
        return CheckResult(None, f"check failed ({type(exc).__name__}); confirm by eye")


def _default_ocr(image: BgrImage) -> str:
    from jamcrammer.vision.ocr import ocr_image

    return str(ocr_image(image, upscale=OCR_UPSCALE).text)
