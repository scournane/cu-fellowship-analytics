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


__all__ = [
    "STATUS_ACKNOWLEDGED",
    "STATUS_CLOSED",
    "STATUS_OPEN",
    "RoutedRequest",
    "acknowledge",
    "list_requests",
    "open_count",
    "record_and_route",
]
