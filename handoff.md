# Handoff

_Last updated: 2026-10-26 evening (Best Guess calibrated from William's recording; gate open; GUI launched for his test)_

## State
- **M0-M11 built and committed** (see git log and `decisions.md`): capture/input/safety primitives, Popup Watchdog, Anti-AFK, notifications, Gem Ledger, Party Watcher + Hunter, PySide6 GUI, Learn (recorder, inbox ingest, analysis, report, review wizard, replay harness), Calibration canvas + gate, Double Up / Jamaa Derby / Gem Ball / Swoopy farmers, Opportunist, Best Guess Phase C.
- **Best Guess is calibrated (2026-10-26 17:30-18:00)** from `inbox/2026-10-26 17-25-39.mp4` + 3 screenshots (now in `inbox/_processed/`). `profiles/bestguess.yaml` (gitignored, values below) has every gate field `calibrated`; the dense replay scores **97.5% over 396 frames**; `gate_status("bestguess")` is **unlocked**. The answer bank holds the 10 facts from the recording. 131 tests, ruff + mypy clean.
- **Verified live earlier today:** party hat click opens My Parties, X closes it. The slot party was *Spooky Party* at 15:00.
- **Not yet run live:** a Best Guess farming round (the GUI was launched for William at the end of this session), the 30-min `hunt --no-join` rotation run, `input-test`, `activity-test`, Learn sessions for Double Up / Derby.

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

## How William tests Best Guess now
1. Start the GUI (`run.bat` or `.venv\Scripts\python -m jamcrammer gui`), mode **Best Guess Hunter**.
2. Stand in the Best Guess arena (or let the Hunter join it). With the arena on screen the engine skips the hunt and farms at once (`engine.arena_detected`).
3. First run with **Dry-run** on: the state pill should walk QUESTION_OPEN → LOCKED → REVEAL and the Answer Bank should grow; no clicks are sent.
4. Then Dry-run off for one round: watch that the animal walks to the green (True) / purple (False) side after ~3 s. If it does not move, adjust `true_point`/`false_point` on the Calibration page (click on the floor, clear of name tags).
5. F11 pauses, F12 stops. Any screen that is not the arena (ad, lobby, loading book) shows UNKNOWN and pauses after 30 s.

## Next steps
1. **Watch William's first live round** (log `data/logs/`, events `bestguess.answer` / `bestguess.reveal`). Check the chosen side matches the bank, the score delta learner agrees with the reveal word, and the heartbeat does not fire mid-question.
2. If the My Parties banner for Best Guess gets captured (`inbox/parties/` screenshot while it is listed), add it to `assets/banners/` and `known_labels`; until then the Hunter identifies it by OCR ("best guess").
3. M3 acceptance when William is away: `hunt --no-join --duration 1800`. With him present: `input-test` (scan codes vs vk), `activity-test`.
4. Learn sessions for Double Up and Jamaa Derby (`learn --target double_up` / `jamaa_derby`) to open those gates.
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
- Learn sessions for Double Up and Jamaa Derby.

## Risks
- `true_point`/`false_point` are untested live; a wrong spot could land on a name tag (harmless click) or outside the floor (no move). Dry-run first.
- The recording was 1280×720 upscaled to the 1920×1080 stage; OCR on the live 1920×1080 capture should only be better, but colour thresholds were measured on the upscaled video.
- Stage mapping assumes uniform 16:9 scaling centered in the client; verified for fullscreen and maximized.
- Toast banners are hidden while AJC is fullscreen; HIGH alerts rely on the repeating sound and ntfy/Discord once configured.
- windows-capture 2.0.1 segfaults if a WGC session is opened after an mss session in the same process. The app never mixes them.
- No gem counter in the HUD on this account; the Best Guess farmer uses the round score instead, the Gem Ledger relies on end screens.
