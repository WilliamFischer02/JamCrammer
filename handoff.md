# Handoff

_Last updated: 2026-11-03 (the map's second pass; live stats, start classification, "Hunt Best Guess" on every farmer, the den gate; the guided calibration wizard)_
## The map's second pass (2026-11-03, PC): kept frames, offline rebuild, the animal by its tag
William's review of the first map: tiles offset (textures repeating at a shift), the animal,
its name tag and the top of the bottom bar over ~70% of it, objects smeared into blobs. He
wants a crisp picture of the level alone, every object crisp and exactly placed, and the
pilot to open the level itself (glow seeds on the mushroom bridges, cracked rocks smashed).
Design and numbers: **`docs/MAPPING.md` section 2b**; rationale in `decisions.md`.
- **Built:** `sight.player_on_screen` (name tag template -> the animal's screen point and
  box, used by every observation / scan / mark; `nametag_*`, `player_from_tag` in the
  adventure profile; `hud_rois` widened); `ObsStore` (every frame + pose kept under the pilot
  session's `obs/`, dialog frames skipped); `modes/adventures/world_build.py` (pairwise
  registration + pose graph + median-of-best-lit composite with the measured light gain:
  `pilot rebuild`, then `pilot reload`); pilot `enter` / `smash` / `reload`; the popup
  watchdog and a mouse keepalive on every pilot input path; the menu driver fixed (list
  entry by template, OCR in halves, three looks before the globe, the "land animal" dialog).
  Tests: `tests/test_world_build.py` (synthetic cave: registered to the pixel, animal / HUD /
  moths gone), `tests/test_adventure_menus.py` (the two live frames that broke the driver).
- **Live today:** a new pilot session `captures/sessions/20261103_110159_mystery_below_pilot`
  (budget 360 min). The animal entered the level through `pilot enter` twice; a lattice
  batch (scratch driver: nearest pending stop by the world grid's plan, `goto` + `scan`,
  every frame kept) walked the start area. **Twice the game logged the account out 8.5 min
  after entering, mid-walk, with no "still playing?" popup in the kept frames** (11:17 and
  11:40; last night's Derby logout at 20:39 looked the same). The mouse keepalive is the
  first answer to test; a server-side kick (a second login elsewhere? the laptop, a browser
  tab) is the other reading: ask William. One more mishap: the old menu driver misread the
  scrolled list as a room, clicked the globe over the open world map, landed on a sea stage
  and the game swapped the animal for a water one ("You must be a land animal"); William
  swapped it back. Fixed in the driver.
- **Kept so far:** ~4,000 frames; the start area's picture was rebuilt from 1,278 of them
  (residual 0.0 px median, max 1.8 px) and committed under
  `profiles/levels/mystery_below/world/`; the Mapping tab shows it.
- **Logouts, the tally:** 11:17 and 11:40 (8.5 min after entering, key walks only), 14:11
  (27 min: key walks, then click walks and the mouse keepalive), 14:30 (the animal idle
  ~14 min while the goto was being fixed; no keepalive running). No "still playing?" popup
  precedes any of them. Rule for next time: start `scripts/pilot_keepalive.ps1` the moment
  the animal is in the level and never leave it idle.
- **The walk, rebuilt twice today:** `goto` now plans on the world grid (A* around the 427
  known wall hits), walks each leg by a click on the floor (the game paths around Cosmo,
  Graham and rocks; keys only bump into them), tracks it by the fused position, records a
  stuck as a collision (only when the minimap dot is still too: in the dark the odometry
  sees nothing) two cells deep and two across, and plans again. Synthetic test in
  `tests/test_world.py`. Not yet proven live beyond a few legs.
- **A fresh instance is gated:** the next room opens only after the opening acts. Done by
  hand through the pilot today up to the blossoms (3 in the slot, the paw pin at the start
  fungus is one click); the first crystal (mm 1548.7,860.2) still needs the drag from
  `item_slot_1`, then crystals 2, 3, 9 lead the first moth to the door (handoff of 11-02).
  The scratch batch drivers (`map_batch.py` with `batch_plan_progress.json` = the recorded
  route's 81 chamber stops in order; `batch_progress.json` = a 450 px lattice) only walk
  and scan: they must run after the opening acts.
- **Evening (17:00-):** the picture of the chamber was rebuilt from 8,973 kept frames
  (4,050 in the largest group, 200 tiles, residual 0.09 px median; 63 crystals, 55 fungi,
  10 decoys placed from it, source `picture`) and installed + committed; the Mapping tab
  shows it. Three mushroom bridges were bloomed by hand (a blossom on the cap: slot click,
  then the cap) at mm 1592,796 / 1570,782 / 1533,768 and marked; the game shows a white
  pin with a **blue droplet** over a cap (or a crystal) in reach: `marker_blue_hsv`, `look`
  reports it as `drop_pin`. The walk was rebuilt again: `goto` plans on the world grid,
  walks each leg by a click on the floor (the game paths around NPCs and rocks), confirms a
  stuck with the minimap dot, bumps block two cells deep and two across, a blocked goal is
  replaced by the nearest open cell, a sealed-in animal unblocks around itself. The
  **long-haul driver** (`scripts/pilot_long_haul.py`, state under `data/pilot/`) walks all
  693 lattice stops nearest-first with the level's acts on the way (paw pin -> pick; unlit
  crystal in reach -> slot click + crystal click; droplet pin over a cap -> bloom; decoy ->
  smash; slot empty -> the nearest fungus spot); it was running at 17:45 with ~105 stops
  done, 16 crystals lit, 8 restocks. Logouts: 16:42 after 2 h 08 min of continuous input
  (so not idleness); the pilot's session budget was raised to 720 min.
- **Next:** let the long haul finish (or halt on a logout: William logs in, `pilot enter`,
  restart the driver, it resumes); then `pilot rebuild` over every session (~1 h for 12k
  frames: registration ~0.35 s per candidate pair), `pilot reload`, commit the world
  folder; review the picture for grey cracked rocks (no colour of their own: locate them on
  the picture, then smash by position), the far-west ledge, the door. The earlier plan:
  (scratch `map_batch.py`, resumable from `batch_progress.json`) over the pending stops;
  watch whether the keepalive holds the session past 9 min; then the hand work: the mushroom
  bridges (a blossom dragged onto the glowing cluster at mm 1582,796 opened the ledge
  yesterday; restock at a glowing fungus), the cracked rocks (`pilot smash`), the decoys and
  crystals walked around and marked; `pilot rebuild`; objects re-placed from the clean
  tiles; the Mapping tab's Export for William's review.

## William's 2026-11-02 evening report: what the logs say, what changed (2026-11-03, PC)
**The night, from `data/logs/jamcrammer.jsonl`:** 19:34:56 Start (Best Guess page, from the
den) ran the planner correctly: tray read "1 hr 26 mins" (due 21:01), already in the den, Derby
PRO opened at 19:35:18, 48 races, Pro unlocked 20:10. At 20:39:49 the track vanished and two
seconds later the watchdog saw **"You were gone for too long and got logged out"** (the fatal
kills the app's modes; the kill switch needs an app restart). So the plan never reached the
hunt. 21:00:34 the restarted app's first Start went down the planner path **although William
was already in the arena**: "My Parties did not open" (the hat clicked twice in the arena), ETA
fell back to the 45-min default, then "den button did not bring us home" while the cursor
fought William (`input.move_failed`). The second Start at 21:01:28 saw the arena at once and
farmed 1091 questions until 21:47. **Root cause of 2 and 3:** `_already_in_arena` /
`_on_derby_track` read `snapshot()`, the preview loop's last frame, which is `None` right after
the first `start_capture()`, so the first Start after an app start could never see the arena.
**Stats at 0 (1):** `state.stats[...]` was written only after `farmer.run()` returned; the
tiles had nothing to read while the bots farmed (the modes did count: `derby.race` x48,
`bestguess.reveal` x1090 are in the log).

**Built (all unit-tested, none seen live yet):**
- **Live stats.** `Engine._attach(obj, key)` + `_publish_stats()` on every status tick:
  `state.stats["bestguess"|"farm"|"hunt"|"opportunist"|"adventure"]` move while the bot runs,
  whichever way it was started (page, dashboard, tray, F9, hand-off). The dashboard's
  accuracy / streak tiles read `bestguess` (they read the wrong key before). Double Up's
  `FarmerStats.as_dict()` added. Test: `tests/test_engine_live.py` drives a
  `BestGuessFarmer` with a scripted detector through `engine._status` and watches the snapshot.
- **Start classifies a fresh frame** (`modes/situation.py`, `Engine.classify_now()`): arena /
  derby / parties_open / games_menu / den / room / unknown, logged as `engine.situation`
  (NOTICE). Best Guess Farmer in the arena -> farms at once (gate open) or records
  (learn-only); on the Derby track -> races; else the plan. `presence.mark()` stays in
  `start_mode` for every mode (test asserts it per mode).
- **"Hunt Best Guess" checkbox** on the Arcade, Adventure and Opportunist run panels
  (`config.yaml: hunt_best_guess: {farm, adventure, opportunist}`, saved on toggle). Checked:
  `BestGuessPlan` (`modes/planner.py`) = read the tray -> (unread: scan My Parties, join now if
  the banner is there, else the default ETA with a NOTICE) -> go to the den -> `plugin.enter()`
  -> `plugin.farm(due and can_pause_now)` -> at the lead `plugin.leave()` (postcondition: in
  the den) -> Hunter every `planner.hunt_interval_s` -> joined -> **hand-off**
  (`Engine._handoff`: lease farm -> hunt, `state.mode = "hunt"`, `engine.handoff` NOTICE, the
  run strip shows "input: hunt (Arcade Farmer -> Best Guess Farmer)", the Best Guess page
  opens) -> Best Guess farmer until stopped. Plugins live in the engine: Derby
  (`_derby_plugin`: `open_derby`, pause only at RESULTS / WAITING / UNKNOWN, X + LEAVE),
  Double Up (`ArcadeEntry` route, needs `double_up.exit_point / games_menu_point /
  game_tile_point`), Adventure (its own menus; leave = HUD den button, **not seen live in an
  adventure**), and a "wait in the den" plugin (keepalive every `heartbeat_s`) for the Best
  Guess Farmer when `hunt.derby_while_waiting` is off. Gem Ball / Swoopy: farming only, NOTICE.
- **Den Planner page removed** (nav, `pages/planner.py`, screenshots); its flow is the shared
  plan above. The Best Guess page keeps "Read time" and the "Race in the Derby until Best
  Guess is due" checkbox (= the old planner from the Best Guess page).
- **Rename:** Best Guess Hunter -> **Best Guess Farmer** (MODE_LABELS, tray, HUD, run strip,
  README, CLI help; the `hunt` subcommand stays).
- **Den gate.** The preview loop classifies the live frame every `planner.gate_poll_s` (2 s)
  while no bot runs (read-only; the bot pages start the capture quietly on show). Start and
  Dry run are disabled with "Return to Den to enable this Farmer (you are in ...)" unless the
  screen is the den (the arena also passes for the Best Guess Farmer, the Derby track for the
  Arcade). `Engine.start_mode` applies the same `start_gate` to tray / F9 / dashboard starts,
  after a fresh look so a stale poll never refuses; `internal=True` (hand-offs) skips it;
  unknown screens and no capture never block. Den = room title ends with "'s den"
  (`navigation.den_title_keywords`, fuzz 80); the live frame of 2026-11-03 10:53 is
  `tests/fixtures/navigation/den_live_2026-11-03.jpg`. The games-menu check now needs both
  scrollbar arrows blue (`games_arrow_min_fill`): the arena floor and a join dialog painted
  the lower one.

**Not verified live (needs William or the live game):** the whole plan end to end (tray ->
Derby -> leave at the lead -> hunt -> GO NOW -> hand-off -> farming); the Double Up and
Adventure plugins' enter / leave; the den gate's 2 s poll against real room changes; the
"wait in the den" keepalive keeping the account alive for an hour-plus wait.
**UI screenshots:** `scripts/ui_qa.py` ran offscreen (`QT_QPA_PLATFORM=offscreen`, dry-run,
hotkeys off) but that platform has no fonts (text renders as boxes), so `docs/ui/100` was
restored to HEAD; regenerate on screen when the game is not being driven. Layout seen: the
checkbox sits on the controls row next to Dry run, nothing clipped at 1360x860.
**Tests:** `tests/test_situation.py`, `tests/test_plan.py`, `tests/test_engine_live.py`.
`tests/test_derby_range.py` (untracked, another agent's in-progress Derby work) fails on
its own; the suite was run with it ignored.

## Guided Calibration wizard (2026-11-03, PC, subagent; not committed)
William asked for the Calibration tab to become an interactive, guided experience. Built,
unit-tested and screenshotted offscreen; **not run against the live game** (another agent
was driving the AJC window).
- **Guided view is the default** (`app/pages/calibration.py`, `calibration_wizard.py`); the old
  power-user canvas is the **Advanced** view (same canvas; the Fields table now shows labels,
  with key / type / help in the tooltip). Switch with the Guided / Advanced control.
- **Per profile** (`calibration_flow.py`, pure, no Qt): screens in plain words ("Open My
  Parties from the party hat", "Enter a Jamaa Derby race and capture at the start line",
  "Stand in your den" ...), each with its fields; a `screen` step (Capture / Retake, the
  capture kept at `captures/calibration/<profile>/<screen>.png` with its timestamp and
  reused next time; a cheap check where one exists: My Parties title template, den OCR,
  Derby WAITING / RACING / RESULTS, Best Guess arena, adventure minimap dot / dialog X;
  otherwise "confirm by eye"), then **one field per step** (label, help, value, status chip
  calibrated / proposed / missing, hint, "Can be a range" for ROIs, Adjust / Type value,
  Confirm / Skip / Back / Next, "Step k of n"), then one **numbers** step (table, double-click
  edits). Confirm marks the field calibrated and saves the profile. Drawn fields no screen
  claims land on an "other" screen (`test_calibration_flow` fails if that happens for a
  committed profile: put every new drawn field on a screen in `PROFILE_SCREENS`).
- **The edited box glows** (breathing halo in tool cyan, `theme.GLOW_*`, a QVariantAnimation
  that only runs while a glow is shown); the other fields of the same screen are dim.
  `*_x` numbers (horse nose, hooves) are drawn as vertical lines; a template field draws
  where its crop matches on the screenshot (>= 0.6, advisory).
- **Every field of every profile has `label` + `help`** (example and live yamls, inserted
  textually: no calibrated value changed; `tests/test_calibration_flow.py` enforces it).
  `ProfileField` gained `label`, `help`, `range` (default false, round-trips).
- **Jamaa Derby range lanes**: `horse_box_roi` flagged `range: true` spans every lane;
  `DerbyDetector.horse_box()` finds the player's lane inside it by the glow under the
  player's horse (`player_marker_hsv`, new, proposed = the hay yellow until sampled live),
  else by the rider pixels ending nearest `horse_front_x`; the lane is learned at the start
  line, kept for the race, dropped on the leaderboard. `horse_lane_h` (new, 250) is the
  single-lane height. The flag is off in the live profile: **William turns it on in the
  wizard and samples the glow**; `tests/test_derby_range.py` covers it on synthetic frames.
- **UI QA**: `scripts/ui_qa.py` shows the window on screen, so it was not run. Offscreen Qt
  renders text as boxes unless `QT_QPA_FONTDIR=C:/Windows/Fonts` is set; with it, the
  Calibration page was shot at 100% and 150% (`docs/ui/{100,150}/calibration.png` guided,
  `calibration_advanced.png` advanced) and read: crisp, nothing clipped. Worth adding the
  font dir to `ui_qa.py` so it can run offscreen while a bot drives the game.
- **Not verified live**: Capture on the real screens, the checks on real captures, dragging
  the glowing box, the Derby lane detection on a real multi-lane race (the glow colour is a
  guess), what NPC horses crossing `strip_roi` do to band detection when our lane is low.
- Open for William: is the horseshoe glow yellow (sample it in the wizard)? Should Confirm
  save the profile at once (it does) or only on Finish? Which screens are worth a check
  that does not exist yet (Double Up board, Gem Ball)?

## Den Planner from the den (2026-11-02 19:00-19:30, PC): the Hunter's "trouble starting"
William: the Best Guess Hunter would not get going; he wants, from his den: read when Best
Guess is due, play Jamaa Derby until then, join Best Guess on time. That is the **Den
Planner** mode (not the Hunter). Found and fixed, each verified live from the den:
- **The Hunter paused itself 2 s after Start** ("Paused - user active", then a 120 s wait,
  the log at 19:00:17): the click on Start is OS input and the presence detector took it as
  a takeover. `PresenceDetector.mark()` now runs in `start_mode`: input up to the start is
  not William coming back. (Every mode had this.)
- **My Parties stayed open after the tray read** (so the games-menu clicks hit the panel):
  the panel slides when the tray toggles, and `ensure_closed` saw no title mid-slide and
  called it closed. It now looks twice with a settle and confirms the close; the planner
  waits `tray_settle_s` after closing the tray.
- **The games list did not scroll**: the thumb sits lower than the recorded drag point. The
  list is now scrolled by its arrows (`games_scroll_up_point` to the top, then
  `games_scroll_down_point` one click per look) until the Jamaa Derby tile matches (0.95
  after one click). Three clicks jump to the bottom past the Derby.
- **PRO was clicked 80 px right of the button** (the old offset came from a 1904x1013
  window shot): `pro_offset` re-measured to [206, 133] and the finder now takes the centre
  of the nearest narrow green run on that row (the wide one is PLAY!). The track showed
  0.6 s after the click.
- **Leaving the Derby**: the X opens a "Leave game?" dialog; LEAVE at `leave_confirm_point`
  [740, 704] confirms it; the leaderboard's X sits at `results_close_x_point` [1676, 80].
  `Engine._leave_derby` tries both. Verified: racing -> den.
- The tray read works: "1 hr 52 mins" -> 6720 s at 19:12 (Best Guess due ~21:04).
- Menu clicks do not count as activity: the account was logged out once during this work
  (the idle logout), William logged back in. A planner run that sits in menus for minutes
  needs the keepalive; the Derby itself counts as activity.
- **Running at 19:30**: `run_planner.py` (scratch) started mode `planner` headless from the
  den; its output is in this session's task log. Not yet seen live: the farm-until-due
  hand-off, the leave at a leaderboard, the hunt and the join.

## Mapping session 1, continued (2026-11-02 17:10-18:20): the chamber's reachable part
- Three batches (`goto` a plan stop, `survey` eight directions, `scan`) over the moth
  chamber's 80 plan stops: **55 reached** (28-50 s each), the rest (the top-left, x <
  1570 / y < 805 in minimap px) blocked by walls on every side: the wiki's left route
  (mushroom bridges) and top route (rocks, a trapped moth). World now: **407 wall hits,
  36% of the known floor seen, 9% walked, 175 things**, map at
  `profiles/levels/mystery_below/world/` (committed), the Mapping tab shows it.
- **Added on the way:** observations align to the mosaic by phase correlation (the dot's
  quantisation no longer leaves seams); the red decoy crystals (H 170-174) have their own
  colour range (`crystal_decoy_red_hsv`) and are found; a scan drops unlit / lit reads
  within 50 px of a fungus (its navy stem / its glow). Still false: the teal water or glow
  patches read as unlit crystals (navy range); Cosmo / Graham's coats; nothing marks
  grey rocks yet (they are the plan's smash spots, marked by hand so far: 208).
- **Mechanics seen live:** a blossom dragged onto a glowing mushroom cluster (mm
  1582,796) was consumed, a glow ball sat on it, and the ledge past it was walkable
  afterwards (whether the drop opened it is not certain). Dialog pages come in twos
  (Cosmo, then Graham), each closed by its X. The counter read 1 then 0 after two uses
  since the pick: one blossom went somewhere unseen.
- **Ledges:** the minimap floor mask has none; the pilot's `goto` plans on it and gives
  up at a ledge (`blocked`, 9 learned walls), while the world grid holds the collisions.
  A4 should walk on the world grid (`World.plan`), not the minimap floor.
- **Session still open** (`pilot status`), the server and the keepalive still running, the
  animal at mm (1599.7, 805.3) on a ledge with 0 blossoms. `pilot stop` ends it;
  `data/pilot/keepalive.stop` ends the keepalive; `pilot quit` the server.

## First live mapping session (2026-11-02 16:38-17:10, PC): what happened
- Session `captures/sessions/20261102_163835_mystery_below_pilot` (log + recording; the
  recorder's own folder `..._163838_...` holds the frames of the first minutes, before the
  `--session-dir` fix). Budget 240 min, goal "map the moth chamber".
- Through the pilot: surveyed the start room (walls south and west, open up / up-right /
  right), scanned it (Cosmo's blue coat read as an unlit crystal and the glowing start
  fungus as a lit crystal: forgotten, both NPCs marked), picked 3 blossoms by the paw pin,
  lit the first crystal with a drag from the slot (counter 3 -> 2, Cosmo's and Graham's
  dialogs closed by their X), the banner moved to "make a path for the glow moth".
- A batch (`goto` spot, `survey`, `scan` at each plan stop) mapped two stops of the first
  corridor (walls on six and three sides, 32 and 26 s per stop) before the guardrail
  refused the third: William was at the keyboard. World now: 19 collisions, 512 walked
  cells, 2 NPCs, mosaic 1.6% of the area; `profiles/levels/mystery_below/world/`.
- **Next session:** continue the batch from stop 3 (`1582.7,841.3`) through the plan's 80
  chamber stops (list them from `level.json` in plan order); review the map crop after
  each batch and `forget` / `mark` the false things; the first path's crystals 2, 3, 9 are
  what leads the first moth to the door. The keepalive keeps the account alive between
  sessions; `pilot status` shows the session and the server.

## Interface overhaul, the Mapping tab, first live pilot steps (2026-11-02 evening, PC)
William's answers: purple crystals and cracked rocks break by **repeated clicks** (several
each); sessions travel in the sync bundle; the desktop is the main machine for a while;
unattended runs are fine; he parked his animal in the Mystery Below start room.
- **Interface** (`app/theme.py`, `widgets.py`, `main_window.py`, `pages/modes.py`): pages have
  a kind (home / bot / tool / system) and an accent (Best Guess violet, Arcade blue,
  Adventure amber, Opportunist green, Den Planner teal, tools cyan); the nav rail is grouped
  Bots / Tools / System with an accent bar on the open page; every bot page starts with a
  **run panel** (one status line, Start / Stop / Pause / Dry run, the bot's own tiles and
  meters); the old top command bar is gone, replaced by a **run strip** that slides in only
  while a bot runs and another page is open; starting a bot (page, dashboard, tray, F9)
  opens its page; buttons pulse on press, meters and stat tiles animate, pages fade in;
  shorter labels throughout; 41 new mono icons. QA screenshots at 100% and 150% in
  `docs/ui/`.
- **Mapping tab** (`pages/mapping.py`): the world map as a zoomable picture (grid colours,
  the low-res mosaic with the unseen part transparent, **full-resolution tiles** that load
  when zoomed in), the things with ids, collisions, the animal's position
  (`data/pilot/position.json`, written by the pilot); meters for the share of the known
  floor seen and walked, counts, the agent's session and budget and its last command;
  Export PNG stitches the observed tiles into one full-resolution file under
  `captures/maps/` and opens it. Refreshes every 3 s while the world file changes.
- **World map**: `TileStore` (`world.py`) keeps 512 px full-resolution tiles under
  `profiles/levels/<target>/world/tiles/<area>/`, written only where observed;
  `World.coverage(area)` gives the numbers the tab shows.
- **Pilot fixes from the first live steps:** (1) the OS counts the pilot's own injected
  keys as input, so a hold stopped itself after 15 px: `_user_input_age` now treats input
  at or just after our last injection as ours (the presence detector's rule); (2) Cosmo's
  dialog held the animal: the executor closes any game dialog before input and treats a
  stop with a dialog open as `dialog`, not a collision; (3) the server died on a Windows
  file lock when a client read the heartbeat while it was replaced: JSON writes retry and
  the serve loop never dies on a file error; (4) `pilot start` now names the recorder's
  folder (`learn --session-dir`) so the pilot log and the recording share a folder.
- **Live numbers so far:** a 0.6 s hold right travelled 254 px (423 px/s, matches the
  recordings' 400), odometry response 0.6-0.96, fused position 7-12 px off the dot. The
  banner moved to "pick some light blossoms" as the animal reached the start fungus.
- **Keepalive:** `scripts/pilot_keepalive.ps1` sends a left/right pair through the pilot
  every 4 min so the parked account is never logged out while Claude works elsewhere
  (log `data/logs/pilot_keepalive.log`; stop file `data/pilot/keepalive.stop`).
- `config.yaml` now has `pilot.enabled: true` and `budget_min: 240` (William's say-so).
- `scripts/sync_state.py export` carries `captures/sessions/*mystery_below*` whole
  (`--no-sessions` to skip).

## The world map and pilot v2 (2026-11-02, PC, main)
William's brief: the replay-and-fix loop on The Mystery Below is too slow; he wants a
virtual map of the level the bot always places itself in, built by Claude piloting the
character (walls by collision, things labelled), with the next objective chosen from the
map. Assessment, design and plan: **`docs/MAPPING.md`**. Built today (milestone A1):
- **`modes/adventures/world.py`**: per area an occupancy grid (35 scene px cells: unknown /
  floor / walked / blocked), a scene mosaic at 1/5 scale, typed things with status, source
  and confidence (merged by kind and distance), collisions, notes; A* on the grid; a
  rendered map PNG; files under `profiles/levels/<target>/world/`. Seeded from the level
  model (floors, 201 spots) when no saved world exists.
- **`modes/adventures/localize.py`**: the position in world px from scene odometry (phase
  correlation between consecutive frames) fused with the minimap dot; a probe knows it is
  stuck in 0.3 s.
- **`pilot.py` / `commands/pilot_cmd.py`**: an `Executor` with injected eyes and hands (the
  synthetic adventure drives it in tests); new commands `probe`, `survey`, `scan`, `mark`,
  `where`, `map`, `unblock`, `forget`, `save`, `status`; `goto --world`; **`pilot serve`**
  keeps one process up and answers command files (`data/pilot/cmd|out`), the client sends
  itself there when the server's heartbeat is fresh. Input now needs a started session and
  stops at its budget (`pilot start --budget 30 --goal ...`).
- `config.yaml`: `adventures.world.*` (`WorldConfig`), `pilot.budget_min`,
  `pilot.serve_poll_s`, `pilot.serve_timeout_s`.
- **Verified:** `tests/test_world.py` (12 tests on the synthetic adventure: coordinates,
  the level seed, save/load, planning around blocks, odometry within 45 px over a 2 s walk,
  a probe that finds a wall within 40 px, a scan that places a crystal within 60 px, a mark,
  the map PNG, the switch and the budget, the server round trip, the real level seeding a
  world). ruff, mypy, the full suite green. `pilot look` ran against the live window
  (read-only): it showed the logged-out dialog, so nothing live was tried.
- **Next (A2, needs William):** log in, stand in the start room of The Mystery Below (or
  the den), `pilot.enabled: true`, then a 30-minute session: `pilot serve`, `where`,
  `survey`, `scan`, `look`, `mark`, `map`. Measure the odometry and the probe stops live
  and put the numbers in `docs/MAPPING.md` §3. Then A3 (the chamber mapped) and A4 (the
  level runner plans on the map). Questions for him are in `docs/MAPPING.md` §6.
- **Note:** the two Mystery Below recordings are on the laptop only; `captures/sessions`
  on the PC has none. The committed `profiles/levels/mystery_below/` (level.json, floors)
  is what seeds the world here.

## Laptop (2026-10-29)
- **Git on the laptop:** work happens on the local branch `laptop`; `main` stays at
  `origin/main`, the PC's code. William (2026-10-29): never push or merge laptop changes that
  touch Jamaa Derby or Best Guess; the PC versions are canonical and already work. The laptop
  rebuilds what it needs locally. Adventure-only commits may be cherry-picked onto `main`
  when he says push.
- **Desktop shortcut:** `OneDrive\Desktop\JamCrammer.lnk` -> `.venv\Scripts\pythonw.exe -m
  jamcrammer gui` in `C:\dev\JamCrammer`, with the `assets\icons\app.ico` icon.
- **Machine:** Lenovo 83LY, a single 2560x1600 panel at 200% (DPI 192). AJC runs maximized with
  a 2560x1459 client (the 45 px title bar and the taskbar are excluded).
- **Root cause of the instant-closing `run.bat`:** `.venv` is gitignored, so it was never
  cloned and `scripts\run.bat` exited at its "venv missing" check. Rebuilt with
  `scripts\setup.ps1`'s steps.
- **Fresh-clone bugs fixed:**
  - The first engine start crashed while seeding the wiki facts: the event payload's `source`
    collided with `emit()`'s own `source`, and the error handler touched `self.state` before it
    existed.
  - `test_party_watcher` depended on a gitignored capture.
- **Laptop bugs fixed:**
  - `StageMap` assumed a letterbox (`min(w/1920, h/1080)`). AJC actually fits the **height**
    and crops the sides when the client is narrower than 16:9; this was 12-16 px off here.
  - No mode focused the game on Start, so on one screen the GUI stayed in front: mss captured
    it, and keys hit its buttons. Start now focuses AJC.
- **Rebuilt local state (gitignored, from handoff/decisions):**
  - `config.yaml`: `arcade_game: jamaa_derby`, `answer_mode: bank_policy`.
  - `profiles/bestguess.yaml`: the calibrated values below.
  - The answer bank was re-seeded with the 281 wiki facts.
  - The replay-gate records are marked as imported: `captures/sessions/20261026_pc_import_*`
    hold the PC's 97.5% / 99.8% scores.
  - The Derby, navigation and parties profiles fall back to their committed examples, which hold
    the live values.
- **Not carried over:**
  - The PC's live-learned Best Guess facts (~206).
  - `data/derby_learned.json`.
  - Fix: run `scripts\sync_state.py export` on the PC after `git pull`, then
    `scripts\sync_state.py import` on the laptop. The bundle goes through
    `OneDrive\WF_SYNC\06_SYNCED_EXPORTS\JamCrammer`, and the answer bank is merged.
- **Verified on the laptop:**
  - All tests (including the live capture tests), ruff and mypy pass.
  - The GUI renders all pages crisply at 200%.
  - A live Jamaa Township frame: the party hat, games and den click points land on their
    icons, and room-name OCR reads "Jamaa Township".
- **Not yet verified on the laptop:** a live Derby race and a live Best Guess round. Dry-run
  first: the colour thresholds were measured at 1920x1080 and are now applied to a
  2560->1920 downscale.

## Derby on the laptop (2026-10-29)
- **Where AJC runs:** an external 1920x1080 monitor left of the laptop, maximized: a 1920x1009
  client at DPI 96. The GUI stays on the laptop panel. Learn and farming both happen there.
- **First live race (12:39):** 23 jumps, ~6 real stumbles, all on 2- and 3-column bands once the
  boosts were spent. The bot pressed 50-160 px before the hooves; William presses much earlier.
  Two bugs:
  - A stumbled band was re-judged every tick: 48 "hits" and 5-10 lead steps per stumble.
  - A press after the edge reached the hooves was never matched to its band.
  Both are fixed. The inflated learned file was kept as
  `data/derby_learned_20261029_overcounted.json`.
- **William's 5 Learn sessions (12:52-12:58)** were analysed with
  `python -m jamcrammer.learn.analyze.derby_timing <session>`: 108 bands, 12 stumbles.
  - **Detection:** every type is right at every width, checked by eye on the crops. The hedge
    class covers three different obstacles: the 1-column leafy hedge, the 2-column tall grass
    and the 3-column flowered bush. One false positive: the checkered finish read as a
    3-column hedge, which is harmless.
  - **His median press, in px ahead of the hooves at ~845 px/s:**
    - 1-column: 295-409 (water 295, mud 349, hay 391, hedge 409).
    - 2-column: 120-263.
    - 3-column: ~114-160.
  - These are now `jump_lead_px_<kind>_<wclass>` in the laptop's gitignored
    `profiles/jamaa_derby.yaml` and scale with the live speed.
  - **He holds Space through landings** (key repeat at 32 ms), so the next jump fires on
    touchdown. The bot now taps every tick until just past the predicted landing when a jump
    comes due in the air.
  - **Boosts:** a stumble's slow zone takes 2.5-3 s to leave unboosted and 0.6-1 s boosted. The
    bot keeps one carrot (`boost_reserve`) for that and spends it unused after 28 s.
  - **Airtime** is measured live per jump (`derby.landed`) and smoothed into the learned file.
- **Still unknown:**
  - The screen after the bot's first leaderboard (UNKNOWN for 30 s, then a pause). The farmer
    now saves that frame to `captures/derby/unknown/`.
  - The race place was not read; the leaderboard reader works on all 5 of William's frames
    (four 1st / 150 gems, one 3rd).
- **Long live run (16:24 onward, still farming at 17:56):** 71 races. Places: 36 first,
  21 second, 8 third, 4 fourth, 2 not read. 8,575 gems, 2.8 stumbles per race, no unknown
  screen and no pause.
  - **Stumbles by class:**
    - Hedge 3-column: 37%.
    - Hedge 1-column: 28%.
    - Mud 3-column: 27%.
    - Mud 1-column: 23%.
    - Water 3-column: 18%.
    - Hay 3-column: 0%.
    - Every 2-column class: 4-8%.
  - **Two learner bugs found from it, fixed on the laptop branch.** These take effect at the
    next GUI start. The running farmer still has the old code.
    - **Clipped 3-column bands:** they were classed 2-column at the press.
    - **Misattributed stumbles:** a stumble on a tight pair's second band was charged to the
      first band. Single-column leads drifted 90-150 px late, and hedge 3-column was pinned at
      -160.
    - See decisions.md.
  - **Learned leads:** on first load the new learner archives the polluted ones to
    `data/derby_learned_v1_<stamp>.json` and starts from William's profile leads again. It
    keeps airtime, colour ranges and history.
  - **Next check:** 10+ races with the fixes, comparing per-class stumble rates with the table
    above. Then watch for `landing-bound: lead tight:...` notes in the log.
- **Import caution:** `sync_state.py import` would replace `profiles/jamaa_derby.yaml` and
  `data/derby_learned.json` with the PC's copies, which are backed up. Re-add the per-class
  leads after an import.

## Autonomous afternoon (2026-10-30 16:00-): pilot, torch hall, economy, bot review
William away for a few hours; his brief: debug and intelligence passes on all bots, a
workflow for Claude to take the controls, learn the economy, plan more bots. Done so far:
- **Input crash fixed** (`platform/input.py`): a raising SetCursorPos is a failed move, not a
  crashed mode; 3 px cursor tolerance for mixed-DPI monitors; a failed move logs where the
  cursor landed (the day's logs had 90 `input.move_failed` at a 1 px tolerance).
- **Pilot** (`jamcrammer/pilot.py`, `commands/pilot_cmd.py`, `docs/PILOT.md`): `jamcrammer
  pilot start|look|key|click|drag|goto|note|crop|stop`, one process per command, guardrails
  (William's `pilot.enabled` switch, forbidden ROIs, a key list, a 2 s yield to his input).
  `pilot look` ran against the live window (read-only) and worked; the frame showed the AJC
  window was not in front (a Snipping Tool over another game), so nothing else was tried.
- **Torch hall from the plan**: bang acts (the door's "!", the torch pin, three basin pins) from
  the recordings; the plan now spans the moth chamber and the torch hall and hands over to
  replay at "travel to the lost city". Not run live.
- **Economy** (`jamcrammer/economy.py`, `docs/ECONOMY.md`): the CP ladder, yields per run,
  rates; the Adventures page prints CP/h for the target. Wiki facts on the moth routes (red
  decoys bottom right, rocks top, mushroom bridges left) are in the docs. Measured today from
  the logs: Jamaa Derby Pro on the laptop build, 107 races, 72 wins, **6,270 gems/h**.
- **Keepalive** (`Engine._keepalive`): the game logged the idle account out five times today
  while a mode waited or nothing ran. During the adventure's waits, when neither the bot nor
  William has touched the controls for `heartbeat_s` (180 s), the anti-AFK movement pair is
  sent (`engine.keepalive`). Not while he is active, not while the bot drives, not when no
  mode runs (his call whether the game should be moved then).
- **Review and roadmap:** `docs/REVIEW_2026-10-30.md` (every bot from today's logs; Derby and
  Best Guess as notes, PC canonical) and `docs/ROADMAP.md` (ranked next bots: finish the
  Mystery Below, keep logged in, Meet Cosmo Hard gems ~10k a run, Derby's last 33%, Double Up
  measured, the Forgotten Desert, a Daily Spin claimer).

## The Mystery Below: tenth live run (2026-10-30 15:43): dropping with an empty slot
- Four crystals lit. Then the stock read **1** while the slot said 0/3: an empty slot dims
  its "0/3" to grey on grey and the OCR engine reads nothing from it at any scale or
  threshold, and the inventory kept the last good value. So it kept dropping on the next
  crystal with nothing to drop.
- **Fixed:** the counter's first digit is read by normalised correlation against digits
  averaged from 400 labelled frames of run B (`assets/templates/adventures/count_digits.png`,
  `count_digit_rois`, `count_digit_min` 0.7): 300 of 300 on run A, every live screenshot
  right including the dimmed 0/3 at score 1.0, a den frame 0.2. OCR is the fallback only.
  The stock before a light and the counter checks use this reading, never a remembered one
  (`Inventory.fresh`). With a fresh 0 it restocks from the nearest fungus spot and comes back.
- 189 tests, ruff, mypy clean.

## The Mystery Below: ninth live run (2026-10-30 15:15): three crystals, then a help loop
- Opening, pick, first crystal, the door wait, the corridor's first crystal: all right. Then
  act 3 (the corridor's second crystal) found nothing unlit where the map put it, the lit
  one was 470 px off (within the map's error but past the 200 px "already" radius), so it
  asked for help again and again, each time William's turn changed nothing.
- **Fixed:** a lit crystal within `pred_tol_px` (600) of the spot counts as done; a light that
  fails again after one help is skipped (`adventure.skipped`, NOTICE), never looped.
- **Smash acts:** the reference run's repeated clicks of 4+ in the moth chamber (40 groups:
  cracked rocks, purple crystals) are plan acts now. A smash walks there (by screen if a
  purple blob is near the prediction), approaches, clicks up to 18 times every 0.25 s and
  stops after 3 clicks without a Courage pop once it has hit, or 6 with none (nothing
  breakable there). Smashes never ask for help: they are CP, not the path.
- Plan now: 36 lights, 16 picks, 40 smashes, the door. Restocking is the same as before
  (the nearest fungus spot when the stock reads 0).
- The synthetic world's texture is dimmer (V < 190) so bright grey never reads as a glow.
- 188 tests, ruff, mypy clean. Smashing is not verified live (no rocks in the synthetic world).

## The Mystery Below: eighth live run (2026-10-30 14:52): still, again (the banner was never read)
- `adventure.phase_wait` with `now: ""` for 50 s: the runner's banner OCR returned nothing.
  The banner strip (`banner_roi`, 36 px tall) reads as **nothing at 1x and perfectly at 2x**
  (checked on six recorded banners and the live screenshot). The level runner, and the
  route replay before it, had never read a banner; the earlier runs got by because nothing
  waited on one. Fixed: both get the banner at `BANNER_UPSCALE` (2.0) in `engine.py`.
- William's note: the first crystal is not visible from the loading point (darkness); the
  plan goes fungus first anyway, and the crystal is found once lit up close.

## The Mystery Below: seventh live run (2026-10-30 14:34): stood still at the start
- **Stood still:** the first act (the pick) was gated on the "pick some light blossoms"
  banner, which only appears as you reach the fungus: a deadlock. An act's phase is now the
  banner 3 s before its click (`act_lead_s`; the walk to it began then): the pick is gated
  on "explore the darkness", the corridor's crystal still on "make a path" (the door).
- **The list:** `menu_list_down` was (450,970), a list row (it opened "The Search for
  Greely" on the 14:13 run and closed the panel on this one); the arrow is at (694,972),
  measured on three live screenshots. The game keeps the list's scroll position, which is
  why later tries found the entry at once. The entry matcher was never the problem here.
- 188 tests, ruff, mypy clean.

## The Mystery Below: sixth live run (2026-10-30 14:13): Cosmo taken for a crystal
- Pick and the first crystal fine. Then, still in the start room, the next act (the
  corridor's crystal) found nothing unlit near its prediction and took the nearest blue
  thing: Cosmo. Two blossoms went on him; the first crystal's glow next to him passed the
  lit check. After the door it believed the corridor's crystal done, walked on to the next
  one, which was still **muddy** (a grey rock shape; it turns dark blue only once the moth
  reaches the one before), and asked for help.
- **Fixed:** every act carries the banner phase it happened under in the recording
  (`Act.phase`, level rebuilt) and never runs before that banner shows (`phase_wait_s` 90 s,
  dialogs closed meanwhile; `adventure.phase_wait`): the corridor's crystal waits for "make
  a path", which is the door opening. A target must be within `pred_tol_px` (600) of where
  the map says the spot is. A drop that left the crystal dark is never dropped on again in
  that act (`bad_drop_px` 120). A light is verified only when the glow or the Courage text
  shows **and** the dark crystal at the drop point is gone (`adventure.lit_but_dark`
  otherwise).
- 188 tests, ruff, mypy clean.

## The Mystery Below: fifth live run (2026-10-30 13:58): back and forth after the first door
- Pick, first crystal (verified lit), the dialog, the door: fine. Then the minimap walk to
  crystal 1 (20 px away, through the corridor) ran back and forth for 30 s until William
  stopped it. The screenshot shows the animal at the lit crystal with the next unlit
  crystal in view at the top right: the lit crystal itself (not in the floor mask) blocked
  the diagonal, the stuck rule learned a wall there, the replan went round, and so on.
- **Fixed:** a target already on screen (an unlit crystal or a fungus within 900 px of the
  animal, near where the spot should be) is walked at by screen in bursts, no minimap plan:
  crystal to crystal along a path is always like that. The minimap plan is for hops where
  nothing of the kind is in view. The navigator sidesteps across its heading for 0.5 s on
  the first stuck (a crystal, a rock, an NPC in the way), learns a wall only on the second,
  and never blocks the goal cell. Every walk keeps a trace (dot, aim, keys, sidesteps,
  walls) that goes into `adventure.walk_failed` and next to the failure screenshot as .txt.
- 187 tests, ruff, mypy clean.

## The Mystery Below: fourth live run (2026-10-30 13:19): out of the start room, 9 crystals lit
- **What worked:** menus, the pin pick, the first crystal, the walk out, restocking from fungi
  (three picks), nine `lit` outcomes with the counter stepping 2, 1, 0.
- **What did not:** it dropped with an unreadable stock and counted a stray reading as
  success; it moved past crystals it could not see (`not_seen`, two were already lit: a
  duplicate spot) and past one it failed (`not_taken`); and once the counter dropped while
  the blossom landed off the crystal, so the next crystal of that path stayed muddy (a path's
  crystals turn muddy → unlit one by one as the moth advances) and the run drifted.
- **Fixed (certain lighting):** the stock is read for up to 1.5 s and must be a number ≥ 1
  (else restock); the animal walks up to the crystal (`drop_reach_px` 260) before the drop;
  a light counts only when the counter drops **and** a lit signal shows within 4 s (the
  "Courage" text by a masked template, `assets/templates/adventures/courage.png`, 0.9+:
  none of 100 other recorded clicks score that; or 3,000+ newly bright pixels where it was
  dark); a drop that left it dark is retried closer (`light_attempts` 3); a crystal that is
  not seen waits up to 8 s for the moth to turn it unlit, and a lit one where the spot is
  counts as `already`. **A light that still fails never moves on:** HIGH help, and the same
  act again after William's turn (if he lit it himself it reads `already` and goes on). A
  pick counts when the slot reads 1+ or goes up; a drop with no readable stock never does.
- Not measured live yet: the Courage / glow recall at 10 Hz polling (the recordings' sparse
  keyframes gave 12 of 39 and 7 of 41 on the text alone).
- 187 tests, ruff, mypy clean.

## The Mystery Below: third live run (2026-10-30 13:04, fixed)
- **Menus worked** (room → ... → lobby → in_adventure in 20 s, one stray room read).
- **Start room:** `no_pin` twice, then the fungus `not_seen`. The runner approached the fungus
  only to 150 px and waited there; the pin needs the animal at the fungus (the recording:
  the animal touching it). Fixed: approach to `pin_stop_px` (50) in short bursts, up to
  `pin_looks` (8) looks, Cosmo's dialogs closed between looks (they pop up right there), a
  pin anywhere within `pin_search_px` (450) of the animal is taken even when the fungus is
  hidden under the animal, and a sighting from a moment ago is kept when the next look loses
  it. Every failed act (no_pin, not_seen, not_taken, walk_*) now saves a screenshot under
  `captures/adventure_shots/`, so the next failure can be seen, not guessed.
- Keys are not logged by the input controller (only dry-run and taps are), which is why the
  cave part of the log shows clicks only; the animal did walk.
- 186 tests, ruff, mypy clean. Litmus unchanged: pick, light, moth, dialog, out of the room.

## The Mystery Below: second live run (2026-10-30 12:37, fixed)
- **Menus:** it clicked a wrong list entry (the old rule matched a word fragment to
  "mystery"), reached that adventure's difficulty dialog, and William took over before PLAY.
  Fixed: a list entry is the line whose nearest of the 20 adventure names (`ADVENTURES` in
  `opening.py`, `fuzz.ratio`) is the target at 50+; the lobby is PLAYed only when its header
  names the target, another adventure's lobby is closed by its X (`lobby_other`); 0.8 s
  settle after every menu click (`menu_settle_s`), as William asked.
- **Start room:** `adventure.no_pin` then `not_seen`: the template did not find the pin
  (his screenshot of it, `inbox/light blossom fungi with grab icon above it.png`, scores 0.62:
  a paler, beige paw and a slightly different size). The pin is now found by colour: a white
  blob of 800-9,000 px with 500+ px of yellow-to-beige inside (recordings 1,079-1,422, his
  screenshot 908, cave text under 450). Profile: `marker_white_hsv`, `marker_area`,
  `marker_yellow_hsv`, `marker_yellow_px`; the template asset stays for the synthetic world.
- **Walk `no_path` from the start room:** the dot sat more than 4 px off the mapped floor.
  Snap is 8 px now and, with no path at all, the navigator walks a straight line at the goal
  and lets the walls be learned instead of giving up.
- 186 tests, ruff, mypy clean. Still the same litmus: pick, light, out of the start room.

## The Mystery Below: first live run of the level model (2026-10-30 12:11, fixed)
- **Menus:** it looped on the adventure list three times (the scrollbar drag from the recorded
  thumb position did nothing or landed elsewhere; one landing switched William's animal to a
  water animal via a lobby animal slot), and "room" misreads clicked the globe. Fixed: the
  list scrolls by its down arrow (450,970), three clicks per look; a room must hold 1.5 s
  before the globe is clicked; every state change is saved to `captures/adventure_menus/`.
- **Cave:** every walk ended `blocked` within 12 s. Bug: the stuck check compared the dot to
  the previous 50 ms tick (it moves 0.3 px per tick), so walking always looked still. Fixed:
  the dot is measured against where the still period began.
- **Start room:** the fungus is picked by its **paw pin** (white teardrop, yellow paw) that
  shows when the animal is within ~300 px; clicking the fungus does nothing. New template
  `assets/templates/adventures/paw_marker.png` (pins 0.87-1.00, cave <= 0.79 with the HUD
  masked, plus a yellow check); the runner walks up until the pin shows and clicks it. The
  builder now counts the first pickup (the counter only appears with it), so the plan starts
  pick → light. The recording shows no "!" click is needed: Cosmo's dialog closes, the door
  opens by itself.
- **Also seen:** the game's "Are you still there?" popup during his long takeovers, then
  `engine.mode_crashed` with a SetCursorPos error (0) when the anti-AFK tried to answer it
  while the runner was in HELP. Not fixed; it is an input/anti-AFK matter outside the farmer.
- 185 tests, ruff, mypy clean. The litmus for the next run: pick, light, out of the start
  room, on its own.

## The Mystery Below: level model (2026-10-30, laptop branch, first live run 12:11)
Full write-up: `docs/MYSTERY_BELOW.md`. In short:
- **Why:** the two supervised runs of 2026-10-30 09:53 / 09:54 died in the menus. The dialog
  closer saw the Join/Host dialog's blue X (same template as Cosmo's) at (1441,426) and closed
  it; HUD clicks were checked for an item-slot change (a panel opening over the slots) and
  retried; the second run read the World Map's blue as the minimap dot. William's second
  recording (`20261030_100745_mystery_below`, 22 min) came with his design: map the level.
- **The minimap is the map.** Fixed, north-up, revealed as explored, identical pixel for pixel
  across runs, one per area (moth chamber, torch hall, city). 70 scene px per minimap px,
  our animal at (1005,500), arrow keys map straight onto its axes.
- **Items are the exact signal.** The "N/3" counters of the two slots were OCR'd on every
  keyframe by the recorder; each change is a use or a pickup, and the world click just before
  it placed on the minimap is the crystal / fungus. 46 crystal spots, 17 fungi, the door, 52
  repeat spots (mirrors, rocks) in the city, with the reference run's 52 acts as the plan.
- **New modules** (`jamcrammer/modes/adventures/`): `level.py` (model + builder, files under
  `profiles/levels/mystery_below/`: level.json, `<area>_floor.png`, `<area>_map.png`),
  `inventory.py` (counters by OCR: 92% at upscale 4, 0 wrong, retry at 2), `objects.py`
  (crystals unlit/lit/decoy and fungi by HSV blobs), `nav.py` (A* on the floor mask, the live
  dot, walls learned from a still dot), `opening.py` (menus by OCR state), `level_runner.py`
  (the moth chamber: walk to the spot, find by colour, act, verify by the counter, restock
  from the nearest fungus, William pauses it, three failures = HIGH help, the door banner
  hands over to route replay at that banner's time).
- **Engine:** `adventures.level.enabled` (default true) runs Opening → LevelRunner →
  RouteRunner(start_rt=the door). Rebuild route on the Adventures tab also rebuilds the level.
  Config: `adventures.level.*` (`core/config.py` LevelConfig). Profile: colour ranges, counter
  ROIs, menu points, `minimap_scale`, `avatar_xy`, `item_slot_1/2`.
- **Replay fixes:** no dialog closing and no item checks before the first banner; a HUD click
  never expects an item change (`ROUTE_VERSION` 5: routes rebuild once).
- **Verified:** 12 new tests on the synthetic adventure (menus, walking around an unmapped
  wall, colours, counters, the model's files and banners, the runner lighting from a plan with
  a restock and handing over, help after three unseen crystals); detectors measured on both
  recordings (unlit crystal near the click 72-83%, fungus 91-93%, lit crystal 35%: the counter
  verifies, not the glow). 183 tests, ruff, mypy clean. **Nothing of this has run live.**
- **Next: a supervised run from the den.** Expect `adventure.menu` states room → world_map →
  adventures → adventure_dialog → difficulty → lobby → loading → in_adventure, then
  `adventure.phase` and one `adventure.act` per crystal / fungus with its outcome. `not_seen`
  = the colour range or the spot is off there; `walk_*` = the floor mask or a wall; the moth
  phase ends with `adventure.handover` and the old replay takes the torches and the city.
- **Synthetic world:** its texture is grey now and its things navy / pale cyan / green, so the
  real colour ranges find them; the route tests still pass on it.

## The Mystery Below: the first live start, from the den (2026-10-29 23:24, fixed 2026-10-30)
- **What happened:** Start in the den raised a HIGH alert at once (`adventure.supervised`,
  toast + three sounds), the route localised to **1880 s, the end of the recording** (which
  ends in that same den, thumbnail match 0.996), his mouse put it into USER, and it sat there
  for 14 minutes without a line in the log until he stopped it. A minute later the idle
  watchdog raised a second HIGH (`unknown_modal`) on the zoomed den view.
- **Three bugs, fixed on the laptop branch** (`modes/adventures/replay.py`, `route.py`,
  `app/engine.py`, `core/config.py`; tests in `tests/test_adventure_route.py`):
  1. **The user turn never ended.** `PresenceDetector.user_active()` is "the OS saw input
     after our last injection"; it never resets while nothing is injected, and nothing is
     injected while waiting for him. The turn now ends when the OS has been quiet for
     `resume_idle_s` (2.5 s); a takeover is his input *and* within that window.
  2. **Starting from a room matched the end of the route.** Start now looks for the minimap
     dot for `start_look_s` (1 s). No dot = a room, not the adventure: the route starts from
     the top (globe, Adventures, ... PLAY; the HUD clicks are static). With a dot it matches
     only inside the adventure (first banner to completion banner) at frames whose dot is
     within `drift_dot_px` of the live one. Event `adventure.start_from_the_top` (NOTICE) says
     which. Measured leave-out of the dot-gated start: within 4 s only 20-30% of the time (the
     route revisits the same spots), so a mid-route start relies on the banner check.
  3. **The opening was treated as the adventure.** Before the first banner (11.8 s) the
     recording is Coral Canyons menus; from the den the scene gate would have held the clock
     (sim 0.35) until the 45 s help alarm. Scene hold, course and stuck checks now start at the
     first banner; a user turn in the opening resumes at the same route time.
- **`adventure.supervised` is NOTICE now** (a toast), not HIGH: it is his own setting.
- **Still expect** the idle watchdog's HIGH `unknown_modal` when capture is running with no
  mode and the den is zoomed/edited; that is M2 behaviour, untouched.
- **AJC geometry checked 2026-10-30:** external monitor, client 1920x1009 at DPI 96, same as
  the recording.
- **What the supervised run from the den should look like:** toast "SUPERVISED run";
  `adventure.start_from_the_top` (dot=false); globe click at 1.6 s, Adventures, the list
  drag, The Mystery Below, Join/Host, Hard, PLAY; then `adventure.scene_wait` while the
  level loads and `scene_ready` when the cave shows. Each of his corrections lands in
  `data/adventures/mystery_below_fixes.jsonl`. Watch for: a wrong list entry after the drag
  (the patch is accepted only within 60 px of the recorded spot; steer and it continues), and
  `adventure.help` (HIGH) if the banner stays behind for 45 s plus two segment retries.

## The Mystery Below route farmer (2026-10-29, v0, laptop branch)
- **Adventures tab** (`app/pages/adventures.py`, after Arcade). It holds:
  - the adventure, the recorded run (automatic = the longest finished) with its route
    summary, and Rebuild route (`engine.rebuild_route`, which also writes the honest replay
    record);
  - Supervised and Loop, and Start adventure / Stop;
  - live route time, objective and counters;
  - the latest fixes from `data/adventures/<target>_fixes.jsonl`.
  The Forgotten Desert is listed, gated like The Mystery Below, but is not suited to a
  route farmer yet.
- **Mode:** "Adventure Farmer" in the mode box (`engine._run_adventure`). It replays the
  longest finished `mystery_below` Learn session (`adventures.route.session` picks another) as
  a route and keeps it on course; `modes/adventures/{route,replay,sight}.py`.
- **The recording:** William's run of 2026-10-29 13:45 (31 min, Hard, solo).
  - It starts in Coral Canyons: globe, Adventures, scroll the list, The Mystery Below,
    Join/Host, Hard, PLAY. HUD clicks are static, so it also starts from his den or any room.
    Started mid-route, it finds its place.
  - 4291 steps: 2788 key events, 1436 clicks, 67 drags. 153 repeat groups (mirrors, rocks).
  - 17 objective banners, from "explore the darkness" to "you did it adventure complete".
- **Course keeping:**
  - The minimap dot catches gross drift. One minimap px is about 70 scene px (walk 399 px/s,
    dot 5.8 px/s).
  - Scene phase correlation catches fine drift (high-passed against the cave vignette; HUD and
    our animal masked, so any animal works).
  - Timing drift moves the route clock. Position drift walks back.
  - Stuck (a held key and no motion where the recording moved): a stored fix, then a walk to
    where the route is a second later with sidesteps, then a HIGH help event.
  - Banner behind for more than 45 s: the segment is retried from 60 s before the missed
    change, twice, then help.
- **Clicks:**
  - Each click's anchor patch is cut where the thing was in the frame before the press
    (motion-compensated: that frame can be up to 0.2 s older), found again live, and clicked.
    Otherwise the click goes to the camera-corrected point and is counted unverified.
  - Dialogs are closed with their blue X whenever one is open; a recorded X click with no
    dialog is skipped.
  - Item pickups and uses must change the item slots: retried, then walk to where William
    stood and once more.
  - An item-slot click must change the cursor (Windows cursor handle) or the slots.
  - A repeat group must end looking like the recorded result (the full beam), else extra
    clicks, up to 4.
- **William steering:** any input of his pauses it (keys released). With 2.5 s hands-off it
  localises and continues. If he only walked, the clicks it passed still run.
  - Every turn of his and every self-fix is a line in
    `data/adventures/mystery_below_fixes.jsonl`. His keys and clicks are recorded with
    pynput (injected events, the bot's own, are ignored) and replayed first when it is stuck
    near that route time again.
- **Gate:** `mystery_below` needs the `adventure` profile (committed example) and a replay
  record. `validate_route` writes one: leave-out localisation, each sampled keyframe located
  with its own +-1 s removed and found within 4 s.
- **Measured on the real run (honest: the gate is LOCKED):**
  - Leave-out localisation: 34.8%. Resuming in the forward window with the clock 2-20 s
    behind: 60-70% right.
  - A recorded click target re-found in the frame before it, within 40 px: 38%.
  - The route clicks 48 times a minute, so a time error above about 1 s crosses a click.
  - With one recording and dark, animated scenes, vision alone cannot place the run more
    finely than that.
  - So a match is trusted only within `patch_accept_px` (60) of the camera-predicted point,
    and otherwise it clicks the prediction.
  - Live runs need `adventures.route.supervised: true` (William's call): he watches and
    steers. Each of his corrections is stored and tried first next time.
- **Not done yet:**
  - Mirror beams are judged only by similarity to the recorded result, not by beam length.
  - Phantoms are not fought on purpose; his recorded attack clicks replay at camera-corrected
    points.
  - No loop between runs; it ends in the den.

## Adventures (2026-10-29)
- Research, rankings and both bot designs are in `docs/ADVENTURES.md`.
- **Build order:** The Mystery Below first (solo, Normal, checkpointed route replay), then The
  Forgotten Desert (members only).
- **Built so far:**
  - `modes/adventures/invites.py`: finds "Join Me - The Forgotten Desert" bubbles. It is tested
    on two live Township frames and is not yet wired to a mode.
  - The Learn targets `mystery_below` and `forgotten_desert`, with inbox folders under
    `inbox/adventures/`.
- **Membership:** confirmed 2026-10-29, so The Forgotten Desert is unblocked.
- **Waiting on William:**
  - 3 solo Normal Mystery Below runs recorded with `learn --target mystery_below`.
  - Forgotten Desert lobby and in-game screenshots.

## State
- **M0-M11 built and committed** (see git log and `decisions.md`): capture/input/safety primitives, Popup Watchdog, Anti-AFK, notifications, Gem Ledger, Party Watcher + Hunter, PySide6 GUI, Learn (recorder, inbox ingest, analysis, report, review wizard, replay harness), Calibration canvas + gate, Double Up / Jamaa Derby / Gem Ball / Swoopy farmers, Opportunist, Best Guess Phase C.
- **Best Guess is calibrated (2026-10-26 17:30-18:00)** from `inbox/2026-10-26 17-25-39.mp4` + 3 screenshots (now in `inbox/_processed/`). `profiles/bestguess.yaml` (gitignored, values below) has every gate field `calibrated`; the dense replay scores **97.5% over 396 frames**; `gate_status("bestguess")` is **unlocked**. The answer bank holds the 10 facts from the recording. 131 tests, ruff + mypy clean.
- **Verified live earlier today:** party hat click opens My Parties, X closes it. The slot party was *Spooky Party* at 15:00.
- **Jamaa Derby is calibrated (2026-10-26 18:00-19:30)** from `inbox/Recording 2026-10-26 175931.mp4` (three won races). `profiles/jamaa_derby.yaml` (gitignored, values below) has every gate field `calibrated`; dense replay 99.8%; the farmer replayed over the recording reads all three results and jumps where William did. `config.yaml` was created from the example with `arcade_game: jamaa_derby`.
- **Not yet run live:** a Best Guess farming round, a live Derby race (William reported trouble with the *old* farmer in the first GUI launch; the rebuilt one was launched at the end of this session), the 30-min `hunt --no-join` rotation run, `input-test`, `activity-test`, a Double Up Learn session.

## Best Guess profile (stage pixels; `profiles/bestguess.yaml`)
| field | value | how it was measured |
|---|---|---|
| `fact_roi` | `[500, 10, 940, 200]` | parchment banner; two-line facts fit |
| `reveal_word_roi` / `reveal_roi` | `[700, 15, 540, 95]` | banner top line: green *True!* / orange *False!* |
| `arena_anchor_roi` | `[120, 8, 110, 60]` | gem-stack icon, 58% saturated in-arena vs <8% outside |
| `score_roi` | `[210, 8, 150, 60]` | round score, OCR at 3× (30/60/250 read correctly) |
| `round_complete_roi` | `[480, 300, 960, 420]` | ROUND COMPLETE scroll + Leave Game dialog |
| `true_point` / `false_point` | `[450, 700]` / `[1500, 700]` | **chosen from the floor layout, not yet clicked live** |
| `countdown_roi`, `gem_counter_roi`, `own_animal_roi` | null | dial timer (elapsed time is used); no gem HUD; dance learner idle |

Round timing seen: question ≈10 s, reveal ≈6 s, a ROUND COMPLETE scroll every ~10 questions ("9 Correct Answers · 250 GEMS EARNED" at x4). The CORRECT!/WRONG! splash is not used (lime on lime).

## Jamaa Derby profile (stage pixels; `profiles/jamaa_derby.yaml`)
| field | value | how it was measured |
|---|---|---|
| `strip_roi` | `[100, 400, 1720, 500]` | track strip below our lane; every obstacle is a full-height band |
| `horse_front_x` / `jump_trigger_x` | 940 / 800 | nose / hooves; William jumps with the edge ~140 px past the nose |
| `horse_box_roi` | `[700, 60, 260, 360]` | our brown horse; its top rises ~60 px while airborne (jump postcondition) |
| `jump_lead_s` | 0.08 | latency lead × live scroll speed (~830 px/s, boost ~1150) |
| `boost_button_point` / `boost_box_roi` | `[960, 975]` / `[840, 945, 240, 60]` | three carrots; orange while available |
| `results_box_roi` / `results_rows_roi` | `[300,150,1300,750]` / `[400,190,560,540]` | beige leaderboard; yellow-framed row = ours, rows 86 px apart from cy 52 |
| `startline_roi` / `progress_bar_roi` | `[860,400,80,500]` / `[300,1042,1300,16]` | white start line = WAITING; green bar = on the track |

Race timing seen: ~25 s waiting at the line, ~65 s racing, ~10 s leaderboard, then it restarts by itself. Payout 100 for 1st (75/50/40/30).

## Den Planner (2026-10-27)
Mode **Den Planner** (first in the mode box). On the Den Planner page type hours/minutes until Best Guess (or press *Read from game*), press Start from anywhere in Jamaa: it reads the tray, goes to the den, opens Jamaa Derby in PRO, farms until 90 s before Best Guess, leaves at a leaderboard, then re-checks My Parties every 20 s, joins Best Guess and farms it. Profile: `profiles/navigation.yaml` (points measured from his screenshots; `Calibration` can move them). Events: `planner.eta`, `planner.den`, `planner.derby`, `planner.left_derby`, `planner.*_missing`. Desktop shortcut: `JamCrammer.lnk`.

## How William tests Jamaa Derby now
1. Open Jamaa Derby in the arcade and press Play. In the GUI pick **Arcade Farmer** (the game is `jamaa_derby` in `config.yaml`) and press Start; Dry-run first if you like.
2. The state pill should show WAITING at the line, RACING once GO fires (jumps logged as `derby.jump`, carrots as `derby.boost`), RESULTS on the leaderboard with the place and gems.
3. If the horse jumps late or early, change `jump_lead_s` (bigger = earlier) on the Calibration page; if it never jumps, check `strip_roi` and the colour ranges under `derby:` in `config.yaml`.
4. Any screen that is not the track (Play dialog, arcade room) is UNKNOWN and pauses the farmer after 30 s; leaving and re-entering the game is not automated yet.
5. If the pill says "stopped: the watchdog saw an unknown dialog", look at the game, dismiss anything odd, and press Start again (Start clears the pause).
6. It learns while it plays: a stumble shows as `derby.hit` in the log (with the band's crop under `captures/derby/hits/`) and the adjustment it made as `derby.learned`; the running totals live in `data/derby_learned.json` (delete it to forget). The pill shows `hits` per session.
7. Timing knobs on the Calibration page: `jump_lead_s` (input latency, 0.05), `jump_lead_px_narrow` (how much earlier one- and two-column bands are jumped, 40 px), `boost_lead_s` (0.03 s extra while boosted).

## How William tests Best Guess now
1. Start the GUI (`run.bat` or `.venv\Scripts\python -m jamcrammer gui`), mode **Best Guess Hunter**.
2. Stand in the Best Guess arena (or let the Hunter join it). With the arena on screen the engine skips the hunt and farms at once (`engine.arena_detected`).
3. **Answer mode must be `bank_policy`** (Best Guess page, saved to `config.yaml`); with the default `learn_only` the farmer only records and never walks. First run with **Dry-run** on: the state pill should walk QUESTION_OPEN → LOCKED → REVEAL and the Answer Bank should grow; no clicks are sent.
4. Then Dry-run off for one round: ~3 s into a question (5 s for an unknown fact, after reading the crowd) the farmer holds Left (green = True) or Right (purple = False) for 2 s. If the animal does not walk, run `python -m jamcrammer input-test` to pick scan codes vs virtual keys (`input.key_method`). `move_method: click` switches back to clicking `true_point`/`false_point`.
5. F11 pauses, F12 stops. Any screen that is not the arena (ad, lobby, loading book) shows UNKNOWN and pauses after 30 s.

## Next steps
1. **Watch William's first live round** (log `data/logs/`, events `bestguess.answer` / `bestguess.reveal`). Check the chosen side matches the bank, the score delta learner agrees with the reveal word, and the heartbeat does not fire mid-question.
2. If the My Parties banner for Best Guess gets captured (`inbox/parties/` screenshot while it is listed), add it to `assets/banners/` and `known_labels`; until then the Hunter identifies it by OCR ("best guess").
3. M3 acceptance when William is away: `hunt --no-join --duration 1800`. With him present: `input-test` (scan codes vs vk), `activity-test`.
4. Watch the first live Derby races: do jumps land (no stumbles), are all three carrots used, does the leaderboard read correctly. Then a Double Up Learn session (`learn --target double_up`) for that gate.
5. Experimental later: Super Sort, Falling Phantoms (stubs in `modes/arcade/stubs.py`).

## Commands
```
.venv\Scripts\python -m jamcrammer ingest                                            # inbox -> sessions + report
.venv\Scripts\python -m jamcrammer report <session>                                  # re-analyse (keeps hand labels)
.venv\Scripts\python -m jamcrammer replay <session> --detector bestguess --dense     # gate check on the video
.venv\Scripts\python -m jamcrammer learn --target bestguess                          # live recorder (F6/F7/F8)
```
Inbox files must go in the mapped subfolders (`inbox/bestguess/`, `inbox/parties/`, ...); files at the inbox root are ignored by ingest.

## Open questions (asked William 2026-10-26, defaults in use)
- ntfy phone pushes + topic? (default: off)
- Owns the Double Up den arcade machine? (default: no; entry route Games menu → Sol Arcade)

## Waiting on William's captures
- A My Parties screenshot while *Best Guess Game* is listed (banner template).
- Full-window My Parties screenshot (current fixture is a 704×552 panel crop).
- Popup screenshots (still-playing, logged-out, Play Now) → `inbox/popups/`.
- A Double Up Learn session.

## Risks
- Derby: the jump lead was fitted to a 30 fps recording of William's own timing; live capture at 20 fps plus input latency may need `jump_lead_s` nudged (0.05-0.12). Boost clicks land on the HUD button, which also counts as 'click anywhere to jump', so they are only sent on clear stretches.
- `true_point`/`false_point` are untested live; a wrong spot could land on a name tag (harmless click) or outside the floor (no move). Dry-run first.
- The recording was 1280×720 upscaled to the 1920×1080 stage; OCR on the live 1920×1080 capture should only be better, but colour thresholds were measured on the upscaled video.
- Stage mapping assumes uniform 16:9 scaling centered in the client; verified for fullscreen and maximized.
- Toast banners are hidden while AJC is fullscreen; HIGH alerts rely on the repeating sound and ntfy/Discord once configured.
- windows-capture 2.0.1 segfaults if a WGC session is opened after an mss session in the same process. The app never mixes them.
- No gem counter in the HUD on this account; the Best Guess farmer uses the round score instead, the Gem Ledger relies on end screens.
