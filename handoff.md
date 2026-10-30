# Handoff

_Last updated: 2026-10-30 (The Mystery Below: level model, menus by state, moth chamber from a plan)_

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

## The Mystery Below: level model (2026-10-30, laptop branch, not run live yet)
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
