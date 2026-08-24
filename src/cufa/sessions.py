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

    #: Which week of the fellowship this is. Drives Part B's rotating question,
    #: and is typed in rather than derived from the date — sessions get
    #: rescheduled, skipped and doubled up, and a calendar-derived week
    #: desynchronises the whole rotation without announcing it.
    week_index: int | None = None
    #: The teacher's own question for the rotating slot. Needed only on the weeks
    #: the schedule assigns to teacher_question, where provisioning refuses
    #: rather than substituting something generic.
    teacher_question: str | None = None

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
            duration_minutes, grace_minutes, passphrase, week_index, teacher_question
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            data.week_index,
            (data.teacher_question or "").strip() or None,
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
               week_index = %s,
               teacher_question = %s,
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
            data.week_index,
            (data.teacher_question or "").strip() or None,
            session_id,
        ),
    )
    log.info("session updated id=%s", session_id)


def get_session(conn: psycopg.Connection, session_id: str) -> dict[str, Any] | None:
    """One session, with BOTH parts' forms attached.

    Part A's columns keep their original unprefixed names so every caller
    written against Part A still reads correctly; Part B's are prefixed ``b_``.
    The two forms are joined separately rather than by a single join on
    ``session_id``, which would now return two rows per session and silently
    double every list.
    """
    return fetch_one(
        conn,
        """
        select s.*,
               fa.form_id, fa.form_url, fa.edit_url,
               fa.provisioned_at, fa.published_at, fa.publish_verified_at,
               fa.response_watermark, fa.last_polled_at,
               fb.form_id             as b_form_id,
               fb.form_url            as b_form_url,
               fb.edit_url            as b_edit_url,
               fb.provisioned_at      as b_provisioned_at,
               fb.published_at        as b_published_at,
               fb.publish_verified_at as b_publish_verified_at,
               fb.response_watermark  as b_response_watermark,
               fb.last_polled_at      as b_last_polled_at,
               (select count(*) from checkin c where c.session_id = s.session_id)
                   as response_count,
               (select count(*) from checkin_b b where b.session_id = s.session_id)
                   as b_response_count
          from "session" s
          left join session_form fa
                 on fa.session_id = s.session_id and fa.part = 'a'
          left join session_form fb
                 on fb.session_id = s.session_id and fb.part = 'b'
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
               s.passphrase, s.announced_at_utc, s.week_index, s.teacher_question,
               fa.form_id, fa.form_url, fa.publish_verified_at,
               fb.form_id             as b_form_id,
               fb.form_url            as b_form_url,
               fb.publish_verified_at as b_publish_verified_at,
               (select count(*) from checkin c where c.session_id = s.session_id)
                   as response_count,
               (select count(*) from checkin_b b where b.session_id = s.session_id)
                   as b_response_count
          from "session" s
          left join session_form fa
                 on fa.session_id = s.session_id and fa.part = 'a'
          left join session_form fb
                 on fb.session_id = s.session_id and fb.part = 'b'
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
               duration_minutes, grace_minutes, passphrase, announced_at_utc,
               week_index, teacher_question
          from "session"
         where cohort_id = %s
         order by scheduled_at_utc
        """,
        (cohort_id,),
    )
