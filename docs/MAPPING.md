# Mapping the adventures: the world map, the localiser and the pilot (v1 · 2026-11-02)

William's brief (2026-11-02): the Mystery Below bot imitates a recorded run and keeps
getting stopped by small things (a blossom dropped next to the crystal, a rock half broken,
the purple crystals never broken, a mushroom bridge it does not know about). He wants a
**virtual map of the level** the bot always places itself in: every wall, obstacle, ledge,
crystal, decoy, rock and fungus at a known position, pathfinding around them, the next
objective chosen from the map instead of from a run's order, and self-correction when the
position estimate drifts. Claude pilots the character to build the map, walking into
things to find the walls and labelling what it sees, so the unattended bot runs on
curated data rather than on raw detections. First the moth chamber, then the torch hall,
then the city (spike seeds, enemies and spawners, flame floors with timing).

This file is the assessment, the design and the plan. The code is
`modes/adventures/world.py` (the map), `localize.py` (the position), `pilot.py` (the
pilot's commands and its server), `commands/pilot_cmd.py` (the real window) and
`tests/test_world.py` (everything on the synthetic adventure).

## 1. Assessment: it is the right approach, and most of its foundation exists

**What already works for it.**

- The minimap is a fixed, north-up, pixel-identical map of each area; the dot on it is an
  absolute position even in the dark (`docs/MYSTERY_BELOW.md`). That is the world frame:
  nothing has to be invented, and nothing drifts across runs.
- The level model has the floors of the three areas from the recordings, 46 crystal spots,
  17 fungi, the door, 52 repeat spots (rocks, decoys, mirrors), and a minimap navigator
  (A*, walls learned from a still dot). The pilot exists with guardrails.
- The item counters and the Courage text verify acts exactly. The world map does not change
  that: it changes **where the bot goes and what it expects to find there**.

**What was missing, and this build adds.**

| Gap | Why it stopped the bot | What is built |
|---|---|---|
| Walls inside revealed floor, obstacles, ledges, NPCs are not in the floor mask; learned walls were per run and lost | the walk oscillated at a lit crystal (13:58), every stuck was a surprise | a persistent occupancy grid (35 scene px cells: unknown / floor / walked / blocked) per area, filled by probes and walks, saved with the level |
| Position was the dot alone: 70 px quantised and a second behind | fine approach and "did I move" needed the screen | a localiser: scene odometry (phase correlation between consecutive frames) fused with the dot; a stuck is known in 0.3 s instead of 1 s, and the position is good to a few px over a probe |
| Things were spots from clicks: approximate, untyped beyond crystal / fungus / repeat, no status | Cosmo taken for a crystal, a decoy for a rock, a muddy crystal for a missing one | a typed object registry (kind, status, source, confidence, note, screenshot), merged by kind and distance, seeded from the level and refined by scans and Claude's marks |
| No picture of the whole area | every decision was made from one dark frame | a scene mosaic per area at 1/5 scale, the lit disc around the animal pasted at every observed position, rendered with the grid, the things and minimap gridlines so Claude (and William) can read the map |
| One pilot command was one 4.5 s process | a mapping session of hundreds of steps would take hours | `pilot serve`: one process keeps the window, the capture and the world open and answers command files in a fraction of a second; the client is unchanged |
| Mapping by hand means many tiny steps | | `probe` (walk until something stops you, record the collision), `survey` (all eight directions and back), `scan` (detections onto the map), `mark` (Claude's word onto the map), `where`, `map`, `unblock`, `forget` |

**What it will not do by itself.** The map does not know the game's rules: which crystals
belong to which moth path and turn unlit only as the moth advances, what breaks a purple
crystal, where the flame floors pulse. Those are **observed in pilot sessions and written
down as object status, notes and, later, rules in the planner**. The map is the memory;
the sessions are how it is filled; the planner (next milestone) is what uses it.

**Practicality.** The moth chamber is about 22,000 minimap px of floor, some 50 screens.
A survey is eight probes of up to 2.5 s plus the walks back: ~40 s per spot. Surveying
every screen's worth once is ~35 min of input, well within a session budget, and the
mosaic plus scans cover the things in the same pass. Claude decides where to go next from
the rendered map between batches; the probes and the bookkeeping are code.

**Alternatives considered.** More recordings of William's runs give more spots and
floor but no walls, no obstacles and no dependencies, and his order varies. Keeping the
replay-based plan and fixing one failure per live run is what the last ten runs were.
A full SLAM mosaic from the recordings alone is possible later (the recordings are on the
laptop; the mosaic builder accepts any frames with a dot) but the pilot's own frames come
with a fused position, which is better.

## 2. The world map (`world.py`)

**Coordinates.** World px = scene px anchored to the minimap: `(dot - minimap origin) *
scale`, scale 70 on The Mystery Below. A screen point seen while the animal was at `pos`
lands at `pos + (screen - avatar)`, the avatar at (1005, 500). One minimap is one area;
the moth chamber is 20,510 x 13,020 world px.

**Per area.**

- **Grid**: `grid_px` (35) scene px per cell. States: unknown (0), floor (1, from the
  recorded minimap: coarse), free (2, the animal stood there), blocked (3, a probe stopped
  heading into it). A collision marks the cell one `grid_px` ahead of the stop blocked and
  the cell under the animal free. `unblock` returns a 3x3 of blocked cells to unknown.
- **Mosaic**: BGR at `mosaic_scale` (0.2) with a weight layer. `observe(pos, frame)` pastes
  the disc of `vignette_px` (480) around the avatar with the HUD masked; a pixel is written
  when it is at least `mosaic_min_v` bright and at least 80% as bright as what is there, so
  the lit parts win and the vignette's dark rim never overwrites.

**Things.** `Obj(id, kind, area, x, y, status, source, confidence, note, seen, ts, shot,
gone)`. Kinds: crystal, fungus, rock, decoy, bridge, mushroom, torch, basin, mirror, switch,
boom_pile, boom_target, clickable, marker, door, npc, enemy, spawner, flame, rune, chest,
ledge, note. Sources and their confidence: `level` 0.4 (a recorded spot), `scan` 0.5 (a
detector), `claude` 0.9 (a mark), `bot` (later). A new sighting within `merge_px` (120) of
one of the same kind merges into it: the position moves toward the more confident
sighting, status and note follow a sighting at least as confident. `forget` marks a thing
gone but keeps it for the record.

**Planning.** `plan(area, a, b)`: A* on the grid, 8-connected, no cutting blocked corners;
walked 0.8, floor 1.0, unknown `unknown_cost` (3.0), blocked never. The level runner does
not use it yet (milestone A4).

**Files.** `profiles/levels/<target>/world/world.json` (meta, things, collisions, notes,
sessions) plus `<area>_grid.png`, `<area>_mosaic.png`, `<area>_weight.png`. Committed with
the level: the map is a product of the sessions, like `level.json`. Loading without a
saved world seeds one from the level; without a level, an empty one from the profile.

## 2b. The picture: kept frames and the offline rebuild (`world_build.py`, 2026-11-03)

William's review of the first map (2026-11-03): tiles offset by a few px to a few tens of px
(the same rock texture repeated at a shift), the animal, its name tag and the top of the
bottom bar pasted over ~70% of the mapped area, crystals and rocks smeared into blobs. The
causes: the live pass placed each frame by the fused position of the moment (the dot is
quantised to 70 px and a second behind, the camera lags the animal and stops at the area's
edge, so "the animal is at `avatar_xy`" was false in most moving frames); "bright pixel wins"
compositing mixed every misplaced copy; the HUD masks were too small.

**What changed.**

- **The animal is found, not assumed.** `sight.player_on_screen` matches the name tag
  (`nametag_template`, score 0.9-1.0 in the cave) and gives the animal's screen point and a
  box over the animal and the tag (`nametag_box`); every observation, scan and mark uses that
  point (`player_from_tag` keeps `avatar_xy`'s convention) and masks that box. The HUD masks
  were widened (`hud_rois`: the bottom bar with the chat tab, the room frame).
- **Every frame is kept** (`ObsStore`, `captures/sessions/<pilot session>/obs/`): at every
  stop and every `obs_every_s` (0.4 s) while a key is held, the full frame as PNG with its
  pose (the fused position, the animal's screen point, its box, the dot), written by a
  thread. Frames with a dialog open are not kept (and are dropped at rebuild time too).
- **`pilot rebuild`** (its own process, minutes; then `pilot reload` for a running server)
  rebuilds an area's picture from all kept frames:
  1. *Registration.* For each frame, its recent overlapping predecessors and the most
     overlapping older frames (loop closures) are registered pairwise: phase correlation of
     the high-passed, masked overlap at half scale, then a 320 px textured patch matched at
     full resolution within +-6 px (normalised correlation >= 0.45). A pair whose shift is
     more than 150 px from the poses is refused.
  2. *Pose graph.* Frame origins solved by least squares: every pair an equation
     `o_i - o_j = s_ij` (weight 1), every kept pose a prior so weak (0.002) that it only
     fixes where the map sits on the minimap; four robust rounds (a pair's weight falls off
     past 2 px of residual), then pairs still off by more than 8 px are dropped. A prior of
     0.03 once held two walks 18 px apart and the outlier cut severed their bridge: the
     picture was two offset copies. Origins are rounded to the pixel; `build.json` keeps
     the pairs and reports the connected components (one is the goal).
  3. *Composite.* Per 512 px tile, per pixel: the 5 samples nearest the animal's light are
     kept and the one with the median brightness of the 3 nearest is the pixel. No two
     samples are blended, so the picture is as crisp as a frame; a moth, another player or
     a dropped blossom in one frame never wins. Samples are divided by the light's gain at
     their radius (`light_gain`, measured on 3,702 registered pairs: flat to 110 px, 0.77 at
     200, 0.52 at 300) so the picture has one exposure; below `build_min_gain` (0.3) a
     sample is not used.
  The tiles, the low-resolution mosaic and the weight layer are replaced; things,
  collisions and the grid stay. `build.json` keeps the solved origin of every frame.
- **Measured on the first 718 frames** of the second pass: 7,065 of 7,969 candidate pairs
  accepted, median residual 0.01 px, max 1.2 px, 652 of 718 frames linked; the kept poses
  were off by 3 px (median) to 210 px (max).
- **Synthetic check** (`tests/test_world_build.py`): frames cut from a known texture under a
  moving lamp with the animal, a HUD and a passing moth painted in and the poses perturbed
  by +-45 px come back registered to the pixel (every pair within 1 px of the truth), with
  the animal box and the HUD gone and at most one moth surviving at the coverage's edge.

**Also new in the pilot.** `enter` (den -> the adventure by the menu driver, with a
template for The Mystery Below's list banner, OCR in halves, a three-look rule before the
globe is clicked and the "land animal" dialog recognised), `smash x y` (click a rock or a
purple crystal until the Courage pop stops), `reload`, the popup watchdog on every input
path (a logged-out or Play Now screen stops all input until William logs in), and a mouse
keepalive (`pilot.mouse_keepalive_s`: a 12 px cursor move over the floor every 40 s while
input runs: the game logged the pilot out 8.5 min into a walk twice with no idle popup
first, so key input alone may not count as activity).

## 3. The localiser (`localize.py`)

- `rejoin(frame)` between commands: no odometry (the frames are not consecutive); a dot
  more than `dot_rejoin_px` (100) from the estimate means someone moved the animal, so
  the estimate restarts from the dot.
- `update(frame, moving)` every tick while a key is held: the scene shift between this
  frame and the last (`sight.scene_shift`, the HUD and the animal masked, the vignette
  high-passed) is the animal's motion, taken only when the correlation peak is at least
  `odom_min_response` and the shift is physically possible (`odom_max_px_s`). Then the
  estimate is pulled toward the dot by `dot_gain` (0.15) per update; off the dot by more
  than `dot_reset_px` (250) for `dot_reset_n` (4) updates running, the dot wins outright.
- A **probe** holds a direction and lets go after `stuck_ticks` (3) ticks of
  `probe_tick_s` (0.1 s) in which the odometry moved less than `stuck_px` (12), after two
  warm-up ticks; that stop, heading that way, is the collision. On the synthetic world the
  collision lands within 40 px of the wall.

Measured on the synthetic adventure (`test_world.py`): a 2 s walk tracked within 45 px
with the dot's 14 px quantisation; a 900 px teleport recovered in 4 updates. Live numbers
come from the first session.

## 4. The pilot (`pilot.py`, `docs/PILOT.md`)

Same guardrails as before plus a **time budget**: input needs a started session
(`pilot start --budget 30 --goal map the start room`) and stops when the budget is spent.
New commands: `probe`, `survey`, `scan`, `mark`, `where`, `map`, `unblock`, `forget`,
`save`, `status`, `serve`, `quit`; `goto --world`, `key` tracked on the map; since
2026-11-03 `enter`, `smash`, `rebuild`, `reload` (section 2b).

**The server.** `pilot serve` builds one executor and answers `data/pilot/cmd/*.json`
with `data/pilot/out/*.json`; every other `pilot` command sends itself there when the
server's heartbeat is fresh and runs in-process otherwise. The server re-reads
`config.yaml` (William's switch) and the session pointer for every command.

**A mapping session, in practice.**

```
pilot start --budget 30 --goal "walls and things of the start room"
pilot serve          # in its own terminal; stays up
pilot where          # position, nearby things, grid state
pilot survey         # eight probes and back; the walls around here
pilot scan           # crystals / fungi / pins on screen onto the map
pilot look           # read the half-size frame; something the detectors missed?
pilot mark rock 1180 420 --status cracked --note "left of the first crystal"
pilot map --crop 9000 5000 4000 3000 --zoom 0.25   # read the PNG: where next?
pilot goto 1530 880  # minimap px, as the level's spots are
...
pilot stop ; pilot quit
```

Every command and answer is in the session's `pilot_log.jsonl`; the frames are under
`captures/pilot/`; the map is saved after every command that changed it.

## 4b. The Mapping tab

The GUI's Mapping page (`app/pages/mapping.py`) shows the world as a zoomable picture: the
occupancy grid's colours, the low-resolution mosaic where seen, and **full-resolution tiles**
(`world/tiles/<area>/<tx>_<ty>.png`, 512 px, written only where the animal has looked) once
zoomed in; the things with ids, the collisions, the animal's position from
`data/pilot/position.json`. Meters: the share of the recordings' floor that has been seen and
walked (`World.coverage`). Export PNG stitches every observed tile into one full-resolution
file under `captures/maps/`, to look at or to edit. The page re-reads the world every 3 s
while the pilot writes it.

## 5. Milestones

| # | Milestone | Acceptance |
|---|---|---|
| **A1** (done) | World map, localiser, pilot v2 (server, probe, survey, scan, mark, where, map), seeded from the level; tests on the synthetic adventure | 12 tests: coordinates, seed, save/load, planning, odometry, a probe finds a wall, a scan places a crystal, a mark, the map PNG, the budget, the server round trip |
| **A2** | First live session (William present): the server against the real window, odometry and probes measured in the cave, the start room and the first corridor surveyed and labelled; `docs/MAPPING.md` gets the live numbers | the rendered moth-chamber map shows walls and labelled things William agrees with; probe stops within ~50 px of the wall by eye; no refusal of his input |
| **A3** | The moth chamber mapped: every crystal with its path order and status, decoys, rocks, fungi, the mushroom bridges, the door; collisions along every corridor | the map answers "what is at this spot" for every act of the plan; a `pilot goto --world` crosses the chamber on the grid without a learned wall |
| **A4** | The level runner plans on the world map: next objective by the map (nearest unlit crystal of the active path, restock when the stock is low, smash what is adjacent, bridges), position by the localiser, walks by the grid; smashes stop when the rock is gone from the mosaic, not after N clicks | a live solo run lights the chamber and hands over at the door with no help event |
| **A5** | The torch hall and the city with the same tools: torches, mirrors (beam check), boomseed piles and rocks, the switches, flame floors (timing noted as a status), enemies and spawners (spike seeds) | one complete bot run on Hard |

## 6. Questions for William (defaults in use)

1. **When can the first live session run?** Needs: AJC logged in, in The Mystery Below at
   the start room (or in the den: the opening module can take it there), `pilot.enabled:
   true` in `config.yaml`, and him within reach. Default: wait.
2. **Purple crystals and rocks.** Answered 2026-11-02: both break by repeated clicks,
   several hits each. The planner (A4) smashes until the thing is gone from the screen.
3. **The recordings.** Answered: `sync_state.py` carries `*mystery_below*` sessions whole;
   run `export` on the laptop and `import` here when convenient.
4. **Unattended sessions.** Answered: yes, while he is away; the guardrails yield to his
   input and stop at the budget.
