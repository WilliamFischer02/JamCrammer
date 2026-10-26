"""SQLite store (KICKOFF §6). One connection, serialized by a lock, WAL mode."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from jamcrammer.core.events import Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS party_sightings (
  id INTEGER PRIMARY KEY,
  banner_id TEXT NOT NULL,
  label TEXT NOT NULL,
  slot_index INTEGER NOT NULL,
  first_seen REAL NOT NULL,
  last_seen REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sightings_banner ON party_sightings(banner_id, last_seen);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  level TEXT NOT NULL,
  source TEXT NOT NULL,
  type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  screenshot_path TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS bg_facts (
  normalized_text TEXT PRIMARY KEY,
  display_text TEXT NOT NULL,
  answer TEXT NOT NULL CHECK (answer IN ('T','F','?')),
  seen_count INTEGER NOT NULL DEFAULT 0,
  confirmed_count INTEGER NOT NULL DEFAULT 0,
  conflict_count INTEGER NOT NULL DEFAULT 0,
  last_seen REAL NOT NULL,
  source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bg_rounds (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  fact_key TEXT,
  ocr_text TEXT,
  ocr_conf REAL,
  match_score REAL,
  chosen TEXT,
  outcome TEXT,
  multiplier INTEGER,
  gems_est INTEGER
);

CREATE TABLE IF NOT EXISTS arcade_rounds (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  game TEXT NOT NULL,
  duration_s REAL,
  gems_reported INTEGER,
  mismatches INTEGER,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS ledger (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  hud_gems INTEGER NOT NULL,
  delta INTEGER,
  mode TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  target TEXT,
  path TEXT NOT NULL,
  started_ts REAL NOT NULL,
  ended_ts REAL,
  stats_json TEXT
);
"""


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _exec(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    # --- events ---------------------------------------------------------------------

    def add_event(self, event: Event) -> int:
        return self._exec(
            "INSERT INTO events(ts, level, source, type, payload_json, screenshot_path)"
            " VALUES (?,?,?,?,?,?)",
            (
                event.ts,
                str(event.level),
                event.source,
                event.type,
                json.dumps(event.payload, default=str),
                str(event.screenshot_path) if event.screenshot_path else None,
            ),
        )

    # --- party sightings ------------------------------------------------------------

    def record_sighting(
        self, banner_id: str, label: str, slot_index: int, ts: float, merge_gap_s: float
    ) -> int:
        """Extend the open sighting of this banner in this slot, or start a new one."""
        rows = self.query(
            "SELECT id, last_seen FROM party_sightings WHERE banner_id=? AND slot_index=?"
            " ORDER BY last_seen DESC LIMIT 1",
            (banner_id, slot_index),
        )
        if rows and ts - float(rows[0]["last_seen"]) <= merge_gap_s:
            self._exec("UPDATE party_sightings SET last_seen=? WHERE id=?", (ts, rows[0]["id"]))
            return int(rows[0]["id"])
        return self._exec(
            "INSERT INTO party_sightings(banner_id, label, slot_index, first_seen, last_seen)"
            " VALUES (?,?,?,?,?)",
            (banner_id, label, slot_index, ts, ts),
        )

    # --- best guess -----------------------------------------------------------------

    def upsert_fact(
        self,
        normalized_text: str,
        display_text: str,
        answer: str,
        source: str,
        *,
        ts: float | None = None,
        confirmed: bool = False,
        conflict: bool = False,
    ) -> None:
        ts = time.time() if ts is None else ts
        self._exec(
            "INSERT INTO bg_facts(normalized_text, display_text, answer, seen_count,"
            " confirmed_count, conflict_count, last_seen, source) VALUES (?,?,?,1,?,?,?,?)"
            " ON CONFLICT(normalized_text) DO UPDATE SET"
            " seen_count=seen_count+1, confirmed_count=confirmed_count+excluded.confirmed_count,"
            " conflict_count=conflict_count+excluded.conflict_count, last_seen=excluded.last_seen,"
            " answer=CASE WHEN excluded.conflict_count>0 THEN answer ELSE excluded.answer END",
            (normalized_text, display_text, answer, int(confirmed), int(conflict), ts, source),
        )

    def add_bg_round(self, **fields: Any) -> int:
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        return self._exec(f"INSERT INTO bg_rounds({cols}) VALUES ({marks})", tuple(fields.values()))

    def add_arcade_round(self, **fields: Any) -> int:
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        return self._exec(
            f"INSERT INTO arcade_rounds({cols}) VALUES ({marks})", tuple(fields.values())
        )

    def add_ledger(self, ts: float, hud_gems: int, delta: int | None, mode: str) -> int:
        return self._exec(
            "INSERT INTO ledger(ts, hud_gems, delta, mode) VALUES (?,?,?,?)",
            (ts, hud_gems, delta, mode),
        )

    # --- sessions -------------------------------------------------------------------

    def start_session(self, kind: str, target: str | None, path: Path, ts: float) -> int:
        return self._exec(
            "INSERT INTO sessions(kind, target, path, started_ts) VALUES (?,?,?,?)",
            (kind, target, str(path), ts),
        )

    def end_session(self, session_id: int, ts: float, stats: dict[str, Any]) -> None:
        self._exec(
            "UPDATE sessions SET ended_ts=?, stats_json=? WHERE id=?",
            (ts, json.dumps(stats, default=str), session_id),
        )
