"""The parts of ingest that both paths share.

Two rules shape everything here:

* **Never drop a submission** (invariant 1). A wrong passphrase, an unknown
  address, a timestamp in no session's window — all of them produce a row, with
  the reason recorded. The cases worth looking at are exactly the ones a
  "reject bad input" parser would delete.

* **Ingest is idempotent** (invariant 5). Re-running writes zero new rows,
  including across the two paths: a CSV re-import of data already pulled from
  the API must collide, not duplicate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg

from ..db import execute, fetch_all, fetch_one
from ..logging_setup import get_logger, mask_email, summarize
from ..text import normalize_answer, normalize_email, levenshtein, sha256_hex
from ..timeutil import UTC, session_window, to_utc

log = get_logger(__name__)


@dataclass
class IngestResult:
    """Counts for one run. Counts are safe to log at INFO; addresses are not."""

    load_id: str | None = None
    rows_read: int = 0
    rows_written: int = 0
    rows_skipped: int = 0
    unmatched_sessions: int = 0
    ambiguous_sessions: int = 0
    unresolved_identities: int = 0
    warnings: list[str] = field(default_factory=list)

    def __str__(self) -> str:  # pragma: no cover - display only
        return summarize(
            read=self.rows_read,
            written=self.rows_written,
            skipped=self.rows_skipped,
            no_session=self.unmatched_sessions,
            ambiguous=self.ambiguous_sessions,
            unknown_email=self.unresolved_identities,
        )


@dataclass(frozen=True)
class SessionAssignment:
    """Which session a timestamp belongs to, and how confidently."""

    match: str  # 'matched' | 'none' | 'ambiguous'
    session_id: str | None
    candidates: tuple[dict[str, Any], ...] = ()


def source_event_id(origin_key: str, email: str, submitted_at_utc: datetime) -> str:
    """The idempotency key: SHA-256 of (origin, normalized email, UTC second).

    Three deliberate choices:

    * **Not the row number.** Re-exporting a sheet with rows reordered would
      produce entirely new keys and duplicate every row.
    * **Not the Forms ``responseId`` alone.** It exists only on the API path, so
      a CSV re-import of the same responses would not collide with it — and
      cross-path collision is the whole requirement.
    * **Truncated to whole seconds.** The API returns milliseconds and a sheet
      export does not. Hashing sub-second precision would make the same
      submission hash differently depending on which door it came through.
    """
    stamp = to_utc(submitted_at_utc).replace(microsecond=0)
    return sha256_hex(origin_key, normalize_email(email), stamp.isoformat().replace("+00:00", "Z"))


def origin_key_for_session(conn: psycopg.Connection, session_id: str | None, fallback: str) -> str:
    """Prefer the form id, so both ingestion paths agree on the same key.

    A CSV exported from a form we provisioned resolves to that form's id, which
    is what the API path already used. Only a manually created form with no
    ``session_form`` row falls back to the file identifier.
    """
    if session_id:
        row = fetch_one(
            conn, "select form_id from session_form where session_id = %s", (session_id,)
        )
        if row and row["form_id"]:
            return str(row["form_id"])
    return fallback


def assign_session(
    sessions: list[dict[str, Any]], submitted_at_utc: datetime
) -> SessionAssignment:
    """Match a timestamp against session windows.

    ``[scheduled_at_utc - grace, scheduled_at_utc + duration + grace]``,
    inclusive. Zero matches and two matches are both recorded outcomes rather
    than errors — the row is written either way.
    """
    stamp = to_utc(submitted_at_utc)
    hits: list[dict[str, Any]] = []
    for session in sessions:
        start, end = session_window(
            session["scheduled_at_utc"], session["duration_minutes"], session["grace_minutes"]
        )
        if start <= stamp <= end:
            hits.append(session)

    if len(hits) == 1:
        return SessionAssignment("matched", str(hits[0]["session_id"]), tuple(hits))
    if not hits:
        return SessionAssignment("none", None, ())
    return SessionAssignment("ambiguous", None, tuple(hits))


def compare_passphrase(
    expected: str | None,
    submitted: str,
    *,
    max_edit_distance: int,
    session_matched: bool,
) -> tuple[str, int | None]:
    """Tier 1's string comparison, recorded on the observation.

    Returns ``(passphrase_match, edit_distance)``. This is a comparison, not a
    judgment: it says how close the typed answer is to the expected word, and
    says nothing about whether the person attended. That is
    ``attendance_decision``'s job.
    """
    if not session_matched:
        return "no_session", None
    if not (expected or "").strip():
        return "not_set", None

    expected_norm = normalize_answer(expected)
    submitted_norm = normalize_answer(submitted)

    if expected_norm == submitted_norm:
        return "exact", 0

    distance = levenshtein(expected_norm, submitted_norm, max_distance=max_edit_distance)
    if distance <= max_edit_distance:
        # Deliberately generous. The passphrase is heard aloud and typed on a
        # phone; rejecting "justise" for "justice" penalises someone who was in
        # the room and heard it, which is backwards from the intent.
        return "fuzzy", distance
    return "mismatch", None


def resolve_identity(
    conn: psycopg.Connection, cohort_id: str | None, email: str
) -> str | None:
    """Look up a fellow, and queue the address for review when there is no match.

    Never blocks ingest (invariant 6) and never auto-links on a name guess: a
    wrong link is worse than an unmatched address, because an unmatched address
    appears in the review queue and a wrong one appears nowhere.
    """
    normalized = normalize_email(email)
    if not normalized:
        return None

    row = fetch_one(
        conn,
        """
        select fellow_id from fellow
         where lower(primary_email) = %s
           and (%s::text is null or cohort_id = %s::text)
         limit 1
        """,
        (normalized, cohort_id, cohort_id),
    )
    if row:
        return row["fellow_id"]

    if cohort_id:
        execute(
            conn,
            """
            insert into identity_unresolved (cohort_id, email)
            values (%s, %s)
            on conflict (cohort_id, email) do update
               set last_seen_at = now(),
                   occurrence_count = identity_unresolved.occurrence_count + 1,
                   resolved_at = null
            """,
            (cohort_id, normalized),
        )
        log.debug("unresolved identity cohort=%s email=%s", cohort_id, normalized)
    return None


def start_load_run(
    conn: psycopg.Connection,
    *,
    source: str,
    origin: str,
    cohort_id: str | None = None,
    input_sha256: str | None = None,
) -> str:
    row = fetch_one(
        conn,
        """
        insert into load_run (source, origin, cohort_id, input_sha256)
        values (%s, %s, %s, %s)
        returning load_id
        """,
        (source, origin, cohort_id, input_sha256),
    )
    assert row is not None
    return str(row["load_id"])


def finish_load_run(
    conn: psycopg.Connection, load_id: str, result: IngestResult, *, error: str | None = None
) -> None:
    execute(
        conn,
        """
        update load_run
           set finished_at = now(),
               rows_read = %s, rows_written = %s, rows_skipped = %s,
               status = %s, error = %s
         where load_id = %s
        """,
        (
            result.rows_read,
            result.rows_written,
            result.rows_skipped,
            "failed" if error else "succeeded",
            error,
            load_id,
        ),
    )


def write_checkin(
    conn: psycopg.Connection,
    *,
    source_event_id_value: str,
    source: str,
    submitted_email: str,
    submitted_at_utc: datetime,
    submitted_at_raw: str,
    source_timezone: str | None,
    session_id: str | None,
    session_match: str,
    passphrase_raw: str,
    passphrase_match: str,
    edit_distance: int | None,
    extra_fields: dict[str, Any],
    load_id: str | None,
) -> str | None:
    """Insert one observation. Returns the id, or None when it already existed.

    ``on conflict do nothing`` against the UNIQUE on ``source_event_id`` is what
    makes a second run write zero rows — the check is in the database, not in a
    "have I seen this?" set that only holds for one process.
    """
    row = fetch_one(
        conn,
        """
        insert into checkin (
            source_event_id, source, submitted_email, submitted_at_utc,
            submitted_at_raw, source_timezone, session_id, session_match,
            passphrase_raw, passphrase_match, edit_distance, extra_fields, load_id
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
        on conflict (source_event_id) do nothing
        returning checkin_id
        """,
        (
            source_event_id_value,
            source,
            normalize_email(submitted_email),
            to_utc(submitted_at_utc),
            submitted_at_raw,
            source_timezone,
            session_id,
            session_match,
            passphrase_raw,
            passphrase_match,
            edit_distance,
            json.dumps(extra_fields or {}),
            load_id,
        ),
    )
    return str(row["checkin_id"]) if row else None


def cohort_for_session(conn: psycopg.Connection, session_id: str) -> str | None:
    row = fetch_one(conn, 'select cohort_id from "session" where session_id = %s', (session_id,))
    return row["cohort_id"] if row else None
