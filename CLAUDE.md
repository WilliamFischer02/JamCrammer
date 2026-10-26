# JamCrammer — session entry point

**Source of truth:** `docs/KICKOFF.md`. Read it completely before writing code. Then read `handoff.md` (current state, next steps, open questions) and `decisions.md` (why things are the way they are).

## Role
Principal-level Windows desktop + game-automation engineer (KICKOFF §0). Blunt, structured updates for William (§1). Batch questions. Use defaults while waiting.

## Hard boundaries (§3)
Screen-only: pixels in, ordinary OS-level mouse/keyboard out. No memory, packets, patching, or disguise. Clicks only inside the AJC client area. Never store or type credentials.

## Toolchain
- Python venv at `.venv` (3.14; 3.11+ OK). `scripts\setup.ps1` builds it. `run.bat` launches the GUI.
- `ruff check .`  ·  `mypy` (strict on `jamcrammer/core`, `jamcrammer/platform`)  ·  `pytest`
- Pinned deps in `pyproject.toml`. No magic numbers in code: config (`config.yaml`) or profiles (`profiles/*.yaml`).

## Layout
See KICKOFF §5. `jamcrammer/` package, `assets/` (SVG only), `profiles/`, `inbox/` (user drops), `captures/` + `data/` (runtime, gitignored), `tests/`, `scripts/`, `docs/`.

## Working rules (§16)
- Milestones in order (§15); each needs its acceptance check before the next.
- Commit at each milestone. Ask before pushing.
- Keep `handoff.md` current; log non-obvious choices in `decisions.md`.
- Milestone reports: 5 lines (done / verified how / next / blocked-on-William / risks).
- If William says "new files in the inbox": ingest, Read the key frames yourself, review the report, update profiles/templates/tests, report a checklist.
- Every input action has a postcondition check; every wait has a timeout; failures degrade to a safe pause; every decision is logged.

## Environment facts (discovered 2026-10-26)
- AJC client: Electron app `AJ Classic.exe` (`%LOCALAPPDATA%\Programs\aj-classic\`), window title exactly `Animal Jam Classic`, class `Chrome_WidgetWin_1`, frameless fullscreen 1920×1080 at (0,0) on the primary monitor, DPI 96 (100%).
- Three monitors: primary 1920×1080 at (0,0); 1920×1080 at (-1920,82); portrait 720×1280 at (-2640,153).
- Title match must be exact and must exclude browser classes (a Firefox tab titled "Animal Jam Classic …" also exists).
- ffmpeg 9 is on PATH. Tesseract is not installed (winocr is the OCR engine).
