"""Recording and routing a request to be checked in with.

This is the most sensitive thing the system touches, and the rules around it are
ethical constraints rather than engineering preferences:

* **Routed immediately on ingest**, not on a batch schedule. A fellow asking for
  contact should not wait for a weekly pipeline run.
* **Never feeds a participation signal.** Nothing here is read by any count,
  rate, score or aggregate. If a fellow can suspect that ticking the box costs
  them something, the field stops working and the programme loses its only
  self-reported distress channel.
* **Never processed by the AI tier**, never exported to a report, never in a CSV
  a staffer might email.
* **Never logged**, at any level, DEBUG included. This module emits counts and
  outcomes and nothing else — not the address, not the name, not the session.

Recording and notifying are separate steps on purpose. A mail failure must not
lose the request: the row is written first, the notification is attempted after,
and a failed notification is recorded on the row's history rather than raising
through ingest and rolling the row back.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from collections.abc import Sequence
from typing import Any

import psycopg

from .db import fetch_all, fetch_one
from .help_routing import (
    HelpNotification,
    HelpRouting,
    Notifier,
    RecordingNotifier,
    build_notification,
    get_help_routing,
)
from .logging_setup import get_logger
from .text import normalize_email

log = get_logger(__name__)

STATUS_OPEN = "open"
STATUS_ACKNOWLEDGED = "acknowledged"
STATUS_CLOSED = "closed"


@dataclass(frozen=True)
class RoutedRequest:
    """What happened when one request landed."""

    help_request_id: str | None
    created: bool
    notification: HelpNotification | None
    notify_error: str | None = None

    @property
    def notified(self) -> bool:
        return self.notification is not None and self.notify_error is None


def record_and_route(
    conn: psycopg.Connection,
    *,
    source_event_id: str,
    submitted_email: str,
    submitted_at_utc: datetime,
    session_id: str | None,
    fellow_id: str | None,
    routing: HelpRouting | None = None,
    notifier: Notifier | None = None,
) -> RoutedRequest:
    """Write the request and tell the configured recipient, now.

    Idempotent on ``source_event_id``: re-pulling the same response does not
    raise the same hand twice, and — just as importantly — does not email the
    recipient a second time about a request they already acknowledged.
    """
    routing = routing if routing is not None else get_help_routing()
    notifier = notifier if notifier is not None else RecordingNotifier()

    row = fetch_one(
        conn,
        """
        insert into help_request
            (source_event_id, submitted_email, submitted_at_utc, session_id, fellow_id)
        values (%s, %s, %s, %s, %s)
        on conflict (source_event_id) do nothing
        returning help_request_id
        """,
        (
            source_event_id,
            normalize_email(submitted_email),
            submitted_at_utc,
            session_id,
            fellow_id,
        ),
    )

    if row is None:
        # Already recorded on an earlier pass. Nothing is sent.
        return RoutedRequest(help_request_id=None, created=False, notification=None)

    help_request_id = str(row["help_request_id"])
    log.info("help request recorded")

    if not routing.has_recipient:
        # Should be unreachable: with no recipient the checkbox is never put on
        # the form. Handled anyway, because "unreachable" and "unreached" are
        # different, and a request nobody is told about must at least be visible
        # in the console.
        log.warning(
            "a help request was recorded but no recipient is configured; it is "
            "visible in the console and nobody has been emailed"
        )
        return RoutedRequest(
            help_request_id=help_request_id,
            created=True,
            notification=None,
            notify_error="no recipient configured",
        )

    context = fetch_one(
        conn,
        """
        select f.full_name, s.title as session_title
          from (select 1) as anchor
          left join fellow f on f.fellow_id = %s
          left join "session" s on s.session_id = %s
        """,
        (fellow_id, session_id),
    ) or {}

    notification = build_notification(
        routing,
        fellow_name=context.get("full_name") or "",
        session_title=context.get("session_title") or "",
        submitted_at_utc=submitted_at_utc.strftime("%Y-%m-%d %H:%M:%S"),
    )

    try:
        notifier.send(notification)
    except Exception as exc:  # noqa: BLE001 — a mail failure must not lose the row
        # Type name only. The exception text can carry an address.
        log.warning("help request notification failed: %s", type(exc).__name__)
        return RoutedRequest(
            help_request_id=help_request_id,
            created=True,
            notification=notification,
            notify_error=type(exc).__name__,
        )

    return RoutedRequest(
        help_request_id=help_request_id, created=True, notification=notification
    )


def list_requests(
    conn: psycopg.Connection,
    *,
    status: str | None = STATUS_OPEN,
    cohort_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Requests for the console's access-gated screen and `cufa help-requests`.

    Deliberately not a view. A view named ``v_help_request_something`` is the
    easiest possible thing for a future report to join to without re-reading what
    it is — keeping this as a function in one module keeps the call sites
    countable.
    """
    return fetch_all(
        conn,
        """
        select h.help_request_id, h.submitted_email, h.submitted_at_utc, h.status,
               h.acknowledged_by, h.acknowledged_at, h.note, h.created_at,
               h.fellow_id, f.full_name, f.cohort_id,
               h.session_id, s.title as session_title, s.scheduled_at_utc
          from help_request h
          left join fellow f on f.fellow_id = h.fellow_id
          left join "session" s on s.session_id = h.session_id
         where (%s::text is null or h.status = %s::text)
           and (%s::text is null or coalesce(f.cohort_id, s.cohort_id) = %s::text)
         order by h.submitted_at_utc desc
         limit %s
        """,
        (status, status, cohort_id, cohort_id, limit),
    )


def open_count(conn: psycopg.Connection) -> int:
    """How many are waiting. The one number about this table anything else sees.

    Used only for the console's badge. It is a count of open requests, not a
    count attached to any fellow or session, and it enters no participation
    computation.
    """
    row = fetch_one(
        conn, "select count(*) as n from help_request where status = 'open'"
    )
    return int((row or {}).get("n") or 0)


def acknowledge(
    conn: psycopg.Connection,
    help_request_id: str,
    *,
    by_email: str,
    note: str | None = None,
    status: str = STATUS_ACKNOWLEDGED,
) -> dict[str, Any]:
    """Mark a request as picked up, or closed. Records who and when."""
    if status not in (STATUS_ACKNOWLEDGED, STATUS_CLOSED):
        raise ValueError(
            f"status must be {STATUS_ACKNOWLEDGED!r} or {STATUS_CLOSED!r}, got {status!r}"
        )
    row = fetch_one(
        conn,
        """
        update help_request
           set status          = %s,
               acknowledged_by = %s,
               acknowledged_at = coalesce(acknowledged_at, now()),
               note            = coalesce(%s, note)
         where help_request_id = %s
        returning help_request_id, status, acknowledged_at
        """,
        (status, (by_email or "").strip().lower() or None, note, help_request_id),
    )
    if row is None:
        raise LookupError(f"No help request with id {help_request_id}")
    log.info("help request %s", status)
    return row


# ---------------------------------------------------------------------------
# Data subject rights.
#
# ADR-025 keeps this table's readers down to one module so that a change to who
# can see a safeguarding disclosure has exactly one place to audit, and
# `tests/test_safeguarding.py::test_19b_the_help_table_is_read_from_exactly_one_module`
# enforces it by grepping the tree. Subject access and erasure genuinely need
# this table — a request a fellow made about themselves is theirs to see, and an
# erasure that skipped it would not be one — so the SQL lives here, next to the
# rest of the help-specific rules, and `data_rights` calls these by name.
#
# Writing it the other way round, with the queries in `data_rights` and this
# module added to the test's allowlist, would have passed the test and lost the
# property the test exists to protect.
# ---------------------------------------------------------------------------


def for_subject_access(
    conn: psycopg.Connection, fellow_id: str, emails: Sequence[str]
) -> list[dict[str, Any]]:
    """What this fellow is shown about their own help requests.

    Deliberately not the whole row. Who picked it up and what they wrote about
    it are a staff record of a safeguarding response, not the fellow's own
    data, and showing them back could make the next person hesitate before
    ticking the box. The fellow gets when they asked, what came of it, and the
    fact that a note exists.
    """
    return [
        dict(r)
        for r in fetch_all(
            conn,
            """
            select h.submitted_at_utc, h.status, h.created_at,
                   s.title as session_title,
                   (h.note is not null) as a_note_exists
              from help_request h
              left join "session" s on s.session_id = h.session_id
             where h.fellow_id = %s or lower(h.submitted_email) = any(%s)
             order by h.submitted_at_utc
            """,
            (fellow_id, list(emails)),
        )
    ]


def erasure_predicate() -> tuple[str, str]:
    """The ``UPDATE`` an erasure runs here, and its ``WHERE``, as SQL text.

    Returned rather than executed because `data_rights` reports every step of a
    plan before applying any of it, and a dry run has to be able to count the
    rows this would touch without running it.

    The row survives with its address and note removed. A safeguarding request
    having been raised and answered is its own record, and the fellowship's
    account of how it responded to a young person asking for help should not be
    erasable by that young person's erasure — the part that identifies them is.
    """
    where = (
        "(fellow_id = %s or lower(submitted_email) = any(%s)) "
        "and (lower(submitted_email) <> %s or note is not null)"
    )
    return f"update help_request set submitted_email = %s, note = null where {where}", where


__all__ = [
    "STATUS_ACKNOWLEDGED",
    "STATUS_CLOSED",
    "STATUS_OPEN",
    "RoutedRequest",
    "acknowledge",
    "erasure_predicate",
    "for_subject_access",
    "list_requests",
    "open_count",
    "record_and_route",
]
