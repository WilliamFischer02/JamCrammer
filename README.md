# JamCrammer

Screen-only automation and "first contact" learning tool for **Animal Jam Classic** (the desktop
app). It captures pixels and sends ordinary mouse/keyboard input. Nothing else: no memory reads,
no packets, no client patching. The full spec is `docs/KICKOFF.md`; engineering notes live in
`decisions.md`, the current state in `handoff.md`.

| Mode | What it does |
|---|---|
| **Best Guess Hunter** | Opens My Parties on a schedule, identifies the banners, alerts (sound/ntfy/Discord) and auto-joins when the *Best Guess Game* appears, then farms it with OCR and a self-learning answer bank once the profile gate is open. |
| **Arcade Farmer** | Farms Double Up (primary), Jamaa Derby, or runs the Gem Ball auto-tuner / Swoopy Eagle prototype. |
| **Opportunist** | Farms the arcade game, checks the party slot every few minutes at a round boundary, hops to Best Guess the moment it appears. |
| **Learn** | You play; JamCrammer records keyframes, your clicks and F6/F7/F8 markers, then proposes the calibration and seeds the answer bank. Also ingests PNG/MP4 from `inbox/`. |

Shared services: Popup Watchdog, Anti-AFK with a room-drift guard, presence detection (hands
off when you touch the mouse), notifications, Gem Ledger, Profit Lab, Rotation Log.

## 1. First launch (clean clone)

Requirements: Windows 10/11, Python 3.11+ (3.12 or 3.14 tested), the AJC desktop app installed.

```powershell
git clone <this repo> C:\dev\JamCrammer
cd C:\dev\JamCrammer
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1   # venv + pinned deps + config.yaml
run.bat                                                       # launches the GUI
```

`setup.ps1` creates `.venv`, installs `pyproject.toml`'s pinned dependencies, copies
`config.example.yaml` to `config.yaml` (gitignored; edit it freely) and creates the runtime folders.

Checks (what CI would run): `.venv\Scripts\python -m ruff check .`, `-m mypy`, `-m pytest`.

## 2. Onboarding

The first GUI start opens a 7-step wizard: detect the AJC window → monitor/DPI notes → test
capture → **input test (needs your consent; one cancelling movement pair)** → notifications
(enter an ntfy topic for phone pushes) → parking-spot reminder → done. Re-run it any time by
deleting `data\onboarded`.

Keep AJC visible and unobstructed on its own monitor. Fullscreen 1920×1080 is ideal; a maximized
window is handled too (the game's 16:9 stage is mapped automatically). Toast banners are hidden
by Windows while a fullscreen game runs, so sound and ntfy are the alerts that reach you.

## 3. Learn (first contact with a game)

GUI: **Learn → pick a target → Start Learning**, play 2–3 rounds, press **F7** when you were
right and **F8** when you were wrong (F6 = generic mark), **Stop**. CLI equivalent:

```
.venv\Scripts\python -m jamcrammer learn --target bestguess      # or double_up, jamaa_derby, ...
.venv\Scripts\python -m jamcrammer replay <session> --detector bestguess --dense   # score the detector on an inbox video
```

Nothing is sent to the game while learning. Files you captured yourself (OBS `.mp4`/`.mkv`,
screenshots) go into `inbox\<target>\` (see `inbox\README.md`; filename tags like
`__reveal_correct` pre-label frames), then **Import inbox** or:

```
.venv\Scripts\python -m jamcrammer ingest
```

Every session lands in `captures\sessions\<timestamp>_<target>\` with `report.html` (the Learn
Report: states, text regions, proposed ROIs, learner leaderboard, seeded answers) and
`profiles\<target>.proposed.yaml`.

## 4. Calibrate and pass the gate

**Learn → Sessions → Review wizard**: name the states, check the ROIs and click targets, review
the answers, run the **Replay check**, **Accept** (promotes the proposed profile and seeds the
answer bank). Fine-tune on **Calibration**: drag ROI handles, double-click to set points, sample
a color, crop a template, then **Save profile** (`profiles\<name>.yaml`, gitignored).

Live modes stay **LOCKED** until every required field is calibrated *and* the replay harness
scores ≥95% on a recorded session (`python -m jamcrammer replay <session>`). The mode pages and
the Calibration page show exactly what is missing.

## 5. Farm

```
.venv\Scripts\python -m jamcrammer hunt --no-join            # scan and log the rotation only
.venv\Scripts\python -m jamcrammer hunt                      # alert + auto-join (+ Phase C when the gate is open)
.venv\Scripts\python -m jamcrammer farm --game double_up     # or jamaa_derby, gem_ball, swoopy_eagle
.venv\Scripts\python -m jamcrammer opportunist               # Double Up + party checks + hop
```

Or press **Start** in the GUI with the mode selected. Hotkeys everywhere: **F9** start/stop the
selected mode, **F11** pause/resume, **F12** kill. Touching the mouse or keyboard pauses the bot
("Paused — user active"); it resumes after `resume_after_idle_s`.

Other commands: `window` (geometry), `snap --benchmark` (capture test), `input-test`,
`test-notify`, `activity-test` (which inputs count as activity), `report <session>`,
`replay <session>`, `gui`.

## 6. Where things are

```
config.yaml           all tunables (copy of config.example.yaml)
profiles/*.yaml       per-mode calibration (examples committed, real ones gitignored)
inbox/                drop screenshots and recordings here
captures/sessions/    Learn sessions, reports, analysis
captures/unknown*/    unidentified banners / dialogs waiting for a label
data/jamcrammer.db    sightings, rounds, answers, events, ledger
data/logs/            jamcrammer.log (human) and jamcrammer.jsonl (structured)
docs/ui/              UI QA screenshots at 100% and 150%
```

## 7. Troubleshooting

- **"AJC window not found"**: the title must be exactly `Animal Jam Classic` and the process
  `AJ Classic.exe` (Settings → Window and capture).
- **Black or wrong-size frames**: `snap --benchmark`. WGC is used only when its frame equals the
  client size; otherwise mss. Never mix the two in one process (see `decisions.md`).
- **No alerts**: `test-notify`. Toasts are suppressed in fullscreen; configure `notify.ntfy_topic`.
- **Mode won't start**: read the gate line on the mode page; calibrate the named fields and run a
  replay on a recorded session.
- **Logged out / Play Now screen**: the watchdog stops everything and alerts; log in yourself.
