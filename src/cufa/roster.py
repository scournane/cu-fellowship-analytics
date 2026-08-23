"""Bulk CSV loading for the roster and for sessions.

The console is the primary way staff create sessions, but everything the
console does has to be doable from the CLI: it keeps the system scriptable, it
keeps it testable, and it keeps it usable on the day the web app breaks.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg

from .db import execute, fetch_one
from .logging_setup import get_logger, summarize
from .sessions import SessionInput, create_session
from .text import normalize_email

log = get_logger(__name__)


@dataclass(frozen=True)
class LoadSummary:
    read: int
    written: int
    skipped: int

    def __str__(self) -> str:  # pragma: no cover - display only
        return summarize(read=self.read, written=self.written, skipped=self.skipped)


def _headers(row: dict[str, Any]) -> dict[str, str]:
    """Map lowercased/stripped header -> original, so column order and case vary freely."""
    return {(k or "").strip().lower(): k for k in row}


def _pick(row: dict[str, Any], headers: dict[str, str], *names: str) -> str:
    for name in names:
        key = headers.get(name)
        if key is not None and row.get(key) not in (None, ""):
            return str(row[key]).strip()
    return ""


def ensure_cohort(conn: psycopg.Connection, cohort_id: str, label: str | None = None) -> None:
    """Create the cohort if it does not exist, so a load never fails on FK."""
    execute(
        conn,
        """
        insert into cohort (cohort_id, label)
        values (%s, %s)
        on conflict (cohort_id) do nothing
        """,
        (cohort_id, label or cohort_id),
    )


def load_roster(conn: psycopg.Connection, path: str | Path, cohort_id: str) -> LoadSummary:
    """Upsert fellows from a CSV.

    Upsert rather than insert: correcting a roster typo and re-running is the
    normal way a mis-attributed check-in gets fixed, and because identity
    resolves at read time, the correction re-attributes history immediately.
    """
    ensure_cohort(conn, cohort_id)
    read = written = skipped = 0

    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            read += 1
            headers = _headers(row)
            fellow_id = _pick(row, headers, "fellow_id", "id")
            email = normalize_email(_pick(row, headers, "primary_email", "email"))
            name = _pick(row, headers, "full_name", "name")
            status = _pick(row, headers, "status") or "active"

            if not fellow_id or not email:
                skipped += 1
                log.warning("roster row %d skipped: missing fellow_id or email", read)
                continue

            execute(
                conn,
                """
                insert into fellow (fellow_id, cohort_id, full_name, primary_email, status)
                values (%s, %s, %s, %s, %s)
                on conflict (fellow_id) do update
                   set cohort_id = excluded.cohort_id,
                       full_name = excluded.full_name,
                       primary_email = excluded.primary_email,
                       status = excluded.status,
                       updated_at = now()
                """,
                (fellow_id, cohort_id, name or fellow_id, email, status),
            )
            written += 1

    log.info("roster loaded cohort=%s %s", cohort_id, summarize(read=read, written=written, skipped=skipped))
    return LoadSummary(read, written, skipped)


def load_sessions(conn: psycopg.Connection, path: str | Path) -> LoadSummary:
    """Create sessions from a CSV. Existing (cohort, title, time) rows are skipped."""
    read = written = skipped = 0

    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            read += 1
            headers = _headers(row)
            cohort_id = _pick(row, headers, "cohort_id", "cohort")
            title = _pick(row, headers, "title")
            local_raw = _pick(row, headers, "scheduled_at_local", "scheduled_at", "starts_at")
            zone = _pick(row, headers, "timezone", "tz")
            duration = _pick(row, headers, "duration_minutes", "duration")
            grace = _pick(row, headers, "grace_minutes", "grace") or "15"
            passphrase = _pick(row, headers, "passphrase")

            if not (cohort_id and title and local_raw and zone and duration):
                skipped += 1
                log.warning(
                    "session row %d skipped: needs cohort_id, title, "
                    "scheduled_at_local, timezone and duration_minutes",
                    read,
                )
                continue

            ensure_cohort(conn, cohort_id)
            local = _parse_local(local_raw)

            existing = fetch_one(
                conn,
                """
                select session_id from "session"
                 where cohort_id = %s and title = %s and scheduled_at_local = %s
                """,
                (cohort_id, title, local.replace(tzinfo=None)),
            )
            if existing:
                skipped += 1
                continue

            create_session(
                conn,
                SessionInput(
                    cohort_id=cohort_id,
                    title=title,
                    scheduled_at_local=local,
                    timezone=zone,
                    duration_minutes=int(duration),
                    grace_minutes=int(grace),
                    passphrase=passphrase or None,
                ),
            )
            written += 1

    log.info("sessions loaded %s", summarize(read=read, written=written, skipped=skipped))
    return LoadSummary(read, written, skipped)


def _parse_local(value: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"could not parse session time {value!r}; expected '2026-09-15 19:00'")
