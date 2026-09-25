"""What staff did about a fellow, and what a fellow asked for through the bot.

Three kinds of row:

* ``outreach`` — a staff member reached out. This is the boolean the Director
  asked for: on the dashboard "has anyone contacted this person?" is
  ``exists(outreach)``, and it is set by a human, never by the system.
* ``check_in_request`` — the fellow pressed the bot's *check in with me*
  button. It pings the staff channel and lands here so it can be picked up.
* ``note`` — anything else a staffer wants on the record.

**This is not the Part B help checkbox.** That lives in ``help_request`` with
its own access list and is never read here or anywhere near a metric. A
check-in request from the bot is an ordinary operational row: the fellow
pressed a button in a public tool and expects a reply.
"""

from __future__ import annotations

from typing import Any

import psycopg

from .db import execute, fetch_all, fetch_one
from .errors import CufaError
from .logging_setup import get_logger

log = get_logger(__name__)

KINDS = ("outreach", "check_in_request", "note")


def record(
    conn: psycopg.Connection,
    fellow_id: str,
    *,
    kind: str,
    by_email: str | None = None,
    by_slack_user: str | None = None,
    note: str | None = None,
    source: str = "console",
) -> str:
    if kind not in KINDS:
        raise CufaError(f"intervention kind must be one of {', '.join(KINDS)}")
    if source not in ("console", "slack", "cli"):
        raise CufaError("source must be console, slack or cli")
    if fetch_one(conn, "select 1 from fellow where fellow_id = %s", (fellow_id,)) is None:
        raise CufaError(f"No fellow with id {fellow_id}")
    row = fetch_one(
        conn,
        """
        insert into intervention (fellow_id, kind, by_email, by_slack_user, note, source)
        values (%s, %s, %s, %s, %s, %s)
        returning intervention_id
        """,
        (fellow_id, kind, (by_email or "").strip().lower() or None, by_slack_user, note, source),
    )
    assert row is not None
    log.info("intervention recorded fellow=%s kind=%s", fellow_id, kind)
    return str(row["intervention_id"])


def mark_reached_out(
    conn: psycopg.Connection, fellow_id: str, *, by_email: str, note: str | None = None, source: str = "console"
) -> str:
    """The staff-set boolean, as a row with provenance."""
    if not (by_email or "").strip():
        raise CufaError("marking outreach needs the address of the staff member who did it")
    return record(conn, fellow_id, kind="outreach", by_email=by_email, note=note, source=source)


def clear_reached_out(conn: psycopg.Connection, fellow_id: str, *, by_email: str) -> int:
    """Un-set it: outreach rows are resolved, not deleted, so history stays."""
    return execute(
        conn,
        """
        update intervention set resolved_at = now(), resolved_by = %s
         where fellow_id = %s and kind = 'outreach' and resolved_at is null
        """,
        ((by_email or "").strip().lower() or "unknown", fellow_id),
    )


def reached_out(conn: psycopg.Connection, fellow_id: str) -> bool:
    row = fetch_one(
        conn,
        "select 1 from intervention where fellow_id = %s and kind = 'outreach' and resolved_at is null limit 1",
        (fellow_id,),
    )
    return row is not None


def request_check_in(
    conn: psycopg.Connection, fellow_id: str, *, by_slack_user: str, note: str | None = None
) -> str:
    return record(conn, fellow_id, kind="check_in_request", by_slack_user=by_slack_user, note=note, source="slack")


def resolve(conn: psycopg.Connection, intervention_id: str, *, by_email: str) -> bool:
    return (
        execute(
            conn,
            "update intervention set resolved_at = now(), resolved_by = %s where intervention_id = %s and resolved_at is null",
            ((by_email or "").strip().lower() or "unknown", intervention_id),
        )
        > 0
    )


def for_fellow(conn: psycopg.Connection, fellow_id: str) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        """
        select intervention_id, kind, by_email, by_slack_user, note, source, resolved_at, resolved_by, created_at
          from intervention where fellow_id = %s order by created_at desc
        """,
        (fellow_id,),
    )


def open_requests(conn: psycopg.Connection, cohort_id: str | None = None) -> list[dict[str, Any]]:
    """Check-in requests nobody has picked up."""
    return fetch_all(
        conn,
        """
        select i.intervention_id, i.fellow_id, f.full_name, f.cohort_id, i.note, i.by_slack_user, i.created_at
          from intervention i
          join fellow f on f.fellow_id = i.fellow_id
         where i.kind = 'check_in_request' and i.resolved_at is null
           and (%s::text is null or f.cohort_id = %s::text)
         order by i.created_at
        """,
        (cohort_id, cohort_id),
    )


__all__ = [
    "KINDS",
    "clear_reached_out",
    "for_fellow",
    "mark_reached_out",
    "open_requests",
    "reached_out",
    "record",
    "request_check_in",
    "resolve",
]
