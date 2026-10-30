# Decisions log

Non-obvious choices and why. Newest at the bottom.

## 2026-10-26 — M0

- **Python 3.14 instead of 3.12.** 3.14 was already installed and every pinned dependency publishes a 3.14 wheel (verified with `pip index versions`). Spec allows 3.11+. `scripts/setup.ps1` still prefers 3.12 when present so a clean machine matches the spec's first choice.
- **Spec file renamed** from `docs/JamCrammer_KICKOFF.md` to `docs/KICKOFF.md`, matching the spec's own instruction and `CLAUDE.md`.
- **Window discovery is exact-title + process-name + browser-class exclusion.** `EnumWindows` showed two visible windows whose titles start with "Animal Jam Classic": the real Electron client and a Firefox tab. Matching on exact title, preferring process `AJ Classic.exe`, and rejecting browser classes avoids capturing a browser. `Chrome_WidgetWin_1` is shared by Chrome and Electron, so it cannot be used as an exclusion.
- **`run.bat` lives at the repo root as a one-line shim** to `scripts/run.bat`, so the M0 acceptance check works from the root while the canonical scripts stay in `scripts/`.
- **Fixture `tests/fixtures/my_parties_2026-10-26.png`** was taken from William's 13:32 MDT screenshot in `OneDrive/Pictures/Screenshots`. It is a 704×552 crop of the panel (not a full-window shot), so panel-locating tests need a separate full-window fixture later.
- **`inbox/` gitignore pattern** keeps every `README.md` and `.gitkeep` while ignoring all dropped files.

## 2026-10-26 — M1

- **AJC client facts.** Electron app `AJ Classic.exe`, frameless fullscreen, client rect exactly (0,0)-(1920,1080) on the primary monitor, DPI 96. The process is made per-monitor-DPI-aware (v2) before any capture so all coordinates are physical pixels.
- **Capture backend: WGC primary, mss fallback, never mixed in one process.** Benchmark (30 frames, 1920×1080): WGC mean 42 ms / p95 67 ms (~24 fps delivered), mss mean 33 ms / p95 35 ms (30 fps). Both are inside the 100 ms budget. WGC keeps working when other windows overlap the client, so `auto` tries WGC first. **Opening a WGC session after an mss session in the same process segfaults the interpreter** (windows-capture 2.0.1 + mss 10.2.0 on Python 3.14); WGC→WGC and mss→WGC are fine. Therefore `auto` only falls back wgc→mss, and `snap --benchmark` runs each backend in its own subprocess (hidden `_bench` subcommand).
- **WGC object must be kept alive.** The `WindowsCapture` instance is referenced from the backend (`self._capture`); the native thread calls back into it, and letting it be garbage-collected crashed the process. `close()` stops and then waits for the native thread before returning; leaving a session open at interpreter exit also crashes, so every CLI path closes the backend in a `finally`.
- **Input via raw `SendInput` (ctypes), not pydirectinput/pyautogui.** One code path with two key methods: hardware scan codes (`KEYEVENTF_SCANCODE`, default, equivalent to pydirectinput) and virtual keys (equivalent to pyautogui). `input-test --method scancode|vk` decides which AJC accepts; the result goes here once William runs it live. Mouse moves use `SetCursorPos` + `GetCursorPos` verification, then press/release through `SendInput`.
- **Presence detection** compares `GetLastInputInfo` against the controller's own last injected timestamp plus `presence_slack_s` (0.5 s), because the OS counts injected input as input.
- **Recorder keyframe threshold raised 6 → 12.** In a static room the ambient animation (water, other players) produces pHash distances of 6–8 between consecutive 5 fps frames; at 6 that was ~1 keyframe/s (≈7 GB/h of PNG). Real scene changes (panel open, room change) are far above 12. The hash is computed on a 256 px-wide grayscale downscale.
- **Recorder is source-agnostic.** It takes a `grab()` callable, so the same code records the live window or a synthetic/replayed source; the unit test drives it with generated frames. The first hash call imports scipy (~1–2 s cold), so `start()` warms it before the capture loop begins.
- **Learn session layout.** `captures/sessions/<YYYYmmdd_HHMMSS>_<target>[_<name>]/` with `frames/kf_*.png` (keyframes), `markers/m###_<kind>_<ms>/*.jpg` (ring dumps ±window), optional `video.mp4`, and `meta.jsonl` (`session` header, `keyframe`, `ocr`, `input`, `marker`, `window`, `end`). Windows OCR runs on every keyframe in the writer thread (best effort).
- **Console output is ASCII-only.** The Windows console is cp1252 by default; a `→` in a print crashed `input-test`. `main()` reconfigures stdout/stderr with `errors="replace"` as a belt-and-braces.

## 2026-10-26 — M2

- **Popup Watchdog detection is layered, never a lone color heuristic.** A plain Crystal Sands frame already contains three saturated-green blobs over 800 px (foliage, the monkey), so a green button alone cannot identify the idle popup. Order: calibrated templates → green button-shaped blob in the center box **and** OCR keywords ("still playing", "still here", …) → OCR keywords alone on a 5 s cadence (logged out, Play Now) → unknown modal = center box differs from the room baseline while the frame border does not (ratio-gated). Thresholds and keyword lists live in `profiles/shared.example.yaml`.
- **Watchdog state machine:** click → wait up to `clear_timeout_s` (3 s) for the popup to vanish → retry once → HIGH alert + pause. While paused it never clicks again; `resume()` clears it. Fatal kinds (logged out / Play Now) never click; they call `on_fatal` (kill switch), save a screenshot and alert HIGH. Unknown dialogs are saved to `captures/unknown_popups/` with a 60 s re-alert cooldown.
- **Room-drift guard** = mean abs diff of a 64×36 grayscale downscale with the HUD bands and the animal box masked (ROIs in the shared profile). Threshold 18. Ambient animation scores ~1–4; a room change scores far higher. The same baseline feeds the unknown-modal detector.
- **Anti-AFK** sends the rotating cancelling pairs from config and verifies with the drift guard after `settle_ms`; on drift it disables itself and alerts HIGH. Focus is obtained with `bring_to_foreground` (no floor click).
- **Notifications** run on a worker thread; `deliver()`/`test_all()` are synchronous for tests and the CLI. Toast via winotify (silent; sound is its own channel), sound via `winsound` system aliases repeated 3× for HIGH, ntfy via POST to `<server>/<topic>` with priority/tags, Discord via webhook with the screenshot attached when present. Verified live: toast ok (23 ms), sound ok; ntfy/Discord disabled pending William's topic/webhook.
- **Gem Ledger** parses the HUD only when `gem_counter_roi` is calibrated (it is not: the counter is not visible in Crystal Sands on this account). Readings that jump more than 5000 are dropped as OCR glitches. Mini-game payouts can be recorded directly (`record_payout`).
- **OCR text utilities:** `parse_int` maps O/l/I/S confusions only inside tokens that contain at least one real digit (so "no" never becomes 0); `normalize_text` fixes confusions before stripping punctuation so "l0ve|y" → "lovely".
- **Activity Test** (`activity-test`) runs phases `movement`, `panel`, `ingame` for 8 min each with the watchdog counting idle popups per phase; the `panel` phase needs the parties profile (M3) for the close button and falls back to Escape. Results go to `data/activity_test_<ts>.json`. Blocked on William being present.
- **Synthetic generators** (`tests/synthetic/popups.py`) draw a textured room with green foliage and centered dialogs; the watchdog replay test clicks through a simulated still-playing popup, and Windows OCR reads the synthetic dialog text, so the green+OCR path is exercised end to end without real captures.
- **Toast banners are suppressed while AJC is fullscreen.** winotify reported success and the notification landed in the Action Center (bell badge), but no banner appeared on screen: Windows hides banners while a full-screen app is active (Focus Assist "when using an app in full-screen mode"). Toasts are therefore a log, not an alert, on this setup; HIGH alerts rely on the repeating sound and on ntfy/Discord once configured. The handoff lists this as a risk.

## 2026-10-26 — M3

- **All profile coordinates are stage pixels (1920×1080 reference).** The AJC window went from fullscreen to maximized mid-session (client 1904×1013). The game scales its 16:9 stage uniformly by `min(w/1920, h/1080)` and centers it (letterbox), verified by matching the fullscreen HUD into the maximized frame: top-left HUD at scale 0.94 / origin (51, −1), bottom-right at 0.935 / (57, 2), prediction (0.938, (51.6, 0)). `vision/stage.py` maps stage↔client; `StageBackend` normalizes every captured frame to 1920×1080 so detectors and templates run at one scale; `InputController(stage=True)` maps clicks back. Fullscreen is the identity, so nothing changes there.
- **WGC is only used when its frame size equals the client size.** On the maximized window WGC returns 1898×1013 for a 1904×1013 client (a few px of the window's own border handling), which would skew click mapping. `auto` falls back to mss in that case; mss grabs the client rect exactly.
- **Panel geometry is relative to the matched "My Parties" title box** (column −0.98…1.99 title widths, rows 1.4…11.2 title heights), with the title, GO NOW and close-X templates matched over a scale list, so the 13:32 fixture crop (true stage scale) and the live panel both work.
- **Banner rows are connected components of the dark border mask, at two thresholds.** Pinned banners have a navy ring (gray ≈51); the party-slot banner's ring is lighter (≈80). Pinned rows use `border_dark_max` 70; slot rows use `slot_border_dark_max` 90 but only where a GO NOW button is found (N slots supported). A single threshold either misses the slot ring or merges adjacent pinned rings through the gap between them. Dark artwork inside a banner (Bitter Sweets' night sky) merges into its ring component without changing the bbox, which is why plain border-band pairing failed.
- **Banner identity = pHash with a margin, then OCR fuzzy, else unknown.** Library crops are produced by the analyzer itself (same crop rules as live) and canonicalized to 320×100 before hashing. Same banner across the 6% scale difference: distance 16; different banners: ≥22. Threshold 18 with a 4-point margin over the runner-up. Unknown banners are saved to `captures/unknown/` with their OCR text in the filename.
- **No Best Guess banner reference exists online** (fandom pages are paywalled to fetch and search finds no "Best Guess Game" party page), so the target is detected by OCR fuzzy match on "best guess" (threshold 80) or a future library entry.
- **Live calibration clicks:** the party hat (684,53 stage) opens the panel and the blue X (1237,335 stage) closes it; verified on screen (diff 63 open, 3 after close). Third party of the day in the slot: *Spooky Party* (Haunted Forest 13:34, Paradise 14:08, Spooky ≈15:00), consistent with a ~30 min rotation.
- **`hunt --no-join`** is the 30-minute acceptance run: it opens/closes the panel and logs sightings but never clicks GO NOW. `--dry-run` sends no input at all (it can only analyse a panel that is already open). On join, Phase C does not exist yet, so the hunter starts a Learn-only recording of the arena (KICKOFF §8.2).
- **Presence detection is wired into the hunt loop:** OS input newer than our last injection pauses hunting ("Paused — user active") and the next scan waits `resume_after_idle_s`. The room changed and the window state changed during this session, which means William was at the keyboard; no further live input was sent after that.

## 2026-10-26 — M4

- **GUI architecture:** `app/engine.py` is a Qt-free facade owning the bus, store, notifier, capture, input, watchdog, anti-AFK, ledger, modes and the recorder; every worker runs on its own thread; the GUI only reads `engine.state` on a 500 ms timer and receives bus events through `QtBus` (a queued Qt signal). Modes not built yet (farm, opportunist) run a watchdog-only placeholder loop and say so.
- **Design tokens live in one place** (`app/theme.py`): the palette dataclass and a generated QSS. Dynamic properties (`kind`, `status`, `state`) select variants; `repolish()` re-applies the sheet after a property change. A global `QLabel/QCheckBox { background: transparent }` rule replaced a `QFrame#Card QWidget` rule that, by specificity, had been overriding button styles inside cards.
- **Icons** are an in-repo 24×24 stroke-2 SVG set (`assets/icons/*.svg`, `currentColor`), tinted from the palette and rendered through QSvgRenderer at `size × devicePixelRatio`; the app icon is `assets/app.svg`, and `scripts/make_icons.py` renders each ICO size separately (16…256) rather than downscaling one raster.
- **Preview frames** shown in the GUI are stage-normalized (1920×1080) and only ever downscaled.
- **Command bar** holds the mode selector, state pill (`engine.state.pill_text`), Start/Stop/Pause, Dry-run, a compact hotkey hint with a tooltip, and the input-lease indicator; minimum widths were trimmed so the bar fits at 1280 px, the window's new minimum.
- **Tray icon** is tinted by state (green running, amber paused, red error, gray idle); the mini HUD is a frameless always-on-top tool window, draggable, toggled from the tray.
- **Onboarding** is a 7-page QWizard shown when `data/onboarded` does not exist; the input test requires an explicit consent checkbox.
- **UI QA** is scripted (`scripts/ui_qa.py`) so it can be repeated after every GUI change; screenshots are committed under `docs/ui/100` and `docs/ui/150`.
- **`QtBus.event` was renamed `received`:** a `Signal` named `event` shadowed `QObject.event()`.

## 2026-10-26 — M5

- **Ingest normalizes every frame to stage space** before hashing or saving, so inbox screenshots taken in any window size line up with live keyframes and the same profiles. Videos are sampled at `learn.ring_fps` and keyframed with the recorder's pHash rule; originals move to `inbox/_processed/<ts>_<folder>/` and are never deleted.
- **Scene clustering features** = 256 dHash(16) bits (×0.5) + an 8×8×4 HSV histogram (×2), agglomerative average-link with `analysis.cluster_distance` 1.6. Calibrated against the synthetic session's ground-truth states (adjusted Rand index): dhash16 = 1.00 (3 clusters), HSV grid = 0.95, pHash+histogram = 0.60 (pHash's 32×32 DCT cannot tell 'fact text present' from 'no text' but over-reacts to *which* fact is shown). Re-check on the first real recording.
- **Text regions** come from the recorder's cached per-keyframe OCR when present (no second OCR pass), else Windows OCR; boxes are grouped by IoU ≥0.35 across frames. Classification: digits decreasing in ≥60% of steps → countdown; monotone non-decreasing integers or lines mentioning "gem" → gem counter; ≥12-char text with ≥2 distinct values at a stable position → fact.
- **Click zones** for Best Guess are the median of left-half vs right-half clicks (True = left, per KICKOFF 4.4); for Double Up, click x/y values are grouped into 4 levels each to derive the grid origin/pitch.
- **Outcome labeling** uses F7/F8 markers: the region of maximal change between a frame ~6 s before and the last frame before the marker is the reveal ROI candidate; the chosen side is the last click in the 15 s before; the gem delta comes from the gem-counter region. Answers are seeded only when both a fact and a chosen side exist; confidence 0.9 with a gem delta, else 0.7.
- **Proposal writes `profiles/<mode>.proposed.yaml`** (status `proposed`, confidence per field) and crops each proposed ROI into `assets/templates/<mode>/` for the review wizard; the active profile is only replaced when the wizard's Accept runs.
- **Replay harness (M5) is self-consistency:** a nearest-state classifier built from the session's own analysis is scored against `analysis/labels.json`. It proves the plumbing and the `≥95%` gate mechanics; the real Best Guess / Double Up detectors (M7/M8) plug into the same `StateDetector` protocol and are scored against the same labels.
- **Synthetic Best Guess session** (`tests/synthetic/bestguess.py`) renders waiting/question/reveal frames with a fact, a countdown, a gem counter, William-style clicks and F7/F8 markers, plus an MP4 and tagged PNGs for the inbox, so the whole M5 acceptance runs in CI without real captures.

## 2026-10-26 — M6

- **Calibration editing is a pure model plus a thin canvas.** `vision/calibrate.py` holds hit-testing (corner/edge/body handles), drag/resize with clamping, rubber-band normalization, HSV range sampling (±10 H, ±60 S/V around the sampled patch) and template cropping, all unit-tested; the Qt canvas only translates mouse events into those calls and draws overlays. Tools: Select/drag, Draw ROI, Sample color, Crop template; double-click sets a point; number/list/text fields edit inline as JSON.
- **The live-mode gate is one function** (`core/gate.py:gate_status`): required fields per mode (`hunt`, `bestguess`, `double_up`) must be usable, and for the two farmers the best `analysis/replay.json` across that target's sessions must reach 95%. The CLI `replay` command and the review wizard both write `replay.json`. Mode pages and the Calibration page show the same summary line, so the GUI always says exactly what is missing.
- **Every committed example profile is parsed by a test** after an unquoted `note:` with a colon in it broke the GUI at startup.

## 2026-10-26 — M7

- **Double Up pairing** describes each tile (15% inset to drop borders) with a 12×12 LAB patch plus an a/b histogram and pairs tiles greedily by mutual best match; confidence = share of mutual pairs × how far the chosen distance beats the runner-up. ORB is only computed as a fallback when confidence is low. On synthetic boards (8 symbols × 2, shuffled) pairing is exact with confidence ≥0.8.
- **Preview burst and verification.** During the 3 s preview the farmer grabs at `double_up.burst_fps`, keeps the highest-confidence pairing, waits until all tiles are hidden, then clicks each pair and verifies the postcondition (both tile faces changed and stayed). A disagreement hands the rest of the round to reveal-and-remember, which also runs when no preview was captured; both finish the synthetic round inside the 60 s limit.
- **The farmer refuses to construct with missing profile fields** (grid origin/pitch/shape, tile size, Play Again point, end-screen gems ROI) and the engine additionally checks the live gate (replay ≥95%) before starting `farm`. Until William's Learn session calibrates the real grid, `farm` reports the gate as locked.
- **End-screen payout** is OCR of the gems ROI parsed with `parse_int`; every round becomes an `arcade_rounds` row with strategy and confidence in `notes`, and the gems are attributed to the ledger as a payout.
- **The simulated Double Up world** (`tests/synthetic/double_up.py`) models the preview grabs, flip timing, mismatch hiding, the end screen and Play Again, so the whole loop runs in CI.

## 2026-10-26 — M8

- **State detection is profile-driven and mostly stateless.** REVEAL = the reveal ROI is dominated by green or purple *with a margin* (a half-green/half-purple arena background scores ~0 margin); QUESTION_OPEN/LOCKED = a fact-length text in the fact ROI that is not a waiting phrase ("get ready", "next round", …); LOCKED when the countdown reads ≤5 s. Windows OCR is unreliable on a lone digit, so the detector also starts a question clock when a new fact appears and falls back to `question_s − elapsed`; the synthetic session stores one keyframe per countdown second so this is exercised.
- **Brain:** `token_set_ratio ≥ fuzzy_threshold` plus a length-ratio guard (≥0.6) so short OCR fragments never match long facts; conflicts bump `conflict_count` and the stored answer flips only when conflicts outnumber confirmations (majority). Policies: learned prior (majority side in the bank), fixed side, skip; answer modes learn-only / bank+policy / coin-flip.
- **Learners vote:** reveal-ROI color (strongest), gem-counter delta, own-animal motion; the confidence-weighted vote decides the correct side, which both updates the bank and scores our choice. Learn's leaderboard ranks the same strategies offline.
- **Phase C stays behind the gate:** the hunter's auto-join starts a Learn-only recording unless `gate_status("bestguess")` is unlocked *and* `answer_mode != learn_only`; then the farmer runs with the watchdog still ticking on the same frames. The replay harness scores the real detector against named states (`analysis/state_names.json` maps analysis clusters to WAITING/QUESTION/LOCKED/REVEAL via the review wizard).

## 2026-10-26 — M9

- **Checks only at round boundaries.** The orchestrator plays whole Double Up rounds and consults the clock between them; a check = leave the game (calibrated `exit_point`, verified by the board disappearing) → one full Hunt cycle (anti-AFK pair, scan, HIGH alert, auto-join) → re-enter via `games_menu_point` + `game_tile_point` (verified by the board appearing, one retry). Every check emits `opportunist.check` with its overhead in seconds; Profit Lab reads those events.
- **Fallback:** two consecutive failed re-entries → `opportunist.fallback` (HIGH) and the hunter's plain loop takes over; a successful join hands off to Phase C if the gate is open.
- **Board detection measures structure on grayscale** after a flat lobby background (constant colour, different per-channel values) was mistaken for a tile grid.
- **Entry route fields** (`exit_point`, `games_menu_point`, `game_tile_point`) live in the Double Up profile and are uncalibrated until William's Learn session or the Calibration page sets them; the orchestrator refuses to start without them.

## 2026-10-26 — M10

- **Derby obstacles are "foreign objects" against a rolling lane background**, no templates needed for v1: a column counts as obstacle when ≥10% of its pixels differ from the exponential-average background by ≥30 gray levels (height-independent; a per-column *mean* diff missed low obstacles). Class = width (≥ `long_obstacle_min_px` → long, jumped later); Space fires when the nearest obstacle's left edge is within the class's lead distance, with a cooldown. Carrot boosts only on clear straights (nothing within `boost_clear_px`), never overlapping the active boost.
- **Synthetic track** (`tests/synthetic/derby.py`): scrolling obstacles with random widths and spacing, air time on Space, speed penalty on a hit, boosts, results screen; place = 1st only when nothing was hit. The farmer wins 10/10 and the 10th first place flips `pro_unlocked` in the profile (payout table switches to Pro).
- **`farm` runs whichever `arcade_game` is configured** (`--game` on the CLI); each game has its own gate (profile fields + replay) and its own `arcade_rounds` rows, so Profit Lab compares Best Guess, Double Up and Derby side by side from measured rounds plus the ledger.

## 2026-10-26 — M11

- **Gem Ball auto-tuner** sweeps hold durations (`hold_min_ms`…`hold_max_ms` by `hold_step_ms`), reads each ball's outcome from the gem-counter delta (jackpot ≥75, top slot ≥3, low, miss), and declares the game deterministic only when one duration hits top/jackpot ≥70% **with at least `min_balls` (6) behind it**; the first version declared victory on two lucky balls out of a 30% random process. Reports go to `data/gemball_tuning_<ts>.json`; `farm --game gem_ball` sweeps then exploits.
- **Swoopy Eagle prototype**: bird = centroid of the bird-colour mask, gap = largest free run in the first pipe column ahead; PD flap controller (`kp`·error + `kd`·vy over a threshold, rate-limited). Gains were swept on a flappy simulation: kp 1.5 / kd 0.05 / threshold 25 clears ~31 gaps per 40 s; the hand-picked kd 0.25 over-flapped into the top pipe. Runs are counted on the crash transition, and the restart wait only begins after the crash screen is seen.
- **Super Sort / Falling Phantoms** are stubs with notes in `handoff.md`; they need a Learn session each and (Super Sort) a payout measurement first.
- **`press_hold`** joined the input controller (mouse down, hold, up) for Gem Ball's launch power; it is bounds-checked and dry-run aware like every other input.

## 2026-10-26 — Best Guess calibrated from William's recording

- **Source:** `inbox/2026-10-26 17-25-39.mp4` (1280×720, 60 fps, 198 s, two full rounds of Best Guess) plus three screenshots (two banner crops, one ROUND COMPLETE scroll). The My Parties banner for Best Guess was **not** captured; target detection stays OCR-based (`target_keywords: ["best guess"]`).
- **Real layout vs. the M8 assumptions:** the fact is on a parchment banner at the top (stage ROI `[500,10,940,200]`), the reveal is a big coloured word on the banner's top line (green *True!* / orange *False!*, measured H40-50 V≈217 and H0-10 V≈194) with the corrected statement under a False, the countdown is a **dial** (no digits → elapsed time), and the round score sits top-left next to a gem-stack icon (+10 × multiplier, x1…x4). The old "reveal ROI turns green/purple" model was replaced by a two-colour verdict (`reveal_ratio` 3, `reveal_min_fill` 0.03) with brightness floors so the brown question text never counts as orange.
- **CORRECT!/WRONG! splash is not used:** it is lime on a lime floor (colour fill cannot separate it) and OCR reads it as `coppec$t`. Learners now vote reveal word (0.95) + score delta (0.85) + dance (0.4/0.2).
- **Arena detection:** parchment share of `fact_roi` ≥ 0.3 **and** saturated share of `arena_anchor_roi` (the gem icon, `[120,8,110,60]`) ≥ 0.25 → otherwise the detector returns UNKNOWN. This keeps the Jamaa rooms, the "Join the Club" ad, the loading book (which is parchment-coloured) and the Leave-Game dialog out of the state machine; the farmer pauses after `unknown_pause_s`.
- **Banner scan** (`learn/analyze/bestguess_banner.py`): ingest now reads the banner through the whole inbox video at 2 fps, pairs each reveal with the question before it (10/10 facts on this recording, OCR noise like "Fuse!" handled by aliases), reads the ROUND COMPLETE scroll (9 correct → 250 gems; the first scroll round was 9/9 at x4), writes `analysis/banner.json` and `analysis/intervals.json`, and seeds the Learn Report answers. The answer bank was seeded with the 10 facts (`source=inbox_2026-10-26`).
- **Replay gate on video:** `replay <session> --detector bestguess --dense` replays the original video at 2 fps against the scan intervals (QUESTION split into OPEN/LOCKED by elapsed time). Result 97.5% over 396 frames → the `bestguess` gate is open. The keyframe replay (42 hand-labelled frames, `analysis/labels_source.txt` protects the labels) scored 90.5% only because keyframe gaps start the question clock late; the dense check is the honest one.
- **Generic clustering sees one state** for Best Guess (synthetic and real), so `test_learn_pipeline` no longer demands ≥3 states; Best Guess semantics come from the detector + banner scan.
- **Hunt shortcut:** if the arena is already on screen when Hunter starts and the gate is open, the engine farms immediately instead of opening My Parties (`engine.arena_detected`).
- **Unverified live:** `true_point [450,700]` / `false_point [1500,700]` were chosen from the floor layout (green left, purple right, clear of HUD) and have not been clicked yet; the first live run should use Dry-run to watch the state pill and then a short real run.

## 2026-10-26 — Jamaa Derby rebuilt from William's recording

- **Source:** `inbox/Recording 2026-10-26 175931.mp4` (1912×1032, 30 fps, 256 s: three full races, all won) and the Play dialog screenshot. The M10 farmer assumed a side-view lane with small obstacles; the real game is a top-down track scrolling right-to-left under a horse pinned at the top lane, and every obstacle is a **full-height vertical band**: yellow hay, brown mud, teal water, dark-green hedge or tall grass. So detection is now per-column colour classification in a strip of the track (`strip_roi` [100,400,1720,500]) instead of a rolling background diff.
- **Colours (OpenCV HSV, measured):** grass H54 S136 V198; hay H24 S163 V249; mud H17 S113 V108 (identical to the horse's body, so the horse box returns "unknown" while a mud band crosses it); water H84 S157 V162; hedge/tall grass H51 V130-152. The checkered finish stretch is the same dark green at V139 but only ~0.4-0.6 of each column, so the hedge class needs a seed column ≥0.68 and grows through columns ≥0.45 with a 150 px minimum width (checker columns are ~130 px).
- **Jump rule:** William presses Space when the band's edge is about **140 px past the nose** (median of 43 of his jumps; p25/p75 −248/−78), i.e. at the hooves. The farmer therefore triggers on `jump_trigger_x` 800 plus a latency lead of `jump_lead_s` 0.08 × the live scroll-speed estimate (~830 px/s nominal, measured from band motion between frames). Airtime is ~0.75 s and only the leading edge is judged, so one press per band.
- **Occlusion at race start:** all horses leave the line stacked in one column, which hides a band's edge exactly when it reaches the hooves (three of 60 bands were missed in the first offline pass). The farmer keeps tracking the nearest band and, if its edge stalls or vanishes while it should still be ahead, acts on the edge predicted from the last sighting and the speed estimate (`track_hold_s` 0.4).
- **HUD anchors:** the green progress bar at the bottom means "on the track" (0.84 fill while racing, 0.62 on the leaderboard, 0 elsewhere → UNKNOWN pauses the farmer after 30 s); the white start line in `startline_roi` means WAITING; beige ≥0.35 in `results_box_roi` means the leaderboard. Carrots are grey until GO and orange while available; all three are clicked on clear stretches ≥4 s apart.
- **Results:** our row has a yellow frame (rows with ≥50 % yellow mark its top and bottom lines; rosettes don't). OCR of that row gives "1st Admiral Angrypotato", the gems column "100 Gems"; the row index is the fallback. Races restart by themselves ("Next Race In N"), so nothing is clicked between races; the X at (1770,80) is recorded but unused.
- **Validation:** `replay <session> --detector derby --dense` scores 99.8 % on hand-labelled WAITING/RACING/RESULTS/UNKNOWN intervals; the farmer replayed over the video reads 3 × "1st / 100 Gems", clicks the boosts three times per race, and jumps within ±0.3 s of William on the bands. Synthetic track: 10/10 first places.
- **Watchdog vs mini-game canvases (2026-10-26, from William's first live Derby attempt):** the centre-vs-border "unknown modal" heuristic fired on the Derby screen itself (a centred canvas with blue borders is exactly what a dialog looks like to it), the watchdog stayed paused, and every later Start exited within milliseconds with no message. Now a running mode passes `known_screen` (its own detector recognised the track / arena) and the watchdog skips that one layer while templates and OCR keywords keep running; Start clears a watchdog or F11 pause ("I checked the screen"); and a mode that is blocked by the watchdog reports it in the status pill (`engine.mode_blocked`).
- **Live Derby geometry (2026-10-26 19:45):** William's recording included the 31 px window title bar, so every profile y was ~30 px low on the live client and the detector saw UNKNOWN; a live `snap` fixed the mapping (y_live ≈ 1.047·(y_rec − 180) + 127, x − 4). His horse is white now, so the horse box no longer looks for brown: the horse is whatever is neither grass nor a band, and its mean row drops ~60 px while airborne.
- **Timed press:** the first live races (4 races, 88 presses) showed 13 presses with the edge already 30-85 px past the hooves and a 200 px spread, because the loop runs at ~10 Hz (mss grab ≈ 74 ms) and a press could only happen on a tick. The farmer now corrects the observed edge for the frame's age, computes when the edge reaches the hooves, and if that is sooner than one measured loop period it sleeps the remainder and presses at that moment (`jump_lead_s` is now pure input latency, 0.05 s). Retries wait for two fresh frames. The scroll-speed floor is 700 px/s (observed minimum ~735).
- **Derby misses on live play (2026-10-26 20:15, William's five Learn sessions + his report):** pale hay (V≥160), pale mud streaks (V up to ~177) and rippled water (S down to ~83) fell below the old colour/fill thresholds, so ranges were widened from the live keyframes and `band_fill` dropped to 0.5; the checkered finish is kept out of the hedge class by requiring a run to be dark on average (`hedge_mean_fill` 0.55). The leaderboard rows were re-measured on a live frame (first row cy 43, pitch 87, gems column 300 px).
- **Width- and boost-aware timing (William's rule):** one- and two-column bands get `jump_lead_px_narrow` (40 px) extra so the horse has landed before the next band; three-column bands are jumped right at the edge; while boosted (speed ≥ 1.15× nominal or a carrot clicked within 3 s) `boost_lead_s` (0.03 s) is added.
- **Perpetual learning (`modes/arcade/derby_learn.py`):** when a band's edge crosses the hooves a record opens (kind, width class, where our press landed, colour percentiles of the band while it was whole). The band that is passing should keep sliding left at the scroll speed; two observations below half of it mean the horse stumbled → `derby.hit`, a crop of that band under `captures/derby/hits/`, and an adjustment in `data/derby_learned.json`: late press → +15 px lead for that (kind, width) class, early (>120 px) → −15, on-the-edge hit → +7; no press at all → that kind's HSV range widens toward the band's colour (≤15 units per event, within the kind's hue family, hedge never above V 172). Adjustments load at the next start; `learn_enabled: false` turns it off.
- **Best Guess 'does not move me' (2026-10-26 21:30):** `answer_mode` was still the safe default `learn_only`, so the engine never started Phase C (the Hunter ran in the arena, paused for user presence, and the watchdog stopped it). `config.yaml` now says `bank_policy`, the Best Guess page persists that choice, and the click points moved to open floor ([330,640] / [1790,560]) because clicking a player opens their card. The live arena was recognised in 389/389 Learn keyframes, so the detector geometry holds live.
- **Best Guess movement and the crowd (2026-10-26 22:00, William):** he moves with the arrow keys (his Learn session: Left/Right held ~1-1.5 s per move), so the farmer now holds Left (green = True) or Right (purple = False) for `move_hold_s` (2 s) instead of clicking the floor; a wall stops the animal and no player card can open. The hold is non-blocking (released from a later tick, always released when the mode stops). His own tag ("Admiral Angrypotato", `own_name`) is looked up by OCR once per round as the postcondition; if it reads on the wrong side the hold is repeated once. Unknown facts use the new `crowd` policy: ~5 s into the question the white name tags on each half of the floor are counted (`floor_roi`), and the side at least 1.25× fuller wins; a tie falls back to the bank's majority prior. The old click points stay for `move_method: click`.
- **Answer bank seeding + first live Best Guess numbers (2026-10-26 23:00):** William pasted the community wiki list; it lives in `assets/bestguess/facts_wiki.txt` (281 verdict lines, 5 without a verdict are skipped), loads once at engine start (`bestguess.seeded`) and on demand with `jamcrammer seed-facts`. The upsert lets the file win over an earlier guess. Live, 22:00-22:50: 187 rounds, 206 facts learned from reveals, bank answers 3/3 and both repeated facts answered correctly from the bank; the crowd guess scored 93/176 because tag counts five seconds in were 0-3. The crowd read now uses the non-floor mass of each half (animals are big and always visible; green floor H28-62, purple H95-165) and falls back to tags; on the live fixture it reads 0.17 vs 0.54.
- **2026-10-27 13:30, William's new horse (yellow, green mane/horns, glowing horseshoe):** yellow and green are also hay and grass colours, so the colour-agnostic horse detector saw nothing on 323 of 341 live frames. Yellow now counts as horse (body + horseshoe glow) unless a hay band is crossing the horse box; on the new-model Learn frames the horse is seen on 179/233 (the rest have a band over the box). Also: pressing Start with the Hunter selected while the Derby track is on screen now farms the Derby (`engine.track_detected`) instead of hunting; the "STARTING then IDLE" he saw was the Hunter being stopped by the watchdog on the track.

## 2026-10-27 — Den Planner (William's evening request)

- **Flow:** demo (open My Parties, open the Upcoming Parties tray, template-match the Best Guess row banner, OCR its "N minutes / 1 hr 13 mins" and set the countdown; the typed hours/minutes on the Den Planner page work too) → `Bingusthewizard's Den` by OCR of the room title, else the hut icon → games icon → drag the scrollbar thumb halfway → template-match the Jamaa Derby tile (fallback point) → PRO located at a fixed offset from the OCR'd "Play to earn Gems" line (green pixel check) → Derby farmer until the countdown is within `leave_lead_s` (90 s), leaving only at a leaderboard or the start line → X → Hunter with `hunt_interval_s` 20 s (open/close My Parties until Best Guess shows in the third row) → auto-join → Best Guess farmer. All coordinates come from William's 2026-10-27 window screenshots converted to the client area (title bar 31 px, side borders 8 px) and then to stage pixels.
- **Templates:** `assets/templates/navigation/jamaa_derby_tile.png` (match 1.0 on the menu, 0.30 elsewhere) and `best_guess_tray_banner.png` (0.84 in the tray, 0.29 on the closed panel). The stylised fonts defeat OCR, hence templates for the banner and the tile, OCR only for plain text (room name, times, "Play to earn Gems").
- **Account:** his character is now "Bingusthewizard" (own name in `bestguess.own_name`); the Best Guess tag check uses it.
- **Shortcut:** `Desktop\JamCrammer.lnk` → `.venv\Scripts\pythonw.exe -m jamcrammer gui` in `C:\dev\JamCrammer` (OneDrive desktop).
- **Not automated:** the Derby "leave" when the end screen is not showing (the planner waits for a leaderboard/start line), and Best Guess rows that are not the third item (the Hunter identifies the row by OCR/pHash, not by position).

## 2026-10-29 — Laptop bring-up

- **The stage fits the client height, not `min(w/1920, h/1080)`.**
  - The laptop's maximized client is 2560x1459, slightly narrower than 16:9.
  - HUD pieces cut from a live PC frame (news, party hat, den hut, globe, chat buttons) matched
    there at scale **1.351 = 1459/1080**, with a constant shift of only +4/+1 stage px.
  - The letterbox rule predicted 1.333, which was 12-16 client px off toward the bottom-right.
  - The PC's maximized window (1904x1013, wider than 16:9) fits both rules, which is why the
    old one went unnoticed.
  - Narrower clients therefore crop the stage's sides (the Expand / Log Out tabs are cut off);
    `normalize()` leaves the cropped stage columns black, so coordinates never shift.
  - Untested: clients much narrower than 1.75:1, e.g. true fullscreen 2560x1600. Keep AJC
    maximized on the laptop.
- **Start focuses the game.** On a single screen the GUI covers AJC after Start: mss would
  capture the GUI, and Space/arrow presses would land on its buttons. `start_mode` calls
  `focus_game()` (the same verified `bring_to_foreground` the anti-AFK uses). Because the focus
  may tap ALT, it is recorded as bot input, so presence detection doesn't read it as "user
  active". It runs in dry-run too: it is a window-manager action, not game input.
- **Gitignored state is carried by `scripts/sync_state.py`, not git.**
  - The bundle includes config, the real profiles, the replay records, `derby_learned.json`,
    and an SQLite-backup copy of the DB.
  - Import backs up whatever it replaces.
  - It **merges** `bg_facts`: union of facts, max of counts, and on a disagreement the side
    with more confirmations wins. Both machines keep learning.
- **Replay-gate records restored as imports.** The PC's Best Guess (97.5%, 396 frames) and Derby
  (99.8%) dense replays opened those gates. Their sessions are not on the laptop, so
  `captures/sessions/20261026_pc_import_{bestguess,jamaa_derby}/analysis/replay.json` carry the
  scores with `imported_from: PC`. The gate line names them. A laptop Learn session replaces
  them once recorded.
- **Fresh-clone crash.** `Engine.__init__` seeded the wiki facts before `self.state` existed,
  and the seed's event used the payload key `source`, which collides with `emit(source=...)`.
  The first launch on any new machine died; on the PC the bank was already seeded. Seeding now
  runs after the state is created, the key is `facts_source`, and
  `test_engine_starts_on_a_fresh_clone` covers it.
- **Adventure invites are found by OCR in two passes** (`modes/adventures/invites.py`). The
  first pass is full-frame OCR for fragments of the adventure name, matched by a token prefix
  or suffix of >=4 chars or by fuzzy match. The room-name label covered "...otten" on the laptop
  frame, and the pale blue "Join Me" header is rarely read at 1x. The second pass OCRs an
  upscaled crop above each candidate for "join"; that header is the click point. A candidate
  without a header is reported but never clicked. A template would fail because the bubble is
  translucent over arbitrary scenery.

## 2026-10-29 — Derby tuned on the laptop from William's Learn sessions (laptop branch only)

- **A band is judged once.** After a stumble the band sits at the hooves for 1-3 s. Opening a
  new record whenever `_pending` was empty re-judged it every tick: one stumble gave 5-10 hits
  and lead steps. `_await_next_band` now holds until the nearest band is ahead of the hooves
  again. A press made while the band is already passing becomes that band's late press, so a
  stumble teaches "earlier". The old stumble test pressed at -260 px, which is outside the
  window the farmer acts in, and only passed through the re-judging.
- **Per-class leads come from William's own presses, not a physical model.**
  - The strip measures a band below our lane, and the bands' ragged edges put the lane edge
    50-100 px from the strip edge, by a different amount per art piece.
  - A reach model (speed x airtime - width) would need the hitbox and an exact airtime that
    5 fps keyframes can't give (0.8-1.1 s, versus the PC video's 0.75).
  - His 85 grounded first presses, which cleared, give a robust median per kind and width.
    Classes with fewer than 4 samples use the 3-column median and are marked `proposed`.
  - The lead scales with live speed / 845 to keep the time margin. The old fixed `jump_lead_s`
    is skipped for class leads, because his press already includes the same input latency.
- **Tapping through a landing.** A press in the air is ignored by the game. William holds Space
  and key repeat (32 ms) fires the jump on touchdown. When the next jump comes due while the
  horse is up (horse box says airborne, or less than one airtime since the last press), the
  farmer taps every tick from then until 0.15 s past the predicted landing, never longer than
  0.9 s, so it can't cause a second jump. Synthetic: a 200-250 px gap is cleared only with the
  train. Such a band's record is tagged `rejump` and teaches no lead, since its timing was the
  landing's.
- **Tight gaps:** a 1- or 2-column band with less than `tight_gap_px` (400) of ground before the
  next band is jumped `tight_extra_px` (40) earlier. Synthetic: a 150 px gap is cleared only
  with it.
- **Boost reserve:** one carrot is kept back and clicked the moment a stumble is recognised.
  His runs: 2.5-3 s to recover unboosted versus 0.6-1 s boosted. It is released on a clear
  stretch after 28 s (a clean race is ~40-45 s of racing).
- **Live airtime:** the farmer tracks the horse's ground row and logs press-to-touchdown per
  jump (`derby.landed`), smoothed into `derby_learned.json` and used for the train's landing
  prediction. The default is 0.8 s until measured.
- **13:43 live run with the per-class leads: three fixes.**
  - **One press per band.** At a ~400 px lead the band is still ahead of the hooves when the
    0.5 s cooldown ends, so it was pressed for again. The horse was up, so a tap train
    started and the horse re-jumped on landing, into the band. `_pressed_for` now skips a band
    of the last press's kind that sits within `same_band_px` (150) of where that band's edge
    should be. Synthetic, 5 bands: 5 presses with it, 50 presses and 5 trains without.
  - **Race clock from GO.** `started_ts` is the start line (~20 s of countdown), so the boost
    reserve was released 8 s into the race and all three carrots were gone before the first
    stumble. Boost timing now uses `go_ts`, the first racing frame.
  - **The learner's verdict comes from the measured airtime.** With per-class leads every
    press is more than 120 px ahead, so the offset rule called every stumble "early". Now a
    stall seen before touchdown is late (+15 px), and one seen after touchdown means the horse
    landed in the band: early (-15 px). The three corrections from that run came from the
    bug and were cleared.
  - **Measured airtime:** 16 live samples, 0.56-1.45 s, smoothed to 0.91 s. That is longer
    than the PC video's 0.75 s.
- **16:24-17:56 live run (71 races): the learner was being fed the wrong band.**
  - **Clipped widths.** The strip ends at x 1816, and 3-column bands still run past it at the
    press: hedges show 680-770 of their 815 px, right ends at 1750-1807. They were classed
    2-column and pressed with the 263 px hedge 2-column lead, ~0.33 s before the edge
    (hay 3-column is pressed 0.13 s before and never stumbled). They landed in the band, 37%
    of them, and the lesson went to the hedge:3col key, which those presses never used. It
    ratcheted to the -160 clamp.
  - **Fix for clipped widths:** a band ending within `clip_margin_px` (80) of the strip edge
    takes the narrowest class whose full width covers what is visible. The full width is the
    median seen at the hooves, where every band is whole, taken from the learned history, with
    `column_px` per column until there are records.
  - **Misattribution.** A record stayed open for `hit_window_s` (1.3 s) after its band reached
    the hooves, so a stall on the next band was charged to the earlier one, after its
    touchdown, as "early". Event sequences showed the horse had already jumped the second band
    by tapping through the landing.
    - It moved single-column leads later every time: mud/hedge 1-column reached -150. That is
      the opposite of William's advice and makes the landing-bound failure more likely.
  - **Fix for misattribution:**
    - Stalls are watched on the record's own band, the leftmost passing band not behind it.
    - When a different band reaches the hooves, the record closes as cleared and the newcomer
      gets its own record.
    - A record takes a press only if it was the press for that band. The last 3 presses are
      kept as `Press` tuples, matched by kind and expected edge.
  - **Landing-bound stumbles now teach.** A stumble on a band jumped by tapping through a
    landing means the band before it was jumped too late to land in time. That band's lead
    moves +15 px. If that band was a tight press, the move goes to its own
    `tight:<kind>:<wclass>` lead, added on top of `tight_extra_px` only when the next band is
    close. Verdicts on a tight press also go to the tight lead, so single tight jumps don't
    shift every jump of the class.
  - **Clean airtime samples.** Only a plain press's landing is an airtime sample. A train's
    takeoff is a prediction, and a retry's is unknown. Those gave the 0.4-0.9 s and 1.2-1.6 s
    tails (1,035 samples: median 1.01 s, EMA drifted to 1.12 s).
  - **Learner version 2.** On load, v1 leads are archived next to the file and dropped. Ranges,
    airtime and history are kept.
  - **Tried and reverted:** timing the next band's press while the first still passes the
    hooves. In the real game the horse is always still in the air then, and the tap train
    covers it, so it changed nothing but the bookkeeping.

## 2026-10-29 — The Mystery Below route farmer (laptop branch)

- **Replay with closed loops, not a scripted solver.**
  - One 31-minute Hard run has 1436 clicks and 1394 key holds.
  - A hand-written solver for moths, torches, mirrors and plates would need many more
    recordings and screens.
  - Instead the run is the route, and every input is anchored to what the screen shows now:
    patch matching for clicks, scene and dot alignment for position, the banner for progress.
  - When it cannot recover, it stops and asks; William's fix is stored and reused there.
- **Minimap: coarse but absolute.**
  - Measured over the run: 399 scene px/s against 5.8 minimap px/s, so the dot resolves ~70 px.
  - It catches gross drift and stuck-ness, and works in the cave's darkness. Fine alignment is
    scene phase correlation.
  - The scene image is high-passed so the darkness vignette, which stays fixed around the
    animal, does not pull the shift to zero.
  - The animal is masked so a different animal aligns the same.
- **Motion-compensated click anchors.** Keyframes come at <=5 fps, and the one before a press
  can be up to 0.2 s old. If the view was scrolling, the clicked thing has moved since: in
  synthetic tests a fungus was missed by 48 px. The anchor undoes the share of the next
  frame's scene shift that had happened by the press time.
- **No step runs twice by accident.** Timing corrections and getting unstuck keep the step
  index; only the start, an objective ahead, a segment retry or William's resume move it.
  Before this, a 0.5 s timing correction re-ran a fungus click, a drag and an item-slot click.
  On a mirror that would be an extra rotation.
- **After William only walked, skipped clicks still run.** A position further along the route
  is not proof that its clicks happened. When he clicked during his turn, he is assumed to
  have done them.
- **The objective banner is matched by word set** (`token_set_ratio`). OCR of the banner
  sometimes starts mid-sentence: "deeper into the cit use the boomseeds to find a wa" was
  read as three objectives before.
- **"Item changed" only counts while the view held still.** The item slots' area shows the
  world when no item is displayed, and walking past changed it: 296 of 1503 clicks were
  flagged, and each would have been retried.
- **Equip check by cursor handle.** mss frames do not contain the cursor (Windows draws it).
  `GetCursorInfo` is the same OS-level read as the cursor position, not game memory. A held
  item changes it; the item slots changing also counts.
- **The watchdog's unknown-dialog layer stays out** (`known_screen=True`). Adventure dialogs
  are centred boxes, exactly what that heuristic flags; the runner closes them itself, and
  logout/fatal checks still run.
- **Dialogs are searched where they open** (`dialog_x_search_roi`). A full-frame template
  search would cost 30-60 ms a tick.
- **Scene gate: hold the route clock only when the scene cannot be aligned at all.** A
  thumbnail that differs from the recording at the current route time (the level still loading,
  the drop to the next floor) stops the clock, which then waits for the live view. But a 64x36
  thumbnail also stops matching after ~140 px of drift. So it only holds when phase correlation
  finds no shift either: a merely shifted scene is drift, which the course correction handles.
  After 45 s it re-localises, else it asks for help. Thumbnails leave the HUD out entirely: it
  used to be zeroed before the mean, which made a black frame correlate ~0.3 with every frame.
- **The route gate stays honest; supervised runs are William's call.**
  - On the real run the vision a route farmer relies on is far below the 95% replay threshold:
    localisation 34.8% leave-out, 60-70% when resuming near the route clock, click targets
    re-found 38%.
  - The gate record keeps the 34.8%.
  - `adventures.route.supervised` (default off) lets the mode send input with the gate locked,
    with a HIGH event saying so. It is meant for runs he watches and steers. It is not turned
    on without his explicit yes.
  - More recordings (different animals) and his stored fixes are what should raise the
    numbers.

## 2026-10-30 — The Mystery Below: starting from the den (laptop branch)

- **A start without a minimap dot goes to the top of the route.** The whole-route thumbnail
  match put a den start at 1880 s (the recording ends in that den, 0.996), and with the tail
  excluded the den still matched a fungi frame at 0.604, over `start_min_sim`. The 64x36
  thumbnails cannot tell rooms from the cave; the dot can: 99% of the recorded adventure frames
  have one (longest gap 4 frames), a den or a room has none. Start waits `start_look_s` (1 s)
  for a dot to cover the gap.
- **A mid-route start is dot-gated and honest about its odds.** With a dot it matches only
  inside the adventure (first banner to completion banner), at frames whose dot is within
  `drift_dot_px`. Leave-out on the real run: within 4 s of the truth 20% (dot within 2.5 px) to
  31% (5 px), even restricted to the live objective's segment, because the route crosses the
  same spots many times over 31 minutes. The position is right, the moment is not; the banner
  check (`objective_ahead` / `behind`) is what fixes the moment, so this stays.
- **The user turn ends on OS quiet, not on `user_active()` dropping.** `user_active()` is
  "last OS input is newer than our last injection"; with nothing injected while waiting for
  him it is true forever, which is why the 23:24 run sat in USER for 14 minutes. A takeover is
  now his input within `resume_idle_s`; the turn ends after `resume_idle_s` of no input at all
  (nothing of ours is sent during a turn, so all input is his).
- **The opening is not aligned.** Before the first banner the recording shows the room he
  started in (Coral Canyons) and the menus; a start from the den would have held the scene
  gate for 45 s and then asked for help. The HUD clicks are screen-fixed, so scene hold,
  course and stuck checks begin at the first banner, and a user turn there resumes at the same
  route time instead of localising against menus.
- **`adventure.supervised` is NOTICE.** It announces a setting he chose; HIGH (three sounds, a
  long toast) is for things that need him now.

## 2026-10-30 — The Mystery Below: a level model instead of blind replay (laptop branch)

- **Minimap coordinates are the world frame.** The minimap is the same map in every run and
  gives an absolute position even in the dark; scene pixels drift with the camera and the
  thumbnails localised the route right only a third of the time. Everything the level model
  stores (floor, spots, the plan) is in minimap px; screen positions are derived from the dot.
- **The item counters verify acts.** A lit crystal's glow is detected only a third of the
  time (the glow washes out), the counter's "N/3" is read 92% of the time and never wrong, and
  a use always takes one. So an act is done when the counter moves, never when the picture
  looks right.
- **Spots are approximate, the screen decides.** The clicks placed on the minimap scatter 3-5
  px (dot quantised to a pixel, a second of camera lag). Rather than chase exact positions,
  the runner walks to within 3 px (a third of a screen) and finds the thing by colour there.
- **The plan is the reference run's act order, not a path model.** William's order differs
  between runs and the game does not care; the newest run's sequence is replayed act by act,
  an act whose crystal is not visible is skipped (it may already be lit), and restocking is
  "nearest fungus spot", not his detour.
- **Menus by OCR state, not by replayed clicks.** The Join/Host dialog has the same blue X as
  the in-game dialogs, so a dialog closer must never run in the menus; and each menu click is
  only right if the next panel shows. "PLAY" OCRs as "nay", so the lobby is recognised by the
  adventure's name in its header.
- **Walls are learned, not surveyed.** A held key with a still dot for a second marks a
  3-cell wall and the path is replanned; a dialog holding the animal is closed first so it is
  not mistaken for a wall. A survey mode driving the animal around was William's idea; the
  recorded minimaps already show the floors, so it is not built.
- **Torches and the city stay on replay for now** (`mode: replay` per phase). The model has
  their spots (mirrors, rocks, piles) but no beam detector or switch timing yet; each phase
  moves to the plan as it is built, and the handover is at the phase's banner.
- **Stuck means still against an anchor, never against the last tick.** The dot moves a
  quarter pixel per 50 ms tick, so any per-tick comparison with a 1 px threshold reads a walk
  as a wall (the 12:11 run: every walk "blocked"). The anchor moves when the dot has gone 1 px.
- **A fungus is picked by its pin, not by itself.** The paw pin appears over a fungus in
  reach; the recordings' pickup clicks are all on it. So the pick act walks up until the pin
  shows (template + yellow check) and clicks the pin.
- **By screen when the thing is in view, by minimap only when it is not.** The crystals of a
  path are a screen apart; a minimap plan between them fights objects the mask does not
  know (the lit crystal blocked the diagonal on the 13:58 run and the walk oscillated).
  Walking at the blob in bursts is direct and self-correcting.
- **Stuck means a sidestep first, a wall second.** Things stand on the floor: the first
  stuck steps across the heading; only a second stuck learns a wall, never at the goal.
- **A crystal is lit only when the counter drops and the screen shows it.** The 13:19 run
  consumed a blossom off the crystal: the counter alone lies. The Courage text (masked
  template, 0 false positives on 100 other clicks) or a glow where it was dark is required.
- **A crystal left dark stops the run, it does not get skipped.** Each path's crystals turn
  unlit one by one behind the moth; skipping one leaves the rest muddy and every later act
  fails. So a failed light asks for help and is tried again, never moved past.
- **A list entry is matched by its nearest adventure name, never by a fragment.** The old
  rule ("mystery" in any word at 75) clicked another adventure on the 12:37 run; the nearest
  of the 20 names, by ratio, is unambiguous on the recorded list (the mystery below at 61,
  the others 40-84 against their own names). The lobby header gets the same test before PLAY.
- **The paw pin is found by colour, not by template.** William's screenshot of it scored
  0.62 against the template cut from his recording (a paler paw, another size); a white
  blob with enough yellow-to-beige inside finds both and nothing else in the cave.
- **The adventure list scrolls by its arrow.** A scrollbar drag depends on where the thumb
  is; released over something else it acts on that thing (it switched William's animal).
- **Join, not Host**, because both recordings did that and it ran solo (`adventures.level.join`).
  **Hard always**: William (2026-10-30) says the second half of the level needs Hard mode, so
  `adventures.level.hard` stays true; Normal is only for a deliberate experiment.

