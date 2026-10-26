from __future__ import annotations

from pathlib import Path

from jamcrammer.core.config import Level
from jamcrammer.core.events import Event
from jamcrammer.core.store import Store


def test_schema_and_inserts(tmp_path: Path) -> None:
    store = Store(tmp_path / "db" / "t.db")
    try:
        eid = store.add_event(Event(type="x.y", source="t", level=Level.HIGH, payload={"a": 1}))
        assert eid == 1
        sid = store.start_session("learn", "bestguess", tmp_path / "s", 1.0)
        store.end_session(sid, 2.0, {"frames": 3})
        row = store.query("SELECT * FROM sessions WHERE id=?", (sid,))[0]
        assert row["ended_ts"] == 2.0
        assert "frames" in row["stats_json"]
        store.add_ledger(1.0, 1234, None, "hunt")
        store.add_arcade_round(
            ts=1.0, game="double_up", duration_s=18.5, gems_reported=44, mismatches=0
        )
        assert store.query("SELECT COUNT(*) AS n FROM arcade_rounds")[0]["n"] == 1
    finally:
        store.close()


def test_sighting_merge(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    try:
        a = store.record_sighting(
            "haunted_forest", "Haunted Forest Party", 0, 1000.0, merge_gap_s=600
        )
        b = store.record_sighting(
            "haunted_forest", "Haunted Forest Party", 0, 1300.0, merge_gap_s=600
        )
        c = store.record_sighting(
            "haunted_forest", "Haunted Forest Party", 0, 5000.0, merge_gap_s=600
        )
        assert a == b
        assert c != a
        rows = store.query("SELECT first_seen, last_seen FROM party_sightings ORDER BY id")
        assert (rows[0]["first_seen"], rows[0]["last_seen"]) == (1000.0, 1300.0)
    finally:
        store.close()


def test_fact_upsert_counts(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    try:
        store.upsert_fact("cats purr", "Cats purr", "T", "learn-session", ts=1.0, confirmed=True)
        store.upsert_fact("cats purr", "Cats purr", "T", "learned", ts=2.0, confirmed=True)
        store.upsert_fact("cats purr", "Cats purr", "F", "learned", ts=3.0, conflict=True)
        row = store.query("SELECT * FROM bg_facts")[0]
        assert row["seen_count"] == 3
        assert row["confirmed_count"] == 2
        assert row["conflict_count"] == 1
        assert row["answer"] == "T"  # conflicting write does not flip the stored answer
    finally:
        store.close()
