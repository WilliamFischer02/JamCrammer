# Decisions log

Non-obvious choices and why. Newest at the bottom.

## 2026-10-26

- **Python 3.14 instead of 3.12.** 3.14 was already installed and every pinned dependency publishes a 3.14 wheel (verified with `pip index versions`). Spec allows 3.11+. `scripts/setup.ps1` still prefers 3.12 when present so a clean machine matches the spec's first choice.
- **Spec file renamed** from `docs/JamCrammer_KICKOFF.md` to `docs/KICKOFF.md`, matching the spec's own instruction and `CLAUDE.md`.
- **Window discovery is exact-title + process-name + class exclusion.** `EnumWindows` showed two visible windows whose titles start with "Animal Jam Classic": the real Electron client and a Firefox tab. Matching on exact title `Animal Jam Classic`, preferring process `AJ Classic.exe`, and rejecting browser classes (`MozillaWindowClass`, Chrome's `Chrome_WidgetWin_1` is shared with Electron so it can't be excluded by class alone) avoids capturing a browser.
- **`run.bat` lives at the repo root as a one-line shim** to `scripts/run.bat`, so the M0 acceptance check ("`run.bat` opens a placeholder window") works from the root while the canonical scripts stay in `scripts/`.
- **Fixture `tests/fixtures/my_parties_2026-10-26.png`** was taken from William's 13:32 MDT screenshot in `OneDrive/Pictures/Screenshots`. It is a 704×552 crop of the panel (not a full-window shot), so panel-locating tests need a separate full-window fixture later.
- **`inbox/` gitignore pattern** keeps every `README.md` and `.gitkeep` while ignoring all dropped files.
