"""Seed the Best Guess answer bank from a plain text list (one fact per line, ending in
"True" or "False"; lines without a verdict are kept out). The community wiki list William
pasted on 2026-10-26 lives in ``assets/bestguess/facts_wiki.txt``."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from jamcrammer.core.store import Store
from jamcrammer.vision.ocr import normalize_text

_LINE = re.compile(r"^(?P<fact>.+?)\s+(?P<answer>true|false)\.?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class SeedFact:
    text: str
    answer: str  # T | F


def parse_facts(text: str) -> list[SeedFact]:
    out: list[SeedFact] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if m is None:
            continue  # no verdict on the wiki: leave it to be learned in play
        out.append(
            SeedFact(m.group("fact").strip(), "T" if m.group("answer").lower() == "true" else "F")
        )
    return out


def seed_store(store: Store, facts: list[SeedFact], source: str = "wiki") -> int:
    """Upsert every fact as a confirmed answer; the file wins over an earlier guess."""
    n = 0
    seen: set[str] = set()
    for f in facts:
        norm = normalize_text(f.text)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        store.upsert_fact(norm, f.text, f.answer, source, confirmed=True)
        n += 1
    return n


def seed_from_file(store: Store, path: Path, source: str = "wiki") -> int:
    if not path.exists():
        return 0
    return seed_store(store, parse_facts(path.read_text(encoding="utf-8")), source)


def already_seeded(store: Store, source: str = "wiki") -> bool:
    rows = store.query("SELECT COUNT(*) AS n FROM bg_facts WHERE source=?", (source,))
    return bool(rows and int(rows[0]["n"]) > 0)
