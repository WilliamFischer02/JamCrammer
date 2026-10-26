# inbox/

Drop files here for JamCrammer to ingest. Contents are gitignored (only these READMEs are committed).

| Folder | What goes there |
|---|---|
| `bestguess/` | PNG screenshots and OBS `.mp4`/`.mkv` recordings of the Best Guess Game |
| `parties/` | My Parties panel screenshots (especially the Best Guess banner when you see it) |
| `popups/` | the idle "still playing?" popup, the logged-out popup, the Play Now screen, any unknown dialog |
| `arcade/double_up/` | Double Up screenshots and recordings |
| `arcade/jamaa_derby/` | Jamaa Derby screenshots and recordings |
| `arcade/gem_ball/` | Gem Ball screenshots and recordings |
| `arcade/other/` | any other mini-game |
| `_processed/` | originals are moved here after ingest. Never deleted. |

## Optional filename tags (pre-label a frame)

Append a double-underscore tag before the extension:

`__question` `__locked` `__reveal_correct` `__reveal_wrong` `__popup_still_here` `__popup_logged_out` `__party_panel`

Example: `bg_001__reveal_correct.png`

## Ingest

```
.venv\Scripts\python -m jamcrammer ingest
```

or the GUI "Import inbox" button. Each import becomes a Learn session under `captures/sessions/<timestamp>_<source>/`.
