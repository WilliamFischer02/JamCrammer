"""``window``, ``snap`` and ``input-test`` subcommands (M1)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import cv2

from jamcrammer.core.config import AppConfig
from jamcrammer.core.events import EventBus
from jamcrammer.core.logging import attach_bus, get_logger
from jamcrammer.platform.capture import BenchResult, benchmark, open_backend
from jamcrammer.platform.input import InputController
from jamcrammer.platform.window import WindowTracker, bring_to_foreground, ensure_dpi_awareness
from jamcrammer.vision.match import mean_abs_diff

log = get_logger("cmd")

BENCH_FRAMES = 30
BENCH_BACKENDS = ("wgc", "mss")
BENCH_TIMEOUT_S = 60
INPUT_TEST_SETTLE_S = 0.6
INPUT_TEST_MID_DELAY_S = 0.05
CENTER_ROI_FRACTION = 0.5
EXIT_NO_WINDOW = 3
EXIT_FAILED = 1


def _tracker(cfg: AppConfig) -> WindowTracker:
    ensure_dpi_awareness()
    tracker = WindowTracker(cfg.window_title_match, cfg.window_process_name)
    tracker.refresh()
    return tracker


def run_window(args: argparse.Namespace) -> int:
    cfg: AppConfig = args.cfg
    tracker = _tracker(cfg)
    info = tracker.info
    if info is None:
        print(
            f"AJC window not found (title={cfg.window_title_match!r}, "
            f"process={cfg.window_process_name!r})"
        )
        return EXIT_NO_WINDOW
    print(json.dumps(info.as_dict(), indent=2))
    return 0


def run_snap(args: argparse.Namespace) -> int:
    cfg: AppConfig = args.cfg
    tracker = _tracker(cfg)
    if tracker.info is None:
        print("AJC window not found")
        return EXIT_NO_WINDOW
    if args.benchmark:
        print(f"benchmarking {BENCH_FRAMES} frames per backend (one subprocess each)...")
        for r in _benchmark_isolated(BENCH_FRAMES):
            if r.ok:
                print(
                    f"  {r.backend:4s} ok   mean={r.mean_ms:6.1f} ms  p95={r.p95_ms:6.1f} ms  "
                    f"fps={r.fps:5.1f}  size={r.size[0]}x{r.size[1]}  "
                    f"brightness={r.mean_brightness:5.1f}"
                )
            else:
                print(f"  {r.backend:4s} FAIL {r.error}")
    kind = args.backend or cfg.capture_backend
    backend = open_backend(kind, tracker.provider())
    try:
        t0 = time.perf_counter()
        frame = backend.grab()
        dt = (time.perf_counter() - t0) * 1000.0
    finally:
        backend.close()
    out = (
        Path(args.out)
        if args.out
        else args.paths.captures / f"snap_{time.strftime('%Y%m%d_%H%M%S')}.png"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), frame.image)
    print(
        f"saved {out}  backend={frame.backend} size={frame.size[0]}x{frame.size[1]} "
        f"grab={dt:.1f} ms brightness={frame.mean_brightness():.1f}"
    )
    return 0


def run_bench_one(args: argparse.Namespace) -> int:
    """Hidden: benchmark one backend and print JSON. Each backend gets its own process because
    a WGC session opened after an mss session segfaults (windows-capture 2.0.1 + mss 10.2)."""
    cfg: AppConfig = args.cfg
    tracker = _tracker(cfg)
    if tracker.info is None:
        print(json.dumps({"backend": args.backend, "ok": False, "error": "no window"}))
        return EXIT_NO_WINDOW
    results = benchmark(tracker.provider(), args.frames, kinds=(args.backend,))
    print(json.dumps(results[0].as_dict()))
    return 0


def _benchmark_isolated(frames: int) -> list[BenchResult]:
    out: list[BenchResult] = []
    for kind in BENCH_BACKENDS:
        cmd = [
            sys.executable,
            "-m",
            "jamcrammer",
            "_bench",
            "--backend",
            kind,
            "--frames",
            str(frames),
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=BENCH_TIMEOUT_S, check=False
            )
            line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "{}"
            doc = json.loads(line)
            doc.setdefault("frames", 0)
            doc.setdefault("mean_ms", 0.0)
            doc.setdefault("p95_ms", 0.0)
            doc.setdefault("fps", 0.0)
            doc.setdefault("mean_brightness", 0.0)
            doc["size"] = tuple(doc.get("size", (0, 0)))
            doc.setdefault("error", proc.stderr.strip()[-200:] if proc.returncode else "")
            doc.setdefault("ok", proc.returncode == 0)
            out.append(BenchResult(**doc))
        except Exception as exc:
            out.append(
                BenchResult(
                    backend=kind,
                    ok=False,
                    frames=0,
                    mean_ms=0.0,
                    p95_ms=0.0,
                    fps=0.0,
                    mean_brightness=0.0,
                    size=(0, 0),
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
    return out


def run_input_test(args: argparse.Namespace) -> int:
    """Focus AJC, capture, send one cancelling movement pair, capture again, report the diff.

    Saves before/mid/after PNGs so the result can be checked by eye.
    """
    cfg: AppConfig = args.cfg
    tracker = _tracker(cfg)
    info = tracker.info
    if info is None:
        print("AJC window not found")
        return EXIT_NO_WINDOW
    bus = EventBus()
    attach_bus(bus)
    method = args.method or cfg.input.key_method
    pair = cfg.antiafk.pattern[0]
    out_dir = args.paths.captures / f"input_test_{time.strftime('%Y%m%d_%H%M%S')}_{method}"
    out_dir.mkdir(parents=True, exist_ok=True)

    if not bring_to_foreground(info.hwnd, cfg.input.foreground_timeout_s):
        print("could not bring AJC to the foreground; aborting (no input sent)")
        return EXIT_FAILED
    backend = open_backend(args.backend or cfg.capture_backend, tracker.provider())
    ctl = InputController(tracker.provider(), cfg.input, dry_run=args.dry_run, bus=bus)
    try:
        before = backend.grab()
        ctl.key_tap(pair[0], cfg.antiafk.tap_ms, method)
        time.sleep(INPUT_TEST_MID_DELAY_S)
        mid = backend.grab()
        time.sleep(cfg.antiafk.gap_ms / 1000.0)
        ctl.key_tap(pair[1], cfg.antiafk.tap_ms, method)
        time.sleep(INPUT_TEST_SETTLE_S)
        after = backend.grab()
    finally:
        backend.close()

    h, w = before.image.shape[:2]
    cx0, cy0 = int(w * (1 - CENTER_ROI_FRACTION) / 2), int(h * (1 - CENTER_ROI_FRACTION) / 2)
    cx1, cy1 = w - cx0, h - cy0
    roi = (slice(cy0, cy1), slice(cx0, cx1))
    d_mid = mean_abs_diff(before.image[roi], mid.image[roi])
    d_after = mean_abs_diff(before.image[roi], after.image[roi])
    for name, fr in (("before", before), ("mid", mid), ("after", after)):
        cv2.imwrite(str(out_dir / f"{name}.png"), fr.image)
    result = {
        "method": method,
        "pair": list(pair),
        "tap_ms": cfg.antiafk.tap_ms,
        "gap_ms": cfg.antiafk.gap_ms,
        "dry_run": args.dry_run,
        "center_roi_diff_before_mid": round(d_mid, 3),
        "center_roi_diff_before_after": round(d_after, 3),
        "keys_sent": ctl.stats.keys,
        "dir": str(out_dir),
    }
    (out_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    print("Interpretation: before->mid should be clearly > 0 if the animal moved;")
    print("before->after should be small (cancelling pair). Check the PNGs in the dir above.")
    return 0
