# Handoff

_Last updated: 2026-10-26 night (Best Guess + Jamaa Derby calibrated from William's recordings; both gates open; GUI relaunched for his test)_

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
4. Then Dry-run off for one round: watch that the animal walks to the green (True) / purple (False) side after ~3 s. If it does not move, adjust `true_point`/`false_point` on the Calibration page (click on the floor, clear of name tags).
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
