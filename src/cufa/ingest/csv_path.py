"""The fallback path: a CSV exported from a manually created form.

Kept because not every form CU ends up with will have been provisioned by this
tool — someone will make one by hand, and the observations in it are still
observations. It is a fallback, not the primary path, for one reason: a Google
Sheets export writes form timestamps in the *spreadsheet's* locale with no
offset marker. ``2026-09-15 13:05:00`` is a string with no timezone in it, and
reading it as UTC shifts every check-in by hours in a way that looks entirely
plausible.

So ``--sheet-timezone`` is mandatory and has **no default**. Not UTC, not the
machine's local zone. Defaulting it would make the most dangerous case — an
operator who did not think about it — the silent one. The raw string and the
zone that was applied are both stored, so any conversion can be re-derived.
"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from typing import Any

import psycopg

from ..config import Settings, get_settings
from ..errors import CufaError
from ..latency import recompute_for_session
from ..logging_setup import get_logger
from ..sessions import sessions_for_matching
from ..timeutil import parse_local_naive
from .common import (
    IngestResult,
    assign_session,
    compare_passphrase,
    finish_load_run,
    origin_key_for_session,
    resolve_identity,
    source_event_id,
    start_load_run,
    write_checkin,
)

log = get_logger(__name__)

MISSING_TIMEZONE_MESSAGE = (
    "--sheet-timezone is required and has no default.\n"
    "\n"
    "A Google Sheets export writes form timestamps in the spreadsheet's own "
    "locale with no offset marker, so '2026-09-15 13:05:00' could be any zone. "
    "Guessing UTC (or this machine's zone) would shift every check-in by hours "
    "without failing.\n"
    "\n"
    "Pass the spreadsheet's timezone as an IANA name, e.g.:\n"
    "    cufa ingest part-a --csv responses.csv --cohort cu-2026 "
    "--sheet-timezone America/New_York\n"
    "\n"
    "Find it in the sheet under File → Settings → Time zone."
)

# Header aliases, matched case-insensitively after stripping. Anything not
# recognised is preserved into extra_fields rather than dropped.
_EMAIL_HEADERS = ("email address", "email", "respondent email", "username")
_TIMESTAMP_HEADERS = ("timestamp", "submitted at", "submission time", "date")
_PASSPHRASE_HEADERS = (
    "today's passphrase",
    "todays passphrase",
    "passphrase",
    "today’s passphrase",
)


class MissingTimezone(CufaError):
    """``--sheet-timezone`` was not supplied."""

    def __init__(self) -> None:
        super().__init__(MISSING_TIMEZONE_MESSAGE)


def _find(headers: dict[str, str], candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate in headers:
            return headers[candidate]
    for lowered, original in headers.items():
        if any(candidate in lowered for candidate in candidates):
            return original
    return None


def ingest_csv(
    conn: psycopg.Connection,
    path: str | Path,
    cohort_id: str,
    sheet_timezone: str | None,
    settings: Settings | None = None,
) -> IngestResult:
    """Ingest a CSV of responses into ``checkin``.

    ``sheet_timezone`` being None raises rather than defaulting — see the module
    docstring. Every row produces a check-in, including rows with a blank
    answer, an unknown address, or a timestamp in no session's window.
    """
    if not sheet_timezone or not str(sheet_timezone).strip():
        raise MissingTimezone()

    settings = settings or get_settings()
    file_path = Path(path)
    raw_bytes = file_path.read_bytes()
    digest = hashlib.sha256(raw_bytes).hexdigest()

    result = IngestResult()
    sessions = sessions_for_matching(conn, cohort_id)
    load_id = start_load_run(
        conn,
        source="csv",
        origin=str(file_path),
        cohort_id=cohort_id,
        input_sha256=digest,
    )
    result.load_id = load_id
    touched_sessions: set[str] = set()

    try:
        text = raw_bytes.decode("utf-8-sig")
        reader = csv.DictReader(text.splitlines())
        fieldnames = reader.fieldnames or []
        headers = {(name or "").strip().lower(): name for name in fieldnames}

        email_col = _find(headers, _EMAIL_HEADERS)
        timestamp_col = _find(headers, _TIMESTAMP_HEADERS)
        passphrase_col = _find(headers, _PASSPHRASE_HEADERS)

        if not email_col or not timestamp_col:
            raise CufaError(
                f"{file_path} needs an email column and a timestamp column. "
                f"Found: {', '.join(fieldnames) or '(no headers)'}"
            )

        for row in reader:
            result.rows_read += 1
            email = (row.get(email_col) or "").strip()
            raw_timestamp = (row.get(timestamp_col) or "").strip()
            passphrase_raw = (row.get(passphrase_col) or "").strip() if passphrase_col else ""

            if not email or not raw_timestamp:
                result.rows_skipped += 1
                log.warning(
                    "csv row %d skipped: missing email or timestamp", result.rows_read
                )
                continue

            _local, submitted_at_utc = parse_local_naive(raw_timestamp, sheet_timezone)
            assignment = assign_session(sessions, submitted_at_utc)

            session = None
            if assignment.match == "matched":
                session = assignment.candidates[0]
                touched_sessions.add(str(session["session_id"]))
                result_session_id: str | None = assignment.session_id
            else:
                result_session_id = None
                if assignment.match == "none":
                    result.unmatched_sessions += 1
                else:
                    result.ambiguous_sessions += 1
                    titles = ", ".join(
                        f"{s['title']} ({s['session_id']})" for s in assignment.candidates
                    )
                    result.warnings.append(
                        f"{raw_timestamp} ({sheet_timezone}) falls inside more than one "
                        f"session window: {titles}. Written with session_match='ambiguous' "
                        "and no session; fix the overlapping schedules and re-adjudicate."
                    )

            match, distance = compare_passphrase(
                session["passphrase"] if session else None,
                passphrase_raw,
                max_edit_distance=settings.max_edit_distance,
                session_matched=assignment.match == "matched",
            )

            # Unrecognised columns are preserved verbatim. A column someone
            # added to the form is data we were not expecting, not data we get
            # to discard.
            known = {email_col, timestamp_col, passphrase_col}
            extra: dict[str, Any] = {
                key: value
                for key, value in row.items()
                if key not in known and key is not None and value not in (None, "")
            }
            if assignment.match == "ambiguous":
                extra["_ambiguous_session_ids"] = [
                    str(s["session_id"]) for s in assignment.candidates
                ]

            origin_key = origin_key_for_session(conn, result_session_id, file_path.name)

            written = write_checkin(
                conn,
                source_event_id_value=source_event_id(origin_key, email, submitted_at_utc),
                source="csv",
                submitted_email=email,
                submitted_at_utc=submitted_at_utc,
                submitted_at_raw=raw_timestamp,
                source_timezone=sheet_timezone,
                session_id=result_session_id,
                session_match=assignment.match,
                passphrase_raw=passphrase_raw,
                passphrase_match=match,
                edit_distance=distance,
                extra_fields=extra,
                load_id=load_id,
            )
            if written:
                result.rows_written += 1
            else:
                result.rows_skipped += 1

            if resolve_identity(conn, cohort_id, email) is None:
                result.unresolved_identities += 1

        for session_id in touched_sessions:
            recompute_for_session(conn, session_id)
        finish_load_run(conn, load_id, result)

    except Exception as exc:
        finish_load_run(conn, load_id, result, error=str(exc))
        raise

    log.info("csv ingest cohort=%s file=%s tz=%s %s", cohort_id, file_path.name, sheet_timezone, result)
    for warning in result.warnings:
        log.warning("%s", warning)
    return result
