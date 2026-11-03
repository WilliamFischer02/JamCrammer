"""Scratch driver kept for the record (2026-11-03). Second-pass mapping batch: walk a lattice of stops over the known floor of the moth
chamber through the pilot server (goto, scan), keeping every frame for the offline rebuild.
Each step goes to the nearest pending stop from where the animal is; a stop the world grid
cannot plan to is deferred; the walk's timeout scales with the distance. Resumable:
progress in batch_progress.json next to this file."""

import json
import math
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jamcrammer.core.config import WorldConfig
from jamcrammer.modes.adventures.world import FLOOR, FREE, World

HERE = Path(__file__).resolve().parents[1] / "data" / "pilot"
PY = r"C:\dev\JamCrammer\.venv\Scripts\python.exe"
WORLD_DIR = Path(r"C:\dev\JamCrammer\profiles\levels\mystery_below\world")
STEP = float(sys.argv[1]) if len(sys.argv) > 1 else 450.0
MIN_CELLS = 12
PROGRESS = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "batch_progress.json"
LOG = HERE / "batch.log"
MM_PER_S = 5.5  # the animal's speed in minimap px per second (400 scene px/s / 70)
TIMEOUT_BASE_S = 8.0
TIMEOUT_MAX_S = 55.0
RELOAD_EVERY = 8  # stops between world reloads (the server saves it)


def pilot(*argv: str, timeout: float = 420.0) -> dict:
    cmd = [PY, "-m", "jamcrammer", "pilot", *argv]
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
        return json.loads(out.stdout)
    except ValueError:
        return {"error": (out.stdout + out.stderr)[-400:]}


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
    cands = [
        (float(np.mean([p[0] for p in v])), float(np.mean([p[1] for p in v])), len(v))
        for v in pts.values()
        if len(v) >= MIN_CELLS
    ]
    return [
        {
            "world": [round(s[0]), round(s[1])],
            "mm": [round(v, 1) for v in w.world_to_dot((s[0], s[1]))],
            "cells": s[2],
        }
        for s in cands
    ]


def position() -> tuple[float, float] | None:
    res = pilot("where")
    pos = (res.get("pos") or {}).get("pos") if isinstance(res, dict) else None
    return (float(pos[0]), float(pos[1])) if pos else None


def main() -> None:
    w = load_world()
    prog = json.loads(PROGRESS.read_text()) if PROGRESS.exists() else {"stops": None, "done": {}}
    if prog["stops"] is None:
        prog["stops"] = stops(w)
        PROGRESS.write_text(json.dumps(prog))
    st = prog["stops"]
    log(f"batch v2: {len(st)} stops, {len(prog['done'])} done, step {STEP}")
    cur = position()
    if cur is None:
        log("no position: is the animal in the adventure?")
        return
    n_since_reload = 0
    while True:
        pending = [k for k in range(len(st)) if str(k) not in prog["done"]]
        if not pending:
            break
        if n_since_reload >= RELOAD_EVERY:
            w = load_world()
            n_since_reload = 0
        # among the nearest pending stops, the one whose planned path runs over the fewest
        # cells the animal has not walked (the recorded floor is coarse and hides walls;
        # walked cells are sure); no plan at all: deferred
        order = sorted(pending, key=lambda k: math.dist(st[k]["world"], cur))
        a = w.area("moths")
        scored = []
        for k in order[:20]:
            path = w.plan("moths", cur, tuple(st[k]["world"]))
            if path is None:
                prog["done"][str(k)] = {"ok": False, "reason": "no_plan"}
                log(f"stop {k}: no plan on the grid from {[round(v) for v in cur]}: deferred")
                continue
            unsure = sum(1 for pt in path if a.state(pt) != FREE)
            scored.append((unsure, len(path), k))
        if not scored:
            PROGRESS.write_text(json.dumps(prog))
            continue
        scored.sort()
        chosen = scored[0][2]
        log(
            f"choosing stop {chosen}: {scored[0][0]} unwalked cells on a {scored[0][1]}-cell path (best of {len(scored)})"
        )
        s = st[chosen]
        mx, my = s["mm"]
        dist_mm = math.dist(s["world"], cur) / w.scale
        timeout = min(TIMEOUT_MAX_S, TIMEOUT_BASE_S + dist_mm / MM_PER_S * 2.0)
        t0 = time.time()
        res = pilot("goto", str(mx), str(my), "--timeout", f"{timeout:.0f}", "--tol", "3")
        if "refused" in res:
            log(f"stop {chosen}: refused: {res['refused']}; batch halted")
            return
        reason = res.get("reason", res.get("error", "?"))
        ok = bool(res.get("ok"))
        dist = res.get("dist_mm")
        sc = pilot("scan")
        found = len(sc.get("found", [])) if isinstance(sc, dict) else -1
        pos = (sc.get("pos") or {}).get("pos") if isinstance(sc, dict) else None
        if pos:
            cur = (float(pos[0]), float(pos[1]))
        prog["done"][str(chosen)] = {
            "ok": ok,
            "reason": reason,
            "dist": dist,
            "found": found,
            "s": round(time.time() - t0, 1),
        }
        PROGRESS.write_text(json.dumps(prog))
        log(
            f"stop {chosen} mm {mx},{my} ({dist_mm:.0f} mm px away, {timeout:.0f} s): {'ok' if ok else reason} dist {dist} found {found} ({round(time.time() - t0)} s); {len(pending) - 1} left"
        )
        n_since_reload += 1
        if n_since_reload % 4 == 0:
            pilot("save")
    log("batch complete")


if __name__ == "__main__":
    main()
