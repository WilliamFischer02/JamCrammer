"""Long-haul scan of the moth chamber: every lattice stop over the known floor, nearest
first by the world grid's plan, with the acts the level needs on the way: pick blossoms at
any paw pin, light any unlit crystal in reach, bloom any mushroom bridge cap in reach
(a blossom on it), smash any purple / red decoy in reach, restock at the nearest fungus
spot when the slot is empty, close dialogs. Every frame is kept for the rebuild. Resumable
(progress next to this file); refusals (William at the controls, a logout) halt it."""

import json
import math
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, r"C:\dev\JamCrammer")
from jamcrammer.core.config import WorldConfig
from jamcrammer.modes.adventures.world import FLOOR, FREE, World

HERE = Path(__file__).resolve().parents[1] / "data" / "pilot"
PY = r"C:\dev\JamCrammer\.venv\Scripts\python.exe"
WORLD_DIR = Path(r"C:\dev\JamCrammer\profiles\levels\mystery_below\world")
LEVEL = Path(r"C:\dev\JamCrammer\profiles\levels\mystery_below\level.json")
STEP = 450.0
MIN_CELLS = 12
PROGRESS = HERE / "long_haul_progress.json"
LOG = HERE / "long_haul.log"
SLOT = (200, 100)
REACH_PX = 520
SCALE = 70.0
MM_PER_S = 5.5
BRIDGE_MIN_AREA = 15000  # a bridge cap reads as a big "fungus" blob (the real fungi too)
RELOAD_EVERY = 8


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


def load_world() -> World:
    w = World.load(WORLD_DIR, WorldConfig())
    assert w is not None
    return w


def stops(w: World) -> list[dict]:
    a = w.area("moths")
    g = w.cfg.grid_px
    floor = (a.grid == FLOOR) | (a.grid == FREE)
    ys, xs = np.nonzero(floor)
    pts: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for y, x in zip(ys, xs, strict=True):
        wx, wy = (x + 0.5) * g, (y + 0.5) * g
        pts.setdefault((int(wx // STEP), int(wy // STEP)), []).append((wx, wy))
    return [
        {
            "world": [
                round(float(np.mean([p[0] for p in v]))),
                round(float(np.mean([p[1] for p in v]))),
            ],
            "mm": [
                round(float(c), 1)
                for c in w.world_to_dot(
                    (float(np.mean([p[0] for p in v])), float(np.mean([p[1] for p in v])))
                )
            ],
            "cells": len(v),
        }
        for v in pts.values()
        if len(v) >= MIN_CELLS
    ]


def look() -> dict:
    d = pilot("look")
    pl = (d.get("player") or {}).get("screen", [1005, 500])
    cs = d.get("crystals", [])
    fs = d.get("fungi", [])

    def near(items, ok, min_area):
        c = [k for k in items if ok(k) and k["area"] >= min_area]
        c.sort(key=lambda k: math.dist((k["x"], k["y"]), pl))
        return [(k["x"], k["y"], k["area"], round(math.dist((k["x"], k["y"]), pl))) for k in c]

    counts = d.get("counts") or [None, None]
    return {
        "refused": d.get("refused"),
        "dialog": d.get("dialog_x"),
        "pin": d.get("pin"),
        "drop": d.get("drop_pin"),
        "count": counts[0],
        "banner": d.get("banner"),
        "mm": d.get("mm"),
        "dot": d.get("dot"),
        "player": pl,
        "unlit": near(cs, lambda k: k["kind"] == "unlit", 2500),
        "lit": near(cs, lambda k: k["kind"] == "lit", 1500),
        "decoy": near(cs, lambda k: k["kind"] == "decoy", 800),
        "caps": near(fs, lambda k: True, BRIDGE_MIN_AREA),
    }


def close_dialogs() -> None:
    for _ in range(4):
        s = look()
        if not s["dialog"]:
            return
        pilot("click", str(s["dialog"][0]), str(s["dialog"][1]))
        time.sleep(0.8)


def pick_at_pin(s: dict) -> dict:
    for _ in range(3):
        if not s["pin"] or s["count"] == 3:
            break
        pilot("click", str(s["pin"][0]), str(s["pin"][1]))
        time.sleep(1.2)
        close_dialogs()
        s = look()
    return s


def drop_on(x: int, y: int) -> None:
    pilot("click", str(SLOT[0]), str(SLOT[1]))
    time.sleep(0.4)
    pilot("click", str(x), str(y))
    time.sleep(1.8)
    close_dialogs()


def _pin_is_bridge(s: dict) -> bool:
    """The droplet pin also floats over a crystal in reach: a bridge has a big cap under
    the pin and no lit crystal beside it."""
    if not s["drop"]:
        return False
    px, py = s["drop"]
    cap_under = any(abs(c[0] - px) < 160 and -40 < c[1] - py < 260 for c in s["caps"])
    lit_near = any(abs(c[0] - px) < 160 and -40 < c[1] - py < 260 for c in s["lit"])
    return cap_under and not lit_near


def acts_here(s: dict, w: World) -> list[str]:
    """What the screen offers at this stop, done: picks, lights, bridges, smashes."""
    done: list[str] = []
    if s["pin"] and s["count"] != 3:
        s = pick_at_pin(s)
        done.append(f"picked -> {s['count']}")
    # unlit crystals in reach, one blossom each (never a decoy: those read as decoy)
    for _ in range(2):
        if not s["unlit"] or s["unlit"][0][3] > REACH_PX or not s["count"]:
            break
        x, y = s["unlit"][0][0], s["unlit"][0][1]
        drop_on(x, y)
        s = look()
        done.append(f"lit {x},{y} -> {s['count']}")
    # a bridge cap: the game shows a white pin with a blue droplet over it when the animal
    # is in reach; only then is a blossom dropped (a blossom fungus looks the same otherwise)
    if _pin_is_bridge(s) and s["count"]:
        x, y = s["drop"][0], s["drop"][1]
        drop_on(x, y + 120)  # the cap sits under its pin
        s2 = look()
        pilot(
            "mark",
            "bridge",
            str(x),
            str(y + 120),
            "--status",
            "open",
            "--note",
            "mushroom bridge bloomed by the long haul",
        )
        done.append(f"bridge at the drop pin {x},{y}: count {s['count']} -> {s2['count']}")
        s = s2
    elif s["caps"] and 160 < s["caps"][0][3] <= REACH_PX and s["count"] and not s["pin"]:
        # a big cap in view with no pin yet: step up to it and look for the droplet
        x, y = s["caps"][0][0], s["caps"][0][1]
        px, py = s["player"]
        pilot("key", "right" if x > px + 40 else "left" if x < px - 40 else "up", "0.3")
        pilot("key", "up" if y < py - 40 else "down" if y > py + 40 else "left", "0.3")
        s2 = look()
        if _pin_is_bridge(s2) and s2["count"]:
            x, y = s2["drop"][0], s2["drop"][1]
            drop_on(x, y + 120)
            s3 = look()
            pilot(
                "mark",
                "bridge",
                str(x),
                str(y + 120),
                "--status",
                "open",
                "--note",
                "mushroom bridge bloomed by the long haul",
            )
            done.append(
                f"bridge at the drop pin {x},{y} after a step: count {s2['count']} -> {s3['count']}"
            )
            s = s3
        else:
            s = s2
    if s["decoy"] and s["decoy"][0][3] <= REACH_PX:
        x, y = s["decoy"][0][0], s["decoy"][0][1]
        r = pilot("smash", str(x), str(y), "--max", "12")
        done.append(f"smash {x},{y} hits {r.get('hits')}")
    return done


def restock(w: World, cur, level: dict) -> tuple[str, tuple]:
    fungi = sorted(
        (math.dist((s["x"], s["y"]), w.world_to_dot(cur)), s["x"], s["y"])
        for s in level["spots"]
        if s["kind"] == "fungus" and s["area"] == "moths"
    )
    for _d, x, y in fungi[:3]:
        r = pilot("goto", str(round(x, 1)), str(round(y, 1)), "--timeout", "50", "--tol", "2")
        if "refused" in r:
            return "refused", cur
        close_dialogs()
        s = look()
        if s["mm"]:
            cur = w.dot_to_world(tuple(s["mm"]))
        if s["pin"]:
            s = pick_at_pin(s)
            return f"restocked -> {s['count']} at {round(x)},{round(y)}", cur
    return "no pin at the three nearest fungi", cur


def main() -> None:  # noqa: PLR0912 - a scratch driver
    w = load_world()
    level = json.loads(LEVEL.read_text(encoding="utf-8"))
    prog = json.loads(PROGRESS.read_text()) if PROGRESS.exists() else {"stops": None, "done": {}}
    if prog["stops"] is None:
        prog["stops"] = stops(w)
        PROGRESS.write_text(json.dumps(prog))
    st = prog["stops"]
    log(f"long haul: {len(st)} stops, {len(prog['done'])} done")
    s = look()
    if not s["mm"]:
        log("no position: is the animal in the adventure?")
        return
    cur = w.dot_to_world(tuple(s["mm"]))
    n = 0
    while True:
        pending = [k for k in range(len(st)) if str(k) not in prog["done"]]
        if not pending:
            break
        if n and n % RELOAD_EVERY == 0:
            w = load_world()
        a = w.area("moths")
        order = sorted(pending, key=lambda k: math.dist(st[k]["world"], cur))
        scored = []
        for k in order[:20]:
            path = w.plan("moths", cur, tuple(st[k]["world"]))
            if path is None:
                prog["done"][str(k)] = {"ok": False, "reason": "no_plan"}
                continue
            scored.append((sum(1 for pt in path if a.state(pt) != FREE), len(path), k))
        if not scored:
            PROGRESS.write_text(json.dumps(prog))
            continue
        scored.sort()
        k = scored[0][2]
        mm = st[k]["mm"]
        dist_mm = math.dist(st[k]["world"], cur) / SCALE
        t0 = time.time()
        close_dialogs()
        r = pilot(
            "goto",
            str(mm[0]),
            str(mm[1]),
            "--timeout",
            f"{min(55.0, 8.0 + dist_mm / MM_PER_S * 2.0):.0f}",
            "--tol",
            "3",
        )
        if "refused" in r:
            log(f"stop {k}: refused: {r['refused']}; halted")
            PROGRESS.write_text(json.dumps(prog))
            return
        s = look()
        if s["mm"]:
            cur = w.dot_to_world(tuple(s["mm"]))
        done = acts_here(s, w)
        pilot("scan")
        if s["count"] == 0:
            msg, cur = restock(w, cur, level)
            done.append(msg)
            if msg == "refused":
                log(f"stop {k}: refused during a restock; halted")
                PROGRESS.write_text(json.dumps(prog))
                return
        prog["done"][str(k)] = {
            "ok": bool(r.get("ok")),
            "reason": r.get("reason"),
            "dist": r.get("dist_mm"),
            "acts": done,
            "s": round(time.time() - t0),
        }
        PROGRESS.write_text(json.dumps(prog))
        log(
            f"stop {k} mm {mm} ({dist_mm:.0f} away): {r.get('reason')} dist {r.get('dist_mm')}; {'; '.join(done) or 'nothing to do'}; {len(pending) - 1} left ({round(time.time() - t0)} s)"
        )
        n += 1
        if n % 4 == 0:
            pilot("save")
    log("long haul complete")


if __name__ == "__main__":
    main()
