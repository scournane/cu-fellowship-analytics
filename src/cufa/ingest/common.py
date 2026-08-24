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

from ..db import execute, fetch_one
from ..logging_setup import get_logger, summarize
from ..text import normalize_answer, normalize_email, levenshtein, sha256_hex
from ..timeutil import session_window, to_utc

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
    #: Sessions a cohort pull could not read at all. Distinct from a warning:
    #: an overlapping window is advisory and the run still collected everything,
    #: whereas a session that failed has responses sitting uncollected. The CLI
    #: exits non-zero on this and not on warnings, so a scheduled pull that
    #: half-worked is visible to whatever is running it.
    sessions_failed: int = 0
    warnings: list[str] = field(default_factory=list)
    _warning_counts: dict[str, int] = field(default_factory=dict, repr=False)
    #: Where each counted warning landed in ``warnings``. Positional
    #: correspondence between the two cannot be assumed — see finalize_warnings.
    _warning_at: dict[str, int] = field(default_factory=dict, repr=False)
    _finalized: bool = field(default=False, repr=False)

    def warn(self, message: str, *, detail: str = "") -> None:
        """Record a warning once per distinct cause, counting repeats.

        One overlapping pair of session windows produces a warning for every
        response in the overlap. Printing thirty copies of the same sentence
        buries the one fact an operator needs — that two sessions overlap — so
        repeats are counted and reported as a multiplier instead.
        """
        seen = self._warning_counts.get(message, 0)
        self._warning_counts[message] = seen + 1
        if seen == 0:
            self._warning_at[message] = len(self.warnings)
            self.warnings.append(f"{message}{(' ' + detail) if detail else ''}")

    def finalize_warnings(self) -> None:
        """Append the repeat count to any warning that fired more than once.

        Rewrites entries **in place, by recorded index**. The obvious
        implementation — zip ``_warning_counts`` against ``warnings`` and rebuild
        the list — assumes the two correspond positionally, and they do not:
        ``warnings`` is a plain list that callers also append to directly. The
        trap-1 "this form may be accepting nothing" warning is one of those, and
        it is added *before* any counted warning.

        That assumption corrupted output three different ways: a directly
        appended warning was dropped when the zip truncated; a repeat count was
        printed against the wrong warning's text; and a cohort roll-up — which
        extends ``warnings`` and never calls ``warn`` — lost every warning it
        had. Each of those quietly removes the one message explaining why no
        responses are arriving.

        Idempotent: calling it twice does not double the suffix.
        """
        if self._finalized:
            return
        self._finalized = True
        for message, index in self._warning_at.items():
            count = self._warning_counts.get(message, 0)
            if count > 1 and 0 <= index < len(self.warnings):
                self.warnings[index] += f" [{count}× in this run]"

    def __str__(self) -> str:  # pragma: no cover - display only
        counts = dict(
            read=self.rows_read,
            written=self.rows_written,
            skipped=self.rows_skipped,
            no_session=self.unmatched_sessions,
            ambiguous=self.ambiguous_sessions,
            unknown_email=self.unresolved_identities,
        )
        if self.sessions_failed:
            counts["sessions_failed"] = self.sessions_failed
        return summarize(**counts)


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


def origin_key_for_session(
    conn: psycopg.Connection, session_id: str | None, cohort_id: str, part: str = "a"
) -> str:
    """The first component of the idempotency key.

    Prefer the **form id**: a CSV exported from a form this system provisioned
    then hashes to the same key the API path already used, so re-importing
    already-ingested responses collides instead of duplicating.

    When there is no provisioned form — a manually created form, the case the
    CSV path exists for — fall back to the **cohort**, deliberately not the file
    name. The spec's shorthand for this component is "form_id_or_file", but
    keying on the file name makes ``responses.csv`` and ``responses (1).csv``
    two different sources for the same submissions, and downloading an export
    twice is the single most likely way a duplicate import actually happens.

    The trade this makes: two distinct manual forms in one cohort receiving a
    submission from the same address in the same *second* would collide and one
    would be dropped. That requires one person submitting two different forms
    within one second, which does not happen — whereas a renamed re-export
    happens routinely. The file name and its SHA-256 are still recorded on
    ``load_run``, so provenance is not lost either way.
    """
    if session_id:
        row = fetch_one(
            conn,
            "select form_id from session_form where session_id = %s and part = %s",
            (session_id, part),
        )
        if row and row["form_id"]:
            return str(row["form_id"])
    return f"cohort:{cohort_id}"


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


# ---------------------------------------------------------------------------
# Part B
# ---------------------------------------------------------------------------

#: The scale the confidence question actually offers. Values outside it are not
#: clamped — see ``parse_confidence``.
CONFIDENCE_LOW = 1
CONFIDENCE_HIGH = 7


def parse_confidence(value: str | None) -> tuple[int | None, str | None]:
    """Parse the 1-7 confidence answer. Returns ``(value, rejected_raw)``.

    Three outcomes, and the middle one is the interesting one:

    * a whole number inside 1..7 -> that number, nothing rejected
    * anything else that was actually typed ("0", "8", "four") -> ``(None, raw)``
    * blank -> ``(None, None)``, because nothing was answered and nothing was
      rejected

    **Never clamped.** An 8 becoming a 7 is a silent lie about what someone
    selected, and it produces a plausible number from a broken form — which is
    the failure mode with no symptom. NULL plus the raw string in
    ``extra_fields`` keeps the observation and forces the question to be asked.
    """
    text = (value or "").strip()
    if not text:
        return None, None
    try:
        number = int(text)
    except ValueError:
        return None, text
    if CONFIDENCE_LOW <= number <= CONFIDENCE_HIGH:
        return number, None
    return None, text


def write_checkin_b(
    conn: psycopg.Connection,
    *,
    source_event_id_value: str,
    source: str,
    submitted_email: str,
    submitted_at_utc: datetime,
    session_id: str | None,
    session_match: str,
    confidence_raw: int | None,
    takeaway_text: str | None,
    rotating_kind: str | None,
    rotating_text: str | None,
    shoutout_text: str | None,
    extra_fields: dict[str, Any],
    load_id: str | None,
) -> str | None:
    """Insert one Part B observation. Returns the id, or None if it existed.

    Same ``on conflict do nothing`` shape as ``write_checkin``, and for the same
    reason: the uniqueness check lives in the database, not in a set that only
    holds for one process.

    Note the absence of a help-request parameter. The checkbox is not a column
    here — it is routed to its own table by the caller, so that no SELECT * over
    this table, and no export derived from one, can carry it.
    """
    row = fetch_one(
        conn,
        """
        insert into checkin_b (
            source_event_id, source, submitted_email, submitted_at_utc,
            session_id, session_match, confidence_raw, takeaway_text,
            rotating_kind, rotating_text, shoutout_text, extra_fields, load_id
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
        on conflict (source_event_id) do nothing
        returning checkin_b_id
        """,
        (
            source_event_id_value,
            source,
            normalize_email(submitted_email),
            to_utc(submitted_at_utc),
            session_id,
            session_match,
            confidence_raw,
            takeaway_text,
            rotating_kind,
            rotating_text,
            shoutout_text,
            json.dumps(extra_fields or {}),
            load_id,
        ),
    )
    return str(row["checkin_b_id"]) if row else None


def cohort_for_session(conn: psycopg.Connection, session_id: str) -> str | None:
    row = fetch_one(conn, 'select cohort_id from "session" where session_id = %s', (session_id,))
    return row["cohort_id"] if row else None
