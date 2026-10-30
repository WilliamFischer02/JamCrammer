# The Mystery Below — level model (v1 · 2026-10-30)

What two recorded Hard runs taught us about the level, and how the farmer uses it. Runs:
`captures/sessions/20261029_134501_mystery_below` (A, 31 min) and
`20261030_100745_mystery_below` (B, 22 min, the reference plan). Both solo, same animal,
started from Coral Canyons (A) and the den (B) via Join → Hard.

## 1. The map is the minimap

The minimap (stage ROI `1480,737 293x186`) is a fixed, north-up map of the **current area**,
revealed as the player explores it. By the end of a run it shows the whole floor plan, and it
is **the same map pixel for pixel in every run**. So positions are stored in minimap pixels:

| Area | Phases (banners) | Floor px | Notes |
|---|---|---|---|
| `moths` | explore the darkness → open the door | 22k | start room + the glow-moth chamber (one map) |
| `torches` | light the 3 torches → travel to the lost city | 10k | a gangway; the minimap shows only a strip |
| `city` | solve the puzzles → adventure complete | 19k | the lost city: mirrors, boomseed rocks, switches, the obelisk |

Measured conversions (`profiles/adventure.example.yaml`):
- **70 scene px per minimap px** (walk 400 px/s vs dot 5.9 px/s; windowed fits 66-72).
- Our animal is at screen **(1005, 500)** (name-tag median over 3,500 frames).
- Arrow keys map straight onto the minimap: up = dot −y, right = dot +x, both held = the
  diagonal at (5.3, 3.4) px/s.
- A world click lands at `dot + (screen − avatar) / 70` on the minimap; good to about 3-5 px.
- The dot detector is fooled by the World Map panel (a light-blue blob in the minimap ROI),
  so menus are handled by OCR state, never by the dot.

Floor masks: pixels brighter than V 28 in at least 3 sampled minimap frames (the dot blanked;
a zoomed minimap or a flash is bright everywhere for a moment) plus the recorded dot track, saved as `profiles/levels/mystery_below/<area>_floor.png`.
`<area>_map.png` is the same with the spots drawn (crystals red, fungi green, door yellow,
repeated clicks cyan, numbered in plan order).

## 2. The phases

Identical in both runs (banner text → phase; moth counts "4 / 3 / 2 / the last" are one phase):

| # | Banner | Phase | Area | A (s) | B (s) |
|---|---|---|---|---|---|
| 1 | Explore the darkness. | start | moths | 12 | 11 |
| 2 | Pick some LIGHT BLOSSOMS from the FUNGI. | pick | moths | 22 | 18 |
| 3 | DRAG a LIGHT BLOSSOM to DROP it on the CRYSTAL. | first_crystal | moths | 24 | 24 |
| 4 | Make a PATH for the GLOW MOTH by illuminating CRYSTALS. | first_moth | moths | 43 | 36 |
| 5 | Find 4 GLOW MOTHS and LEAD THEM to the door. | moths | moths | 105 | 86 |
| 6 | Open the door to travel deeper underground. | door | moths | 783 | 548 |
| 7 | Light the 3 TORCHES to ILLUMINATE the room. | torches | torches | 791 | 555 |
| 8 | Light the LAST TORCH to ILLUMINATE the area. | last_torch | torches | 813 | 579 |
| 9 | Travel to the LOST CITY. | travel | torches | 823 | 589 |
| 10 | Solve the PUZZLES throughout the ANCIENT CITY. | puzzles | city | 836 | 600 |
| 11 | Use the BOOMSEEDS to find a way DEEPER into the city. | boomseeds | city | 1040 | 761 |
| 12 | Press ALL 3 SWITCHES within 60 SECONDS. | switches | city | 1183 | 892 |
| 13 | Find the LIGHT SOURCE and REFLECT IT through the room. | light_source | city | 1325 | 917 |
| 14 | REDIRECT the light to the OBELISK. | obelisk | city | 1691 | 1157 |
| 15 | SPEAK with the ALPHAS at the OBELISK. | alphas | city | 1850 | 1287 |
| 16 | You did it! Adventure COMPLETE! | done | city | 1868 | 1303 |

The table is `MYSTERY_BELOW_PHASES` in `modes/adventures/level.py`; phases 1-5 run from the
level model (`mode: plan`), 6-15 by route replay for now (`mode: replay`).

## 3. Items are the exact signal

The two item slots at the top left show **"N/3" counters** (`item_counter_rois`: light
blossoms at y 48, boomseeds at y 188). The recorder's OCR read them on 1,935 keyframes, so
every pickup (count up) and every use (count down) is exact in time, and the world click
within 2.5 s before it is what did it. From that:

| | A | B |
|---|---|---|
| blossom uses (crystals lit) | 46 | 40 |
| blossom pickups (fungi) | 21 | 15 |
| boomseed uses | 21 | 13 |
| boomseed pickups | 9 | 6 |

Live, the counters are read by OCR of the upscaled crop: **92% of frames at upscale 4, 88% at 2,
0 wrong**, 6 ms each; a miss keeps the last value and the second look at the other scale
catches most of them. The runner verifies every act by the counter, not by what the crystal
looks like.

## 4. Spots (what the plan is made of)

Clustered at 5 minimap px across both runs:

| Kind | moths | city | seen in both runs |
|---|---|---|---|
| crystal (blossom use) | 46 | – | 25 |
| fungus (blossom pickup) | 17 | – | 10 |
| boom_target (boomseed use) | – | 21 | 4 |
| boom_pile (boomseed pickup) | – | 6 | 4 |
| repeat (mirror / rock clicks) | 52 | 52 | 66 |
| door (where the banner came) | 1-2 | – | – |

Positions are good to a few px, which is all the navigator needs: it walks to within
`approach_px` (3 px ≈ one third of a screen) and then **looks for the thing by colour**.
The plan (B's acts in order) is 52 acts in the moth chamber: 36 lights, 15 picks, the door.

## 5. What things look like (HSV, OpenCV ranges)

Measured on the frames around the recorded uses and pickups:

| Thing | H | S | V | Detector hit rate |
|---|---|---|---|---|
| unlit crystal (navy) | 85-115 | 130-255 | 60-215 | 72% (B) / 83% (A) of uses have one within 300 px of the click half a second before |
| lit crystal (pale cyan glow) | 85-110 | 20-150 | 215-255 | 33-37% after the use: weak, so the counter verifies, not this |
| purple decoy | 130-165 | 90-255 | 80-255 | not measured |
| green fungus cap | 50-90 | 90-255 | 140-255 | 93% (B) / 91% (A) of pickups |

The cave background falls in the unlit range for 1.3% of pixels and the lit range for 1.0%,
so blobs under 1,500 px are ignored and the edges of the screen (tabs, bar) are masked.

## 6. The menus, by state

`modes/adventures/opening.py` reads the words on screen and acts per state, each click
checked by the next panel showing within 4 s (both recordings classify cleanly; a panel
sliding in reads as "room" for one frame, so a state must hold a quarter second first):

room → globe (1729,992) → **world_map** → Adventures (159,809) → **adventures** → click the
list's scroll-down arrow (450,970), three per look, until a list line's nearest adventure
name (of the 20, by `fuzz.ratio`) is "the mystery below" at 50+ → click that line →
**adventure_dialog** → Join (826,592; `level.join: false` hosts) → **difficulty** → Hard
(1100,630; **always Hard**: the second half of the level needs it, William 2026-10-30) → **lobby** (headed by the adventure's name; "PLAY" OCRs as "nay") → PLAY (955,1010)
→ splash + black loading (never clicked: the splash reads as a room) → **in_adventure**
(the first banner). The lobby is PLAYed only when its header's nearest name is the target;
another adventure's lobby (`lobby_other`) is closed by its blue X near the top right. Every
menu click is followed by `menu_settle_s` (0.8 s) before the next look.

Why: the first supervised run (2026-10-30 09:53) replayed the clicks blind, and its dialog
closer saw the Join/Host dialog's blue X (same template as Cosmo's) at (1441,426) and closed
it, so Join, Hard and PLAY clicked into nothing. The 12:11 run dragged the scrollbar thumb
from where the recording had it; released elsewhere, the drag landed on a lobby animal slot
and switched William's animal. A room must read as one for 1.5 s before the globe is clicked
(a panel sliding away reads as a room), and every state change is saved as a screenshot under
`captures/adventure_menus/`.

## 7. The level runner (moth chamber)

`modes/adventures/level_runner.py`, after the opening:

1. Banner every 2 s → phase. A phase with `mode: replay` (the door) ends the runner; the engine
   continues by route replay from that banner's route time (`RouteRunner.run(start_rt=...)`).
2. Dialogs are closed by their blue X (also while walking: Cosmo's dialog holds the animal,
   which would otherwise look like a wall).
3. For each act: `light` needs a blossom (else restock from the nearest fungus spots, up to
   three); walk to the spot by A* on the floor mask with the live dot (`nav.py`: a held key
   and the dot within 1 px of where the still period began, for 1 s = an unmapped wall,
   learned 3 cells wide, path replanned; more than 8 of those or no path = the walk failed);
   find the nearest unlit crystal / fungus to where the spot should be on screen, walking at
   it for 1.2 s when it is out of reach (420 px). A **pick** is the paw pin (white teardrop,
   yellow paw) that floats over a fungus once the animal is **at** it: walk up to 50 px in short
   bursts, closing Cosmo's dialogs on the way, until it shows, then click it (a pin anywhere
   within 450 px of the animal counts, the fungus may be under the animal by then). The pin is found by colour (a white blob of 800-9,000 px with
   500+ px of yellow-to-beige paw inside), not by template: William's screenshot of the pin
   scored 0.62 against the recorded template (a paler paw, a slightly different size) while
   the colour test finds both. A **light** drags the blossom slot (200,100)
   onto the crystal, or clicks the slot then the crystal; the counter must move within
   2.5 s, two retries, else the act is skipped. The first act of the plan is the start
   room's pickup (the counter only appears with it); no "!" click is needed there, Cosmo's
   dialog closes and the door opens by itself.
4. William's input pauses it (keys released); 2.5 s of quiet and it goes on with the next act.
5. Three failed acts in a row raise `adventure.help` (HIGH) and wait for him.

Events: `adventure.menu`, `adventure.phase`, `adventure.act` (action, spot, outcome, counts),
`adventure.not_seen`, `adventure.walk_failed`, `adventure.restock`, `adventure.handover`.

Verified: on the synthetic adventure (menus faked by words, counters faked from the world) it
walks the menus, lights both crystals from a plan with a restock, closes Cosmo's dialog on
the way, hands over at the door banner, and asks for help after three unseen crystals. The
real detectors are verified on the recordings as in §3-§6. **Not yet run live.**

## 8. Next

- Live supervised run from the den: the opening, then the moth chamber. Watch `adventure.act`
  outcomes; `not_seen` means the colour ranges or the spot position need work there.
- Torches and the city from the model: torches (3 clicks on a gangway), mirrors (repeat spots:
  turn until the beam reaches the next mirror; beam detection needed), boomseed rocks and
  piles (spots exist), switches (timed), the obelisk. Each phase moves from `replay` to `plan`
  as it is built.
- Purple decoys and rocks for extra CP once the main line is reliable.
- A survey mode (drive the animal along the floor mask to fill unexplored cells) is not
  needed: the recorded minimaps already cover the areas; learned walls are kept per run only.
