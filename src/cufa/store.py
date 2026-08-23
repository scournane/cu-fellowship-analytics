"""SQLite warehouse.

SQLite because it is a single file with no server, it ships with Python, and
the CU team can open it with any free tool after the contract ends. Ingestion
is idempotent: re-running last week's export does not double anyone's score.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

from .model import Event, Fellow, Identity, Unresolved

SCHEMA = """
CREATE TABLE IF NOT EXISTS fellows (
    fellow_id    TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    cohort       TEXT NOT NULL,
    joined_on    TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS identities (
    source         TEXT NOT NULL,
    source_user_id TEXT NOT NULL,
    fellow_id      TEXT NOT NULL REFERENCES fellows(fellow_id),
    PRIMARY KEY (source, source_user_id)
);
CREATE TABLE IF NOT EXISTS events (
    event_id    TEXT PRIMARY KEY,
    fellow_id   TEXT NOT NULL REFERENCES fellows(fellow_id),
    source      TEXT NOT NULL,
    kind        TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    week        TEXT NOT NULL,
    value       REAL NOT NULL DEFAULT 1.0,
    meta        TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_fellow_week ON events(fellow_id, week);
CREATE INDEX IF NOT EXISTS idx_events_week ON events(week);

-- Events that matched no Fellow. Kept, never dropped: a rising count here is
-- the earliest warning that the identity map has drifted.
CREATE TABLE IF NOT EXISTS unresolved (
    source         TEXT NOT NULL,
    source_user_id TEXT NOT NULL,
    kind           TEXT NOT NULL,
    occurred_at    TEXT NOT NULL,
    reason         TEXT NOT NULL,
    PRIMARY KEY (source, source_user_id, kind, occurred_at)
);

-- Append-only record of every ingest, so any number in a report can be
-- traced back to the file it came from.
CREATE TABLE IF NOT EXISTS ingest_log (
    run_at     TEXT NOT NULL,
    source     TEXT NOT NULL,
    artifact   TEXT NOT NULL,
    accepted   INTEGER NOT NULL,
    unresolved INTEGER NOT NULL
);
"""


class Store:
    def __init__(self, path: Path | str = "out/cif.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # -- writes -------------------------------------------------------------

    def upsert_fellows(self, fellows: list[Fellow]) -> int:
        self.db.executemany(
            "INSERT INTO fellows VALUES (?,?,?,?,?) "
            "ON CONFLICT(fellow_id) DO UPDATE SET "
            "display_name=excluded.display_name, cohort=excluded.cohort, "
            "joined_on=excluded.joined_on, status=excluded.status",
            [(f.fellow_id, f.display_name, f.cohort, f.joined_on.isoformat(), f.status)
             for f in fellows],
        )
        self.db.commit()
        return len(fellows)

    def upsert_identities(self, ids: list[Identity]) -> int:
        self.db.executemany(
            "INSERT INTO identities VALUES (?,?,?) "
            "ON CONFLICT(source, source_user_id) DO UPDATE SET "
            "fellow_id=excluded.fellow_id",
            [(i.source, i.source_user_id, i.fellow_id) for i in ids],
        )
        self.db.commit()
        return len(ids)

    def add_events(self, events: list[Event]) -> int:
        # INSERT OR IGNORE + deterministic event_id == idempotent ingest.
        cur = self.db.executemany(
            "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?)",
            [(e.event_id, e.fellow_id, e.source, e.kind,
              e.occurred_at.isoformat(), e.week, e.value,
              json.dumps(e.meta, sort_keys=True, default=str)) for e in events],
        )
        self.db.commit()
        return cur.rowcount

    def add_unresolved(self, rows: list[Unresolved]) -> int:
        self.db.executemany(
            "INSERT OR IGNORE INTO unresolved VALUES (?,?,?,?,?)",
            [(u.source, u.source_user_id, u.kind,
              u.occurred_at.isoformat(), u.reason) for u in rows],
        )
        self.db.commit()
        return len(rows)

    def log_ingest(self, source: str, artifact: str, accepted: int, unresolved: int) -> None:
        self.db.execute(
            "INSERT INTO ingest_log VALUES (?,?,?,?,?)",
            (dt.datetime.now(dt.timezone.utc).isoformat(), source, artifact,
             accepted, unresolved),
        )
        self.db.commit()

    # -- reads --------------------------------------------------------------

    def identity_map(self) -> dict[tuple[str, str], str]:
        return {(r["source"], r["source_user_id"]): r["fellow_id"]
                for r in self.db.execute("SELECT * FROM identities")}

    def fellows(self, cohort: str | None = None) -> list[Fellow]:
        sql = "SELECT * FROM fellows"
        args: tuple = ()
        if cohort:
            sql += " WHERE cohort = ?"
            args = (cohort,)
        return [
            Fellow(r["fellow_id"], r["display_name"], r["cohort"],
                   dt.date.fromisoformat(r["joined_on"]), r["status"])
            for r in self.db.execute(sql + " ORDER BY display_name", args)
        ]

    def weekly_counts(self, cohort: str | None = None) -> dict[tuple[str, str], dict[str, float]]:
        """(fellow_id, week) -> {kind: summed value}."""
        sql = ("SELECT e.fellow_id, e.week, e.kind, SUM(e.value) AS total "
               "FROM events e JOIN fellows f USING (fellow_id) ")
        args: tuple = ()
        if cohort:
            sql += "WHERE f.cohort = ? "
            args = (cohort,)
        sql += "GROUP BY e.fellow_id, e.week, e.kind"
        out: dict[tuple[str, str], dict[str, float]] = {}
        for r in self.db.execute(sql, args):
            out.setdefault((r["fellow_id"], r["week"]), {})[r["kind"]] = r["total"]
        return out

    def weeks(self, cohort: str | None = None) -> list[str]:
        sql = ("SELECT DISTINCT e.week FROM events e JOIN fellows f USING (fellow_id)")
        args: tuple = ()
        if cohort:
            sql += " WHERE f.cohort = ?"
            args = (cohort,)
        return sorted(r["week"] for r in self.db.execute(sql + " ORDER BY e.week", args))

    def unresolved_summary(self) -> list[sqlite3.Row]:
        return list(self.db.execute(
            "SELECT source, source_user_id, COUNT(*) AS n FROM unresolved "
            "GROUP BY source, source_user_id ORDER BY n DESC"))

    def last_ingests(self, limit: int = 10) -> list[sqlite3.Row]:
        return list(self.db.execute(
            "SELECT * FROM ingest_log ORDER BY run_at DESC LIMIT ?", (limit,)))
