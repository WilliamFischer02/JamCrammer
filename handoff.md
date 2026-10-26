# Handoff

_Last updated: 2026-10-26 (M2 done, M3 next)_

## State
- **M0 done:** venv (Python 3.14), pyproject, ruff + mypy (strict core/platform) + pytest green, skeleton, inbox READMEs, `run.bat` placeholder window verified.
- **M1 done:** window discovery + DPI, WGC/mss capture with benchmark, SendInput controller (bounds-checked, postcondition retry, dry-run), kill/pause/lease/presence/hotkeys primitives, SQLite store, JSONL logging, **CLI emergency recorder** `learn --target bestguess` (keyframes + ring-buffer marker dumps + local input log + OCR snapshots + meta.jsonl). Verified live: `snap` PNG read and correct; 15 s recording produced a valid session folder.
- **M2 done:** Popup Watchdog (templates → green+OCR → OCR → unknown-modal), Anti-AFK + room-drift guard, notifications (toast/sound verified live; ntfy/Discord wired, disabled until configured), Gem Ledger, profiles system (`profiles/shared.example.yaml`), replay frame sources, synthetic popup generators, `test-notify` and `activity-test` commands. 68 tests green.
- Not yet run live: `input-test` with real keys and the Activity Test (both need William present). Dry-run paths verified.

## How to record Best Guess the moment it appears
```
cd C:\dev\JamCrammer
.venv\Scripts\python -m jamcrammer learn --target bestguess
```
Then join the party and play. **F6** mark, **F7** I was correct, **F8** I was wrong, **F12** or Ctrl+C to stop. Nothing is sent to the game. Add `--video` for a continuous 5 fps mp4 as well. Drop OBS recordings in `inbox\bestguess\` as a backup.

## Next steps
1. **M3:** Party Watcher (panel open/scan/close, banner ID vs the fixture, rotation log) + Best Guess Hunter Phases A/B (hunt cycle, HIGH alert, auto-join); `hunt` CLI with `--dry-run`.
2. With William present: `python -m jamcrammer input-test` (then `--method vk` if scan codes don't move the animal) → record in `decisions.md`, set `input.key_method`. Then `python -m jamcrammer activity-test` (24 min) → set Anti-AFK defaults from the result.
3. Popup templates: when `inbox/popups/` gets the still-playing / logged-out / Play Now screenshots, crop them into `assets/templates/popups/` and flip the template fields in the shared profile to `calibrated`.

## Open questions (asked William 2026-10-26, defaults in use)
- ntfy phone pushes + topic? (default: off)
- Owns the Double Up den arcade machine? (default: no; entry route Games menu → Sol Arcade)

## Waiting on William's captures
- Best Guess: a Learn session (command above) or OBS video → `inbox/bestguess/`.
- Full-window My Parties screenshot (current fixture is a 704×552 panel crop).
- Popup screenshots (still-playing, logged-out, Play Now) → `inbox/popups/`.

## Risks
- windows-capture 2.0.1 segfaults if a WGC session is opened after an mss session in the same process (see decisions.md). The app never mixes them; keep it that way.
- The gem counter is not visible in the Crystal Sands HUD on this account (non-member?). Gem Ledger OCR needs a screen where it shows (mini-game end screens, or the HUD elsewhere).
