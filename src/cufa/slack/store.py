"""Write observations, resolve identity, report counts.

Every write here is ``on conflict (source_event_id) do nothing``. Slack
redelivers events (with an ``X-Slack-Retry-Num`` header), the backfill
re-reads what the bot already saw, and a bot restart re-receives anything that
was in flight — so the same act arriving several times has to produce one row,
and this is where that is enforced rather than hoped for.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import psycopg

from ..db import execute, fetch_all, fetch_one
from ..ingest.common import resolve_identity
from ..logging_setup import get_logger
from .events import Skipped, SlackObservation
from .users import resolve_user

log = get_logger(__name__)


@dataclass(frozen=True)
class WorkspaceInfo:
    team_id: str
    team_name: str
    bot_user_id: str
    cohort_id: str | None


def ensure_workspace(conn: psycopg.Connection, client: Any, cohort_id: str | None) -> WorkspaceInfo:
    """Learn which workspace the token belongs to, and attach it to a cohort.

    ``auth.test`` is the one call that works with any token, so it doubles as
    the connectivity check at startup.
    """
    response = client.auth_test()
    info = WorkspaceInfo(
        team_id=response["team_id"],
        team_name=response.get("team") or response["team_id"],
        bot_user_id=response.get("user_id") or "",
        cohort_id=cohort_id,
    )
    if cohort_id:
        execute(
            conn,
            "insert into cohort (cohort_id, label) values (%s, %s) on conflict do nothing",
            (cohort_id, cohort_id),
        )
    execute(
        conn,
        """
        insert into slack_workspace (team_id, team_name, cohort_id, bot_user_id, last_seen_at)
        values (%s, %s, %s, %s, now())
        on conflict (team_id) do update
           set team_name = excluded.team_name,
               cohort_id = coalesce(excluded.cohort_id, slack_workspace.cohort_id),
               bot_user_id = excluded.bot_user_id,
               last_seen_at = now()
        """,
        (info.team_id, info.team_name, cohort_id, info.bot_user_id),
    )
    return info


def cohort_for_team(conn: psycopg.Connection, team_id: str) -> str | None:
    row = fetch_one(conn, "select cohort_id from slack_workspace where team_id = %s", (team_id,))
    return row["cohort_id"] if row else None


def record(conn: psycopg.Connection, obs: SlackObservation, *, email: str | None, load_id: str | None) -> bool:
    """Insert one observation. True if it was new, False if already recorded."""
    written = execute(
        conn,
        """
        insert into slack_event (
            source_event_id, team_id, event_type, channel_id, channel_type,
            slack_user_id, user_email, message_ts, thread_ts, is_thread_reply,
            reaction, item_user_id, text_length, word_count, has_link,
            has_attachment, text, event_time_utc, raw, load_id
        ) values (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s::jsonb, %s
        )
        on conflict (source_event_id) do nothing
        """,
        (
            obs.source_event_id, obs.team_id, obs.event_type, obs.channel_id, obs.channel_type,
            obs.slack_user_id, email, obs.message_ts, obs.thread_ts, obs.is_thread_reply,
            obs.reaction, obs.item_user_id, obs.text_length, obs.word_count, obs.has_link,
            obs.has_attachment, obs.text, obs.event_time_utc, json.dumps(obs.raw), load_id,
        ),
    )
    return written == 1


@dataclass(frozen=True)
class RecordOutcome:
    written: bool
    skipped: Skipped | None
    email: str | None
    fellow_id: str | None
    event_type: str | None

    @property
    def status(self) -> str:
        if self.skipped:
            return "skipped"
        return "written" if self.written else "duplicate"


def resolve_and_record(
    conn: psycopg.Connection,
    client: Any,
    result: SlackObservation | Skipped,
    *,
    cohort_id: str | None,
    load_id: str | None,
) -> RecordOutcome:
    """The whole per-event path: user → email → row → review queue.

    Order matters. The row is written *before* identity is resolved against
    the roster, so an address that matches nobody still leaves an observation
    behind and lands in ``identity_unresolved`` for a human — the same queue
    the forms feed.
    """
    if isinstance(result, Skipped):
        log.debug("slack event skipped: %s (%s/%s)", result.reason, result.event_type, result.subtype)
        return RecordOutcome(False, result, None, None, result.event_type)

    user = resolve_user(conn, client, result.team_id, result.slack_user_id)
    email = user.email if not user.is_bot else None
    written = record(conn, result, email=email, load_id=load_id)

    fellow_id = None
    if written and user.resolvable and email:
        fellow_id = resolve_identity(conn, cohort_id, email)

    log.debug(
        "slack %s %s channel=%s user=%s written=%s fellow=%s",
        result.event_type, result.source_event_id[:10], result.channel_id,
        result.slack_user_id, written, fellow_id or "-",
    )
    return RecordOutcome(written, None, email, fellow_id, result.event_type)


def touch_load_run(conn: psycopg.Connection, load_id: str, *, read: int = 0, written: int = 0, skipped: int = 0) -> None:
    """Bump a long-running load_run's counters. Cheap; called per event."""
    execute(
        conn,
        """
        update load_run
           set rows_read = rows_read + %s,
               rows_written = rows_written + %s,
               rows_skipped = rows_skipped + %s
         where load_id = %s
        """,
        (read, written, skipped, load_id),
    )


def stats(conn: psycopg.Connection, team_id: str | None = None) -> dict[str, Any]:
    """Counts a human can read at a glance. No addresses."""
    where = "where team_id = %s" if team_id else ""
    params = (team_id,) if team_id else ()

    totals = fetch_one(
        conn,
        f"""
        select count(*)                                              as events,
               count(*) filter (where event_type = 'message')        as messages,
               count(*) filter (where event_type = 'reaction_added') as reactions,
               count(*) filter (where event_type = 'member_joined_channel') as joins,
               count(distinct slack_user_id)                         as distinct_users,
               count(distinct slack_user_id) filter (where user_email is null) as unattributed_users,
               count(distinct channel_id)                            as channels,
               min(event_time_utc)                                   as first_event,
               max(event_time_utc)                                   as last_event,
               max(received_at)                                      as last_received
          from slack_event {where}
        """,
        params,
    ) or {}

    matched = fetch_one(
        conn,
        f"""
        select count(distinct e.slack_user_id) as n
          from slack_event e
          join slack_workspace w on w.team_id = e.team_id
          join fellow f on f.cohort_id = w.cohort_id
                       and lower(f.primary_email) = lower(e.user_email)
          {where.replace('team_id', 'e.team_id')}
        """,
        params,
    ) or {}

    by_type = fetch_all(
        conn,
        f"select event_type, count(*) as n from slack_event {where} group by event_type order by n desc",
        params,
    )
    by_channel = fetch_all(
        conn,
        f"""
        select coalesce(c.name, e.channel_id) as channel, count(*) as n
          from slack_event e
          left join slack_channel c on c.team_id = e.team_id and c.channel_id = e.channel_id
          {where.replace('team_id', 'e.team_id')}
         group by 1 order by n desc limit 12
        """,
        params,
    )
    unresolved = fetch_one(
        conn,
        "select count(*) as n from identity_unresolved where resolved_at is null",
    ) or {}

    return {
        **{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in totals.items()},
        "users_on_roster": matched.get("n", 0),
        "by_type": {r["event_type"]: r["n"] for r in by_type},
        "by_channel": {r["channel"]: r["n"] for r in by_channel},
        "identity_unresolved_open": unresolved.get("n", 0),
    }


def per_fellow(conn: psycopg.Connection, cohort_id: str, *, days: int | None = None) -> list[dict[str, Any]]:
    """Messages and reactions per fellow, roster-joined, unattributed rows kept."""
    since = "and e.event_time_utc >= now() - (%s || ' days')::interval" if days else ""
    params: tuple[Any, ...] = (cohort_id, str(days)) if days else (cohort_id,)
    return fetch_all(
        conn,
        f"""
        select f.fellow_id,
               coalesce(f.full_name, '(not on roster)') as full_name,
               e.user_email,
               count(*) filter (where e.event_type = 'message')        as messages,
               count(*) filter (where e.event_type = 'message' and e.is_thread_reply) as thread_replies,
               count(*) filter (where e.event_type = 'reaction_added') as reactions_given,
               count(distinct e.channel_id) filter (where e.event_type = 'message') as channels,
               count(distinct (e.event_time_utc at time zone 'UTC')::date)
                   filter (where e.event_type in ('message', 'reaction_added')) as active_days
          from slack_event e
          join slack_workspace w on w.team_id = e.team_id
          left join fellow f on f.cohort_id = w.cohort_id
                            and lower(f.primary_email) = lower(e.user_email)
         where w.cohort_id = %s
           and e.event_type in ('message', 'reaction_added')
           {since}
         group by f.fellow_id, f.full_name, e.user_email
         order by messages desc, reactions_given desc, full_name
        """,
        params,
    )
