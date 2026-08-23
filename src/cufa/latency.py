"""How long after the announcement a check-in arrived.

Stored, never interpreted. There is no threshold here, no "suspicious" flag, no
derived judgment — those would be a policy CU has not written, encoded by
whoever happened to implement this. It is an input to an analysis that does not
exist yet, and the honest thing is to record it and stop.

T0 is the announcement:

* ``session.announced_at_utc`` when the teacher pressed "Announce now". This is
  the real thing being measured — the moment the passphrase entered the room.
* Otherwise the earliest submission matched to that session. Under this derived
  T0 the first submitter always has a latency of exactly 0. That is expected,
  not a bug: with no announcement stamp, the first arrival *is* the only
  evidence of when the form went out.

NULL when no session matched — there is nothing to measure from.
"""

from __future__ import annotations

import psycopg

from .db import execute, fetch_one
from .logging_setup import get_logger

log = get_logger(__name__)


def t0_for_session(conn: psycopg.Connection, session_id: str) -> tuple[object | None, str]:
    """Return ``(t0, source)`` where source is 'announced' or 'derived' or 'none'."""
    row = fetch_one(
        conn,
        """
        select s.announced_at_utc,
               (select min(c.submitted_at_utc) from checkin c where c.session_id = s.session_id)
                   as first_submission
          from "session" s
         where s.session_id = %s
        """,
        (session_id,),
    )
    if row is None:
        return None, "none"
    if row["announced_at_utc"] is not None:
        return row["announced_at_utc"], "announced"
    if row["first_submission"] is not None:
        return row["first_submission"], "derived"
    return None, "none"


def recompute_for_session(conn: psycopg.Connection, session_id: str) -> int:
    """Recompute ``latency_seconds`` for every check-in matched to one session.

    This is the one column on ``checkin`` that is allowed to change after
    insert, and the database trigger enforces that boundary. It has to be
    recomputable because T0 legitimately moves: a teacher presses "Announce now"
    after the first fellow has already submitted, and every latency in that
    session was measured from the wrong origin until this runs.
    """
    t0, source = t0_for_session(conn, session_id)
    if t0 is None:
        return 0

    updated = execute(
        conn,
        """
        update checkin
           set latency_seconds = greatest(0, extract(epoch from (submitted_at_utc - %s))::int)
         where session_id = %s
           and latency_seconds is distinct from
               greatest(0, extract(epoch from (submitted_at_utc - %s))::int)
        """,
        (t0, session_id, t0),
    )
    if updated:
        log.info("latency recomputed session=%s rows=%d t0=%s", session_id, updated, source)
    return updated


def recompute_for_cohort(conn: psycopg.Connection, cohort_id: str) -> int:
    from .db import fetch_all

    total = 0
    for row in fetch_all(
        conn, 'select session_id from "session" where cohort_id = %s', (cohort_id,)
    ):
        total += recompute_for_session(conn, str(row["session_id"]))
    return total
