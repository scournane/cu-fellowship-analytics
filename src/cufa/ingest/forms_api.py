"""The primary ingestion path: ``forms.responses.list``.

Trap 3: ``Form.setDestination()`` exists in Apps Script and has no REST
equivalent, so an API-provisioned form has no linked spreadsheet and there is no
CSV to export. Reading responses directly is not a workaround — it is strictly
better. The API returns ``respondentEmail`` and RFC3339 UTC timestamps, which
removes the Sheets timezone trap completely: a linked sheet writes form
timestamps in the spreadsheet's locale with no offset marker, and reading those
as UTC shifts every check-in by hours while looking entirely correct.

Polling is incremental via ``filter=timestamp > <watermark>``. The watermark
advances **only after a complete successful pass** over every page, so a failure
halfway through re-reads rather than skipping — and re-reading is free, because
``source_event_id`` makes the writes idempotent.

Part A is the exit ticket, so a response is several answers. They are stored
**raw**: every value the API returned, keyed by ``questionId``, with the title
the question had at the time — on the immutable check-in row, never joined and
never resolved here. ``part_a_form_question`` turns ids into question keys at
read time (``v_checkin_answer``). So a form with no recorded map is a warning,
not a refusal: the answers are kept exactly as they arrived and resolve the
moment the map is re-recorded. Refusing would lose nothing today and cost the
attendance record for a whole session — the wrong way round for invariant 1.

Nothing here reads what an answer *says*. Attendance is decided later from the
verified address and the submit time; answers are counted and shown, never
graded, and no model sees them.
"""

from __future__ import annotations

import random
import time
from typing import Any

import psycopg

from ..config import Settings
from ..db import execute, fetch_all, fetch_one
from ..google.base import FormResponse, FormsClient, GoogleApiError, ResponsePage
from ..latency import recompute_for_session
from ..logging_setup import get_logger
from ..sessions import sessions_for_matching
from ..timeutil import iso_utc, parse_rfc3339
from .common import (
    IngestResult,
    assign_session,
    finish_load_run,
    resolve_identity,
    source_event_id,
    start_load_run,
    write_checkin,
)

log = get_logger(__name__)

_MAX_RATE_LIMIT_RETRIES = 4


def raw_answers(response: FormResponse, page: ResponsePage) -> dict[str, dict[str, Any]]:
    """``{questionId: {"values": [...], "title": ...}}`` for one response.

    Every value kept, in order, unjoined: a checkbox answer ticked three times
    is three values, and "Budgets, taxes" stays distinguishable from "Budgets"
    plus "taxes". The title is what the question was called when this page was
    read — a snapshot that travels with the answer even if the map is missing.
    """
    titles = page.titles_by_id or {}
    return {
        question_id: {"values": list(values), "title": titles.get(question_id)}
        for question_id, values in response.values_by_id().items()
    }


def list_with_backoff(
    client: FormsClient, form_id: str, *, response_filter: str | None, page_token: str | None
) -> Any:
    """One page, retrying 429s with exponential backoff.

    Shared with the Part B pull rather than reimplemented there: the retry
    policy is a property of the API, not of which form is being read.
    """
    for attempt in range(_MAX_RATE_LIMIT_RETRIES):
        try:
            return client.list_responses(
                form_id, response_filter=response_filter, page_token=page_token
            )
        except GoogleApiError as exc:
            if exc.status != 429 or attempt == _MAX_RATE_LIMIT_RETRIES - 1:
                raise
            delay = min(2**attempt, 16) * (0.5 + random.random() / 2)
            log.warning("rate limited by Forms API; retrying in %.1fs", delay)
            time.sleep(delay)
    raise GoogleApiError("exhausted retries listing responses", status=429)


def pull_session(
    conn: psycopg.Connection,
    client: FormsClient,
    session_id: str,
    settings: Settings | None = None,
) -> IngestResult:
    """Pull every new response for one provisioned session.

    ``settings`` is accepted for signature compatibility and not read.
    """
    result = IngestResult()

    session_row = fetch_one(
        conn,
        """
        select s.session_id, s.cohort_id, s.title, s.scheduled_at_utc,
               s.duration_minutes, s.grace_minutes,
               sf.form_id, sf.response_watermark, sf.publish_verified_at,
               sf.question_set_id
          from "session" s
          join session_form sf
            on sf.session_id = s.session_id and sf.part = 'a'
         where s.session_id = %s
        """,
        (session_id,),
    )
    if session_row is None:
        raise LookupError(
            f"Session {session_id} has no provisioned form. Run "
            f"`cufa provision --session {session_id}` first."
        )
    if session_row["publish_verified_at"] is None:
        result.warnings.append(
            f"session {session_id}: form {session_row['form_id']} has never been "
            "verified as published, so it may be accepting nothing (trap 1)"
        )

    form_id = session_row["form_id"]
    watermark = session_row["response_watermark"]
    cohort_id = session_row["cohort_id"]
    sessions = sessions_for_matching(conn, cohort_id)

    # The map is consulted only to warn. Answers are stored raw either way.
    mapped = {
        row["question_id"]
        for row in fetch_all(
            conn, "select question_id from part_a_form_question where form_id = %s", (form_id,)
        )
    }
    if not mapped and session_row["question_set_id"] is None:
        # A passphrase-era form: provisioned before question sets existed, so
        # there is no set to map it against. Its answers keep their titles.
        result.warnings.append(
            f"session {session_id}: form {form_id} predates exit-ticket questions, so "
            "its answers are stored by question id with the title each question had."
        )
    elif not mapped:
        result.warnings.append(
            f"session {session_id}: form {form_id} has no recorded question map, so "
            "its answers are stored by question id and cannot be shown against their "
            "questions yet. Nothing is lost: provision this session's Part A again to "
            "re-record the map, and every stored answer resolves."
        )

    load_id = start_load_run(
        conn, source="forms_api", origin=form_id, cohort_id=cohort_id
    )
    result.load_id = load_id

    try:
        newest_seen = watermark
        page_token: str | None = None
        response_filter = f"timestamp > {watermark}" if watermark else None

        while True:
            page = list_with_backoff(
                client, form_id, response_filter=response_filter, page_token=page_token
            )

            for response in page.responses:
                result.rows_read += 1
                submitted_at = parse_rfc3339(response.submitted_at)
                iso = iso_utc(submitted_at)
                if newest_seen is None or iso > newest_seen:
                    newest_seen = iso

                assignment = assign_session(sessions, submitted_at)

                # The form itself implies a session. When the timestamp says
                # otherwise, that is a configuration error worth flagging — not a
                # reason to move the response to a different session. Both facts
                # are recorded: the form's session wins, the discrepancy is warned.
                implied_session_id = str(session_row["session_id"])
                if assignment.match == "matched" and assignment.session_id != implied_session_id:
                    result.warn(
                        f"config error: responses on form {form_id} (session "
                        f"{implied_session_id}) carry timestamps inside session "
                        f"{assignment.session_id}'s window — check both sessions' "
                        "scheduled_at, duration_minutes and grace_minutes"
                    )
                elif assignment.match == "none":
                    # Expected, not an error: a fellow who opens the exit ticket
                    # the next morning is exactly the case the window exists to
                    # tell apart. The row is kept with the form's session and
                    # attendance decides it (outside_session_window). Only when
                    # *every* response is outside is the schedule worth a look.
                    result.warn(
                        f"note: some responses on form {form_id} arrived outside "
                        f"session {implied_session_id}'s window; they are kept and "
                        "will be decided as outside the window (review them under "
                        "“Outside the window”). If all of them are, check the "
                        "session's scheduled time, duration and grace",
                        detail=f"(first seen at {iso})",
                    )
                elif assignment.match == "ambiguous":
                    titles = ", ".join(s["title"] for s in assignment.candidates)
                    result.warn(
                        f"overlapping session windows ({titles}); the form's own "
                        "session is used, but the schedules should be fixed"
                    )

                effective_session_id = implied_session_id
                effective_match = "matched"

                answers = raw_answers(response, page)
                unmapped = sorted(set(answers) - mapped) if mapped else []
                if unmapped:
                    # A question somebody added in the Forms UI after
                    # provisioning. Kept, and said out loud.
                    result.warn(
                        f"form {form_id} has answers to question(s) not in its map "
                        f"({', '.join(unmapped)}); they are stored and shown by title"
                    )

                # Only provenance goes here now. The answers themselves have a
                # column of their own, keyed by question id.
                extra: dict[str, Any] = {}
                if response.response_id:
                    extra["_response_id"] = response.response_id
                if assignment.match != "matched" or assignment.session_id != implied_session_id:
                    extra["_timestamp_session_match"] = assignment.match
                    extra["_timestamp_session_id"] = assignment.session_id

                written = write_checkin(
                    conn,
                    source_event_id_value=source_event_id(
                        form_id, response.respondent_email, submitted_at
                    ),
                    source="forms_api",
                    submitted_email=response.respondent_email,
                    submitted_at_utc=submitted_at,
                    submitted_at_raw=response.submitted_at,
                    source_timezone=None,  # already UTC; nothing was converted
                    session_id=effective_session_id,
                    session_match=effective_match,
                    extra_fields=extra,
                    load_id=load_id,
                    form_id=form_id,
                    answers=answers,
                )
                if written:
                    result.rows_written += 1
                else:
                    result.rows_skipped += 1

                if resolve_identity(conn, cohort_id, response.respondent_email) is None:
                    result.unresolved_identities += 1

            page_token = page.next_page_token
            if not page_token:
                break

        # Only now — every page consumed without raising — is it safe to move on.
        execute(
            conn,
            """
            update session_form
               set response_watermark = coalesce(%s, response_watermark),
                   last_polled_at = now(),
                   last_successful_poll_at = now()
             where session_id = %s and part = 'a'
            """,
            (newest_seen, session_id),
        )
        recompute_for_session(conn, session_id)
        result.finalize_warnings()
        finish_load_run(conn, load_id, result)

    except Exception as exc:
        # The watermark is deliberately untouched: a partial pull re-reads next
        # time, and the idempotency key makes re-reading harmless.
        execute(
            conn,
            "update session_form set last_polled_at = now() "
            "where session_id = %s and part = 'a'",
            (session_id,),
        )
        finish_load_run(conn, load_id, result, error=str(exc))
        raise

    log.info("pull session=%s form=%s %s", session_id, form_id, result)
    return result


def pull_cohort(
    conn: psycopg.Connection,
    client: FormsClient,
    cohort_id: str,
    settings: Settings | None = None,
) -> IngestResult:
    """Pull every provisioned session in a cohort, incrementally."""
    combined = IngestResult()
    rows = fetch_all(
        conn,
        """
        select s.session_id
          from "session" s
          join session_form sf
            on sf.session_id = s.session_id and sf.part = 'a'
         where s.cohort_id = %s
         order by s.scheduled_at_utc
        """,
        (cohort_id,),
    )
    for row in rows:
        session_id = str(row["session_id"])
        try:
            result = pull_session(conn, client, session_id, settings)
        except Exception as exc:  # noqa: BLE001 - see below
            # One session's form being unreadable must not cost the other nine
            # their responses. The failure is carried out as a warning rather
            # than raised: ingest is idempotent, so the session that failed is
            # re-read on the next run, and the watermark it did not advance
            # makes that automatic.
            log.warning("pull failed session=%s: %s", session_id, type(exc).__name__)
            combined.sessions_failed += 1
            combined.warnings.append(f"session {session_id} could not be pulled: {exc}")
            continue
        combined.rows_read += result.rows_read
        combined.rows_written += result.rows_written
        combined.rows_skipped += result.rows_skipped
        combined.unresolved_identities += result.unresolved_identities
        combined.warnings.extend(result.warnings)
    log.info("pull cohort=%s sessions=%d %s", cohort_id, len(rows), combined)
    return combined
