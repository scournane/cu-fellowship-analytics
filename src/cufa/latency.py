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

Negative values are possible and are kept. They mean a submission arrived
before the announcement was stamped, which is worth seeing rather than
rounding away.
"""

from __future__ import annotations

import psycopg

from .db import execute, fetch_one
from .logging_setup import get_logger

log = get_logger(__name__)


# Which observation table each part's submissions live in. Part B measures
# latency from the same T0 as Part A — the announcement — but falls back to its
# OWN earliest submission, not Part A's: the two forms go out at different
# moments, so deriving Part B's origin from a Part A arrival would measure the
# gap between two different events and call it a response time.
_OBSERVATION_TABLE = {"a": "checkin", "b": "checkin_b"}


def t0_for_session(
    conn: psycopg.Connection, session_id: str, part: str = "a"
) -> tuple[object | None, str]:
    """Return ``(t0, source)`` where source is 'announced' or 'derived' or 'none'."""
    table = _OBSERVATION_TABLE[part]
    row = fetch_one(
        conn,
        f"""
        select s.announced_at_utc,
               (select min(c.submitted_at_utc) from {table} c
                 where c.session_id = s.session_id) as first_submission
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


def recompute_for_session(
    conn: psycopg.Connection, session_id: str, part: str = "a"
) -> int:
    """Recompute ``latency_seconds`` for every check-in matched to one session.

    This is the one column on ``checkin`` / ``checkin_b`` that is allowed to
    change after insert, and the database triggers enforce that boundary. It has
    to be recomputable because T0 legitimately moves: a teacher presses "Announce
    now" after the first fellow has already submitted, and every latency in that
    session was measured from the wrong origin until this runs.
    """
    table = _OBSERVATION_TABLE[part]
    t0, source = t0_for_session(conn, session_id, part)
    if t0 is None:
        return 0

    # Deliberately NOT clamped at zero. A submission that predates the
    # announcement stamp is a real observation — usually the teacher pressed
    # "Announce now" late, occasionally something else — and clamping it to 0
    # would be interpreting the number, which this module explicitly does not
    # do. A negative value is visible; a clamped one is not.
    updated = execute(
        conn,
        f"""
        update {table}
           set latency_seconds = extract(epoch from (submitted_at_utc - %s))::int
         where session_id = %s
           and latency_seconds is distinct from
               extract(epoch from (submitted_at_utc - %s))::int
        """,
        (t0, session_id, t0),
    )
    if updated:
        log.info(
            "latency recomputed session=%s part=%s rows=%d t0=%s",
            session_id, part, updated, source,
        )
    return updated


def recompute_for_cohort(conn: psycopg.Connection, cohort_id: str, part: str = "a") -> int:
    from .db import fetch_all

    total = 0
    for row in fetch_all(
        conn, 'select session_id from "session" where cohort_id = %s', (cohort_id,)
    ):
        total += recompute_for_session(conn, str(row["session_id"]), part)
    return total
