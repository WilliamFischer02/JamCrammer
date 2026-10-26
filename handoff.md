# Handoff

_Last updated: 2026-10-26 (M10 built; M11 next)_

## State
- **M0 done:** venv (Python 3.14), pyproject, ruff + mypy (strict core/platform) + pytest green, skeleton, inbox READMEs, `run.bat` placeholder window verified.
- **M1 done:** window discovery + DPI, WGC/mss capture with benchmark, SendInput controller (bounds-checked, postcondition retry, dry-run), kill/pause/lease/presence/hotkeys primitives, SQLite store, JSONL logging, **CLI emergency recorder** `learn --target bestguess` (keyframes + ring-buffer marker dumps + local input log + OCR snapshots + meta.jsonl). Verified live: `snap` PNG read and correct; 15 s recording produced a valid session folder.
- **M2 done:** Popup Watchdog (templates → green+OCR → OCR → unknown-modal), Anti-AFK + room-drift guard, notifications (toast/sound verified live; ntfy/Discord wired, disabled until configured), Gem Ledger, profiles system (`profiles/shared.example.yaml`), replay frame sources, synthetic popup generators, `test-notify` and `activity-test` commands. 68 tests green.
- **M3 built:** Party Watcher (title/GO NOW/X templates, banner rows via border-ring components, pHash + OCR identification, sightings → rotation log, unknown-banner capture), Best Guess Hunter A/B (`hunt` CLI; HIGH alert; auto-join → Learn-only recording), stage-space coordinate layer (`vision/stage.py`), presence detection in the hunt loop. 85 tests green incl. fixture banner ID and a simulated Best Guess HIGH alert/join.
- **M4 done:** PySide6 GUI shell (Jamaa Night tokens + generated QSS, SVG icon set, app ICO), engine facade + Qt bridge, nav rail (12 pages, Ctrl+1-9, collapsible), command bar (mode, state pill, Start/Stop/Pause, Dry-run, hotkeys, lease), Dashboard (mode cards, live preview with overlay, stat tiles with sparklines, timeline), mode pages with profile checklists, Learn (recorder + inbox drop + sessions), Calibration (zoom/pan canvas, pixel inspector, field chips, point picking, save), Answer Bank (search/edit/CSV), Parties (timeline, sightings, unknown-banner labeling), Profit Lab, Notifications (tests), Settings (save config.yaml), Logs, mini HUD, tray, onboarding wizard, overlap warning. UI QA screenshots at 100%/150% reviewed (docs/ui/).
- **M5 done:** inbox ingest (PNG/MP4 → stage-normalized sessions, tags from filenames), analysis pipeline steps 1-9 (`jamcrammer/learn/analyze/`), Learn Report (`report.html`), proposed profiles + template crops, generic replay harness (`replay <session>`), review wizard (States → ROIs → Clicks → Answers → Replay → Accept), Learn page wired (Import inbox, Review wizard, Learn Report). Verified on the synthetic Best Guess session: states, fact/countdown/gem regions, True/False points, 4 rounds labeled, answers seeded, proposal written, replay 100%.
- **M6 done:** Calibration canvas with drag handles (move/resize), Draw ROI, Sample color → HSV range, Crop template → assets, point picking, inline editing, mark calibrated/uncalibrated, save; live-mode gate (`core/gate.py`) shown on Calibration and mode pages; replay results feed the gate. UI QA re-captured.
- **M7 built:** `vision/grid.py` (grid geometry, tile descriptors, mutual-best pairing), `modes/arcade/double_up.py` (preview burst → pair → click/verify → end-screen OCR → Play Again; reveal-and-remember fallback), `farm` CLI + engine mode behind the gate, simulated Double Up world tests (perfect round, fallback, gate refusal). **Live rounds need William's Learn session** (`learn --target double_up`, 2-3 manual rounds) to calibrate the grid.
- **M8 built:** `modes/bestguess/{state,brain,learner,farmer}.py` (Phase C state machine, profile-driven detector with countdown fallback, fuzzy bank with majority on conflicts, learner vote, bg_rounds rows, stats), engine hand-off from hunt join → farmer when the gate is open (else Learn-only), review wizard state naming → replay scores the real detector. Synthetic: detector ≥95%, farmer 3/4 correct with learning. **Needs William's Best Guess Learn session** to calibrate fact/true/false/reveal ROIs and pass the gate.
- **M9 built:** `modes/opportunist.py` (ArcadeEntry leave/enter with verification, Opportunist loop with boundary checks, overhead events, hop to Best Guess, fallback to Hunt), engine `opportunist` mode behind the Double Up gate + entry route, `opportunist` CLI, Profit Lab overhead line. Simulated tests: boundary checks + re-entry, hop, fallback, route gate.
- **M10 built:** `modes/arcade/derby.py` (rolling-background obstacle detection, class-based jump lead, boosts on clear straights, results OCR, Pro tracking), `profiles/jamaa_derby.example.yaml`, `farm --game jamaa_derby`, gate entries, Profit Lab rows per game + opportunist overhead. Synthetic: 10/10 first places. **Live Derby needs a Learn session** (lane ROI, horse x, boost button, results ROIs, race-again point).
- **Verified live:** party hat click opens My Parties, X closes it (panel probe 14:59). Current slot: *Spooky Party*.
- **Not yet run live:** the 30-min `hunt --no-join` rotation run (M3 acceptance), `input-test` with real keys, the Activity Test. William appeared to be at the keyboard (window un-fullscreened, room changed to Coral Canyons ~15:05), so no further input was sent.

## How to record Best Guess the moment it appears
```
cd C:\dev\JamCrammer
.venv\Scripts\python -m jamcrammer learn --target bestguess
```
Then join the party and play. **F6** mark, **F7** I was correct, **F8** I was wrong, **F12** or Ctrl+C to stop. Nothing is sent to the game. Add `--video` for a continuous 5 fps mp4 as well. Drop OBS recordings in `inbox\bestguess\` as a backup.

## Next steps
1. **M3 acceptance, when William is away:** `python -m jamcrammer hunt --no-join --duration 1800` → rotation log printed at the end; confirm the HIGH sound fires if Best Guess appears. Then `hunt` (auto-join on) for real.
2. **M11:** Gem Ball auto-tuner (hold-duration sweep → outcome histogram → deterministic or not), Swoopy Eagle prototype (small-ROI 60 fps loop, PD flap controller), Super Sort / Falling Phantoms stubs, README walkthrough reproducible from a clean clone.
3. With William present: `python -m jamcrammer input-test` (then `--method vk` if scan codes don't move the animal) → record in `decisions.md`, set `input.key_method`. Then `python -m jamcrammer activity-test` (24 min) → set Anti-AFK defaults from the result.
3. Popup templates: when `inbox/popups/` gets the still-playing / logged-out / Play Now screenshots, crop them into `assets/templates/popups/` and flip the template fields in the shared profile to `calibrated`.

## Open questions (asked William 2026-10-26, defaults in use)
- ntfy phone pushes + topic? (default: off)
- Owns the Double Up den arcade machine? (default: no; entry route Games menu → Sol Arcade)

## Waiting on William's captures
- Best Guess: a Learn session (command above) or OBS video → `inbox/bestguess/`.
- Full-window My Parties screenshot (current fixture is a 704×552 panel crop).
- Popup screenshots (still-playing, logged-out, Play Now) → `inbox/popups/`.

## Risks
- Stage mapping assumes uniform 16:9 scaling centered in the client; verified for fullscreen (identity) and maximized (0.938). If William plays in a small window, OCR on the upscaled stage may degrade.
- Toast banners are hidden while AJC is fullscreen (Focus Assist); HIGH alerts rely on the repeating sound and ntfy/Discord once configured.
- windows-capture 2.0.1 segfaults if a WGC session is opened after an mss session in the same process (see decisions.md). The app never mixes them; keep it that way.
- The gem counter is not visible in the Crystal Sands HUD on this account (non-member?). Gem Ledger OCR needs a screen where it shows (mini-game end screens, or the HUD elsewhere).
