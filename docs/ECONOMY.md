# The Animal Jam Classic economy, for measuring the bots (v1 · 2026-10-30)

What a run yields, what it is worth, and how fast a bot earns it. Numbers tagged *[wiki]*
come from the AJ Classic wiki read on 2026-10-30 through its API; *[measured]* from
William's recordings; *[est]* are ours. The code is `jamcrammer/economy.py`; the Adventures
page shows the CP-per-hour estimate for the current target.

## Currencies

- **Gems** [wiki]: the main currency. Earned from games, adventures, the Safety Quiz, the
  Daily Spin, the Member Spin, recycling, codes (once each). Shop items run 100-800 gems,
  Rare Item Monday 700+, Epic Wonders 1,250-2,500, the Gold Brick 10,000 (the most
  expensive item). Animals 1,000, pets 400, dens 2,000-7,500.
- **Diamonds** [wiki]: from the Daily/Member Spin, AJHQ contests, the Jamaaliday calendar, and
  **My Shop** sales. The Diamond Exchange buys gems with Diamonds (not the other way), so
  gems do not convert up. Community item worth is quoted in Den Betas and Diamonds; we use
  **15,000 gems ≈ 1 Diamond-equivalent** [est] only to put gem income and item worth on one
  axis, never as a real exchange.
- **Courage Points (CP)** [wiki]: adventure experience. The ladder (total CP): level 1 at 50,
  2 at 250, 3 at 700, 4 at 1,550, 5 at 2,750, 6 at 4,450, 7 at 6,500, 8 at 9,500, 9 at
  13,000, **10 at 17,000**, 11 at 22,000, then roughly 27.5k, 34k, 41k, 49k, 58k, 68k, 79k,
  91k, **20 at ~104k**, 21 at ~118k; the cap is 30. Hearts: 3 to level 7, 4 from 8, 5 from
  16, 6 from 24. CP per task: breaking an obstacle 2-5, a phantom 5, a torch (fire drum) 5,
  a chest 5, a lever 5, a cage 10, a phantom door 15-25, an animal passage 25, finishing an
  adventure 50, the Phantom King 200. The Forgotten Desert: green shard 3, orange 5, white 7,
  blue 10, purple 30, watering an oasis 5, a chest 25.

## What a run yields

| Activity | CP | Gems | Minutes | Source |
|---|---|---|---|---|
| The Mystery Below, Hard | 1,290-2,253 | 300 | 22 (31 on the first recording) | wiki + measured |
| The Mystery Below, Normal | ~1,000-1,868 | 150 | ~15 | wiki + est; William plays Hard only |
| Jamaa Derby, Normal | – | 100 / 75 / 50 / 40 / 30 / 25 by place | ~1.5 | wiki |
| Jamaa Derby, Pro | – | 150 / 125 / 100 / 75 / 50 / 25 | ~1.5 | wiki |
| Double Up | – | 3 a match, +20 for all 8 (44 max) | 0.75 | wiki |
| Best Guess | – | not on the wiki; the Gem Ledger reads it live | ~0.5 a round | est |

The Mystery Below's CP [wiki, ADVENTURES.md]: 43 light crystals on Hard at 25 CP each, 3 decoys
at 25, 23 optional rune stones at 25 (575), rocks 2 each, phantoms 25 (Hard only), finishing 50.
Its five chests give fixed Ancient den items (Hard: Wall Hanging, Stepping Stones, Lamppost,
Fountain, Writing Desk), worth about a Diamond together [aj-item-worth]. Two animal passages
(Snow Leopard, Arctic Wolf) and two hidden chests (Hanging Lantern over the moth cave by a
mushroom bridge, Armoire in a passage at the bottom of the cave) hold more den items.

Per the wiki's walkthrough, the moth routes differ: the bottom-right route has **red
crystals the moths are drawn to, which must be destroyed**; the top route has **rocks in the
way and a moth trapped in a cracked rock**; the left route has **mushroom bridges that a
light blossom activates**. Rune stones lit by a blossom show messages (and pay 25 CP).

## Rates at the recorded pace

`rate_from_runs(key, runs, minutes_total, gems_seen)` turns a bot's own count and time into
CP/h and gems/h; `hours_to_level(cp_now, level, cp_per_hour)` says how long to a level.

- The Mystery Below, Hard, at William's 22-minute pace: **~4,800 CP/h** (typical 1,770 CP a
  run), ~820 gems/h. Level 10 (17,000 CP) in about **3.5 hours** of runs; level 20 in ~22.
  A bot that takes 35 minutes a run still makes ~3,000 CP/h; the cost of a failed run is the
  whole run, which is why the farmer asks for help rather than skipping a crystal.
- Jamaa Derby: 100 gems a win in ~1.5 min is **~4,000 gems/h** at a perfect win rate, ~1,600 at
  the typical 40% (the speedrun forum's "1.5 gems a second" is the same order).
- Double Up: 44 gems in 45 s is **~3,500 gems/h** at perfect play; a miss costs a few gems.
- Best Guess: measure with the ledger; nothing to quote.

## How the bots are measured

- The **Gem Ledger** (`services/ledger.py`) reads the HUD gem total and mode payouts into
  `ledger` rows; `rate_from_runs(..., gems_seen=...)` prefers those over the table.
- The level runner's stats (`lit`, `picked`, `smashed`, `helps`) and the route time per run
  give CP: 25 per crystal lit, 2 per rock, 25 per decoy, 50 at completion; the end screen's
  total, when it shows one, is the truth and should be OCR'd (not done yet).
- The Adventures page prints the CP/h estimate for the current target next to the level
  model.

## Open questions

- The real CP per Hard run for William's animal (level 2 → 4 over the two recordings is
  consistent with ~1,500-2,000 each).
- Best Guess payouts and the gem cap, if any.
- Current Diamond and Den Beta values (the worth wiki's pages are dated mid-2025 to early
  2026; its Discord is the live source).
