"""Scratch driver kept for the record (2026-11-03). Walk the recorded route's chamber acts through the pilot server: goto each spot, then
pick (the paw pin), light (click the blossom slot, click the unlit crystal), smash (the
pilot's smash on the nearest purple / red blob), closing dialogs on the way; every frame is
kept for the rebuild. Resumable: progress in route_acts_progress.json. Refusals (William
at the controls) are retried for a minute, then the run halts."""

import json
import math
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / "data" / "pilot"
PY = r"C:\dev\JamCrammer\.venv\Scripts\python.exe"
LEVEL = Path(r"C:\dev\JamCrammer\profiles\levels\mystery_below\level.json")
PROGRESS = HERE / "route_acts_progress.json"
LOG = HERE / "route_acts.log"
SLOT = (200, 100)  # the light blossom slot
START = int(sys.argv[1]) if len(sys.argv) > 1 else 0
REACH_PX = 520  # a thing this far from the animal on screen can be acted on
MM_PER_S = 5.5


def pilot(*argv: str, timeout: float = 420.0) -> dict:
    cmd = [PY, "-m", "jamcrammer", "pilot", *argv]
    for _ in range(20):
        try:
            out = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                cwd=r"C:\dev\JamCrammer",
            )
        except subprocess.TimeoutExpired:
            return {"error": "client timeout"}
        try:
            d = json.loads(out.stdout)
        except ValueError:
            return {"error": (out.stdout + out.stderr)[-300:]}
        if "refused" in d and "controls" in d["refused"]:
            time.sleep(3.0)
            continue
        return d
    return {"refused": "William at the controls for a minute"}


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def look() -> dict:
    d = pilot("look")
    pl = (d.get("player") or {}).get("screen", [1005, 500])
    cs = d.get("crystals", [])

    def near(kind_ok, min_area=1500):
        c = [k for k in cs if kind_ok(k["kind"]) and k["area"] >= min_area]
        c.sort(key=lambda k: math.dist((k["x"], k["y"]), pl))
        return [(k["x"], k["y"], k["area"], round(math.dist((k["x"], k["y"]), pl))) for k in c]

    counts = d.get("counts") or [None, None]
    return {
        "dialog": d.get("dialog_x"),
        "pin": d.get("pin"),
        "count": counts[0],
        "banner": d.get("banner"),
        "mm": d.get("mm"),
        "player": pl,
        "unlit": near(lambda k: k == "unlit", 2500),
        "lit": near(lambda k: k == "lit", 1500),
        "decoy": near(lambda k: k == "decoy", 800),
        "refused": d.get("refused"),
    }


def close_dialogs() -> int:
    n = 0
    for _ in range(4):
        s = look()
        if not s["dialog"]:
            break
        pilot("click", str(s["dialog"][0]), str(s["dialog"][1]))
        n += 1
        time.sleep(0.8)
    return n


def goto(mm, timeout: float) -> dict:
    return pilot("goto", str(mm[0]), str(mm[1]), "--timeout", f"{timeout:.0f}", "--tol", "2")


def do_pick(s: dict) -> str:
    for _ in range(4):
        if s["count"] == 3:
            return "full"
        if not s["pin"]:
            return "no_pin"
        pilot("click", str(s["pin"][0]), str(s["pin"][1]))
        time.sleep(1.2)
        close_dialogs()
        s = look()
    return f"count {s['count']}"


SEARCH_MM = [(0, -4), (4, 0), (0, 4), (-4, 0), (4, -4), (-4, 4), (-4, -4), (4, 4)]


def search(s: dict, mm, want: str) -> dict:
    """No ``want`` (unlit / decoy) in the light at the spot: the recorded spot is good to a
    few hundred px and the cave is black past the light, so step around it and look."""
    for dx, dy in SEARCH_MM:
        if s[want] and s[want][0][3] <= REACH_PX:
            return s
        goto((mm[0] + dx, mm[1] + dy), 25.0)
        close_dialogs()
        s = look()
    return s


def do_light(s: dict, mm=None) -> str:
    if s["lit"] and s["lit"][0][3] <= 250 and not s["unlit"]:
        return "already_lit"
    if (not s["unlit"] or s["unlit"][0][3] > REACH_PX) and mm is not None:
        s = search(s, mm, "unlit")
    if not s["unlit"] or s["unlit"][0][3] > REACH_PX:
        return "no_unlit_in_reach" if not s["lit"] or s["lit"][0][3] > 250 else "already_lit"
    if (s["count"] or 0) < 1:
        return "no_blossom"
    x, y = s["unlit"][0][0], s["unlit"][0][1]
    pilot("click", str(SLOT[0]), str(SLOT[1]))
    time.sleep(0.4)
    pilot("click", str(x), str(y))
    time.sleep(1.5)
    close_dialogs()
    after = look()
    lit_now = after["lit"] and math.dist((after["lit"][0][0], after["lit"][0][1]), (x, y)) < 120
    return f"lit (count {after['count']})" if lit_now else f"dropped? count {after['count']}"


SCALE = 70.0  # scene px per minimap px


def do_smash(s: dict, mm=None) -> str:
    """A purple / red decoy blob when one is on screen, else the recorded spot itself (grey
    rocks have no colour of their own): the pilot's smash stops after a few clicks without
    a Courage pop, so a miss is cheap."""
    if s["decoy"] and s["decoy"][0][3] <= REACH_PX:
        x, y = s["decoy"][0][0], s["decoy"][0][1]
        how = "decoy"
    elif mm is not None and s["mm"]:
        px, py = s["player"]
        x = int(px + (mm[0] - s["mm"][0]) * SCALE)
        y = int(py + (mm[1] - s["mm"][1]) * SCALE)
        how = "spot"
        if not (100 <= x <= 1820 and 60 <= y <= 920):
            return "spot_off_screen"
    else:
        return "no_position"
    r = pilot("smash", str(x), str(y), "--max", "8")
    return f"smash {how} hits {r.get('hits')} clicks {r.get('clicks')}"


def main() -> None:
    level = json.loads(LEVEL.read_text(encoding="utf-8"))
    spots = {s["id"]: s for s in level["spots"]}
    acts = level["plan"]["moths"]
    prog = json.loads(PROGRESS.read_text()) if PROGRESS.exists() else {"done": {}}
    log(f"route acts: {len(acts)} acts, {len(prog['done'])} done, from {START}")
    cur = None
    for i, act in enumerate(acts):
        if i < START or str(i) in prog["done"]:
            continue
        sp = spots[act["spot"]]
        mm = (round(sp["x"], 1), round(sp["y"], 1))
        if act["action"] == "smash":
            prog["done"][str(i)] = {"outcome": "skipped: rocks are smashed from the picture later"}
            PROGRESS.write_text(json.dumps(prog))
            continue
        t0 = time.time()
        close_dialogs()
        dist = math.dist(mm, cur) if cur else 20.0
        res = goto(mm, min(90.0, 12.0 + dist / MM_PER_S * 3.0))
        if "refused" in res:
            log(f"act {i}: refused: {res['refused']}; halted")
            return
        s = look()
        cur = s["mm"] or cur
        if act["action"] == "pick":
            outcome = do_pick(s)
        elif act["action"] == "light":
            outcome = do_light(s, mm)
        elif act["action"] == "smash":
            outcome = do_smash(s, mm)
        else:
            outcome = "skipped"
        pilot("scan")
        prog["done"][str(i)] = {
            "goto": res.get("reason"),
            "dist": res.get("dist_mm"),
            "outcome": outcome,
            "s": round(time.time() - t0),
        }
        PROGRESS.write_text(json.dumps(prog))
        log(
            f"act {i} {act['action']} {act['kind']} @ {mm}: goto {res.get('reason')} ({res.get('dist_mm')} mm px off), {outcome}, banner '{s['banner']}' ({round(time.time() - t0)} s)"
        )
        if i % 5 == 0:
            pilot("save")
    log("route acts complete")


if __name__ == "__main__":
    main()
