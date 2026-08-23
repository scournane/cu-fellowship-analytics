"""Session records: create, edit, list, and stamp the announcement.

The local wall-clock time and the IANA zone a human typed are stored alongside
the UTC instant they produce. Keeping all three means a mistake is visible — if
the zone was wrong, the local value still says what the person meant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg

from .db import execute, fetch_all, fetch_one
from .logging_setup import get_logger
from .timeutil import get_zone, to_utc

log = get_logger(__name__)


@dataclass(frozen=True)
class SessionInput:
    """The fields the console form and the bulk CSV both supply."""

    cohort_id: str
    title: str
    scheduled_at_local: datetime
    timezone: str
    duration_minutes: int
    grace_minutes: int = 15
    passphrase: str | None = None

    def scheduled_at_utc(self) -> datetime:
        """Convert the typed local time using the typed zone.

        The local value is naive by construction — it is what a date-time picker
        produces — so the zone is applied here and nowhere else.
        """
        zone = get_zone(self.timezone)
        local = self.scheduled_at_local
        if local.tzinfo is not None:
            return to_utc(local)
        return to_utc(local.replace(tzinfo=zone))


def create_session(conn: psycopg.Connection, data: SessionInput) -> str:
    row = fetch_one(
        conn,
        """
        insert into "session" (
            cohort_id, title, scheduled_at_local, timezone, scheduled_at_utc,
            duration_minutes, grace_minutes, passphrase
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        returning session_id
        """,
        (
            data.cohort_id,
            data.title,
            data.scheduled_at_local.replace(tzinfo=None),
            data.timezone,
            data.scheduled_at_utc(),
            data.duration_minutes,
            data.grace_minutes,
            (data.passphrase or "").strip() or None,
        ),
    )
    assert row is not None
    session_id = str(row["session_id"])
    log.info("session created id=%s cohort=%s", session_id, data.cohort_id)
    return session_id


def update_session(conn: psycopg.Connection, session_id: str, data: SessionInput) -> None:
    execute(
        conn,
        """
        update "session"
           set title = %s,
               scheduled_at_local = %s,
               timezone = %s,
               scheduled_at_utc = %s,
               duration_minutes = %s,
               grace_minutes = %s,
               passphrase = %s,
               updated_at = now()
         where session_id = %s
        """,
        (
            data.title,
            data.scheduled_at_local.replace(tzinfo=None),
            data.timezone,
            data.scheduled_at_utc(),
            data.duration_minutes,
            data.grace_minutes,
            (data.passphrase or "").strip() or None,
            session_id,
        ),
    )
    log.info("session updated id=%s", session_id)


def get_session(conn: psycopg.Connection, session_id: str) -> dict[str, Any] | None:
    return fetch_one(
        conn,
        """
        select s.*,
               sf.form_id, sf.form_url, sf.edit_url,
               sf.provisioned_at, sf.published_at, sf.publish_verified_at,
               sf.response_watermark, sf.last_polled_at,
               (select count(*) from checkin c where c.session_id = s.session_id) as response_count
          from "session" s
          left join session_form sf on sf.session_id = s.session_id
         where s.session_id = %s
        """,
        (session_id,),
    )


def list_sessions(conn: psycopg.Connection, cohort_id: str | None = None) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        """
        select s.session_id, s.cohort_id, s.title, s.scheduled_at_local, s.timezone,
               s.scheduled_at_utc, s.duration_minutes, s.grace_minutes,
               s.passphrase, s.announced_at_utc,
               sf.form_id, sf.form_url, sf.publish_verified_at,
               (select count(*) from checkin c where c.session_id = s.session_id) as response_count
          from "session" s
          left join session_form sf on sf.session_id = s.session_id
         where (%s::text is null or s.cohort_id = %s::text)
         order by s.scheduled_at_utc
        """,
        (cohort_id, cohort_id),
    )


def announce_now(conn: psycopg.Connection, session_id: str, when: datetime | None = None) -> datetime:
    """Stamp ``announced_at_utc``. This is what latency is measured from.

    Re-announcing overwrites deliberately: if the teacher hit the button early
    by mistake, the second press is the real announcement, and a stale T0 would
    silently skew every latency for the session.
    """
    row = fetch_one(
        conn,
        """
        update "session"
           set announced_at_utc = coalesce(%s, now()), updated_at = now()
         where session_id = %s
        returning announced_at_utc
        """,
        (to_utc(when) if when else None, session_id),
    )
    assert row is not None
    log.info("session announced id=%s", session_id)
    return row["announced_at_utc"]


def sessions_for_matching(conn: psycopg.Connection, cohort_id: str) -> list[dict[str, Any]]:
    """Every session in the cohort, with the fields window matching needs."""
    return fetch_all(
        conn,
        """
        select session_id, cohort_id, title, scheduled_at_utc,
               duration_minutes, grace_minutes, passphrase, announced_at_utc
          from "session"
         where cohort_id = %s
         order by scheduled_at_utc
        """,
        (cohort_id,),
    )
