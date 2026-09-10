"""Per-fellow engagement, and who might be falling behind.

Three signals, each compared with the cohort rather than with an absolute:

* **Slack** — messages sent in fellow-facing channels, as a share of the
  cohort mean.
* **Attendance** — sessions attended (Part A, current decision) over sessions
  held so far. ``needs_review`` counts as neither: absent evidence is not
  evidence of absence.
* **Form completeness** — of the Part B forms submitted, how many fields were
  filled in. This is *counted, never graded*: a takeaway is either there or it
  is not, and its length or quality is never scored, because rating writing
  penalises ESL and neurodivergent fellows for reasons unrelated to engagement.

The three combine into an **attention index** from 0 to 100, where higher
means "more reason for a human to look". The weights are in ``WEIGHTS`` and are
a starting point CU can change; the components are always shown next to the
index so nobody has to trust the number.

**What this is not.** It is not a grade, not a prediction, and not a label
that follows a fellow anywhere. It is a sorted list for a staff member on a
Monday morning. Two things are excluded from it by design and by test:
``help_request`` (asking for help never lowers any signal) and assignment
scores (a mark on the Solvathon is not participation).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg

from .db import fetch_all, fetch_one

WEIGHTS = {"slack": 0.35, "attendance": 0.45, "forms": 0.20}

#: Below this share of the cohort mean a signal is flagged on its own.
LOW_SHARE = 0.5


@dataclass
class FellowEngagement:
    fellow_id: str
    full_name: str
    status: str
    messages: int
    messages_7d: int
    words: int
    cohort_mean_messages: float
    slack_share: float | None          # messages / cohort mean, None when the cohort is silent
    sessions_held: int
    attended: int
    needs_review: int
    attendance_rate: float | None      # attended / held, None before the first session
    forms_submitted: int
    forms_expected: int
    form_completeness: float | None    # fields answered / fields available
    attention_index: int
    flags: list[str] = field(default_factory=list)
    reached_out: bool = False
    open_check_in_requests: int = 0
    last_message_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def attention_index(
    slack_share: float | None, attendance_rate: float | None, form_completeness: float | None
) -> tuple[int, list[str]]:
    """0..100, higher = more reason to look. Also the reasons, as short flags."""
    parts: list[tuple[float, float]] = []
    flags: list[str] = []
    if slack_share is not None:
        parts.append((WEIGHTS["slack"], 1.0 - _clamp(slack_share)))
        if slack_share < LOW_SHARE:
            flags.append("quiet on Slack")
    if attendance_rate is not None:
        parts.append((WEIGHTS["attendance"], 1.0 - _clamp(attendance_rate)))
        if attendance_rate < LOW_SHARE:
            flags.append("missing sessions")
    if form_completeness is not None:
        parts.append((WEIGHTS["forms"], 1.0 - _clamp(form_completeness)))
        if form_completeness < LOW_SHARE:
            flags.append("thin exit tickets")
    if not parts:
        return 0, ["no data yet"]
    weight = sum(w for w, _ in parts)
    score = sum(w * v for w, v in parts) / weight
    return round(score * 100), flags


def cohort_engagement(
    conn: psycopg.Connection, cohort_id: str, *, now: datetime | None = None
) -> list[FellowEngagement]:
    """Every active fellow in the cohort, most attention-worthy first."""
    now = now or datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    fellows = fetch_all(
        conn,
        "select fellow_id, full_name, status from fellow where cohort_id = %s and status = 'active' order by full_name",
        (cohort_id,),
    )
    if not fellows:
        return []

    slack = {
        r["fellow_id"]: r
        for r in fetch_all(
            conn,
            """
            select fellow_id,
                   count(*)                                             as messages,
                   count(*) filter (where posted_at_utc >= %s)          as messages_7d,
                   coalesce(sum(word_count), 0)                         as words,
                   max(posted_at_utc)                                   as last_message_at
              from v_slack_message_resolved
             where cohort_id = %s and fellow_id is not null and not staff_channel
             group by fellow_id
            """,
            (week_ago, cohort_id),
        )
    }
    held = int(
        (
            fetch_one(
                conn,
                'select count(*) as n from "session" where cohort_id = %s and scheduled_at_utc <= %s',
                (cohort_id, now),
            )
            or {}
        ).get("n")
        or 0
    )
    attendance = {
        r["fellow_id"]: r
        for r in fetch_all(
            conn,
            """
            select fellow_id,
                   count(distinct session_id) filter (where status = 'attended')     as attended,
                   count(distinct session_id) filter (where status = 'needs_review') as needs_review
              from v_checkin_resolved
             where cohort_id = %s and fellow_id is not null and session_id is not null
             group by fellow_id
            """,
            (cohort_id,),
        )
    }
    forms = {
        r["fellow_id"]: r
        for r in fetch_all(
            conn,
            """
            select fellow_id,
                   count(*) as submitted,
                   -- three fields are always on the form; the shoutout is optional
                   -- and the help checkbox is not a field this query may see.
                   sum((confidence_raw is not null)::int + has_takeaway::int + has_rotating_answer::int) as answered,
                   count(*) * 3 as available
              from v_checkin_b_resolved
             where cohort_id = %s and fellow_id is not null
             group by fellow_id
            """,
            (cohort_id,),
        )
    }
    outreach = {
        r["fellow_id"]
        for r in fetch_all(
            conn,
            "select distinct fellow_id from intervention where kind = 'outreach' and resolved_at is null",
        )
    }
    requests = {
        r["fellow_id"]: int(r["n"])
        for r in fetch_all(
            conn,
            "select fellow_id, count(*) as n from intervention where kind = 'check_in_request' and resolved_at is null group by fellow_id",
        )
    }

    mean_messages = sum(int(slack.get(f["fellow_id"], {}).get("messages") or 0) for f in fellows) / len(fellows)

    out: list[FellowEngagement] = []
    for f in fellows:
        fid = f["fellow_id"]
        s = slack.get(fid, {})
        a = attendance.get(fid, {})
        b = forms.get(fid, {})
        messages = int(s.get("messages") or 0)
        attended = int(a.get("attended") or 0)
        needs_review = int(a.get("needs_review") or 0)
        submitted = int(b.get("submitted") or 0)
        slack_share = (messages / mean_messages) if mean_messages > 0 else None
        # A session under review is neither attended nor missed, so it comes
        # out of the denominator rather than counting against the fellow.
        decidable = held - needs_review
        attendance_rate = (attended / decidable) if decidable > 0 else None
        completeness = (int(b.get("answered") or 0) / int(b.get("available") or 0)) if submitted else None
        index, flags = attention_index(slack_share, attendance_rate, completeness)
        out.append(
            FellowEngagement(
                fellow_id=fid,
                full_name=f["full_name"],
                status=f["status"],
                messages=messages,
                messages_7d=int(s.get("messages_7d") or 0),
                words=int(s.get("words") or 0),
                cohort_mean_messages=round(mean_messages, 2),
                slack_share=round(slack_share, 3) if slack_share is not None else None,
                sessions_held=held,
                attended=attended,
                needs_review=needs_review,
                attendance_rate=round(attendance_rate, 3) if attendance_rate is not None else None,
                forms_submitted=submitted,
                forms_expected=held,
                form_completeness=round(completeness, 3) if completeness is not None else None,
                attention_index=index,
                flags=flags,
                reached_out=fid in outreach,
                open_check_in_requests=requests.get(fid, 0),
                last_message_at=s.get("last_message_at"),
            )
        )
    out.sort(key=lambda e: (-e.attention_index, e.full_name))
    return out


def fellow_engagement(conn: psycopg.Connection, fellow_id: str, *, now: datetime | None = None) -> FellowEngagement | None:
    row = fetch_one(conn, "select cohort_id from fellow where fellow_id = %s", (fellow_id,))
    if row is None:
        return None
    for item in cohort_engagement(conn, row["cohort_id"], now=now):
        if item.fellow_id == fellow_id:
            return item
    return None


def most_active(
    conn: psycopg.Connection, cohort_id: str, *, days: int = 7, limit: int = 5, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Who posted most in fellow-facing channels over the last ``days``."""
    now = now or datetime.now(timezone.utc)
    return fetch_all(
        conn,
        """
        select fellow_id, full_name, count(*) as messages, coalesce(sum(word_count), 0) as words
          from v_slack_message_resolved
         where cohort_id = %s and fellow_id is not null and not staff_channel and posted_at_utc >= %s
         group by fellow_id, full_name
         order by messages desc, full_name
         limit %s
        """,
        (cohort_id, now - timedelta(days=days), limit),
    )


def quiet_fellows(
    conn: psycopg.Connection, cohort_id: str, *, days: int = 7, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Active fellows with no message in fellow-facing channels for ``days``."""
    now = now or datetime.now(timezone.utc)
    return fetch_all(
        conn,
        """
        select f.fellow_id, f.full_name, max(m.posted_at_utc) as last_message_at
          from fellow f
          left join v_slack_message_resolved m on m.fellow_id = f.fellow_id and not m.staff_channel
         where f.cohort_id = %s and f.status = 'active'
         group by f.fellow_id, f.full_name
        having coalesce(max(m.posted_at_utc), 'epoch'::timestamptz) < %s
         order by last_message_at nulls first, f.full_name
        """,
        (cohort_id, now - timedelta(days=days)),
    )


def cohort_attendance(conn: psycopg.Connection, cohort_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Overall attendance for the staff dashboard: attended over (fellows × sessions held)."""
    now = now or datetime.now(timezone.utc)
    row = fetch_one(
        conn,
        """
        with held as (
            select count(*) as n from "session" where cohort_id = %s and scheduled_at_utc <= %s
        ), active as (
            select count(*) as n from fellow where cohort_id = %s and status = 'active'
        ), att as (
            select count(distinct (fellow_id, session_id)) as n
              from v_checkin_resolved
             where cohort_id = %s and fellow_id is not null and status = 'attended'
        )
        select held.n as sessions_held, active.n as active_fellows, att.n as attended,
               case when held.n * active.n > 0 then round(att.n::numeric / (held.n * active.n), 3) end as rate
          from held, active, att
        """,
        (cohort_id, now, cohort_id, cohort_id),
    ) or {}
    return {k: (float(v) if k == "rate" and v is not None else v) for k, v in row.items()}


__all__ = [
    "LOW_SHARE",
    "WEIGHTS",
    "FellowEngagement",
    "attention_index",
    "cohort_attendance",
    "cohort_engagement",
    "fellow_engagement",
    "most_active",
    "quiet_fellows",
]
