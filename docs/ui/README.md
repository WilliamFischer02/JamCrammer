# UI QA screenshots

Captured by `scripts/ui_qa.py` (KICKOFF §12.4): every page, the mini HUD and the onboarding
wizard, at 100% (`100/`) and 150% (`150/`, via `QT_SCALE_FACTOR=1.5`) Windows scaling.

```
.venv\Scripts\python scripts\ui_qa.py
set QT_SCALE_FACTOR=1.5 && .venv\Scripts\python scripts\ui_qa.py
```

Checked by eye on 2026-10-26 (M4):
- icons are SVG rendered at the device pixel ratio (crisp at 150%);
- the only raster content is the live preview / calibration canvas, which is always a downscale of the captured frame;
- cards, chips, pills and buttons take every color from `jamcrammer/app/theme.py` tokens;
- fixed: card-child transparency rule overriding button styles, label background boxes in the nav rail and HUD, command-bar overlap at narrow widths, preview placement.
