"""Walk ``conversations.history`` for what the live bot did not see.

Two uses:

* **First connect.** The bot starts recording the day it is invited to a
  channel; everything before that is only in Slack. A backfill reads it while
  Slack still returns it — which on a free workspace is 90 days, so this is
  time-limited insurance, not a substitute for the bot being up.
* **A gap.** The bot was down for a weekend. Re-running the backfill over a
  channel fills the gap and, because every row is keyed by the act rather than
  by delivery, writes nothing for what the bot already recorded.

Reactions come back on history messages as an aggregated ``reactions`` block,
not as events; they are expanded into reaction_added observations so the
Director's definition ("reacting to messages") is honoured on the backfill
path too. The event_ts of a backfilled reaction is unknown, so the message ts
is used as the best lower bound — it is recorded as such in ``raw``.

Thread replies are NOT in ``conversations.history``; each thread has to be
walked with ``conversations.replies``. That is done for the Q&A channels only,
where the replies are the answers and the whole point — one call per thread is
cheap there and unbounded everywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from slack_sdk.errors import SlackApiError

from ..db import execute, fetch_all, fetch_one
from ..ingest.common import IngestResult, finish_load_run, start_load_run
from ..logging_setup import get_logger, summarize
from .events import history_message_to_event, history_reactions_to_events, parse_event
from .store import cohort_for_team, resolve_and_record

log = get_logger(__name__)


@dataclass
class BackfillResult:
    channels: int = 0
    messages_read: int = 0
    replies_read: int = 0
    events_written: int = 0
    events_duplicate: int = 0
    events_skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:  # pragma: no cover - display only
        return summarize(
            channels=self.channels, messages_read=self.messages_read, replies_read=self.replies_read,
            written=self.events_written, duplicate=self.events_duplicate,
            skipped=self.events_skipped, errors=len(self.errors),
        )


def sync_channels(conn: psycopg.Connection, client: Any, team_id: str, *, include_private: bool = True) -> list[dict[str, Any]]:
    """Refresh ``slack_channel`` from ``conversations.list``. Returns the channels."""
    types = "public_channel,private_channel" if include_private else "public_channel"
    found: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        response = client.conversations_list(types=types, cursor=cursor, limit=200)
        for ch in response.get("channels") or []:
            found.append(ch)
            execute(
                conn,
                """
                insert into slack_channel (team_id, channel_id, name, is_private, is_member, fetched_at)
                values (%s, %s, %s, %s, %s, now())
                on conflict (team_id, channel_id) do update
                   set name = excluded.name,
                       is_private = excluded.is_private,
                       is_member = excluded.is_member,
                       fetched_at = now()
                """,
                (team_id, ch["id"], ch.get("name"), bool(ch.get("is_private")), ch.get("is_member")),
            )
        cursor = ((response.get("response_metadata") or {}).get("next_cursor") or "").strip()
        if not cursor:
            break
    log.info("slack channels synced team=%s found=%d", team_id, len(found))
    return found


def _watermark(conn: psycopg.Connection, team_id: str, channel_id: str) -> str | None:
    row = fetch_one(
        conn,
        "select backfilled_through_ts from slack_channel where team_id = %s and channel_id = %s",
        (team_id, channel_id),
    )
    return row["backfilled_through_ts"] if row else None


def _set_watermark(conn: psycopg.Connection, team_id: str, channel_id: str, ts: str) -> None:
    execute(
        conn,
        """
        insert into slack_channel (team_id, channel_id, backfilled_through_ts)
        values (%s, %s, %s)
        on conflict (team_id, channel_id) do update
           set backfilled_through_ts = greatest(
                   coalesce(slack_channel.backfilled_through_ts, '0'), excluded.backfilled_through_ts)
        """,
        (team_id, channel_id, ts),
    )


def backfill_channel(
    conn: psycopg.Connection,
    client: Any,
    team_id: str,
    channel_id: str,
    *,
    since: datetime | None = None,
    store_text: bool = False,
    load_id: str | None = None,
    result: BackfillResult | None = None,
    qa: Any = None,
) -> BackfillResult:
    """Read one channel's history forward from the watermark (or ``since``).

    The watermark only advances after a page is fully written, so a failure
    mid-channel leaves the next run starting from the last complete page,
    never past rows it has not seen. Re-reading a page is free — every row is
    keyed by the act.

    ``qa`` is the ``QaService``; when this is one of its channels, every message
    also feeds the Q&A tables and each thread is walked for its replies.
    """
    result = result or BackfillResult()
    cohort_id = cohort_for_team(conn, team_id)
    is_qa = qa is not None and qa.is_qa_channel(channel_id)

    def record_raw(message: dict[str, Any]) -> None:
        for raw in [history_message_to_event(message, channel_id), *history_reactions_to_events(message, channel_id)]:
            parsed = parse_event(raw, team_id, store_text=store_text)
            outcome = resolve_and_record(conn, client, parsed, cohort_id=cohort_id, load_id=load_id)
            if outcome.status == "written":
                result.events_written += 1
            elif outcome.status == "duplicate":
                result.events_duplicate += 1
            else:
                result.events_skipped += 1

    oldest: str | None = None
    if since is not None:
        oldest = f"{since.timestamp():.6f}"
    mark = _watermark(conn, team_id, channel_id)
    if mark and (oldest is None or float(mark) > float(oldest)):
        oldest = mark

    cursor: str | None = None
    newest_seen: float = float(oldest) if oldest else 0.0
    page = 0
    while True:
        try:
            response = client.conversations_history(
                channel=channel_id, oldest=oldest, cursor=cursor, limit=200
            )
        except SlackApiError as exc:
            error = (getattr(exc, "response", None) or {}).get("error", str(exc))
            result.errors.append(f"{channel_id}: {error}")
            log.warning("history failed channel=%s error=%s", channel_id, error)
            return result

        page += 1
        messages = response.get("messages") or []
        for message in messages:
            result.messages_read += 1
            record_raw(message)
            if is_qa:
                qa.observe_history(conn, message, channel_id)
                if int(message.get("reply_count") or 0) > 0 and message.get("ts"):
                    for reply in _thread_replies(client, channel_id, str(message["ts"]), result):
                        result.replies_read += 1
                        record_raw(reply)
                        qa.observe_history(conn, reply, channel_id)
            ts = message.get("ts")
            if ts:
                newest_seen = max(newest_seen, float(ts))

        # Advance only once the whole page is written.
        if newest_seen:
            _set_watermark(conn, team_id, channel_id, f"{newest_seen:.6f}")
        conn.commit()

        cursor = ((response.get("response_metadata") or {}).get("next_cursor") or "").strip()
        if not response.get("has_more") or not cursor:
            break

    result.channels += 1
    log.info("backfill channel=%s pages=%d %s", channel_id, page, result)
    return result


def _thread_replies(client: Any, channel_id: str, thread_ts: str, result: BackfillResult) -> list[dict[str, Any]]:
    """Every reply in one thread, oldest first. The parent comes back as the
    first message and is dropped; the caller already has it."""
    replies: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        try:
            response = client.conversations_replies(channel=channel_id, ts=thread_ts, cursor=cursor, limit=200)
        except SlackApiError as exc:
            error = (getattr(exc, "response", None) or {}).get("error", str(exc))
            result.errors.append(f"{channel_id}/{thread_ts}: {error}")
            log.warning("replies failed channel=%s thread=%s error=%s", channel_id, thread_ts, error)
            return replies
        for message in response.get("messages") or []:
            if str(message.get("ts")) != thread_ts:
                replies.append(message)
        cursor = ((response.get("response_metadata") or {}).get("next_cursor") or "").strip()
        if not response.get("has_more") or not cursor:
            return replies


def backfill_workspace(
    conn: psycopg.Connection,
    client: Any,
    team_id: str,
    *,
    channels: list[str] | None = None,
    days: int | None = None,
    store_text: bool = False,
    include_private: bool = True,
    qa: Any = None,
) -> BackfillResult:
    """Backfill every channel the bot can read, or the ones named."""
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    known = sync_channels(conn, client, team_id, include_private=include_private)
    targets = [ch["id"] for ch in known if channels is None or ch["id"] in channels or ch.get("name") in channels]

    load_id = start_load_run(conn, source="slack_backfill", origin=team_id, cohort_id=cohort_for_team(conn, team_id))
    result = BackfillResult()
    error: str | None = None
    try:
        for channel_id in targets:
            backfill_channel(
                conn, client, team_id, channel_id,
                since=since, store_text=store_text, load_id=load_id, result=result, qa=qa,
            )
    except Exception as exc:  # pragma: no cover - defensive
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        summary = IngestResult(
            rows_read=result.messages_read,
            rows_written=result.events_written,
            rows_skipped=result.events_duplicate + result.events_skipped,
        )
        finish_load_run(conn, load_id, summary, error=error or ("; ".join(result.errors) or None))
    return result


def channels_for(conn: psycopg.Connection, team_id: str) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        "select channel_id, name, is_private, backfilled_through_ts from slack_channel where team_id = %s order by name",
        (team_id,),
    )
