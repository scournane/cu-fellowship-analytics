"""The terminal report and the queries behind the review screens.

Every number here is counted from ``v_checkin_resolved`` and
``v_checkin_b_resolved`` — that is, from the current decision joined to the
roster at read time. Nothing is precomputed and stored, so correcting a roster
entry or overriding a decision changes the report on the next run with no
rebuild step.

**``help_request`` is not read here, and must never be.** Design invariant 1:
asking for help never lowers any participation signal, so the table takes no
part in any count, rate, score or aggregate, and appears in no report or export.
``EXPORT_PATHS`` below names every function in this codebase that produces
output for consumption outside the console, and there is a test that runs each
of them against a database containing a help request and asserts nothing from it
comes out — and a second test that inspects the SQL each of them actually
executes. Convention is not the enforcement; those two tests are.

Free text is **counted, never graded**. The Part B block reports how many
substantive answers arrived, not how good they were. Rating writing quality
would penalise ESL and neurodivergent fellows for reasons unrelated to
engagement.
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
    #: Part B rollups. A separate block rather than merged columns, because Part
    #: A and Part B are independent observations: a fellow may submit either, and
    #: interleaving them invites reading one as evidence for the other.
    part_b: dict[str, Any] = field(default_factory=dict)
    confidence: list[dict[str, Any]] = field(default_factory=list)

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
          left join session_form sf
                 on sf.session_id = s.session_id and sf.part = 'a'
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

    part_b = part_b_summary(conn, cohort_id)

    from .confidence import trend as confidence_trend

    return CohortReport(
        cohort_id=cohort_id,
        sessions=sessions,
        fellows=fellows,
        totals=totals,
        review=review,
        provisioning=provisioning,
        part_b=part_b,
        confidence=confidence_trend(conn, cohort_id),
    )


def part_b_summary(conn: psycopg.Connection, cohort_id: str) -> dict[str, Any]:
    """Part B rollups for one cohort.

    Counts of what arrived, never a rating of it. ``substantive_takeaways`` is a
    count of answers with any non-whitespace content — the whole test — because
    counting free text is fair and grading it is not.
    """
    totals = fetch_one(
        conn,
        """
        select
            count(*)                                                  as responses,
            count(distinct fellow_id) filter (where fellow_id is not null)
                                                                      as fellows,
            count(*) filter (where fellow_id is null)                 as unknown_email,
            count(*) filter (where confidence_raw is not null)         as confidence_answered,
            count(*) filter (where extra_fields ? '_confidence_rejected_raw')
                                                                      as confidence_rejected,
            count(*) filter (where has_takeaway)                       as substantive_takeaways,
            count(*) filter (where has_rotating_answer)                as rotating_answered,
            count(*) filter (where has_shoutout)                       as shoutouts_given
          from v_checkin_b_resolved
         where cohort_id = %s
        """,
        (cohort_id,),
    ) or {}

    rotating = fetch_all(
        conn,
        """
        select rotating_kind, count(*) as n
          from v_checkin_b_resolved
         where cohort_id = %s and rotating_kind is not null
         group by rotating_kind
         order by rotating_kind
        """,
        (cohort_id,),
    )

    shoutouts = fetch_one(
        conn,
        """
        select
            count(*)                                             as names,
            count(*) filter (where p.match_method = 'exact_name') as auto_linked,
            count(*) filter (where p.match_method = 'manual')     as manually_linked,
            count(*) filter (where p.match_method = 'unresolved') as unresolved
          from peer_shoutout p
          join v_checkin_b_resolved v on v.checkin_b_id = p.checkin_b_id
         where v.cohort_id = %s
        """,
        (cohort_id,),
    ) or {}

    themes = fetch_one(
        conn,
        """
        select count(*) as themes
          from muddiest_theme t
          join "session" s on s.session_id = t.session_id
         where s.cohort_id = %s and t.superseded_at is null
        """,
        (cohort_id,),
    ) or {}

    # Both parts, per session, side by side — joined on the session, never
    # merged. A blank in one column and a number in the other is legal data.
    per_session = fetch_all(
        conn,
        """
        select s.session_id, s.title, s.week_index, s.scheduled_at_utc,
               fb.form_id as b_form_id,
               fb.publish_verified_at is not null as b_form_ready,
               (select count(*) from checkin  c where c.session_id = s.session_id) as part_a,
               (select count(*) from checkin_b b where b.session_id = s.session_id) as part_b
          from "session" s
          left join session_form fb
                 on fb.session_id = s.session_id and fb.part = 'b'
         where s.cohort_id = %s
         order by s.scheduled_at_utc
        """,
        (cohort_id,),
    )

    return {
        "totals": totals,
        "rotating": rotating,
        "shoutouts": shoutouts,
        "themes": themes.get("themes", 0),
        "sessions": per_session,
    }


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
    add("Review queue")
    add(f"  needs_review check-ins   {t.get('needs_review', 0):>5}")
    add(f"  unresolved addresses     {len(unresolved):>5}")
    add(f"  ai cache entries         {(report.review.get('ai_cache') or {}).get('entries', 0):>5}")
    add("")
    add("Latency is recorded, not interpreted: no thresholds and no flags are")
    add("applied to it. Where a session has no announced_at_utc, T0 is the")
    add("earliest matched submission, so its first submitter reads 0s.")
    add("")
    lines.extend(_part_b_lines(report))
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


def _part_b_lines(report: CohortReport) -> list[str]:
    """The Part B block. Short and explicit when nothing has been collected yet."""
    block = report.part_b or {}
    totals = block.get("totals") or {}
    if not totals.get("responses"):
        return ["Part B — end-of-session check-in", "  (no responses yet)"]

    lines: list[str] = []
    add = lines.append

    add("Part B — end-of-session check-in")
    add(f"  responses               {totals.get('responses', 0):>6}")
    add(f"    distinct fellows      {totals.get('fellows', 0):>6}")
    add(f"    email not on roster   {totals.get('unknown_email', 0):>6}")
    add("")
    add("  Confidence (1-7, stored raw — never rescaled)")
    add(f"    answered              {totals.get('confidence_answered', 0):>6}")
    if totals.get("confidence_rejected"):
        add(f"    out of range          {totals.get('confidence_rejected', 0):>6}")
        add("      stored NULL with the raw value in extra_fields. Never clamped.")
    add("")
    add("  Free text (counted, never graded)")
    add(f"    takeaways with content{totals.get('substantive_takeaways', 0):>6}")
    add(f"    rotating answered     {totals.get('rotating_answered', 0):>6}")
    for row in block.get("rotating") or []:
        add(f"      {row['rotating_kind']:<20}{row['n']:>6}")
    add("")

    shoutouts = block.get("shoutouts") or {}
    add("  Peer shoutouts (collected and resolved; no ranking, by design)")
    add(f"    names extracted       {shoutouts.get('names', 0):>6}")
    add(f"    linked automatically  {shoutouts.get('auto_linked', 0):>6}")
    add(f"    linked by a human     {shoutouts.get('manually_linked', 0):>6}")
    add(f"    unresolved            {shoutouts.get('unresolved', 0):>6}")
    add("      ambiguous, or a name not on the roster. Both are legal.")
    add("")
    add(f"  Muddiest-point themes   {block.get('themes', 0):>6}")
    add("")

    add("  Both parts per session — joined, never merged")
    add(f"    {'wk':>3} {'session':<34}{'A':>6}{'B':>6}")
    for row in block.get("sessions") or []:
        week = row["week_index"]
        add(
            f"    {(week if week is not None else '—'):>3} "
            f"{(row['title'] or '')[:33]:<34}{row['part_a']:>6}{row['part_b']:>6}"
        )
    add("")
    add("  A fellow may submit one part and not the other. Both are valid data,")
    add("  and neither is ever used to backfill the other.")
    add("")
    add("  The help checkbox appears nowhere above. It is excluded from every")
    add("  count, rate and aggregate, permanently — see docs/safeguarding.md.")
    return lines


#: Every function that produces output for consumption outside the console.
#: Named here so the safeguarding tests can enumerate them rather than trusting
#: that someone remembered to check a new one. Adding an export means adding it
#: here; the tests run each against a database containing a help request and
#: assert nothing from it comes out, and separately inspect the SQL each one
#: actually executes.
EXPORT_PATHS: tuple[str, ...] = (
    "cufa.report.cohort_report",
    "cufa.report.render_report_text",
    "cufa.report.part_b_summary",
    "cufa.report.needs_review_queue",
    "cufa.report.ai_decisions",
    "cufa.report.unresolved_identities",
    "cufa.confidence.trend",
    "cufa.confidence.by_fellow",
    "cufa.confidence.for_session",
    "cufa.confidence.straightliners",
    "cufa.confidence.render_trend_text",
    "cufa.themes.current_themes",
    "cufa.themes.muddiest_answers",
    "cufa.shoutouts.review_queue",
    # The HTML report and the queries only it makes.
    "cufa.report_html.render_report_html",
    "cufa.report_html.fellow_grid",
    "cufa.report_html.slack_summary",
    "cufa.report_html.provenance",
)
