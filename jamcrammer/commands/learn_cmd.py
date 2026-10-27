"""``learn --target <x>``: the CLI emergency recorder (M1)."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from jamcrammer.core.config import AppConfig
from jamcrammer.core.events import EventBus
from jamcrammer.core.logging import attach_bus, get_logger
from jamcrammer.core.store import Store
from jamcrammer.learn.recorder import Recorder, RecorderStats
from jamcrammer.platform.capture import open_stage_backend
from jamcrammer.platform.window import WindowTracker, ensure_dpi_awareness

log = get_logger("cmd.learn")

WINDOW_WAIT_S = 30.0
WINDOW_POLL_S = 1.0
STATS_TICK_S = 2.0
EXIT_NO_WINDOW = 3
EXIT_NOT_FOUND = 4


def _session_path(args: argparse.Namespace, name: str) -> Path | None:
    p = Path(name)
    if p.exists():
        return p
    q = args.paths.sessions / name
    return q if q.exists() else None


def run_ingest(args: argparse.Namespace) -> int:
    """Import inbox files into sessions, analyse each, render reports."""
    from jamcrammer.learn.analyze import analyze_session
    from jamcrammer.learn.ingest import ingest_inbox
    from jamcrammer.learn.report import render_report

    cfg: AppConfig = args.cfg
    res = ingest_inbox(args.paths, cfg.learn)
    if not res.sessions:
        print("inbox is empty (nothing under inbox/<target>/ to import)")
        return 0
    print(
        f"ingested {res.images} images and {res.videos} videos into "
        f"{len(res.sessions)} session(s), {res.keyframes} keyframes"
    )
    for s in res.sessions:
        result = analyze_session(s, cfg.analysis, args.paths)
        report = render_report(s, result.as_dict())
        print(
            f"  {s.name}: {len(result.states)} states, {len(result.rounds)} rounds, "
            f"{len(result.answers)} answers, missing={result.missing}"
        )
        print(f"  report: {report}")
    return 0


def run_report(args: argparse.Namespace) -> int:
    from jamcrammer.learn.analyze import analyze_session
    from jamcrammer.learn.report import render_report

    cfg: AppConfig = args.cfg
    session = _session_path(args, args.session)
    if session is None:
        print(f"session not found: {args.session}")
        return EXIT_NOT_FOUND
    result = analyze_session(session, cfg.analysis, args.paths, write_profile=not args.no_profile)
    report = render_report(session, result.as_dict())
    print(
        f"{session.name}: {result.frames} frames, {len(result.states)} states, cycle={result.cycle}"
    )
    print(f"regions: {[(r['kind'], r['box']) for r in result.regions if r['kind'] != 'text']}")
    print(f"proposal: {list(result.proposal)}  missing: {result.missing}")
    print(f"answers seeded: {len(result.answers)}")
    print(f"report: {report}")
    return 0


def run_replay(args: argparse.Namespace) -> int:
    from jamcrammer.replay.harness import NearestStateDetector, format_timeline, replay

    session = _session_path(args, args.session)
    if session is None:
        print(f"session not found: {args.session}")
        return EXIT_NOT_FOUND
    if not (session / "analysis" / "labels.json").exists():
        print("no analysis labels yet; run: jamcrammer report <session>")
        return EXIT_NOT_FOUND
    which = getattr(args, "detector", "nearest")
    if which == "bestguess":
        from jamcrammer.core.profiles import load_profile
        from jamcrammer.modes.bestguess.state import BestGuessDetector

        cfg: AppConfig = args.cfg
        detector: Any = BestGuessDetector(load_profile("bestguess", args.paths), cfg.bestguess)
    elif which == "derby":
        from jamcrammer.core.profiles import load_profile
        from jamcrammer.modes.arcade.derby import DerbyDetector

        cfg = args.cfg
        detector = DerbyDetector(load_profile("jamaa_derby", args.paths), cfg.derby)
    else:
        detector = NearestStateDetector(session)
    if getattr(args, "dense", False):
        from jamcrammer.learn.analyze.bestguess_banner import DEFAULT_SCAN_FPS, session_video
        from jamcrammer.replay.harness import interval_labeler, load_intervals
        from jamcrammer.replay.sources import VideoSource

        video = session_video(session, args.paths)
        intervals = load_intervals(session)
        if video is None or not intervals:
            print("dense replay needs the processed inbox video and analysis/intervals.json")
            return EXIT_NOT_FOUND
        bg = args.cfg.bestguess
        labeler = interval_labeler(intervals, bg.question_s, bg.lock_seconds)
        frames = VideoSource(video, DEFAULT_SCAN_FPS).frames()
        result = replay(session, detector, frames=frames, labeler=labeler)
        which += "-dense"
    else:
        result = replay(session, detector)
    print(format_timeline(result))
    from jamcrammer.core.gate import write_replay_record

    print(f"saved {write_replay_record(session, result.accuracy, result.frames, which)}")
    return 0


def run_learn(args: argparse.Namespace) -> int:
    cfg: AppConfig = args.cfg
    ensure_dpi_awareness()
    tracker = WindowTracker(cfg.window_title_match, cfg.window_process_name)
    deadline = time.monotonic() + WINDOW_WAIT_S
    while tracker.refresh() is None:
        if time.monotonic() > deadline:
            print(f"AJC window not found after {WINDOW_WAIT_S:.0f}s. Is the game open?")
            return EXIT_NO_WINDOW
        print("waiting for the AJC window...")
        time.sleep(WINDOW_POLL_S)
    info = tracker.require()

    bus = EventBus()
    attach_bus(bus)
    store = Store(args.paths.db)
    backend = open_stage_backend(cfg.capture_backend, tracker.provider())

    stamp = time.strftime("%Y%m%d_%H%M%S")
    name = f"{stamp}_{args.target}" + (f"_{args.name}" if args.name else "")
    session_dir = args.paths.sessions / name
    session_id = store.start_session("learn", args.target, session_dir, time.time())

    recorder = Recorder(
        session_dir=session_dir,
        target=args.target,
        cfg=cfg.learn,
        grab=backend.grab,
        window=tracker.provider(),
        bus=bus,
        capture_input=True,
        video=args.video,
        ocr=cfg.learn.ocr_keyframes,
        extra_meta={"source": "live", "backend": backend.name, "app_cfg": cfg.model_dump()},
    )

    print(f"Recording {args.target} -> {session_dir}")
    w, h = info.client_size
    print(
        f"window: {w}x{h} @ ({info.client_rect.left},{info.client_rect.top}) "
        f"dpi={info.dpi} backend={backend.name}"
    )
    print("Play normally. Nothing is sent to the game.")
    print("  F6 = mark   F7 = correct   F8 = wrong   F12 or Ctrl+C = stop")
    recorder.start()

    def tick(stats: RecorderStats) -> None:
        elapsed = time.time() - stats.started_ts
        if args.duration is not None and elapsed >= args.duration:
            recorder.stop()
            return
        sys.stdout.write(
            f"\r  {elapsed:6.0f}s  frames={stats.frames:5d}  keyframes={stats.keyframes:4d}  "
            f"markers={stats.markers:3d}  inputs={stats.inputs:4d}  ring={stats.ring_dumps:4d}  "
            f"ocr={stats.ocr_snapshots:4d}  errors={stats.grab_errors}   "
        )
        sys.stdout.flush()
        if tracker.poll() == "lost":
            log.warning("window lost during recording")

    try:
        recorder.run_until_stopped(STATS_TICK_S, tick)
    finally:
        backend.close()
        store.end_session(session_id, time.time(), recorder.stats.as_dict())
        store.close()
    s = recorder.stats
    print()
    print(
        f"Stopped. frames={s.frames} keyframes={s.keyframes} markers={s.markers} "
        f"inputs={s.inputs} ocr={s.ocr_snapshots}"
    )
    print(f"Session: {session_dir}")
    return 0
