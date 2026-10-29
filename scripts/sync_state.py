"""Carry the gitignored machine state between William's PC and laptop.

git syncs the code but not config.yaml, the calibrated profiles, the answer bank, the Derby
learned offsets or the replay records that open the live gate. ``export`` zips them into a
OneDrive folder; ``import`` unpacks a bundle, backing up whatever it replaces. The answer bank
is merged (both machines keep learning), never overwritten.

    .venv\\Scripts\\python scripts\\sync_state.py export          # on the machine that has the state
    .venv\\Scripts\\python scripts\\sync_state.py import          # on the other one (newest bundle)
    .venv\\Scripts\\python scripts\\sync_state.py import --bundle <zip>

A copy outside the repo (e.g. in the OneDrive folder) works on the repo you ``cd`` into.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import sqlite3
import sys
import tempfile
import time
import zipfile
from pathlib import Path


def _is_repo(p: Path) -> bool:
    return (p / "pyproject.toml").is_file() and (p / "jamcrammer" / "__init__.py").is_file()


_HERE = Path(__file__).resolve().parent.parent
# a copy run from the OneDrive folder (the PC may not have this script in git) uses the cwd's repo
ROOT = _HERE if _is_repo(_HERE) else Path.cwd()
DB = "data/jamcrammer.db"
DEFAULT_DIR = (
    Path(os.environ.get("ONEDRIVE", str(Path.home())))
    / "WF_SYNC"
    / "06_SYNCED_EXPORTS"
    / "JamCrammer"
)
FILE_GLOBS = (
    "config.yaml",
    "profiles/*.yaml",
    "data/derby_learned.json",
    "data/onboarded",
    "captures/sessions/*/analysis/*.json",
    "captures/sessions/*/analysis/*.txt",
)


def _state_files() -> list[Path]:
    found: list[Path] = []
    for pattern in FILE_GLOBS:
        for p in sorted(ROOT.glob(pattern)):
            if p.is_file() and not p.name.endswith((".example.yaml", ".proposed.yaml")):
                found.append(p)
    return found


def export(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle = (
        out_dir / f"jamcrammer_state_{socket.gethostname()}_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    )
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in _state_files():
            zf.write(p, p.relative_to(ROOT).as_posix())
        db = ROOT / DB
        if db.exists():
            # sqlite backup API: a consistent copy even while the GUI holds the database open
            with tempfile.TemporaryDirectory() as tmp:
                snap = Path(tmp) / "jamcrammer.db"
                src, dst = sqlite3.connect(db), sqlite3.connect(snap)
                with dst:
                    src.backup(dst)
                src.close()
                dst.close()
                zf.write(snap, DB)
        names = zf.namelist()
    print(f"exported {len(names)} files to {bundle}")
    for n in names:
        print(f"  {n}")
    return bundle


def _merge_facts(incoming: Path, local: Path) -> tuple[int, int]:
    """Merge bg_facts from ``incoming`` into ``local``: new facts are added; on a shared fact
    the counts take the max and a disagreement keeps the side with more confirmations."""
    con = sqlite3.connect(local)
    con.execute("ATTACH DATABASE ? AS inc", (str(incoming),))
    added = con.execute("INSERT OR IGNORE INTO bg_facts SELECT * FROM inc.bg_facts").rowcount
    updated = con.execute(
        """
        UPDATE bg_facts SET
          answer = CASE WHEN i.confirmed_count > bg_facts.confirmed_count THEN i.answer
                        ELSE bg_facts.answer END,
          display_text = CASE WHEN i.confirmed_count > bg_facts.confirmed_count
                              THEN i.display_text ELSE bg_facts.display_text END,
          seen_count = MAX(bg_facts.seen_count, i.seen_count),
          confirmed_count = MAX(bg_facts.confirmed_count, i.confirmed_count),
          conflict_count = MAX(bg_facts.conflict_count, i.conflict_count),
          last_seen = MAX(bg_facts.last_seen, i.last_seen)
        FROM inc.bg_facts AS i
        WHERE i.normalized_text = bg_facts.normalized_text
        """
    ).rowcount
    con.commit()
    con.execute("DETACH DATABASE inc")
    con.close()
    return added, updated - added  # the freshly inserted rows match the UPDATE too


def import_bundle(bundle: Path) -> None:
    backup = ROOT / "data" / "sync_backup" / time.strftime("%Y%m%d_%H%M%S")
    with zipfile.ZipFile(bundle) as zf, tempfile.TemporaryDirectory() as tmp:
        zf.extractall(tmp)
        for name in zf.namelist():
            src = Path(tmp) / name
            dst = ROOT / name
            if name == DB and dst.exists():
                (backup / "data").mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, backup / DB)
                added, updated = _merge_facts(src, dst)
                print(f"  {name}: answer bank merged ({added} new facts, {updated} shared)")
                continue
            if dst.exists():
                (backup / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, backup / name)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print(f"  {name}")
    print(f"imported {bundle.name}; replaced files backed up under {backup}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("action", choices=["export", "import"])
    ap.add_argument(
        "--dir", type=Path, default=DEFAULT_DIR, help=f"bundle folder (default {DEFAULT_DIR})"
    )
    ap.add_argument("--bundle", type=Path, help="import this zip instead of the newest in --dir")
    args = ap.parse_args()
    if not _is_repo(ROOT):
        print(f"{ROOT} is not the JamCrammer repo; cd into it first", file=sys.stderr)
        return 2
    if args.action == "export":
        export(args.dir)
        return 0
    bundle = args.bundle
    if bundle is None:
        mine = socket.gethostname()
        candidates = sorted(
            (p for p in args.dir.glob("jamcrammer_state_*.zip") if f"_{mine}_" not in p.name),
            key=lambda p: p.stat().st_mtime,
        )
        if not candidates:
            print(f"no bundle from another machine in {args.dir}", file=sys.stderr)
            return 1
        bundle = candidates[-1]
    import_bundle(bundle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
