# Decisions log

Non-obvious choices and why. Newest at the bottom.

## 2026-10-26 — M0

- **Python 3.14 instead of 3.12.** 3.14 was already installed and every pinned dependency publishes a 3.14 wheel (verified with `pip index versions`). Spec allows 3.11+. `scripts/setup.ps1` still prefers 3.12 when present so a clean machine matches the spec's first choice.
- **Spec file renamed** from `docs/JamCrammer_KICKOFF.md` to `docs/KICKOFF.md`, matching the spec's own instruction and `CLAUDE.md`.
- **Window discovery is exact-title + process-name + browser-class exclusion.** `EnumWindows` showed two visible windows whose titles start with "Animal Jam Classic": the real Electron client and a Firefox tab. Matching on exact title, preferring process `AJ Classic.exe`, and rejecting browser classes avoids capturing a browser. `Chrome_WidgetWin_1` is shared by Chrome and Electron, so it cannot be used as an exclusion.
- **`run.bat` lives at the repo root as a one-line shim** to `scripts/run.bat`, so the M0 acceptance check works from the root while the canonical scripts stay in `scripts/`.
- **Fixture `tests/fixtures/my_parties_2026-10-26.png`** was taken from William's 13:32 MDT screenshot in `OneDrive/Pictures/Screenshots`. It is a 704×552 crop of the panel (not a full-window shot), so panel-locating tests need a separate full-window fixture later.
- **`inbox/` gitignore pattern** keeps every `README.md` and `.gitkeep` while ignoring all dropped files.

## 2026-10-26 — M1

- **AJC client facts.** Electron app `AJ Classic.exe`, frameless fullscreen, client rect exactly (0,0)-(1920,1080) on the primary monitor, DPI 96. The process is made per-monitor-DPI-aware (v2) before any capture so all coordinates are physical pixels.
- **Capture backend: WGC primary, mss fallback, never mixed in one process.** Benchmark (30 frames, 1920×1080): WGC mean 42 ms / p95 67 ms (~24 fps delivered), mss mean 33 ms / p95 35 ms (30 fps). Both are inside the 100 ms budget. WGC keeps working when other windows overlap the client, so `auto` tries WGC first. **Opening a WGC session after an mss session in the same process segfaults the interpreter** (windows-capture 2.0.1 + mss 10.2.0 on Python 3.14); WGC→WGC and mss→WGC are fine. Therefore `auto` only falls back wgc→mss, and `snap --benchmark` runs each backend in its own subprocess (hidden `_bench` subcommand).
- **WGC object must be kept alive.** The `WindowsCapture` instance is referenced from the backend (`self._capture`); the native thread calls back into it, and letting it be garbage-collected crashed the process. `close()` stops and then waits for the native thread before returning; leaving a session open at interpreter exit also crashes, so every CLI path closes the backend in a `finally`.
- **Input via raw `SendInput` (ctypes), not pydirectinput/pyautogui.** One code path with two key methods: hardware scan codes (`KEYEVENTF_SCANCODE`, default, equivalent to pydirectinput) and virtual keys (equivalent to pyautogui). `input-test --method scancode|vk` decides which AJC accepts; the result goes here once William runs it live. Mouse moves use `SetCursorPos` + `GetCursorPos` verification, then press/release through `SendInput`.
- **Presence detection** compares `GetLastInputInfo` against the controller's own last injected timestamp plus `presence_slack_s` (0.5 s), because the OS counts injected input as input.
- **Recorder keyframe threshold raised 6 → 12.** In a static room the ambient animation (water, other players) produces pHash distances of 6–8 between consecutive 5 fps frames; at 6 that was ~1 keyframe/s (≈7 GB/h of PNG). Real scene changes (panel open, room change) are far above 12. The hash is computed on a 256 px-wide grayscale downscale.
- **Recorder is source-agnostic.** It takes a `grab()` callable, so the same code records the live window or a synthetic/replayed source; the unit test drives it with generated frames. The first hash call imports scipy (~1–2 s cold), so `start()` warms it before the capture loop begins.
- **Learn session layout.** `captures/sessions/<YYYYmmdd_HHMMSS>_<target>[_<name>]/` with `frames/kf_*.png` (keyframes), `markers/m###_<kind>_<ms>/*.jpg` (ring dumps ±window), optional `video.mp4`, and `meta.jsonl` (`session` header, `keyframe`, `ocr`, `input`, `marker`, `window`, `end`). Windows OCR runs on every keyframe in the writer thread (best effort).
- **Console output is ASCII-only.** The Windows console is cp1252 by default; a `→` in a print crashed `input-test`. `main()` reconfigures stdout/stderr with `errors="replace"` as a belt-and-braces.
