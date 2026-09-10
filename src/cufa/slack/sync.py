"""Pulling the workspace into the database: members, channels, messages.

Idempotent throughout. Users upsert on their Slack id; messages insert on
``channel:ts`` and collide harmlessly; each channel keeps the ``ts`` of the last
message seen, so a re-run pulls only what is new. The same functions serve the
event-driven path (a ``team_join`` or ``message`` event) and the scheduled pull,
so a missed event is repaired by the next pull rather than lost.

Roster alerts are raised here, at the one place a new account is first seen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import psycopg

from ..db import execute, fetch_all, fetch_one
from ..logging_setup import get_logger, summarize
from .client import SlackClient, SlackMessage, SlackUser
from .events import source_event_id
from .identity import raise_alert

log = get_logger(__name__)

_WORD_RE = re.compile(r"[\w'’-]+", re.UNICODE)
_MENTION_RE = re.compile(r"<[@#!][^>]*>")
_LINK_RE = re.compile(r"<https?://[^>]*>|https?://\S+")


def word_count(text: str) -> int:
    """Words in a message, with mentions and bare links not counted."""
    cleaned = _LINK_RE.sub(" ", _MENTION_RE.sub(" ", text or ""))
    return len(_WORD_RE.findall(cleaned))


@dataclass
class SyncSummary:
    users_seen: int = 0
    users_new: int = 0
    alerts_raised: int = 0
    channels: int = 0
    messages_read: int = 0
    messages_written: int = 0
    new_alert_user_ids: list[str] = field(default_factory=list)

    def __str__(self) -> str:  # pragma: no cover - display only
        return summarize(
            users=self.users_seen,
            new_users=self.users_new,
            alerts=self.alerts_raised,
            channels=self.channels,
            messages_read=self.messages_read,
            messages_written=self.messages_written,
        )


# ---------------------------------------------------------------------------
# members
# ---------------------------------------------------------------------------


def ensure_workspace_row(conn: psycopg.Connection, team_id: str, cohort_id: str | None = None) -> None:
    """The workspace row every Slack table hangs off. Cohort attached if known."""
    execute(
        conn,
        """
        insert into slack_workspace (team_id, team_name, cohort_id, last_seen_at)
        values (%s, %s, %s, now())
        on conflict (team_id) do update
           set cohort_id = coalesce(slack_workspace.cohort_id, excluded.cohort_id),
               last_seen_at = now()
        """,
        (team_id, team_id, cohort_id),
    )


def upsert_user(
    conn: psycopg.Connection, user: SlackUser, *, joined_at: datetime | None = None, team_id: str | None = None
) -> bool:
    """Write one member. Returns True when the row is new."""
    team = user.team_id or team_id or "T-unknown"
    ensure_workspace_row(conn, team)
    row = fetch_one(
        conn,
        """
        insert into slack_user (
            team_id, slack_user_id, email, display_name, real_name, tz, tz_offset_s,
            is_admin, is_bot, is_deleted, joined_at_utc, raw, fetched_at
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, now())
        on conflict (team_id, slack_user_id) do update
           set email        = coalesce(excluded.email, slack_user.email),
               display_name = excluded.display_name,
               real_name    = excluded.real_name,
               tz           = coalesce(excluded.tz, slack_user.tz),
               tz_offset_s  = coalesce(excluded.tz_offset_s, slack_user.tz_offset_s),
               is_admin     = excluded.is_admin,
               is_bot       = excluded.is_bot,
               is_deleted   = excluded.is_deleted,
               joined_at_utc = coalesce(slack_user.joined_at_utc, excluded.joined_at_utc),
               last_seen_at = now(),
               fetched_at   = now(),
               raw          = excluded.raw
        returning (xmax = 0) as inserted
        """,
        (
            team,
            user.id,
            user.email,
            user.display_name,
            user.real_name,
            user.tz,
            user.tz_offset,
            user.is_admin,
            user.is_bot,
            user.deleted,
            joined_at,
            _json(user.raw),
        ),
    )
    return bool(row and row["inserted"])


def _json(value: Any) -> str:
    import json

    try:
        return json.dumps(value, default=str)
    except TypeError:
        return "{}"


def is_rostered(conn: psycopg.Connection, slack_user_id: str) -> bool:
    row = fetch_one(
        conn, "select fellow_id from v_slack_user_resolved where slack_user_id = %s", (slack_user_id,)
    )
    return bool(row and row["fellow_id"])


def observe_user(
    conn: psycopg.Connection,
    user: SlackUser,
    *,
    joined_at: datetime | None = None,
    summary: SyncSummary | None = None,
    staff_emails: tuple[str, ...] = (),
    team_id: str | None = None,
) -> bool:
    """Record a member and raise a roster alert if nobody knows them.

    Bots, deleted accounts, workspace admins and configured staff addresses
    never alert: staff are expected to be in the workspace and not on the
    roster.
    """
    summary = summary or SyncSummary()
    inserted = upsert_user(conn, user, joined_at=joined_at, team_id=team_id)
    summary.users_seen += 1
    if inserted:
        summary.users_new += 1
    if user.is_bot or user.deleted or user.is_admin or (user.email and user.email in staff_emails):
        return inserted
    if not is_rostered(conn, user.id):
        if raise_alert(conn, user.id, user.email):
            summary.alerts_raised += 1
            summary.new_alert_user_ids.append(user.id)
            log.info("roster alert raised for slack user %s", user.id)
    return inserted


def sync_users(
    conn: psycopg.Connection,
    client: SlackClient,
    *,
    summary: SyncSummary | None = None,
    staff_emails: tuple[str, ...] = (),
) -> SyncSummary:
    summary = summary or SyncSummary()
    for user in client.list_users():
        observe_user(conn, user, summary=summary, staff_emails=staff_emails, team_id=client.team_id)
    return summary


# ---------------------------------------------------------------------------
# channels and messages
# ---------------------------------------------------------------------------


def _is_staff_channel(channel: Any, configured: str | None) -> bool:
    """Whether this channel is the staff one, named by id OR by name.

    By name matters: `is_staff` is what keeps staff conversation out of every
    fellow-facing count, and somebody configuring `#staff` rather than
    `C0123456789` must not silently get a channel that is counted as
    participation.
    """
    if not configured:
        return False
    wanted = configured.strip().lstrip("#").lower()
    return channel.id.lower() == wanted or (channel.name or "").lower() == wanted


def sync_channels(conn: psycopg.Connection, client: SlackClient, *, staff_channel: str | None = None) -> int:
    team = client.team_id
    ensure_workspace_row(conn, team)
    n = 0
    for channel in client.list_channels():
        execute(
            conn,
            """
            insert into slack_channel (team_id, channel_id, name, is_private, is_member, is_staff, fetched_at)
            values (%s, %s, %s, %s, %s, %s, now())
            on conflict (team_id, channel_id) do update
               set name = excluded.name,
                   is_private = excluded.is_private,
                   is_member = excluded.is_member,
                   is_staff = slack_channel.is_staff or excluded.is_staff,
                   fetched_at = now()
            """,
            (team, channel.id, channel.name, channel.is_private, channel.is_member,
             _is_staff_channel(channel, staff_channel)),
        )
        n += 1
    return n


def mark_staff_channel(conn: psycopg.Connection, channel_id: str, *, is_staff: bool = True, team_id: str = "T-unknown") -> None:
    ensure_workspace_row(conn, team_id)
    execute(
        conn,
        """
        insert into slack_channel (team_id, channel_id, is_staff) values (%s, %s, %s)
        on conflict (team_id, channel_id) do update set is_staff = excluded.is_staff
        """,
        (team_id, channel_id, is_staff),
    )


def record_message(
    conn: psycopg.Connection, message: SlackMessage, *, team_id: str, store_text: bool = False
) -> bool:
    """Write one message into ``slack_event``. Returns True when new.

    The same idempotency key the live bot and the backfill use, so a message
    seen by any of the three paths is one row. Text is stored only when
    ``CUFA_SLACK_STORE_TEXT`` says so (ADR-031). Bot and system posts are skipped.
    """
    if not message.user or message.subtype in ("bot_message", "channel_join", "channel_leave"):
        return False
    ensure_workspace_row(conn, team_id)
    execute(
        conn,
        "insert into slack_channel (team_id, channel_id) values (%s, %s) on conflict do nothing",
        (team_id, message.channel_id),
    )
    email = (fetch_one(conn, "select email from slack_user where slack_user_id = %s", (message.user,)) or {}).get("email")
    is_reply = bool(message.thread_ts and message.thread_ts != message.ts)
    row = fetch_one(
        conn,
        """
        insert into slack_event (
            source_event_id, team_id, event_type, channel_id, slack_user_id, user_email,
            message_ts, thread_ts, is_thread_reply, text_length, word_count, has_link, text,
            event_time_utc, raw
        )
        values (%s, %s, 'message', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        on conflict (source_event_id) do nothing
        returning slack_event_id
        """,
        (
            source_event_id(team_id, message.channel_id, "message", message.ts),
            team_id,
            message.channel_id,
            message.user,
            email,
            message.ts,
            message.thread_ts,
            is_reply,
            len(message.text or ""),
            word_count(message.text),
            bool(_LINK_RE.search(message.text or "")),
            message.text if store_text else None,
            message.posted_at,
            _json({"source": "sync", "subtype": message.subtype}),
        ),
    )
    return row is not None


def sync_messages(
    conn: psycopg.Connection, client: SlackClient, *, summary: SyncSummary | None = None, store_text: bool = False
) -> SyncSummary:
    """Pull new history for every tracked channel the bot is in.

    Shares ``backfilled_through_ts`` with ``cufa slack backfill``: whichever
    ran last, the next one starts where it stopped.
    """
    summary = summary or SyncSummary()
    team = client.team_id
    channels = fetch_all(
        conn,
        "select channel_id, backfilled_through_ts from slack_channel where team_id = %s and tracked and coalesce(is_member, false) order by channel_id",
        (team,),
    )
    for channel in channels:
        latest = channel["backfilled_through_ts"]
        for message in client.channel_history(channel["channel_id"], oldest=latest):
            summary.messages_read += 1
            if record_message(conn, message, team_id=team, store_text=store_text):
                summary.messages_written += 1
            if latest is None or float(message.ts) > float(latest):
                latest = message.ts
        execute(
            conn,
            "update slack_channel set backfilled_through_ts = %s, synced_at = now() where team_id = %s and channel_id = %s",
            (latest, team, channel["channel_id"]),
        )
        summary.channels += 1
    return summary


def sync_all(
    conn: psycopg.Connection,
    client: SlackClient,
    *,
    staff_channel: str | None = None,
    staff_emails: tuple[str, ...] = (),
    store_text: bool = False,
    cohort_id: str | None = None,
    messages: bool = True,
) -> SyncSummary:
    """Members, channels and — unless the live bot is capturing them — messages.

    ``messages=False`` is what the bot's own scheduler passes: the live event
    handler writes each message as it arrives and runs the Q&A logic on it, so
    a pull that raced ahead of it would claim the row first and silence that.
    From cron with no bot running, leave it on.
    """
    summary = SyncSummary()
    ensure_workspace_row(conn, client.team_id, cohort_id)
    sync_users(conn, client, summary=summary, staff_emails=staff_emails)
    sync_channels(conn, client, staff_channel=staff_channel)
    if messages:
        sync_messages(conn, client, summary=summary, store_text=store_text)
    log.info("slack sync %s", summary)
    return summary


def last_data_received(conn: psycopg.Connection, cohort_id: str | None = None) -> dict[str, datetime | None]:
    """When each source last produced a row. The dashboard's "last updated"."""
    scope = "where (%s::text is null or cohort_id = %s::text)"
    return {
        "slack_message": (fetch_one(conn, "select max(event_time_utc) as t from slack_event where event_type = 'message'") or {}).get("t"),
        "slack_sync": (fetch_one(conn, "select greatest(max(synced_at), max(fetched_at)) as t from slack_channel") or {}).get("t"),
        "part_a": (fetch_one(conn, f"select max(ingested_at) as t from v_checkin_resolved {scope}", (cohort_id, cohort_id)) or {}).get("t"),
        "part_b": (fetch_one(conn, f"select max(ingested_at) as t from v_checkin_b_resolved {scope}", (cohort_id, cohort_id)) or {}).get("t"),
    }


def seen_event(conn: psycopg.Connection, event_id: str | None, event_type: str) -> bool:
    """True when this Slack event was already handled. Slack retries deliveries."""
    if not event_id:
        return False
    row = fetch_one(
        conn,
        "insert into slack_event_log (event_id, event_type) values (%s, %s) on conflict do nothing returning event_id",
        (event_id, event_type),
    )
    return row is None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "SyncSummary",
    "ensure_workspace_row",
    "is_rostered",
    "last_data_received",
    "mark_staff_channel",
    "observe_user",
    "record_message",
    "seen_event",
    "sync_all",
    "sync_channels",
    "sync_messages",
    "sync_users",
    "upsert_user",
    "word_count",
]
