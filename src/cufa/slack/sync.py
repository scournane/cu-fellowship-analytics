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


def upsert_user(
    conn: psycopg.Connection, user: SlackUser, *, joined_at: datetime | None = None
) -> bool:
    """Write one member. Returns True when the row is new."""
    row = fetch_one(
        conn,
        """
        insert into slack_user (
            slack_user_id, team_id, email, display_name, real_name, tz, tz_offset_s,
            is_admin, is_bot, deleted, joined_at_utc, raw
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        on conflict (slack_user_id) do update
           set team_id      = excluded.team_id,
               email        = coalesce(excluded.email, slack_user.email),
               display_name = excluded.display_name,
               real_name    = excluded.real_name,
               tz           = coalesce(excluded.tz, slack_user.tz),
               tz_offset_s  = coalesce(excluded.tz_offset_s, slack_user.tz_offset_s),
               is_admin     = excluded.is_admin,
               is_bot       = excluded.is_bot,
               deleted      = excluded.deleted,
               joined_at_utc = coalesce(slack_user.joined_at_utc, excluded.joined_at_utc),
               last_seen_at = now(),
               raw          = excluded.raw
        returning (xmax = 0) as inserted
        """,
        (
            user.id,
            user.team_id,
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
) -> bool:
    """Record a member and raise a roster alert if nobody knows them.

    Bots, deleted accounts, workspace admins and configured staff addresses
    never alert: staff are expected to be in the workspace and not on the
    roster.
    """
    summary = summary or SyncSummary()
    inserted = upsert_user(conn, user, joined_at=joined_at)
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
        observe_user(conn, user, summary=summary, staff_emails=staff_emails)
    return summary


# ---------------------------------------------------------------------------
# channels and messages
# ---------------------------------------------------------------------------


def sync_channels(conn: psycopg.Connection, client: SlackClient, *, staff_channel: str | None = None) -> int:
    n = 0
    for channel in client.list_channels():
        execute(
            conn,
            """
            insert into slack_channel (channel_id, name, is_private, is_member, is_staff)
            values (%s, %s, %s, %s, %s)
            on conflict (channel_id) do update
               set name = excluded.name,
                   is_private = excluded.is_private,
                   is_member = excluded.is_member,
                   is_staff = slack_channel.is_staff or excluded.is_staff
            """,
            (channel.id, channel.name, channel.is_private, channel.is_member, channel.id == staff_channel),
        )
        n += 1
    return n


def mark_staff_channel(conn: psycopg.Connection, channel_id: str, *, is_staff: bool = True) -> None:
    execute(
        conn,
        """
        insert into slack_channel (channel_id, is_staff) values (%s, %s)
        on conflict (channel_id) do update set is_staff = excluded.is_staff
        """,
        (channel_id, is_staff),
    )


def record_message(conn: psycopg.Connection, message: SlackMessage) -> bool:
    """Write one message. Returns True when new. Bot and system posts are skipped."""
    if not message.user or message.subtype in ("bot_message", "channel_join", "channel_leave"):
        return False
    execute(
        conn,
        "insert into slack_channel (channel_id) values (%s) on conflict do nothing",
        (message.channel_id,),
    )
    is_reply = bool(message.thread_ts and message.thread_ts != message.ts)
    row = fetch_one(
        conn,
        """
        insert into slack_message (
            message_key, channel_id, slack_user_id, ts, posted_at_utc, thread_ts,
            is_thread_reply, subtype, text, word_count, reaction_count
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (message_key) do nothing
        returning message_key
        """,
        (
            f"{message.channel_id}:{message.ts}",
            message.channel_id,
            message.user,
            message.ts,
            message.posted_at,
            message.thread_ts,
            is_reply,
            message.subtype,
            message.text,
            word_count(message.text),
            message.reactions,
        ),
    )
    return row is not None


def sync_messages(
    conn: psycopg.Connection, client: SlackClient, *, summary: SyncSummary | None = None
) -> SyncSummary:
    """Pull new history for every tracked channel the bot is in."""
    summary = summary or SyncSummary()
    channels = fetch_all(
        conn, "select channel_id, last_ts from slack_channel where tracked and is_member order by channel_id"
    )
    for channel in channels:
        latest = channel["last_ts"]
        for message in client.channel_history(channel["channel_id"], oldest=channel["last_ts"]):
            summary.messages_read += 1
            if record_message(conn, message):
                summary.messages_written += 1
            if latest is None or float(message.ts) > float(latest):
                latest = message.ts
        execute(
            conn,
            "update slack_channel set last_ts = %s, synced_at = now() where channel_id = %s",
            (latest, channel["channel_id"]),
        )
        summary.channels += 1
    return summary


def sync_all(
    conn: psycopg.Connection,
    client: SlackClient,
    *,
    staff_channel: str | None = None,
    staff_emails: tuple[str, ...] = (),
) -> SyncSummary:
    summary = SyncSummary()
    sync_users(conn, client, summary=summary, staff_emails=staff_emails)
    sync_channels(conn, client, staff_channel=staff_channel)
    sync_messages(conn, client, summary=summary)
    log.info("slack sync %s", summary)
    return summary


def last_data_received(conn: psycopg.Connection, cohort_id: str | None = None) -> dict[str, datetime | None]:
    """When each source last produced a row. The dashboard's "last updated"."""
    scope = "where (%s::text is null or cohort_id = %s::text)"
    return {
        "slack_message": (fetch_one(conn, "select max(posted_at_utc) as t from slack_message") or {}).get("t"),
        "slack_sync": (fetch_one(conn, "select max(synced_at) as t from slack_channel") or {}).get("t"),
        "part_a": (fetch_one(conn, f"select max(ingested_at) as t from v_checkin_resolved {scope}", (cohort_id, cohort_id)) or {}).get("t"),
        "part_b": (fetch_one(conn, f"select max(ingested_at) as t from v_checkin_b_resolved {scope}", (cohort_id, cohort_id)) or {}).get("t"),
    }


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "SyncSummary",
    "is_rostered",
    "last_data_received",
    "mark_staff_channel",
    "observe_user",
    "record_message",
    "sync_all",
    "sync_channels",
    "sync_messages",
    "sync_users",
    "upsert_user",
    "word_count",
]
