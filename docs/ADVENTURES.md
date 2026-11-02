# Adventure bots — research and design (v0 · 2026-10-29)

Two new modes: **The Mystery Below** (Courage Points / XP) and **The Forgotten Desert** (prize
items). This file records what the research found, what it means for the bots, and what is
still needed from William. Facts tagged *[doc]* come from a source; *[est]* are our estimates.

## 1. What the research found

**Sources.** The AJ Classic wiki (animaljam.fandom.com) was read through its MediaWiki API, plus
search excerpts. Item worth comes from aj-item-worth.fandom.com, a Discord-sourced value wiki
updated in 2026. Run times are speedrun.com solo world records. Most guides date from 2014–2018,
and The Forgotten Desert was offline from 2023-12-18 to 2024-04-04, so check everything against
the live client.

### 1.1 XP is "Courage Points"

- **Courage Points (CP)** raise your adventure level. The cap is level 30; level 10 needs
  17,000 CP and level 20 about 104,000. *[doc: wiki/Courage_Points]*
- The number on a name tag is the adventure level. Bingusthewizard showed **1** on the
  2026-10-29 Jamaa Township frame.
- **Hard mode needs adventure level 2**, not membership. *[doc: wiki/Adventures]*
- **CP goes to whoever does the action.** Fewer players means more CP each, so solo is best for
  CP. *[doc: wiki/Return_of_the_Phantoms]*
- Seasonal adventures mostly give no CP; Bitter Sweets, live now, gives none. *[doc]*

### 1.2 Adventures compared

| Adventure | Players | Solo WR N/H (min) | Max CP N/H | Rewards |
|---|---|---|---|---|
| **The Mystery Below** | 1–4 | 6.2 / 9.7 | **1,868 / 2,253** | 5 fixed "Ancient" den items per mode, 150/300 gems, hidden chests |
| Storming the Fortress | 1–4 | 6.5 / 7.3 | 1,148 / 1,253 | den items |
| River's Heart | 1–4 | 7.8 / 10.0 | 1,000 / 1,265 | den items, about 440 gems |
| Trials of Zios | 1–4 | 6.1 / 9.2 | 800 / 843 | den items, 25–300-gem chests |
| Phantom Fortress | 1–4 | 4.2 / 5.8 | 458 / 593 | den items |
| **Meet Cosmo (Hard)** | 1–4 | 4.5 / 6.1 | ? | **≈10,415 gems per Hard run** (side quests; Hard doubles gems) *[doc]* |
| Return of the Phantoms (Hard) | 1–4, land | 2.5 / 5.6 | ? | 3 random clothing chests (12 of 54 items Rare), about 2,750 gems max |
| **The Forgotten Desert** | 1–4, **members only, flying animal** | 2.9 (one crystal) | ≈1,000 per team | 1 random prize per completed crystal (up to 5), every player gets the same one; 5 ground chests of 50–150 gems |

The Great Escape cannot be completed (a glitch since late 2025). The fixed completion items of
every year-round adventure are worth "a few RIMs", about 1 Diamond (≈15,000 gems), so they
are low value. *[doc: aj-item-worth/Adventure_Rewards_and_Prizes]*

**My Shop:** only members can buy from it, and prices are capped at 15,000 gems per item.
*[doc: wiki/My_Shop]*

### 1.3 Rankings (all rates are estimates)

**CP per hour:**
1. **The Mystery Below, Normal: ≈7–9k.** Hard is ≈7–8k: more CP per run, but much longer.
2. Storming the Fortress: ≈5–6k.
3. In Too Deep: ≈5–6k (needs an ocean animal).
4. The Forgotten Desert: ≤3.5k (fixed timer, shards shared by the team).

**Resale value per hour:**
1. **The Forgotten Desert with 4 players: ≈6 Diamonds-equivalent per hour.** It is the only
   year-round pool with Den Betas (2–20 Diamonds; Books, Cami's Frog and Wood Floor are
   ≈15–20) and Rare Spiked Wristbands (long 18–50 Diamonds, short 12–20). Most of its 123 Rare
   clothing items are worth about 1 RIM. Drop odds are not documented anywhere.
2. **Meet Cosmo, Hard: 30–50k gems per hour, all liquid.** The gems per run are documented;
   the run time is our estimate. Hard needs adventure level 2.
3. The Forgotten Desert solo: ≈2–4 Diamonds per hour. The purple crystal needs exactly 4
   players.

### 1.4 William's claims

| Claim | Verdict |
|---|---|
| The Forgotten Desert is best for valuable items | **Mostly true** among year-round adventures. Caveats: the odds are unknown, most prizes are about 1 RIM, and Meet Cosmo Hard pays more in liquid gems. |
| The Mystery Below is best for XP | **True** on the documented numbers. Its max CP is about 50% above the next best. CP totals for 10 older adventures are undocumented. |
| The Forgotten Desert needs 4 players for the best items | **Partly true.** It runs with 1–4 players. Only the **purple** crystal needs exactly 4; 4 players lets you complete all 5 crystals and get 5 prizes. There is no evidence that purple's pool is better. |
| Decoy crystals, rocks and enemies in The Mystery Below grant XP | **Partly true.** Fake beacons (3) give 25 CP each. Rocks give **2 CP** (the screen shows +5). Phantoms give 25 CP but only appear in Hard. The real CP comes from **33 light-crystal beacons on Normal (43 on Hard) at 25 CP each**, plus **23 optional rune stones at 25 CP each (575)**. |
| Playing The Mystery Below with others helps | **No reward bonus.** Others speed up the moth phase but take CP and can change shared puzzle state. **Solo is the right choice for a bot.** |

## 2. Build order

1. **The Mystery Below first.** Solo, Normal mode, no membership needed, and as far as anyone
   documents, the layout is the same every run. It is also the XP target.
2. **The Forgotten Desert second.** William is a member (confirmed 2026-10-29) and his eagle
   is the flying animal. It is the hardest bot: 3 map variants, five shard mechanics and
   strangers.
3. Worth considering: a **Meet Cosmo (Hard)** gem bot, once his adventure level reaches 2 (The
   Mystery Below will get him there).

## 3. The Mystery Below bot — design

**Route, per the walkthroughs:**
1. Zios gate with Cosmo and Graham.
2. Pick Light Blossoms from the green fungi.
3. Drop blossoms on unlit pale-blue crystals; each lit crystal counts as a beacon.
4. Lure 5 moths to the Zios-mask door. On the way, smash 3 decoy beacons and the rocks, and use
   the mushroom bridges.
5. Click "!" at the door, then take the stairs down.
6. Light the 3 hallway torches.
7. Mirror puzzle 1.
8. Press 3 plates within 60 s.
9. Mirror puzzle 2.
10. Talk to Cosmo and Graham at the obelisk.
11. Pick a prize chest and exit.

Optional extra CP: 23 rune stones. Hidden chests: Hanging Lantern, Armoire, Vanity, Wall.

**Approach: checkpointed route replay, not blind replay.**

- *Record.* William plays 3 solo Normal runs with `learn --target mystery_below`. The recorder
  already logs every key and click with client coordinates, plus change-triggered keyframes.
- *Segment.* Each run is split into **legs** between **checkpoints**. A checkpoint is a stable
  scene that recurs across the three runs: a zone entry, a puzzle solved, a door open. A leg is
  the inputs between two checkpoints: arrow-key holds and clicks in stage coordinates, with
  timing.
- *Re-anchor.* The camera follows the animal, so a click lands relative to where the animal is.
  Small drift would compound over the run. At every checkpoint the bot registers the live frame
  against the recorded checkpoint frame (phase correlation or ORB, with the HUD masked). It
  shifts the next leg's clicks by the measured offset and only continues once the scene matches.
- *Verify.* Every leg ends with a postcondition: the checkpoint scene within a timeout, a
  beacon lit (colour change at the target), a mirror beam template, or a door open. A failure
  retries the leg once from its checkpoint, then pauses safely and alerts. This is the project
  rule for every input.
- *Variance.* Phantoms (Hard, and possibly the plate room) and moth paths are not frame-exact.
  The legs around them get closed-loop handling once the recordings show what varies.
- *Loop.* Adventure menu → The Mystery Below → Normal → play solo → prize chest (fixed choice)
  → exit → repeat. CP per run is read from the end screen if it shows a total.

**Needed from William:**
- 3 solo Normal runs recorded with `learn --target mystery_below`, same animal each time.
- Screenshots of the adventure menu, the start/host screen and the end/prize screens.

## 4. The Forgotten Desert bot — design

**Rules of the adventure:**
- **Players:** members only, flying animal, 1–4 players.
- **Timer:** 17 min for 1–2 players, 15 min for 3–4. At time-out the shards and watering cans
  disappear, but the chests stay.
- **Shards:** five colours, each needing a different action:

  | Colour | How to get it | Count | CP each |
  |---|---|---|---|
  | Green | lying in the open | 25 | 3 |
  | Orange | buried; stand on the periodic **sparkle** and click the paw button to dig | 25 | 5 |
  | Blue | fetch a watering can from a pool and pour it on a dry oasis | 20 | 10 |
  | White | perch on a sparkling cactus marked with gold talon prints | 15 | 7 |
  | Purple | all **4** players perch together on a rock formation | 5 | 30 |

- **Chests:**
  - Each completed crystal gives a prize; every player gets the same one.
  - 5 ground chests hold 50–150 gems.
  - Prizes accepted before 3:00 has elapsed are not given.
  - If no crystal is completed, a 500-gem consolation chest appears.
- **Map:** 3 variants with different shard and chest positions. A corner minimap shows your
  blue dot. *[doc; positions must be detected, not hard-coded]*

**Lobby loop (William's spec):**
1. **Scan the room for invites.** `modes/adventures/invites.py` finds "Join Me — The Forgotten
   Desert" bubbles by OCR in two passes:
   - First pass: name fragments, which survive the room label covering half the bubble.
   - Second pass: the blue "Join" header above them, which becomes the click point.

   It is tested on two live Jamaa Township frames, the PC on 2026-10-27 and the laptop on
   2026-10-29. Hosts clearly gather in Jamaa Township. A chat line that only mentions the
   desert is reported but never clicked.
2. **Join, then verify the lobby.** This needs a capture of the lobby and its player count.
3. Otherwise **host** from the adventure menu and wait up to N minutes for players.
4. Otherwise **rotate rooms** and repeat.

**In the adventure:**
- **Variant:** identify which of the 3 maps this is from the first frames.
- **Shard collection:**
  - green by colour;
  - orange by temporal blink detection (a sparkle is a brief bright flash at a fixed ground
    spot), then the paw button;
  - blue with a carry-the-can state machine;
  - white by perch targets;
  - purple only when 3 other players are present, and best-effort, since strangers decide.
- **Progress:** the left-edge counters tell when a crystal is complete.
- **Wrap-up:** open every chest, accept the prizes after 3:00, exit through the portal, repeat.

**Needed from William:**
- Screenshots of the lobby and player count, the left-edge shard counters, a sparkle and the
  paw button, the watering can pickup and pour, a talon-print cactus, a chest and its prize
  dialog, and the exit portal.
- 2–3 full recorded runs, ideally with 4 players.
- Files go to `inbox/adventures/forgotten_desert/` or are recorded with
  `learn --target forgotten_desert`.

## 5. Hard boundaries

The same rules as KICKOFF §3 apply:
- Screen-only, ordinary input, clicks inside the client.
- No chat typing.
- The bot never accepts trades or buddy requests; the watchdog's unknown-dialog path pauses on
  them.
- Recorded inputs are replayed for their *positions and order*, never jittered to look human.
