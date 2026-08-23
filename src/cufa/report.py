"""The terminal report and the queries behind the review screens.

Every number here is counted from ``v_checkin_resolved`` — that is, from the
current decision joined to the roster at read time. Nothing is precomputed and
stored, so correcting a roster entry or overriding a decision changes the report
on the next run with no rebuild step.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import psycopg

from .db import fetch_all, fetch_one


@dataclass
class CohortReport:
    cohort_id: str
    sessions: list[dict[str, Any]] = field(default_factory=list)
    fellows: list[dict[str, Any]] = field(default_factory=list)
    totals: dict[str, Any] = field(default_factory=dict)
    review: dict[str, Any] = field(default_factory=dict)
    provisioning: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def cohort_report(conn: psycopg.Connection, cohort_id: str) -> CohortReport:
    totals = fetch_one(
        conn,
        """
        select
            count(*)                                              as checkins,
            count(*) filter (where status = 'attended')           as attended,
            count(*) filter (where status = 'not_attended')       as not_attended,
            count(*) filter (where status = 'needs_review')       as needs_review,
            count(*) filter (where status is null)                as undecided,
            count(*) filter (where decided_by = 'rule')           as by_rule,
            count(*) filter (where decided_by = 'ai')             as by_ai,
            count(*) filter (where decided_by = 'human')          as by_human,
            count(*) filter (where session_match = 'matched')     as session_matched,
            count(*) filter (where session_match = 'none')        as session_none,
            count(*) filter (where session_match = 'ambiguous')   as session_ambiguous,
            count(*) filter (where fellow_id is null)             as unknown_email,
            count(*) filter (where passphrase_match = 'exact')    as pass_exact,
            count(*) filter (where passphrase_match = 'fuzzy')    as pass_fuzzy,
            count(*) filter (where passphrase_match = 'mismatch') as pass_mismatch,
            count(*) filter (where passphrase_match = 'not_set')  as pass_not_set,
            count(*) filter (where passphrase_match = 'no_session') as pass_no_session
          from v_checkin_resolved
         where cohort_id = %s
        """,
        (cohort_id,),
    ) or {}

    sessions = fetch_all(
        conn,
        """
        select s.session_id, s.title, s.scheduled_at_utc, s.timezone,
               s.passphrase is not null as has_passphrase,
               s.announced_at_utc is not null as announced,
               sf.form_id, sf.publish_verified_at is not null as form_ready,
               count(c.checkin_id)                                    as checkins,
               count(*) filter (where d.status = 'attended')           as attended,
               count(*) filter (where d.status = 'needs_review')       as needs_review,
               count(*) filter (where d.status = 'not_attended')       as not_attended,
               avg(c.latency_seconds)::int                             as avg_latency_seconds
          from "session" s
          left join session_form sf on sf.session_id = s.session_id
          left join checkin c on c.session_id = s.session_id
          left join v_current_decision d on d.checkin_id = c.checkin_id
         where s.cohort_id = %s
         group by s.session_id, s.title, s.scheduled_at_utc, s.timezone,
                  s.passphrase, s.announced_at_utc, sf.form_id, sf.publish_verified_at
         order by s.scheduled_at_utc
        """,
        (cohort_id,),
    )

    fellows = fetch_all(
        conn,
        """
        select f.fellow_id, f.full_name, f.status,
               count(v.checkin_id)                                as checkins,
               count(*) filter (where v.status = 'attended')      as attended,
               count(*) filter (where v.status = 'needs_review')  as needs_review,
               count(*) filter (where v.status = 'not_attended')  as not_attended
          from fellow f
          left join v_checkin_resolved v on v.fellow_id = f.fellow_id
         where f.cohort_id = %s
         group by f.fellow_id, f.full_name, f.status
         order by f.full_name
        """,
        (cohort_id,),
    )

    review = {
        "unresolved_identities": fetch_all(
            conn,
            """
            select email, occurrence_count, first_seen_at, last_seen_at
              from identity_unresolved
             where cohort_id = %s and resolved_at is null
             order by last_seen_at desc
            """,
            (cohort_id,),
        ),
        "ai_cache": fetch_one(
            conn, "select count(*) as entries from ai_adjudication_cache"
        )
        or {},
    }

    provisioning = fetch_all(
        conn,
        """
        select p.action, p.outcome, count(*) as n
          from provisioning_log p
          join "session" s on s.session_id = p.session_id
         where s.cohort_id = %s
         group by p.action, p.outcome
         order by p.action, p.outcome
        """,
        (cohort_id,),
    )

    return CohortReport(
        cohort_id=cohort_id,
        sessions=sessions,
        fellows=fellows,
        totals=totals,
        review=review,
        provisioning=provisioning,
    )


def render_report_text(report: CohortReport) -> str:
    """Human-readable report for the terminal."""
    t = report.totals
    lines: list[str] = []
    add = lines.append

    add(f"Attendance report — cohort {report.cohort_id}")
    add("=" * 62)
    add("")
    add("Check-ins")
    add(f"  observed                {t.get('checkins', 0):>6}")
    add(f"    matched a session     {t.get('session_matched', 0):>6}")
    add(f"    matched none          {t.get('session_none', 0):>6}")
    add(f"    ambiguous             {t.get('session_ambiguous', 0):>6}")
    add(f"    email not on roster   {t.get('unknown_email', 0):>6}")
    add("")
    add("Passphrase comparison (observation, not judgment)")
    add(f"  exact                   {t.get('pass_exact', 0):>6}")
    add(f"  fuzzy                   {t.get('pass_fuzzy', 0):>6}")
    add(f"  mismatch                {t.get('pass_mismatch', 0):>6}")
    add(f"  not set                 {t.get('pass_not_set', 0):>6}")
    add(f"  no session              {t.get('pass_no_session', 0):>6}")
    add("")
    add("Current decisions")
    add(f"  attended                {t.get('attended', 0):>6}")
    add(f"  not attended            {t.get('not_attended', 0):>6}")
    add(f"  needs review            {t.get('needs_review', 0):>6}")
    if t.get("undecided"):
        add(f"  no decision yet         {t.get('undecided', 0):>6}")
    add("")
    add("Decided by")
    add(f"  rule                    {t.get('by_rule', 0):>6}")
    add(f"  ai                      {t.get('by_ai', 0):>6}")
    add(f"  human                   {t.get('by_human', 0):>6}")
    add("")

    add("Sessions")
    add(f"  {'title':<34}{'form':>6}{'in':>6}{'att':>6}{'rev':>6}{'lat':>7}")
    for session in report.sessions:
        title = (session["title"] or "")[:33]
        form = "ready" if session["form_ready"] else ("—" if not session["form_id"] else "unpub")
        latency = session["avg_latency_seconds"]
        add(
            f"  {title:<34}{form:>6}{session['checkins']:>6}"
            f"{session['attended'] or 0:>6}{session['needs_review'] or 0:>6}"
            f"{(f'{latency}s' if latency is not None else '—'):>7}"
        )
    add("")

    add("Fellows")
    add(f"  {'name':<28}{'in':>5}{'att':>5}{'rev':>5}{'not':>5}")
    for fellow in report.fellows:
        name = (fellow["full_name"] or fellow["fellow_id"])[:27]
        add(
            f"  {name:<28}{fellow['checkins']:>5}{fellow['attended'] or 0:>5}"
            f"{fellow['needs_review'] or 0:>5}{fellow['not_attended'] or 0:>5}"
        )
    add("")

    unresolved = report.review.get("unresolved_identities") or []
    add(f"Review queue")
    add(f"  needs_review check-ins   {t.get('needs_review', 0):>5}")
    add(f"  unresolved addresses     {len(unresolved):>5}")
    add(f"  ai cache entries         {(report.review.get('ai_cache') or {}).get('entries', 0):>5}")
    add("")
    add("Latency is recorded, not interpreted: no thresholds and no flags are")
    add("applied to it. Where a session has no announced_at_utc, T0 is the")
    add("earliest matched submission, so its first submitter reads 0s.")
    return "\n".join(lines)


def needs_review_queue(
    conn: psycopg.Connection, cohort_id: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    """Oldest first — the longest-waiting judgment is the most overdue one."""
    return fetch_all(
        conn,
        """
        select * from v_checkin_resolved
         where status = 'needs_review'
           and (%s::text is null or cohort_id = %s::text)
         order by submitted_at_utc asc
         limit %s
        """,
        (cohort_id, cohort_id, limit),
    )


def ai_decisions(
    conn: psycopg.Connection, cohort_id: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    """AI decisions with their reasoning, so a human can sample the model.

    The tier 2 layer has to be auditable by someone reading its judgments, not
    trusted because it is a model.
    """
    return fetch_all(
        conn,
        """
        select * from v_checkin_resolved
         where decided_by = 'ai'
           and (%s::text is null or cohort_id = %s::text)
         order by decided_at desc
         limit %s
        """,
        (cohort_id, cohort_id, limit),
    )


def unresolved_identities(
    conn: psycopg.Connection, cohort_id: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        """
        select * from identity_unresolved
         where resolved_at is null
           and (%s::text is null or cohort_id = %s::text)
         order by last_seen_at desc
         limit %s
        """,
        (cohort_id, cohort_id, limit),
    )
