"""Read-time signals over the Slack observation stream.

Every function here is a query over ``slack_event``; nothing is precomputed
and nothing is written. Each one is shaped by a rule about what it must NOT
say, and those rules are the point, so they are stated up front:

* **Received recognition is never ranked.** Who replies to whom and who was
  mentioned are recorded on the rows. They are read here for one purpose:
  finding the fellow nobody talks to. ``reply_graph`` returns an alphabetical
  list of people with no inbound reply and no inbound mention, and edge counts
  ordered by the *giver*. There is no "most replied to" and no "mentions
  received" column, and a test fails if one appears.
* **Emoji use is a cohort mood, not a personal one.** ``emoji_mood`` takes a
  cohort and a window and nothing else. Its SQL has no user column and a test
  asserts that stays true.
* **Time-of-day is read in the fellow's own zone**, and it exists to make the
  bot arrive when people are around, not to notice who is up late.
* **Poll results are option totals.** Who voted for what is recorded — it is
  participation — and is never listed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Any

import psycopg

from ..db import fetch_all, fetch_one

# The acts that count as "being around" for the rhythm signal.
_PRESENCE_TYPES = "('message', 'reaction_added', 'huddle_joined', 'poll_vote', 'canvas_comment')"


def _window(days: int | None) -> tuple[str, tuple[Any, ...]]:
    if days:
        return "and e.event_time_utc >= now() - (%s || ' days')::interval", (str(days),)
    return "", ()


# ---------------------------------------------------------------------------
# who replies to whom
# ---------------------------------------------------------------------------

def reply_graph(conn: psycopg.Connection, cohort_id: str, *, days: int | None = 28) -> dict[str, Any]:
    """Thread-reply edges between fellows, and who has none coming in.

    ``edges`` is ordered by the replier — the person doing the talking — and
    then by whom they replied to. ``not_replied_to`` is every active roster
    fellow with no inbound reply AND no inbound mention in the window, in
    alphabetical order, with whether they posted at all so a silent person
    and an ignored person can be told apart. Neither list is a ranking.
    """
    since, params = _window(days)
    edges = fetch_all(
        conn,
        f"""
        select coalesce(rf.fellow_id, '—')                as replier_id,
               coalesce(rf.full_name, e.replier_email, e.replier_user_id) as replier,
               coalesce(pf.fellow_id, '—')                as parent_id,
               coalesce(pf.full_name, e.parent_email, e.parent_user_id)   as replied_to,
               count(*)                                    as replies
          from slack_reply_edge e
          left join fellow rf on rf.cohort_id = e.cohort_id
                             and lower(rf.primary_email) = lower(e.replier_email)
          left join fellow pf on pf.cohort_id = e.cohort_id
                             and lower(pf.primary_email) = lower(e.parent_email)
         where e.cohort_id = %s
           {since}
         group by 1, 2, 3, 4
         order by replier, replied_to
        """,
        (cohort_id, *params),
    )

    # Inbound is computed only to test for zero. It is not returned.
    isolated = fetch_all(
        conn,
        f"""
        with fellows as (
            select f.fellow_id, f.full_name, lower(f.primary_email) as email
              from fellow f
             where f.cohort_id = %s and f.status = 'active'
        ),
        ids as (
            select fe.fellow_id, u.slack_user_id
              from fellows fe
              join slack_workspace w on w.cohort_id = %s
              join slack_user u on u.team_id = w.team_id and lower(u.email) = fe.email
        ),
        replied as (
            select distinct lower(e.parent_email) as email
              from slack_reply_edge e
             where e.cohort_id = %s {since}
        ),
        mentioned as (
            select distinct m.uid as slack_user_id
              from slack_event e
              join slack_workspace w on w.team_id = e.team_id
              cross join lateral unnest(e.mentions) as m(uid)
             where w.cohort_id = %s and e.event_type = 'message' {since}
        ),
        posted as (
            select distinct lower(e.user_email) as email
              from slack_event e
              join slack_workspace w on w.team_id = e.team_id
             where w.cohort_id = %s and e.event_type = 'message' {since}
        )
        select fe.fellow_id, fe.full_name,
               exists (select 1 from posted p where p.email = fe.email) as posted
          from fellows fe
         where not exists (select 1 from replied r where r.email = fe.email)
           and not exists (
               select 1 from ids i join mentioned m on m.slack_user_id = i.slack_user_id
                where i.fellow_id = fe.fellow_id
           )
         order by fe.full_name, fe.fellow_id
        """,
        (cohort_id, cohort_id, cohort_id, *params, cohort_id, *params, cohort_id, *params),
    )
    return {"days": days, "edges": edges, "not_replied_to": isolated}


# ---------------------------------------------------------------------------
# mentions given
# ---------------------------------------------------------------------------

def mentions_given(conn: psycopg.Connection, cohort_id: str, *, days: int | None = 28) -> list[dict[str, Any]]:
    """How often each fellow @-mentions somebody, and how many different people.

    Given only. Mentions *received* are on the rows and are not summed
    anywhere — see the module docstring.
    """
    since, params = _window(days)
    return fetch_all(
        conn,
        f"""
        select f.fellow_id,
               coalesce(f.full_name, '(not on roster)') as full_name,
               e.user_email,
               count(*) filter (where cardinality(e.mentions) > 0) as messages_with_mentions,
               coalesce(sum(cardinality(e.mentions)), 0)            as mentions_given,
               count(distinct m.uid)                                as people_mentioned
          from slack_event e
          join slack_workspace w on w.team_id = e.team_id
          left join fellow f on f.cohort_id = w.cohort_id
                            and lower(f.primary_email) = lower(e.user_email)
          left join lateral unnest(e.mentions) as m(uid) on true
         where w.cohort_id = %s
           and e.event_type = 'message'
           and e.mentions is not null
           {since}
         group by f.fellow_id, f.full_name, e.user_email
        having coalesce(sum(cardinality(e.mentions)), 0) > 0
         order by full_name, f.fellow_id
        """,
        (cohort_id, *params),
    )


# ---------------------------------------------------------------------------
# huddles and canvases
# ---------------------------------------------------------------------------

def huddles(conn: psycopg.Connection, cohort_id: str, *, days: int | None = 28) -> dict[str, Any]:
    """Huddle joins per fellow (an act, like a message) and cohort totals."""
    since, params = _window(days)
    totals = fetch_one(
        conn,
        f"""
        select count(*) filter (where e.event_type = 'huddle_joined') as joins,
               count(*) filter (where e.event_type = 'huddle_left')   as leaves,
               count(distinct e.call_id)                             as huddles,
               count(distinct e.slack_user_id)
                   filter (where e.event_type = 'huddle_joined')     as people
          from slack_event e
          join slack_workspace w on w.team_id = e.team_id
         where w.cohort_id = %s and e.event_type in ('huddle_joined', 'huddle_left')
           {since}
        """,
        (cohort_id, *params),
    ) or {}
    per_fellow = fetch_all(
        conn,
        f"""
        select f.fellow_id,
               coalesce(f.full_name, '(not on roster)') as full_name,
               count(*) filter (where e.event_type = 'huddle_joined') as joins,
               count(distinct e.call_id)                             as huddles
          from slack_event e
          join slack_workspace w on w.team_id = e.team_id
          left join fellow f on f.cohort_id = w.cohort_id
                            and lower(f.primary_email) = lower(e.user_email)
         where w.cohort_id = %s and e.event_type = 'huddle_joined'
           {since}
         group by f.fellow_id, f.full_name
         order by full_name, f.fellow_id
        """,
        (cohort_id, *params),
    )
    return {"days": days, **totals, "per_fellow": per_fellow}


def canvases(conn: psycopg.Connection, cohort_id: str, *, days: int | None = 28) -> dict[str, Any]:
    """Canvas activity for the cohort. Cohort-level: an edit names no editor."""
    since, params = _window(days)
    row = fetch_one(
        conn,
        f"""
        select count(*) filter (where e.event_type = 'canvas_created') as created,
               count(*) filter (where e.event_type = 'canvas_edited')  as edits,
               count(*) filter (where e.event_type = 'canvas_shared')  as shares,
               count(*) filter (where e.event_type = 'canvas_comment') as comments,
               count(distinct e.file_id)                               as canvases,
               max(e.event_time_utc)                                   as last_activity
          from slack_event e
          join slack_workspace w on w.team_id = e.team_id
         where w.cohort_id = %s and e.event_type like 'canvas%%'
           {since}
        """,
        (cohort_id, *params),
    ) or {}
    return {"days": days, **row}


# ---------------------------------------------------------------------------
# emoji, as a cohort mood
# ---------------------------------------------------------------------------

def emoji_mood(conn: psycopg.Connection, cohort_id: str, *, days: int | None = 28, top: int = 15) -> dict[str, Any]:
    """Which reactions the cohort reaches for, and how that moves week to week.

    Takes a cohort and a window. Deliberately NOT a person: there is no user
    column in either query, and ``tests/test_slack_signals.py`` reads this
    function's SQL and fails if one appears.
    """
    since, params = _window(days)
    overall = fetch_all(
        conn,
        f"""
        select e.reaction, count(*) as n
          from slack_event e
          join slack_workspace w on w.team_id = e.team_id
         where w.cohort_id = %s and e.event_type = 'reaction_added'
           {since}
         group by e.reaction
         order by n desc, e.reaction
         limit %s
        """,
        (cohort_id, *params, top),
    )
    total = sum(r["n"] for r in overall) or 0
    for r in overall:
        r["share"] = round(r["n"] / total, 3) if total else 0.0
    weekly = fetch_all(
        conn,
        f"""
        select date_trunc('week', e.event_time_utc)::date as week,
               e.reaction, count(*) as n
          from slack_event e
          join slack_workspace w on w.team_id = e.team_id
         where w.cohort_id = %s and e.event_type = 'reaction_added'
           {since}
         group by 1, 2
         order by 1, n desc, e.reaction
        """,
        (cohort_id, *params),
    )
    by_week: dict[str, list[dict[str, Any]]] = {}
    for r in weekly:
        by_week.setdefault(str(r["week"]), []).append({"reaction": r["reaction"], "n": r["n"]})
    return {"days": days, "total_reactions": total, "top": overall, "by_week": by_week}


# ---------------------------------------------------------------------------
# channel liveness
# ---------------------------------------------------------------------------

def channel_liveness(conn: psycopg.Connection, team_id: str, *, quiet_days: int = 7) -> list[dict[str, Any]]:
    """Which channels are alive, which have gone quiet, which are silent.

    ``alive``: a message in the last ``quiet_days``. ``quiet``: one in the
    last 30 days but not since. ``silent``: nothing in 30 days. Posters is a
    count of distinct people, never who.
    """
    rows = fetch_all(
        conn,
        """
        select c.channel_id,
               coalesce(c.name, c.channel_id)                       as name,
               c.is_private,
               count(e.*) filter (where e.event_time_utc >= now() - (%s || ' days')::interval) as messages_recent,
               count(e.*) filter (where e.event_time_utc >= now() - interval '30 days')       as messages_30d,
               count(distinct e.slack_user_id)
                   filter (where e.event_time_utc >= now() - (%s || ' days')::interval)         as posters_recent,
               max(e.event_time_utc)                                as last_message_at
          from slack_channel c
          left join slack_event e on e.team_id = c.team_id and e.channel_id = c.channel_id
                                 and e.event_type = 'message'
         where c.team_id = %s and c.is_member is distinct from false
         group by c.channel_id, c.name, c.is_private
         order by last_message_at desc nulls last, name
        """,
        (str(quiet_days), str(quiet_days), team_id),
    )
    for r in rows:
        if r["messages_recent"]:
            r["status"] = "alive"
        elif r["messages_30d"]:
            r["status"] = "quiet"
        else:
            r["status"] = "silent"
    return rows


# ---------------------------------------------------------------------------
# when people are around
# ---------------------------------------------------------------------------

def activity_rhythm(
    conn: psycopg.Connection, cohort_id: str, *, days: int | None = 28, default_zone: str = "America/New_York"
) -> dict[str, Any]:
    """Hour-of-day and day-of-week histograms, each act read in its own
    fellow's zone. Cohort totals — this says when the cohort is around, so
    the bot can arrive then. Weekday 0 is Monday."""
    since, params = _window(days)
    rows = fetch_all(
        conn,
        f"""
        select extract(hour from local_t)::int                as hour,
               ((extract(dow from local_t)::int + 6) %% 7)     as weekday,
               count(*)                                        as n
          from (
              select e.event_time_utc at time zone
                     coalesce(p.timezone, f.timezone, %s) as local_t
                from slack_event e
                join slack_workspace w on w.team_id = e.team_id
                left join fellow f on f.cohort_id = w.cohort_id
                                  and lower(f.primary_email) = lower(e.user_email)
                left join fellow_reminder_preference p on p.fellow_id = f.fellow_id
               where w.cohort_id = %s and e.event_type in {_PRESENCE_TYPES}
                 {since}
          ) t
         group by 1, 2
        """,
        (default_zone, cohort_id, *params),
    )
    by_hour = [0] * 24
    by_weekday = [0] * 7
    for r in rows:
        by_hour[r["hour"]] += r["n"]
        by_weekday[r["weekday"]] += r["n"]
    total = sum(by_hour)
    return {
        "days": days,
        "acts": total,
        "by_hour": by_hour,
        "by_weekday": by_weekday,
        # Only buckets with something in them: three busiest hours out of one
        # active hour is one hour, not one hour and two zeros.
        "busiest_hours": sorted((h for h in range(24) if by_hour[h]), key=lambda h: -by_hour[h])[:3],
        "busiest_weekdays": sorted((d for d in range(7) if by_weekday[d]), key=lambda d: -by_weekday[d])[:2],
    }


@dataclass(frozen=True)
class ObservedQuietHours:
    """The stretch of local clock a fellow is reliably NOT around."""

    start: time
    end: time
    acts: int


def quiet_hours_from_histogram(by_hour: list[int], *, min_acts: int, min_gap_hours: int = 4) -> ObservedQuietHours | None:
    """The longest circular run of empty hours, if it is long enough to trust.

    Pure, so it can be tested without a database. Returns ``None`` when there
    are too few acts to say anything, or no gap of ``min_gap_hours`` — in
    which case the deployment's default quiet hours stand.
    """
    total = sum(by_hour)
    if total < min_acts:
        return None
    active = [h for h in range(24) if by_hour[h] > 0]
    if not active or len(active) == 24:
        return None
    best_len, best_start = 0, 0
    for i, h in enumerate(active):
        nxt = active[(i + 1) % len(active)]
        gap = (nxt - h - 1) % 24
        if gap > best_len:
            best_len, best_start = gap, (h + 1) % 24
    if best_len < min_gap_hours:
        return None
    return ObservedQuietHours(time(best_start), time((best_start + best_len) % 24), total)


def observed_quiet_hours(
    conn: psycopg.Connection,
    team_id: str,
    email: str,
    zone: str,
    *,
    days: int = 28,
    min_acts: int = 20,
) -> ObservedQuietHours | None:
    """When is this one fellow not around, judged from what they have done.

    Read in the fellow's zone. Used by the reminder engine in place of the
    deployment default quiet hours — and only there, and only when the fellow
    has not set their own. It is not reported anywhere.
    """
    rows = fetch_all(
        conn,
        f"""
        select extract(hour from e.event_time_utc at time zone %s)::int as hour, count(*) as n
          from slack_event e
         where e.team_id = %s and lower(e.user_email) = lower(%s)
           and e.event_type in {_PRESENCE_TYPES}
           and e.event_time_utc >= now() - (%s || ' days')::interval
         group by 1
        """,
        (zone, team_id, email, str(days)),
    )
    by_hour = [0] * 24
    for r in rows:
        by_hour[r["hour"]] += r["n"]
    return quiet_hours_from_histogram(by_hour, min_acts=min_acts)


# ---------------------------------------------------------------------------
# polls
# ---------------------------------------------------------------------------

def poll_results(conn: psycopg.Connection, poll_id: str) -> dict[str, Any] | None:
    """Option totals for one poll, counting each person's latest vote once."""
    poll = fetch_one(conn, "select * from slack_poll where poll_id = %s", (poll_id,))
    if not poll:
        return None
    rows = fetch_all(
        conn,
        """
        select poll_choice, count(*) as n
          from (
              select distinct on (slack_user_id) slack_user_id, poll_choice
                from slack_event
               where event_type = 'poll_vote' and poll_id = %s
               order by slack_user_id, event_time_utc desc
          ) latest
         group by poll_choice
        """,
        (poll_id,),
    )
    counts = {r["poll_choice"]: r["n"] for r in rows}
    options = list(poll["options"] or [])
    return {
        "poll_id": str(poll["poll_id"]),
        "question": poll["question"],
        "channel_id": poll["channel_id"],
        "created_at": poll["created_at"],
        "closed_at": poll["closed_at"],
        "results": [{"option": o, "votes": counts.get(o, 0)} for o in options],
        "voters": sum(counts.values()),
    }


def list_polls(conn: psycopg.Connection, team_id: str | None = None) -> list[dict[str, Any]]:
    where = "where team_id = %s" if team_id else ""
    return fetch_all(
        conn,
        f"select poll_id, team_id, channel_id, question, options, created_at, closed_at from slack_poll {where} order by created_at desc",
        (team_id,) if team_id else (),
    )


def all_insights(
    conn: psycopg.Connection, *, cohort_id: str, team_id: str | None, days: int | None, quiet_days: int, default_zone: str
) -> dict[str, Any]:
    """Every section, for the CLI and the bot's ``/insights``."""
    return {
        "cohort_id": cohort_id,
        "days": days,
        "reply_graph": reply_graph(conn, cohort_id, days=days),
        "mentions_given": mentions_given(conn, cohort_id, days=days),
        "huddles": huddles(conn, cohort_id, days=days),
        "canvases": canvases(conn, cohort_id, days=days),
        "emoji_mood": emoji_mood(conn, cohort_id, days=days),
        "channel_liveness": channel_liveness(conn, team_id, quiet_days=quiet_days) if team_id else [],
        "rhythm": activity_rhythm(conn, cohort_id, days=days, default_zone=default_zone),
        "polls": [poll_results(conn, str(p["poll_id"])) for p in list_polls(conn, team_id)],
    }
