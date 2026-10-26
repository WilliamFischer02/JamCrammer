# JamCrammer — Master Kickoff Spec (v1 · 2026-10-26)

> **How to use this file:** Save it as `C:\dev\JamCrammer\docs\KICKOFF.md`. It is the source of truth for the project. Read it completely before writing any code. `CLAUDE.md` must point here, and every later session starts by re-reading `CLAUDE.md`, `handoff.md`, and this file.

---

## 0. Your role

You are a **principal-level Windows desktop and game-automation engineer** with decades of shipped experience in:

- **Win32 internals:** window management, per-monitor DPI, SendInput, focus rules, Windows Graphics Capture.
- **Real-time computer vision:** OpenCV, template and feature matching, OCR pipelines, temporal state inference.
- **Game-client behavior:** loops, timers, popups, anti-idle systems.
- **Desktop UX in Qt** for tools that run unattended for days.

You build tools that are boring in the best way: every action is verified, every wait has a timeout, every failure degrades to a safe pause, and every decision is logged.

**Engineering standards (non-negotiable):**

- **Toolchain:** Python 3.12 (3.11+ OK), a venv, `pyproject.toml`, `ruff`, `mypy` (strict on `core/` and `platform/`), and `pytest`.
- **Types and config:** typed code; dataclasses/pydantic for config and messages; no magic numbers (they belong in config or profiles).
- **Postcondition checks:** every input action is followed by a check (did the panel open? did the side light up?). If the check fails, retry once, then pause and alert.
- **Responsive GUI:** the GUI thread never blocks. Capture, vision, OCR, and input all run in workers and talk over an event bus.
- **Logging:** structured logs (JSONL, rotating) plus a human-readable log.
- **Replayability:** every mode can run offline against recorded sessions (replay harness), so detectors are developed and regression-tested without the live game.
- **Performance budget:** capture + detect under 100 ms per frame at 4–5 fps, and steady-state CPU under ~15% on a modern desktop.

## 1. Who you're working for

William. He wants blunt, structured updates: TL;DRs, checklists, short sections, no fluff. Batch your questions into one message. Don't stall waiting on answers you can discover yourself, and use defaults while you wait.

## 2. Product summary

**JamCrammer** is a Windows desktop app that automates **Animal Jam Classic (AJC, desktop app)** while William is away, controlled from a polished GUI.

| Mode | What it does |
|---|---|
| **Best Guess Hunter** | Waits for the *Best Guess Game* party to rotate into My Parties, joins it, and farms it with OCR plus a self-learning answer bank. |
| **Arcade Farmer** | Farms the most profitable always-available mini-game (see §9; primary target **Double Up**). |
| **Opportunist** | Farms the arcade game, checks the party list every 5 minutes between rounds, and hops to Best Guess the moment it appears. |
| **Learn** | A one-click "first contact" system. William plays a game manually; JamCrammer records it, works out the UI states and regions on its own, proposes calibration, and seeds the answer bank. It also ingests his PNG screenshots and OBS videos. |

**Shared services:** Popup Watchdog, Anti-AFK, Safety, Notifications, Gem Ledger (real earnings via HUD OCR), Profit Lab (gems/hr per mode), Rotation Log.

## 3. Hard boundaries (do not cross, even if a later message asks)

- **Screen-only:** capture pixels and send ordinary OS-level mouse/keyboard input.
  - No reading or writing game memory.
  - No packet capture or injection.
  - No decompiling, modding, or patching the client.
- **No disguise:** nothing designed to hide the automation from the developer (no "humanized" input to dodge detection, no driver-level input spoofing).
- **Click scope:** only click inside the AJC client area.
- **Login:** never store or type credentials. If logged out, stop and alert.
- **Risk:** William knows botting violates AJC's ToS and accepts the account risk. Don't re-litigate it.

## 4. Ground truth (verify on screen; treat as hypotheses until confirmed)

### 4.1 Environment
- Windows 10/11 desktop. The AJC desktop app (Flash-era game wrapped in a desktop app). The repo lives at `C:\dev\JamCrammer`.
- Discover the exact window title, monitor layout, resolution, and display scaling yourself via the Windows API.

### 4.2 My Parties panel (fixture: `tests/fixtures/my_parties_2026-10-26.png`)
- **Opening it:** click the party-hat button on the HUD.
- **Layout:**
  - Title bar "My Parties", with a round blue **X** close button at the top-right.
  - Two large pinned banners that are **not parties**:
    - **Adventure Base Camp:** a shortcut to the adventures hub room.
    - **Bitter Sweets:** the current seasonal adventure.
  - Below them is the **party slot**: a smaller banner with a blue **GO NOW!** button on its right. Support N slots, in case more than one GO NOW row (or a scroll) appears.
- **Rotation is confirmed live.** On 2026-10-26 the slot showed *Haunted Forest Party* at ~13:34 MDT and *Paradise Party* by ~14:08 MDT. The wiki says temporary parties change every 30 minutes; the Rotation Log must confirm the real period and phase.
- **Best Guess Game** appears **in the party slot** as its own banner. Its GO NOW drops you straight into the Best Guess arena (the room *is* the game).
- Players stay inside a party after its banner expires (no kick, no popup), so one join per session is enough.
- A banner can show GO NOW before the party actually starts; clicking then produces a "wait" popup.
- Assume the panel does **not** refresh while open: every scan is close, then reopen.
- The party-hat icon historically flashed when a new party started. Check whether it still does; if so, use it as a cheap "rescan now" trigger.

### 4.3 Idle and popups
- **Idle popup:** after ~6.5 min with no interaction, a "still playing?" popup with a **green button** appears. If it isn't clicked within 30 s, a **logged-out** popup appears, and its OK button returns to the **Play Now** screen.
- **What counts as activity:** UI-only clicks may not count (browsing a shop window doesn't). Assume party-menu clicks don't count and avatar movement does. **Verify with the Activity Test (§7.3).**
- **Popup quirks:**
  - The logged-out popup also appears on connection loss.
  - The idle popup can glitch-appear even while active.
  - Some mini-games (e.g., Overflow) have their own "Are you still playing?" popup.

### 4.4 Best Guess Game
- **Screen:** a true/false animal fact appears at the top of the screen. Walk **LEFT** (green, check mark) for **True** or **RIGHT** (purple, X) for **False**; clicking a spot makes the animal walk there.
- **Timing:** ~10 s to answer, with a 5 s warning; after that you can't switch sides. Joining with ≤5 s left means waiting for the next round.
- **Reveal and payout:** your animal dances if you were correct. 10 gems per correct answer, multiplier up to 4x, reset to 1x on a miss.
- **Constraints:** the fact pool changes over time. You can't change animal or look inside. Underwater animals render broken there, so use a land animal.

### 4.5 Double Up (primary arcade target — see §9)
- **Where:** Crystal Sands, the Sol Arcade, and a den arcade-machine version. Not members-only. Single-player.
- **How it plays:** a memory game with **8 pairs (16 tiles)**. The board is shown for **3 s**, then hidden. Click two tiles at a time. The round ends when all pairs are found or **60 s** runs out.
- **Payout:** **3 gems per match + 20 bonus for all 8 = 44 max per round.**
- **Achievements:** finish in 15 s; finish with no mismatches.
- **Unknown (Learn must answer these):** grid geometry; whether positions shuffle each round; the replay/Play Again flow; whether it's in the Games menu; whether the **den machine** version pays gems (if it does, parking in the den becomes the ideal loop).

### 4.6 Jamaa Derby (secondary arcade target)
- **Where:** Jamaa Township, the Sol Arcade, and the Horses Only Party. Non-members race a default horse.
- **Field:** 6 racers per race; the wiki mentions robot players, so lobbies fill.
- **Controls:** **Space** (or right-click) to jump. Three single-use **carrot boosts** via a button; boosts don't stack.
- **Payout by place:** Normal 100/75/50/40/30/25 · Pro 150/125/100/75/50/25. Pro unlocks after **10 first-place wins** on Normal.

### 4.7 Other mini-game facts that affect shared services
- Overflow has its own in-game "Are you still playing?" popup. Expect other mini-games may too — the watchdog handles per-mode templates.
- The Claw's spacebar auto-play and plushie recycle values were cut on 2025-03-06. Don't build claw farming.

## 5. Architecture

```
jamcrammer/
  __main__.py            CLI: gui | hunt | farm | opportunist | learn | ingest | replay | snap | report | test-notify | activity-test
  core/
    config.py            app config (pydantic) — config.yaml
    profiles.py          per-mode UI profiles (points, ROIs, templates, colors) — profiles/*.yaml
    events.py            thread-safe event bus (surfaced to Qt as signals)
    store.py             SQLite (data/jamcrammer.db): sightings, rounds, answers, events, ledger
    safety.py            hotkeys, user-presence detection, input lease, dry-run
    scheduler.py         ModeManager: exactly one mode holds the input lease; watchdog can preempt
    logging.py           JSONL + human logs, rotation
  platform/
    window.py            find/validate the AJC window, client rect, DPI, focus, resize detection
    capture.py           Windows Graphics Capture backend (primary) + mss fallback; benchmark both
    input.py             SendInput mouse/keys, bounds-checked, with postcondition hooks
  vision/
    ocr.py               Windows OCR (winocr) + preprocessing; Tesseract optional fallback
    match.py             templates, phash, color sampling, ORB, scene fingerprints
    grid.py              tile-grid segmentation + descriptor matching (Double Up)
  services/
    watchdog.py          popup detection and handling (§7.1)
    antiafk.py           lobby movement pairs, in-game heartbeat, room-drift guard (§7.2)
    notify.py            Windows toast, sound, ntfy.sh push, Discord webhook
    ledger.py            HUD gem OCR, per-round and per-session earnings
  modes/
    base.py              GameMode interface: required_profile_fields, detect_state(frame), step(state) -> actions, stats
    party_watcher.py     panel open/scan/close, banner identification, rotation log, auto-join
    bestguess/           state machine, detectors, brain (answer bank), learner, actor
    arcade/double_up.py  Double Up farmer (§9.2)
    arcade/derby.py      Jamaa Derby farmer (§9.3)
    arcade/gem_ball.py   experimental auto-tuner (§9.4)
    arcade/swoopy.py     experimental low-latency prototype (§9.4)
    opportunist.py       orchestrator (§10)
  learn/
    recorder.py          keyframes + ring buffer + markers + user input log
    ingest.py            inbox PNG/MP4 import
    analyze/             scene clustering, text-region discovery, temporal model, outcome labeling, profile proposal
    report.py            HTML Learn Report
  replay/                offline harness: run any mode against a recorded session, no input
  app/                   PySide6 GUI (§12)
assets/   banners/ templates/<mode>/ icons/ (SVG only) app.svg
profiles/ *.example.yaml (committed) — real profiles gitignored
inbox/    (see §11.1)
captures/ sessions/ unknown/ unknown_popups/  (gitignored)
data/     jamcrammer.db, logs/  (gitignored)
tests/    fixtures/ synthetic/ replay/
scripts/  setup.ps1 (venv + deps), run.bat (launch GUI), make_icons.py (SVG → multi-size ICO)
docs/     KICKOFF.md (this file)
CLAUDE.md  decisions.md  handoff.md  README.md
```

**Dependencies (pin versions):** PySide6, pyqtgraph, qtawesome (or bundled Lucide SVGs), opencv-python, numpy, mss, windows-capture (or winsdk WGC), pywin32, pydirectinput, pyautogui, keyboard, pynput, winocr, rapidfuzz, imagehash, Pillow, scikit-learn, pyyaml, pydantic, requests, winotify, pytest. Test which input path AJC accepts (pydirectinput scan codes vs pyautogui) and record the result in `decisions.md`.

**Capture note:** Windows Graphics Capture can capture the AJC window even when other windows overlap it. Input still lands wherever the cursor is, though, so the AJC window must stay visible and unobstructed. Recommend a dedicated monitor.

## 6. Data model (SQLite)

| Table | Contents |
|---|---|
| `party_sightings` | banner_id, label, first_seen, last_seen, slot_index |
| `events` | ts, level, source, type, payload_json, screenshot_path |
| `bg_facts` | normalized_text PK, display_text, answer (T/F), seen_count, confirmed_count, conflict_count, last_seen, source (learned/manual/import/learn-session) |
| `bg_rounds` | ts, fact_key, ocr_text, ocr_conf, match_score, chosen, outcome, multiplier, gems_est |
| `arcade_rounds` | ts, game, duration_s, gems_reported, mismatches, notes |
| `ledger` | ts, hud_gems (OCR), delta, mode |
| `sessions` | learn and farm sessions, with paths |

## 7. Shared services

### 7.1 Popup Watchdog (highest priority, all modes)
- Runs on every capture tick (≥2 fps), independent of the active mode, and preempts it.
- **Still-playing popup:**
  - Detect by template match (fixture `tests/fixtures/popup_still_here.png` once William supplies it). Until then, use a heuristic: a centered modal dialog plus a saturated green button.
  - Click the green button and confirm it's gone within 3 s.
  - Count these popups per hour. If any appear while Anti-AFK is on, alert "anti-AFK input isn't registering."
- **Logged-out popup or Play Now screen:** stop all modes, send a HIGH alert, save a screenshot. No auto-login.
- **Game-specific idle popups** (e.g., Overflow's): templates per mode, handled the same way.
- **Unknown modal dialogs** (trade or buddy requests, etc.): screenshot to `captures/unknown_popups/`, alert, pause clicking. Never guess-click.

### 7.2 Anti-AFK
- **Lobby movement:** a **cancelling pair** within each cycle — tap a direction, then its opposite, same duration (default 200 ms each, 300 ms apart). Rotate pairs between cycles: (Left, Right) → (Up, Down) → (Right, Left) → (Down, Up). Net displacement stays ≈ 0, so layouts never drift.
- **Focus:** make sure the game has keyboard focus *without* clicking walkable floor. Find a safe focus target during calibration and verify it.
- **Room-drift guard:** at start, store a baseline fingerprint of the room (downscaled background, excluding the HUD and the animal's area). If it changes past a threshold, stop movement, send a HIGH alert, save a screenshot.
- **In-game heartbeat:** each mode declares how it stays active. For Best Guess, if there's been no bot input for 180 s, click the side we're already standing on.
- **Parking spot (README):** William's own den, locked, standing mid-room away from the exit.

### 7.3 Activity Test (run once with William; record results in `decisions.md`)
1. Eight minutes of party-panel open/close only. Does the idle popup appear?
2. Eight minutes of movement pairs only. Does it appear?
3. Eight minutes inside a mini-game (Double Up rounds). Does it appear?

Set the defaults from the results.

### 7.4 Safety
- **Global hotkeys:** **F12 = kill immediately**, **F11 = pause/resume**, **F9 = start/stop the selected mode**. Learn markers: **F6** "mark", **F7** "correct", **F8** "wrong".
- **User presence:** if the mouse moved or a key was pressed since the bot's last action, assume William is back. Release the input lease, show "Paused — user active", and resume after `resume_after_idle_s` (default 120). Careful: `GetLastInputInfo` also counts the bot's own injected input, so track your own input timestamps.
- **Click bounds:** every click is bounds-checked to the client rect; refuse anything outside it.
- **Dry-run:** capture, analyze, and log, but never send input.
- **Window changes:** if the window is resized, moved, or loses its aspect ratio, pause and require a profile re-check.

### 7.5 Notifications
- **Channels:** Windows toast, sound, ntfy.sh push (topic in config), optional Discord webhook. Each is a toggle and has a test button.
- **Levels:** INFO (log only), NOTICE (toast), HIGH (toast + repeating sound + push).
- **HIGH events:** Best Guess found or joined, logged out, room drift, unknown popup, repeated detector failure.

### 7.6 Gem Ledger
OCR the HUD gem total when it's visible (lobby and party rooms) and read each mini-game's end-screen payout. These are the **real** earnings numbers. Every mode reports estimated *and* ledger-verified gems.

## 8. Mode 1 — Best Guess Hunter

### 8.1 Phase A: Hunt
- **Cycle period:** **300 s by default** (William's spec). Once the Rotation Log knows the rotation phase, add a scan ~30 s after each boundary.
- **Each cycle:**
  1. If My Parties is open, close it via the blue X and confirm it closed.
  2. Anti-AFK movement pair (§7.2).
  3. Open My Parties and wait for the panel (template on the title bar). On timeout: screenshot, retry once, alert.
  4. Segment the banners. Classify each as a pinned adventure or a party slot (has GO NOW).
  5. Identify each party-slot banner:
     - perceptual hash against `assets/banners/`;
     - OCR of the banner title (stylized font: upscale, threshold, fuzzy match);
     - if unknown, save the crop to `captures/unknown/`, log it, and queue it for labeling in the GUI.
  6. Log the sighting (first_seen / last_seen) to build the rotation history.
  7. If the target is not found, close the panel and confirm it closed.
- **Seed templates:**
  - Crop seeds from the fixture: Adventure Base Camp, Bitter Sweets, Haunted Forest Party.
  - Paradise Party is unlabeled until it's seen live.
  - Look for a reference image of the "Best Guess Game" party banner on the AJ Classic wiki (animaljam.fandom.com — the Parties page or the Best Guess Game page). If you can't find one, rely on OCR plus unknown-banner capture.

### 8.2 Phase B: Join
- When Best Guess is identified, send a HIGH alert.
- If **Auto-join** is on:
  1. Click its GO NOW.
  2. If the "wait" popup appears, retry every 30 s, capped.
  3. Confirm arrival in the arena (green/purple split scene).
- If **Start farming after join** is on, move to Phase C. If the profile is incomplete, start **Learn-only** (records everything) instead.

### 8.3 Phase C: Farm
- **States:**
  - Main loop: `WAITING_FOR_ROUND → QUESTION_OPEN → ANSWER_COMMITTED → LOCKED → REVEAL → (learn) → WAITING_FOR_ROUND`.
  - Exception states: `INACTIVITY_POPUP`, `DISCONNECTED`, `UNKNOWN`. Entering `UNKNOWN` saves a screenshot and logs it; if it persists past N s, pause and alert.
- **Detectors** are driven entirely by `profiles/bestguess.yaml` (ROIs, templates, color thresholds). Ship placeholder ROIs marked `uncalibrated`: fact text top-center, True zone left, False zone right.
  - **Live-mode gate:** live farming stays LOCKED until the required fields exist *and* the replay harness hits **≥95% state accuracy** on at least one recorded session. The GUI shows exactly what's missing.
- **Brain:**
  - OCR the fact ROI.
  - Normalize the text: lowercase, strip punctuation, collapse whitespace, fix common OCR confusions.
  - Fuzzy-match against `bg_facts` (rapidfuzz `token_set_ratio`, default threshold 90, plus a length-ratio check). If records conflict, take the majority.
  - **Commit within 2–4 s**, then verify the chosen side lit up; re-click once if not.
- **Unknown-fact policy** (GUI selectable):
  - learned prior (whichever side wins more often in the bank) — default;
  - fixed side;
  - skip (still counts as activity via heartbeat).
- **Answer modes:** Learn-only (never clicks) · Bank + policy · Coin-flip (baseline for comparison).
- **Learner:** pluggable "which answer was correct" strategies. Real recordings will decide which one works; Learn's analysis ranks them.
  - reveal-indicator ROI (color or template);
  - own-animal dance detection (frame diff in a calibrated ROI);
  - gem-counter delta.
- **Stats:** rounds, accuracy, current streak, multiplier, estimated gems (10 × multiplier, capped at 4x), ledger gems.

## 9. Mode 2 — Arcade Farmer

### 9.1 Target selection (research summary, 2026-10-26)
The goal is the best **(gems/hr × bot reliability)** for an always-available, non-member game. Profit Lab (§12.2) settles the final ranking with measured numbers.

| Rank | Game | Documented payout | Inputs / bot difficulty | Verdict |
|---|---|---|---|---|
| **1** | **Double Up** | 3/match + 20 for all 8 = **44/round**; 60 s limit; 3 s preview | Clicks only; the board is fully visible during the preview, so the game is effectively deterministic for a bot. **Easy.** | **PRIMARY.** Highest certainty of near-100% perfect rounds. Est. (unverified) ~90–130 gems/min if a round plus overhead is ~20–30 s |
| 2 | **Jamaa Derby** | Normal 100/75/50/40/30/25 · Pro 150/125/100/75/50/25 by place; Pro unlocks after 10 first-place wins; 6 racers, robot players fill | Space (or right-click) to jump at obstacles + 3 single-use carrot boosts (don't overlap). **Medium** (timing CV in one lane). | **SECONDARY.** Speedrunners historically camped Derby at ~1.5 gems/s |
| 3 | Swoopy Eagle | 5 gems per gap passed, endless until a crash | One input (click/Space/Up). Flappy-style; needs a low-latency 30–60 fps loop. **Medium-hard.** | Experimental. Highest *uncapped* ceiling if the controller is stable |
| 4 | Falling Phantoms | Up to 400/round surviving with all bonuses; ~5 if out first; bots fill | Left/right arrows dodging. Reaction-critical, dense final phase. **Hard.** | Experimental |
| 5 | Super Sort | Formula undocumented; display caps at 999 but pays in full; Easy levels repeat after level 20 | Binary color classification (pink plastic vs blue paper) → arrow key (item goes *opposite* the key). **Medium.** | Experimental. Measure the payout first |
| 6 | Gem Ball | Max 255/round (10 balls; 3-gem top slot; 75-gem jackpot for lighting all 3 Zios faces) | One click-and-hold (power). **Easy if deterministic.** | Experimental auto-tuner (§9.4) |

**Rejected (with reason):**
- **Needs a real human or depends on others:**
  - Best Dressed: placement depends on other players' votes, and you get 0 gems if you don't vote.
  - Buddy games (Tic Tac Toe, RPS, Marbles, Shell Game, Four Gem): need a real opponent.
- **Not worth farming:**
  - The Claw / Octopus Claw: plushie recycling was gutted on 2025-03-06, and it costs gems per play.
  - Phantom Invasion, food machines, Brady's Chemistry Set: no gem payout.
  - Dunk-A-Phantom: costs gems, pays tickets, and is seasonal.
- **Gated:** Disc Toss and Ducky Dash need members-only pets.
- **Low rate:** Hedge Hog, Touch Pool.
- **Revisit later** (solver- or physics-heavy for uncertain per-minute payout): Overflow, Pill Bugs, Gem Breaker, Fruit Slinger, Phantom Fighter, Phantom's Treasure, Eat 'Em Up, Sky High, Wind Rider.

### 9.2 Double Up farmer (primary — build first)
- **Entry route:** Learn discovers and calibrates it — Games menu (preferred) → Sol Arcade → Crystal Sands. **Also test the den arcade-machine version:** if it pays gems, the ideal loop is den-parked with no navigation.
- **Per round:**
  1. Wait for the board state, then **burst-capture during the 3 s preview** (≥10 fps).
  2. Segment the tile grid (geometry from the profile; auto-proposed by Learn from William's clicks).
  3. Build a descriptor per tile: downscaled LAB patch + color histogram, with an ORB fallback.
  4. Pair the 16 tiles by best mutual matches, with a confidence score.
  5. If pairing confidence is below threshold, **fall back to the classic reveal-and-remember strategy**, which still finishes well within 60 s.
  6. Click each pair, wait out the flip animation (a timing value in the profile), and verify the match as a postcondition. On a mismatch, update memory.
  7. At the end screen, OCR the gems earned, click Play Again (or exit and re-enter), and write an `arcade_rounds` row.
- **Targets:**
  - ≥98% perfect rounds (8/8, no mismatches) after calibration;
  - median round time measured and shown;
  - aim for under 20 s of play per round. The "Fast Match!" (15 s) and "Perfectionist!" achievements are side effects.
- **Activity:** apply the Activity Test result. If in-game clicks don't count as activity, insert a lobby movement pair every N rounds (exit → move pair → re-enter), with N computed from the measured round time.

### 9.3 Jamaa Derby farmer (secondary — after Best Guess Phase C)
- **Learn first:** calibrate from 2–3 manual races — the lane ROI, the horse's x-position, obstacle templates, and the start/finish screens.
- **Jumping:** detect obstacles entering the horse's lane and press **Space** at a calibrated lead distance. Handle the "short obstacle followed by another" case with an earlier jump, and the longest obstacles with a last-moment jump. Profile the timing per obstacle class.
- **Boosts:** click the carrot button on clear straights, never overlapping an active boost.
- **Results:** OCR the finishing place and gems. Track progress toward the **Pro unlock (10 first-place wins)**, then switch to Pro (150 for 1st).
- **Target:** ≥90% first-place finishes before Profit Lab compares it against Double Up.

### 9.4 Experimental targets (framework hooks now, implementations last)
- **Gem Ball auto-tuner:**
  1. Sweep the hold duration in 10–25 ms steps across the usable range.
  2. Detect the landing slot or gem delta per ball and build an outcome histogram per duration.
  3. If one duration hits the top slot or jackpot ≥70% of the time, exploit it. Otherwise mark the game "non-deterministic" and stop.
- **Swoopy Eagle prototype:**
  - dedicated low-latency loop: small-ROI capture at 60 fps (WGC);
  - track the bird's y-position and the next gap's center;
  - flap rule plus a simple PD controller;
  - log the gaps-per-run distribution.
  Only start this after M10.
- **Super Sort / Falling Phantoms:** stubs only, with notes in `handoff.md`.

## 10. Mode 3 — Opportunist (orchestrator)
**Today's real workflow:** William farms an always-available game but wants Best Guess the moment it rotates in.

**Loop:**
1. Run Arcade Farmer rounds.
2. Every `opportunist_check_s` (default 300), at the next **round boundary** (never mid-round):
   a. Exit the mini-game cleanly.
   b. Do the Hunt-cycle scan (§8.1, steps 1–7).
3. If Best Guess is present, go to Best Guess Hunter Phase B, then C.
4. Otherwise, re-enter the arcade game through the calibrated entry route and continue.

**Rules:**
- Measure and display the overhead of each check (seconds lost) in Profit Lab.
- If re-entry fails twice, fall back to plain Hunt mode and alert.

## 11. Learn System — "First Contact"
**Goal:** the first time William plays any target game, he presses one button and plays normally. JamCrammer captures everything needed to calibrate and seed that mode, with minimal manual tagging.

### 11.1 Inbox (for files William drops manually)
Create this tree, each folder with a `README.md` that explains what goes there and its naming. The contents are gitignored except the READMEs and `.gitkeep`.

```
inbox\
  bestguess\      PNG screenshots + OBS .mp4/.mkv of Best Guess
  parties\        My Parties panel screenshots (incl. Best Guess banner when seen)
  popups\         idle popup, logged-out popup, Play Now screen, unknown dialogs
  arcade\double_up\  arcade\jamaa_derby\  arcade\gem_ball\  arcade\other\
  _processed\     originals moved here after ingest (never deleted)
```

- **Optional filename tags** pre-label frames: `__question`, `__locked`, `__reveal_correct`, `__reveal_wrong`, `__popup_still_here`, `__popup_logged_out`, `__party_panel`. Example: `bg_001__reveal_correct.png`.
- **Ingest triggers:** `python -m jamcrammer ingest`, or the GUI "Import inbox" button, or a folder watcher when the GUI is open.
  - Videos are decoded with OpenCV (or ffmpeg if present) into change-triggered keyframes.
  - Everything becomes a normal Learn session under `captures/sessions/<timestamp>_<source>/`.
- **When William tells you "new files in the inbox":**
  1. Run ingest and analysis.
  2. **Read the key frames yourself** (Claude Code can view PNGs).
  3. Review the Learn Report.
  4. Update the profiles, templates, and tests.
  5. Report back with a checklist of what is now calibrated and what's still missing.

### 11.2 Recorder (live)
- **Start:** GUI → Learn → pick a target (Best Guess / Double Up / Jamaa Derby / Gem Ball / My Parties / Custom) → **Start Learning**. The CLI equivalent is `learn --target bestguess`. **Ship a minimal CLI recorder early (Milestone 1)** so it's ready the moment Best Guess rotates in, even if the GUI isn't finished.
- **Capture streams:**
  - change-triggered keyframes (perceptual diff threshold);
  - a 5 fps ring buffer saved ±5 s around every marker or detected event;
  - optional continuous low-fps video.
- **Input log:** William's own clicks and keys (pynput, local only), timestamped and mapped to client coordinates. During a learn session, nothing is ever sent to the game.
- **Markers:** F6 / F7 / F8 (§7.4).
- **Output:** `meta.jsonl` with the window rect, DPI, timestamps, OCR snapshots, input events, and markers.

### 11.3 Analysis pipeline (offline, re-runnable, generic across games)
1. **Stability:** dedupe frames; separate stable frames from transitions.
2. **Scene clustering:** pHash + color histograms → agglomerative clustering → candidate screen states, each with representative thumbnails.
3. **Text-region discovery:** full-frame OCR → text boxes.
   - A box at a stable position whose content changes between rounds → **fact/question** candidate.
   - A digits-only box decreasing ~1/s → **countdown** candidate.
   - A digits box that only increases → **gem counter** candidate.
4. **Temporal model:** infer the round cycle (e.g., fact appears → countdown → lock → reveal) and its durations from text events and scene transitions.
5. **Zones from behavior:** cluster William's clicks per state (e.g., left vs right during questions) → True/False target points. For Double Up, his clicks on tiles → grid geometry.
6. **Outcome labeling:** around each reveal, correlate the region of maximal change, the side William chose, the gem-counter delta, and his F7/F8 markers → label correct/wrong and rank the learner strategies by agreement.
7. **Answer seeding:** fact text + outcome + chosen side → `bg_facts` rows with source `learn-session` and a confidence value.
8. **Profile proposal:** write `profiles/<mode>.proposed.yaml` with a **confidence per field**, plus templates cropped into `assets/templates/<mode>/`.
9. **Learn Report:** `captures/sessions/<id>/report.html` — state gallery, timeline strip, detected ROIs drawn on frames, the learner-strategy leaderboard, seeded answers, and the missing/low-confidence fields. William can open it directly or share it.

### 11.4 Review wizard (GUI)
A stepper:
1. States
2. ROIs (overlays on sample frames; drag to adjust)
3. Click targets
4. Answers
5. Replay check — runs the replay harness on the session and shows predicted-vs-labeled accuracy

Then **Accept**, which promotes the proposed profile to the active one.

## 12. GUI — design and UX spec (put real effort here)
Framework: **PySide6**. It should feel like a purpose-built pro tool, not a default Qt form.

### 12.1 Design system
- **Theme:** a "Jamaa Night" dark theme, driven entirely by design tokens in one QSS file plus a Python palette (no inline colors). Suggested tokens to refine:

| Token | Value |
|---|---|
| bg | `#0E1116` |
| surface | `#151A22` |
| raised | `#1C2330` |
| border | `#283142` |
| text | `#E7EAF0` |
| muted | `#97A1B3` |
| Best Guess accent | gem violet `#9B6BFF` |
| Arcade accent | reef blue `#3AA0FF` |
| Opportunist accent | jungle green `#2FD08A` |
| warn | `#F5B941` |
| danger | `#FF5D5D` |

- **Contrast:** WCAG AA against the backgrounds.
- **Type:** Segoe UI Variable (fallback Segoe UI). Scale 12/13/15/18/24/32 px; tabular numerals for stats.
- **Spacing:** 4/8 px grid, 12 px card radius, consistent 16/24 px paddings.
- **Icons:** SVG only (Lucide or an equivalent permissive set), one stroke weight, tinted via palette. The app icon is authored as SVG, and `scripts/make_icons.py` renders an exact-size multi-resolution ICO (16/24/32/48/64/128/256). **Never upscale raster images.**

### 12.2 Layout
- **Left nav rail** (icons + labels, collapsible): Dashboard · Best Guess · Arcade · Opportunist · Learn · Calibration · Answer Bank · Parties · Profit Lab · Notifications · Settings · Logs.
- **Top command bar** (always visible):
  - mode selector;
  - a large **state pill** (e.g., "HUNTING · next scan 3:42", "FARMING · Q14 · 4x");
  - Start/Stop/Pause buttons;
  - Dry-run toggle;
  - a hotkey reminder (F9/F11/F12);
  - input-lease indicator (who controls input right now).
- **Dashboard:**
  - one mode card per mode (status, last event, gems/hr);
  - live preview with an **overlay toggle** (ROIs, detected state, click targets drawn on the frame);
  - stat tiles with sparklines (pyqtgraph): gems this session, gems/hr, accuracy, streak, popups dismissed;
  - event timeline with severity colors and screenshot thumbnails.
- **Mode pages:** every toggle and setting for that mode, grouped in titled cards with inline help tooltips and validation, plus a profile-completeness checklist with "Go to Calibration" links.
- **Calibration:**
  - zoom/pan canvas on a snapped frame;
  - point picker, ROI rectangles with drag handles, color sampler with a pixel inspector (RGB/HSV readout), template crop-and-save;
  - marker-overlay preview;
  - per-field status chips (calibrated / proposed / missing).
- **Learn:** target picker, a big Start Learning button, live recording stats (frames, events, markers), an inbox panel with drag-and-drop import, the session list, and the review wizard (§11.4).
- **Answer Bank:** a searchable, sortable table, inline edit, a conflicts filter, CSV import/export.
- **Parties:**
  - a rotation timeline (Gantt-style by party, color-coded);
  - "distinct parties seen", "time since slot last changed", "next expected change";
  - an unknown-banner labeling queue.
- **Profit Lab:** gems/hr by mode (estimated vs ledger), round-time distributions, Opportunist overhead, and a recommendation of which mode currently earns most.
- **Logs:** filterable live log with a copy/export button.

### 12.3 Extras
- **Mini HUD:** a compact always-on-top widget (~320×120): mode, state pill, gems/hr, pause button. Meant for a second monitor and draggable.
- **System tray:** icon tinted by state (green running, amber paused, red error, gray idle), with a quick menu.
- **First-run onboarding wizard:**
  1. detect the AJC window;
  2. explain DPI/monitor requirements;
  3. test capture;
  4. run the input test (with consent);
  5. set up notifications (ntfy topic);
  6. choose a parking-spot reminder;
  7. done.
- **Keyboard navigation:** Ctrl+1…9 switches pages.
- **Responsiveness:** never block the UI; long work shows progress.
- **Overlap warning:** warn if the GUI overlaps the AJC window (it would steal clicks).

### 12.4 UI QA (required before calling the GUI done)
- Screenshot **every page at 100% and 150% Windows scaling**, then Read each screenshot yourself.
- Fix any blurry icons, clipped text, misalignment, or inconsistent spacing.
- Verify no raster asset is scaled.
- Store the QA screenshots in `docs/ui/`.
- Note: William is strict about crisp visuals — no soft or blurry assets anywhere.

## 13. Config (config.example.yaml committed; config.yaml gitignored)

| Area | Keys |
|---|---|
| Window and capture | `window_title_match`, `capture_backend`, `fps` |
| Timing | `hunt_interval_s` (300), `opportunist_check_s` (300), `resume_after_idle_s` (120), `heartbeat_s` (180) |
| Anti-AFK | `antiafk.tap_ms` (200), `antiafk.gap_ms` (300), `antiafk.pattern` |
| Behavior toggles | `auto_join`, `start_farming_after_join`, `unknown_fact_policy`, `answer_mode`, `fuzzy_threshold` (90) |
| Notifications | `notify.{toast,sound,ntfy_topic,discord_webhook,levels}` |
| Controls | `hotkeys`, `dry_run` |
| Learn | `learn.{keyframe_threshold, ring_fps, ring_window_s}` |

## 14. Testing
- **Unit tests** for:
  - text normalization and fuzzy matching;
  - banner identification against the fixture (must identify Adventure Base Camp, Bitter Sweets, Haunted Forest Party, and locate GO NOW);
  - grid pairing on synthetic boards;
  - state machines with mocked detector outputs.
- **Replay tests:** every recorded session that gets labels becomes a regression test (`tests/replay/`). `python -m jamcrammer replay <session> --mode <mode>` prints a predicted state timeline and accuracy.
- **Synthetic generators** for Best Guess frames and Double Up boards, so tests run before real data exists.

## 15. Milestones (in this order; each needs its acceptance check before moving on)
William's priorities **today**: (1) be ready to *learn* Best Guess the moment it rotates in, (2) get alerted when it does, (3) farm an always-available game meanwhile.

| # | Milestone | Acceptance |
|---|---|---|
| M0 | Bootstrap: venv, pyproject, lint/type/test tooling, folder skeleton **incl. inbox/ with READMEs**, .gitignore, `setup.ps1`, `run.bat`, CLAUDE.md / decisions.md / handoff.md | `pytest` green; `run.bat` opens a placeholder window |
| M1 | Platform core + **CLI emergency recorder**: window discovery, DPI, capture (benchmark WGC vs mss), input test, safety hotkeys, presence detection, config, event bus, store, logging; `learn --target bestguess` records keyframes + markers + input log | `snap` PNG Read and verified by you; recorder produces a session folder; input test moves the animal with one movement pair while William watches |
| M2 | Shared services: Popup Watchdog, Anti-AFK + room-drift guard, Activity Test, notifications (all channels), Gem Ledger | Activity Test run with William; test notifications delivered; watchdog clears a simulated popup in replay |
| M3 | Party Watcher + Best Guess Hunter Phases A/B: banner ID, rotation log, auto-join | fixture test passes; live dry-run for 30 min logs rotations; HIGH alert fires on a simulated Best Guess banner |
| M4 | GUI shell + design system: all pages scaffolded and wired to the engine, command bar, mini HUD, tray, onboarding | UI QA screenshots (100%/150%) reviewed and clean |
| M5 | Learn System v1: GUI recorder, inbox ingest (PNG/MP4), analysis pipeline steps 1–9, Learn Report, review wizard | ingest a sample MP4 + PNG set; report renders; proposed profile written |
| M6 | Calibration tools + profile gate | each profile field editable on the canvas; completeness checklist live |
| M7 | Arcade Farmer — Double Up (§9) | after a Learn session of 2–3 manual rounds: replay ≥95%, then live rounds finish with 8/8 pairs; ledger confirms gems |
| M8 | Best Guess Farmer (Phase C) | after a Best Guess Learn session: replay ≥95% state accuracy; Learn-only live run logs facts/outcomes; live Bank+policy unlocked |
| M9 | Opportunist orchestrator | farms Double Up, checks the party slot every 5 min at round boundaries, returns cleanly; overhead shown in Profit Lab |
| M10 | Profit Lab + Jamaa Derby farmer (§9.3) | Derby: ≥90% first place after Learn calibration; Profit Lab shows measured gems/hr for Best Guess, Double Up, and Derby side by side |
| M11 | Experimental: Gem Ball auto-tuner, Swoopy Eagle prototype (§9.4) + README walkthrough (first launch → onboarding → Learn → calibrate → farm) | auto-tuner reports deterministic/non-deterministic with data; README reproducible from a clean clone |

## 16. How we work
- **First message to William** (one message only): ask whether he wants ntfy phone pushes (and the topic), and whether he has the Double Up den arcade machine. Discover everything else yourself.
- **Commits:** commit at each milestone with clear messages. Ask before pushing.
- **Docs:**
  - Keep `handoff.md` current: state, next steps, open questions, what's waiting on William's captures.
  - Log every non-obvious choice in `decisions.md`.
- **Milestone reports:** at each milestone boundary, post a 5-line status (done / verified how / next / blocked-on-William / risks).
- **If Best Guess rotates in early:** if William says it's in rotation before M1 is done, tell him to record with OBS and drop the video in `inbox\bestguess\`. Stay on M1 so the CLI recorder lands fast.

## 17. Definition of done (this build)
- [ ] `run.bat` launches the polished GUI.
- [ ] Hunter alerts (and optionally auto-joins) on Best Guess, with a rotation report building.
- [ ] Learn records live play and ingests inbox PNG/MP4.
- [ ] Learn produces a report plus a proposed profile.
- [ ] Double Up farms live after calibration.
- [ ] Best Guess farms live once its Learn session passes the replay gate.
- [ ] Opportunist ties the two together.
- [ ] Watchdog and Anti-AFK keep everything alive unattended.
