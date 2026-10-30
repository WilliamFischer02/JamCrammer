# Bot roadmap (2026-10-30, written during William's afternoon away)

What is worth building next, ranked by what it earns per hour of his attention, with what
each needs first. Rates are from `docs/ECONOMY.md`; the measured ones are from today's logs.

## Measured today

| Bot | Measured | Note |
|---|---|---|
| Jamaa Derby, Pro, laptop build | **107 races, 72 wins (67%), 14,300 gems in 2.28 h = 6,270 gems/h** | 04:28-06:45; 20 seconds, 7 thirds, 4 fourths, 4 unknown; a SetCursorPos error crashed the farm mode at 06:45 (fixed today) and the game logged the idle account out at 06:58 |
| The Mystery Below, Hard | 0 completed bot runs; the best run lit 4 crystals and restocked 3 times | every failure of the day is in `handoff.md`; at William's pace a run is ~1,770 CP in 22 min ≈ 4,800 CP/h |

## Ranked

1. **Finish The Mystery Below** (CP). The moth chamber and the torch hall run from the level
   model; the city (mirrors, boomseed rocks, switches, the obelisk) is still blind replay.
   Next pieces, in order: (a) a live run that gets through the chamber with the smash acts,
   (b) the mushroom bridges of the left route and the trapped moth of the top route (wiki),
   which the plan does not know, (c) the city as plan acts: mirrors are repeat spots already,
   need a beam check; boomseed rocks and piles are spots already; the switches are a timed
   run of three clicks, (d) rune stones: 23 × 25 CP = 575 CP a run, +30%, for the cost of
   one blossom each, positions from a pilot session.
2. **Keep the account logged in.** Five "gone too long" logouts today, all while no mode was
   running or the farmer waited for William. The anti-AFK movement pair only ticks inside
   hunt and opportunist. A keepalive for every mode's waits (and for the idle engine, as an
   option) stops the losses; see the review notes.
3. **Meet Cosmo, Hard, gem bot.** The wiki quotes ~10,415 gems a Hard run with the side
   quests, double what Derby makes an hour if a run takes 30 minutes. William is level 4,
   Hard needs 2. Needs one recorded run; the level model, the pilot and the opening are all
   reusable (another `ADVENTURES` name, another phase table).
4. **Jamaa Derby: the last 33%.** 20 second places and 7 thirds in 107 races. The hit counter
   says where: races with 3-5 hits lost. Worth a look at which obstacle class misses, with
   the PC build canonical (laptop changes stay local).
5. **Double Up at perfect play.** 44 gems in 45 s is ~3,500 gems/h, no RNG, no other players.
   The arcade bot exists; the question is its measured gems/h in the Profit Lab.
6. **The Forgotten Desert** (items, members only, flying animal). The prizes are where the
   Diamond-equivalent value is, but drop odds are unknown and the map has 3 variants. After
   Cosmo.
7. **Daily Spin / Member Spin claimer.** Small, certain, once a day: gems and the occasional
   Diamond. A menu task the opening's state machine style handles.
8. **Return of the Phantoms, Hard**: 3 clothing chests a run, 12 of 54 items Rare, ~2,750
   gems max. Second to Cosmo for gems, better for items.

## What every new bot gets from today

- The **level model** (`level.py`): floors from the minimap, spots from the item counters,
  acts with phases; `MYSTERY_BELOW_PHASES` is the only level-specific table.
- The **opening** by menu state and the adventure name list; the **pilot** for mapping a
  room by hand before any recording; the marker detector (paw / flame / bang), the digit
  counters, the Courage text; `nav.py` for any minimap level.
- **Economy** numbers to say what a bot is worth before it is built.

## Not worth building

- Anything that types in chat, trades, or accepts friend requests (KICKOFF §3).
- The Great Escape (uncompletable since late 2025), seasonal adventures (no CP).
- Gem Ball / Swoopy Eagle beyond the M11 experiments: no measured return.
