"""Pulling Part B responses — the end-of-session check-in.

Mechanically identical to Part A: incremental ``forms.responses.list`` with
``filter=timestamp > <watermark>``, full pagination, the watermark advanced only
after a complete successful pass, and the same ``source_event_id`` hash so a
cross-path re-import collides instead of duplicating.

Three things are different, and all three are the point of Part B:

* **Every answer is resolved through ``form_question_map``.** The API keys
  answers by ``questionId``; nothing here matches on a title. A form whose map
  is missing or incomplete is REFUSED — the run stops with an explanation rather
  than filing a confidence score as a takeaway.
* **The help checkbox never reaches ``checkin_b``.** It is routed to its own
  table and emailed to the configured recipient the moment it lands, in the same
  pass, not on a later batch.
* **Confidence is validated, never clamped.** An out-of-range value lands as
  NULL with the raw string kept in ``extra_fields``, and warns.

Part A and Part B are independent observations. A fellow may submit one and not
the other; both are valid data, not errors. Nothing in this module reads
``checkin`` and nothing writes to it.
"""

from __future__ import annotations

from typing import Any

import psycopg

from ..config import Settings, get_settings
from ..db import execute, fetch_all, fetch_one
from ..form_content_b import (
    HELP_OPTION,
    SLOT_CONFIDENCE,
    SLOT_HELP,
    SLOT_ROTATING,
    SLOT_SHOUTOUT,
    SLOT_TAKEAWAY,
    expected_slots,
)
from ..google.base import FormsClient
from ..help_requests import record_and_route
from ..help_routing import HelpRouting, Notifier, get_help_routing
from ..latency import recompute_for_session
from ..logging_setup import get_logger
from ..question_map import require_map, resolve_answers, rotating_kind_for
from ..sessions import sessions_for_matching
from ..shoutouts import record_shoutouts, roster_index
from ..timeutil import iso_utc, parse_rfc3339
from .common import (
    IngestResult,
    assign_session,
    finish_load_run,
    parse_confidence,
    resolve_identity,
    source_event_id,
    start_load_run,
    write_checkin_b,
)
from .forms_api import list_with_backoff

log = get_logger(__name__)


def _ticked(value: str | None) -> bool:
    """Whether the single-option help checkbox was ticked.

    A Forms checkbox answers as the option's own text when ticked and is absent
    from the payload when it is not, so presence of any non-empty value is the
    signal. The option text is compared case-insensitively as a second check,
    but a value that does not match is still treated as ticked: a fellow whose
    answer arrived in an unexpected shape asked for something, and the failure
    mode of guessing wrong here is not answering someone who raised their hand.
    """
    text = (value or "").strip()
    if not text:
        return False
    if text.casefold() == HELP_OPTION.casefold():
        return True
    log.warning(
        "help checkbox answered with an unexpected value shape; treating it as "
        "ticked rather than risk ignoring a request"
    )
    return True


def pull_session_b(
    conn: psycopg.Connection,
    client: FormsClient,
    session_id: str,
    settings: Settings | None = None,
    *,
    routing: HelpRouting | None = None,
    notifier: Notifier | None = None,
) -> IngestResult:
    """Pull every new Part B response for one provisioned session."""
    settings = settings or get_settings()
    routing = routing if routing is not None else get_help_routing()
    result = IngestResult()

    session_row = fetch_one(
        conn,
        """
        select s.session_id, s.cohort_id, s.title, s.scheduled_at_utc,
               s.duration_minutes, s.grace_minutes, s.week_index,
               sf.form_id, sf.response_watermark, sf.publish_verified_at
          from "session" s
          join session_form sf
            on sf.session_id = s.session_id and sf.part = 'b'
         where s.session_id = %s
        """,
        (session_id,),
    )
    if session_row is None:
        raise LookupError(
            f"Session {session_id} has no provisioned Part B form. Run "
            f"`cufa provision --session {session_id} --part b` first."
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

    # Refuse before a single row is written. An incomplete map is not a
    # degraded run to be partially completed — it means the answers cannot be
    # attributed at all, and half a table of mis-keyed values is worse than none.
    mapping = require_map(conn, form_id, expected_slots(include_help=routing.has_recipient))
    kind = rotating_kind_for(mapping)
    names = roster_index(conn, cohort_id)

    load_id = start_load_run(conn, source="forms_api", origin=form_id, cohort_id=cohort_id)
    result.load_id = load_id

    try:
        newest_seen = watermark
        page_token: str | None = None
        response_filter = f"timestamp > {watermark}" if watermark else None
        implied_session_id = str(session_row["session_id"])

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

                # The form itself implies a session. A timestamp that says
                # otherwise is a configuration error worth flagging, not a reason
                # to move the response — Part B goes out at the END of a lesson,
                # so a submission a few minutes past the window is expected and
                # the form's own session is the right answer.
                if assignment.match == "matched" and assignment.session_id != implied_session_id:
                    result.warn(
                        f"config error: responses on Part B form {form_id} (session "
                        f"{implied_session_id}) carry timestamps inside session "
                        f"{assignment.session_id}'s window — check both sessions' "
                        "scheduled_at, duration_minutes and grace_minutes"
                    )
                elif assignment.match == "none":
                    result.warn(
                        f"responses on Part B form {form_id} land outside session "
                        f"{implied_session_id}'s window. That is common for an "
                        "end-of-session form — widen grace_minutes if it is "
                        "consistent",
                        detail=f"(first seen at {iso})",
                    )
                elif assignment.match == "ambiguous":
                    titles = ", ".join(s["title"] for s in assignment.candidates)
                    result.warn(
                        f"overlapping session windows ({titles}); the form's own "
                        "session is used, but the schedules should be fixed"
                    )

                slots, extras = resolve_answers(mapping, response.answers_by_id)

                confidence, rejected = parse_confidence(slots.get(SLOT_CONFIDENCE))
                extra: dict[str, Any] = dict(extras)
                if rejected is not None:
                    # Recorded, not corrected. Somebody has to see that the form
                    # produced a value the scale cannot express.
                    extra["_confidence_rejected_raw"] = rejected
                    result.warn(
                        f"confidence answers outside 1-7 arrived on form {form_id} "
                        "and were stored as NULL with the raw value kept in "
                        "extra_fields — they are NOT clamped. Check the form's "
                        "scale question has not been edited"
                    )

                if response.response_id:
                    extra["_response_id"] = response.response_id
                if assignment.match != "matched" or assignment.session_id != implied_session_id:
                    extra["_timestamp_session_match"] = assignment.match
                    extra["_timestamp_session_id"] = assignment.session_id

                event_id = source_event_id(form_id, response.respondent_email, submitted_at)

                # Verbatim, whitespace included. Whitespace is what tells the
                # difference between "answered with a space" and "did not answer",
                # and both are real.
                checkin_b_id = write_checkin_b(
                    conn,
                    source_event_id_value=event_id,
                    source="forms_api",
                    submitted_email=response.respondent_email,
                    submitted_at_utc=submitted_at,
                    session_id=implied_session_id,
                    session_match="matched",
                    confidence_raw=confidence,
                    takeaway_text=slots.get(SLOT_TAKEAWAY),
                    rotating_kind=kind,
                    rotating_text=slots.get(SLOT_ROTATING),
                    shoutout_text=slots.get(SLOT_SHOUTOUT),
                    extra_fields=extra,
                    load_id=load_id,
                )

                fellow_id = resolve_identity(conn, cohort_id, response.respondent_email)
                if fellow_id is None:
                    result.unresolved_identities += 1

                if checkin_b_id:
                    result.rows_written += 1
                    record_shoutouts(
                        conn, checkin_b_id, slots.get(SLOT_SHOUTOUT), names
                    )
                else:
                    result.rows_skipped += 1

                # Routed immediately, and independently of whether the check-in
                # row was new. The help table has its own idempotency key, so a
                # re-pull neither duplicates the request nor re-emails anyone —
                # and a request that arrived on a pass where the check-in already
                # existed still reaches a human.
                if _ticked(slots.get(SLOT_HELP)):
                    record_and_route(
                        conn,
                        source_event_id=event_id,
                        submitted_email=response.respondent_email,
                        submitted_at_utc=submitted_at,
                        session_id=implied_session_id,
                        fellow_id=fellow_id,
                        routing=routing,
                        notifier=notifier,
                    )

            page_token = page.next_page_token
            if not page_token:
                break

        # Only now — every page consumed without raising — is it safe to move on.
        execute(
            conn,
            """
            update session_form
               set response_watermark = coalesce(%s, response_watermark),
                   last_polled_at = now()
             where session_id = %s and part = 'b'
            """,
            (newest_seen, session_id),
        )
        recompute_for_session(conn, session_id, "b")
        result.finalize_warnings()
        finish_load_run(conn, load_id, result)

    except Exception as exc:
        # The watermark is deliberately untouched: a partial pull re-reads next
        # time, and the idempotency key makes re-reading harmless.
        execute(
            conn,
            "update session_form set last_polled_at = now() "
            "where session_id = %s and part = 'b'",
            (session_id,),
        )
        finish_load_run(conn, load_id, result, error=str(exc))
        raise

    log.info("pull part=b session=%s form=%s %s", session_id, form_id, result)
    return result


def pull_cohort_b(
    conn: psycopg.Connection,
    client: FormsClient,
    cohort_id: str,
    settings: Settings | None = None,
    *,
    routing: HelpRouting | None = None,
    notifier: Notifier | None = None,
) -> IngestResult:
    """Pull every Part B form in a cohort, incrementally."""
    combined = IngestResult()
    rows = fetch_all(
        conn,
        """
        select s.session_id
          from "session" s
          join session_form sf
            on sf.session_id = s.session_id and sf.part = 'b'
         where s.cohort_id = %s
         order by s.scheduled_at_utc
        """,
        (cohort_id,),
    )
    for row in rows:
        session_id = str(row["session_id"])
        try:
            result = pull_session_b(
                conn, client, session_id, settings,
                routing=routing, notifier=notifier,
            )
        except Exception as exc:  # noqa: BLE001 - see below
            # A form whose question map is incomplete refuses to ingest, and
            # that refusal is right — but it must refuse *that form*, not the
            # cohort. Nine sessions of responses are not worth losing to one
            # broken map, and the broken one is named loudly enough to fix.
            log.warning("pull failed session=%s: %s", session_id, type(exc).__name__)
            combined.sessions_failed += 1
            combined.warnings.append(f"session {session_id} could not be pulled: {exc}")
            continue
        combined.rows_read += result.rows_read
        combined.rows_written += result.rows_written
        combined.rows_skipped += result.rows_skipped
        combined.unresolved_identities += result.unresolved_identities
        combined.warnings.extend(result.warnings)
    log.info("pull part=b cohort=%s sessions=%d %s", cohort_id, len(rows), combined)
    return combined


__all__ = ["pull_cohort_b", "pull_session_b"]
