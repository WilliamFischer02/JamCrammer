# The pilot: Claude at the controls (v1 · 2026-10-30)

William's idea: let Claude Code, which can look at a frame and think about it, drive the
game directly, to explore rooms, map levels, learn the menus and collect templates, with
longer and more deliberate "learn" sessions than a scripted bot can run. This is the
workflow and the tool for it. Nothing here runs on its own: every action is one command
Claude issues after reading the previous frame.

## How it works

`jamcrammer pilot <command>` is one short process per command. It finds the AJC window,
takes a frame, acts through the same `InputController` every bot uses (clicks only inside
the client), takes a second frame and prints what changed as JSON. The frames are saved
under `captures/pilot/` as full PNGs plus half-size copies for reading.

| Command | What it does |
|---|---|
| `pilot start [--target mystery_below]` | starts a Learn recording in the background (`learn --stop-file`) and makes it the active pilot session; keyframes, OCR and every input land in `captures/sessions/<stamp>_<target>_pilot/` as in any Learn session |
| `pilot look` | one frame: menu state, banner and phase, minimap dot, item counts, dialog X, crystals / fungi / the paw pin, OCR lines; saves the frame |
| `pilot key <up/down/left/right/space/escape> <seconds>` | holds a key (at most `max_hold_s`), reports the dot before and after and the scene shift |
| `pilot click <x> <y>` | a stage-pixel click; refused on the chat field or the log-out button |
| `pilot drag <x0> <y0> <x1> <y1>` | a drag (item slot onto a crystal, a scrollbar) |
| `pilot goto <mx> <my> [--timeout s]` | walks to a minimap point by the level's floor mask (all floor when there is no level), the navigator's trace in the answer |
| `pilot note <text>` | a note into the session's `pilot_log.jsonl` (what I saw, what I think a thing is) |
| `pilot crop <x> <y> <w> <h> <name>` | saves a template crop of the live frame under `captures/pilot/templates/` for promotion to `assets/templates/` later |
| `pilot stop` | ends the recording |

Every command and its outcome is appended to `pilot_log.jsonl` in the session, next to the
Learn recording. A pilot session can then be fed to the route and level builders exactly
like one of William's runs (`finished_sessions` wants the completion banner, so a partial
exploration is read with `build_route` / `Route.load` directly).

## Guardrails (not optional)

- **William's switch:** input commands work only with `pilot.enabled: true` in `config.yaml`.
  `look`, `note` and `crop` always work. He turns it on for a session and off after.
- **His controls:** any command that would send input is refused if the OS saw his own
  keyboard or mouse within `yield_s` (2 s). A `goto` stops the moment he touches anything.
- **Never:** the chat field, the log-out button (`forbidden_rois`), typing text (the key list
  is arrows, space, escape), a key held longer than `max_hold_s` (3 s), a walk longer than
  `max_goto_s` (60 s), a click outside the client (the controller refuses it), trades,
  buddy requests, purchases, settings. Dialogs that are not the game's own are left to the
  watchdog and to William.
- **In sight:** Claude narrates what it is doing in the session notes and keeps the half-size
  frames; William can read the log and the frames afterwards.
- **Time-boxed:** a pilot session has a stated goal and a budget (for example, "map the torch
  hall: 10 minutes"); when the budget is spent the session stops, mapped or not.

## The loop

```
pilot start --target mystery_below
pilot look                      # where am I? read the half-size frame, the dot, the banner
pilot key up 1.0                # move; the answer says how the dot and the scene moved
pilot look
pilot crop 980 300 160 200 torch_unlit   # a thing worth remembering
pilot note "torch 1 of the hall at dot 1588,860; the ! bubble sits 90 px above it"
...
pilot stop
```

Then, offline: `Route.load(session)` / `build_route`, the minimap floor from the frames, the
notes as the spot list; promote the crops that proved useful into `assets/templates/` with
a measured threshold, as `paw_marker.png`, `courage.png` and `count_digits.png` were.

## What to use it for first

1. **The torch hall and the city**, which the level model only replays: walk them slowly,
   note each torch, basin, mirror, switch, boomseed pile and rock with its dot position and
   a crop, and the banner each belongs to. That gives the plan for those areas without
   another full recording.
2. **Muddy vs unlit vs lit crystals, and the decoys**, up close, in one place, for a clean
   colour calibration with notes on what each looked like.
3. **The menus**: every panel of the world map, the lobby, the animal picker, with a crop of
   each button, so the opening never clicks blind again.
4. **Unknown dialogs**: whatever pops up, crop its X and its header for the watchdog.

## What it is not

Not a bot: it never loops, never decides on its own, and it stops when William touches the
controls. The bots are the product; the pilot is how their maps and templates get made
between his recordings.
