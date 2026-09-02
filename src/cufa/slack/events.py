"""Turn a Slack event into an observation. No I/O, no database.

Everything here is a pure function of the payload Slack sent, which is what
makes it testable against recorded fixtures rather than against a live
workspace. The one design decision worth reading is the idempotency key —
see ``source_event_id``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..text import sha256_hex

#: Event types this package records. Anything else is skipped with a reason.
EVENT_TYPES = (
    "message",
    "message_changed",
    "message_deleted",
    "reaction_added",
    "reaction_removed",
    "member_joined_channel",
    "member_left_channel",
)

#: Message subtypes that are system noise rather than a person saying
#: something. ``channel_join`` is the "X has joined" line — the join itself is
#: captured by the member_joined_channel event, so counting the line too would
#: double-count. ``bot_message`` is the obvious one.
_SKIP_SUBTYPES = frozenset(
    {
        "bot_message",
        "channel_join",
        "channel_leave",
        "channel_topic",
        "channel_purpose",
        "channel_name",
        "channel_archive",
        "channel_unarchive",
        "pinned_item",
        "unpinned_item",
        "group_join",
        "group_leave",
        "ekm_access_denied",
        "tombstone",
    }
)

_LINK_RE = re.compile(r"<https?://|https?://", re.IGNORECASE)


@dataclass(frozen=True)
class SlackObservation:
    """One act in Slack, reduced to what the participation definition needs."""

    source_event_id: str
    team_id: str
    event_type: str
    channel_id: str
    channel_type: str | None
    slack_user_id: str
    message_ts: str | None
    thread_ts: str | None
    is_thread_reply: bool
    reaction: str | None
    item_user_id: str | None
    text_length: int | None
    word_count: int | None
    has_link: bool | None
    has_attachment: bool | None
    #: Only populated when the caller asked for text. NULL in the database
    #: otherwise — see the migration header for why that is the default.
    text: str | None
    event_time_utc: datetime
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Skipped:
    """Why an event produced no observation. Logged, never raised."""

    reason: str
    event_type: str | None = None
    subtype: str | None = None


ParseResult = SlackObservation | Skipped


def ts_to_utc(ts: str | float | int) -> datetime:
    """Slack's ``ts`` is seconds since the epoch with a microsecond suffix."""
    return datetime.fromtimestamp(float(ts), tz=timezone.utc)


def source_event_id(*parts: str) -> str:
    """The idempotency key.

    Built from what identifies the *act*, never from Slack's ``event_id``:
    Slack retries deliveries with the same event_id, which would be fine, but a
    message read back through ``conversations.history`` has no event_id at
    all — and the backfill must collide with what the live bot already wrote,
    not sit beside it. Same principle as the forms pipeline, where an API pull
    and a CSV import of one response hash to one key.
    """
    return sha256_hex("slack", *parts)


def _text_metrics(text: str | None, event: dict[str, Any]) -> tuple[int, int, bool, bool]:
    body = text or ""
    has_attachment = bool(event.get("files")) or bool(event.get("attachments"))
    return len(body), len(body.split()), bool(_LINK_RE.search(body)), has_attachment


def _raw_subset(event: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """The parts of the payload worth keeping. Never the text."""
    keep = {}
    for key in ("type", "subtype", "channel_type", "event_ts", "client_msg_id"):
        if key in event:
            keep[key] = event[key]
    if "edited" in event:
        keep["edited_ts"] = (event.get("edited") or {}).get("ts")
    keep.update({k: v for k, v in extra.items() if v is not None})
    return keep


def parse_event(
    event: dict[str, Any],
    team_id: str,
    *,
    store_text: bool = False,
    retry_num: int | None = None,
) -> ParseResult:
    """Reduce one Events API ``event`` object to an observation, or say why not.

    ``team_id`` comes from the envelope, not the event: some event types carry
    it and some do not, and the envelope always does.
    """
    etype = event.get("type")
    if etype == "message":
        return _parse_message(event, team_id, store_text=store_text, retry_num=retry_num)
    if etype in ("reaction_added", "reaction_removed"):
        return _parse_reaction(event, team_id, retry_num=retry_num)
    if etype in ("member_joined_channel", "member_left_channel"):
        return _parse_membership(event, team_id, retry_num=retry_num)
    return Skipped("unhandled event type", event_type=etype)


def _parse_message(
    event: dict[str, Any], team_id: str, *, store_text: bool, retry_num: int | None
) -> ParseResult:
    subtype = event.get("subtype")
    channel = event.get("channel")
    if not channel:
        return Skipped("message without channel", "message", subtype)

    if subtype == "message_changed":
        return _parse_edit(event, team_id, channel, store_text=store_text, retry_num=retry_num)
    if subtype == "message_deleted":
        return _parse_deletion(event, team_id, channel, retry_num=retry_num)
    if subtype in _SKIP_SUBTYPES:
        return Skipped("system or bot message", "message", subtype)
    if event.get("bot_id"):
        return Skipped("posted by a bot", "message", subtype)

    user = event.get("user")
    ts = event.get("ts")
    if not user or not ts:
        return Skipped("message without user or ts", "message", subtype)

    thread_ts = event.get("thread_ts")
    # A thread parent carries thread_ts == ts. Only a reply has a DIFFERENT one.
    is_reply = bool(thread_ts) and thread_ts != ts
    text = event.get("text") or ""
    length, words, link, attachment = _text_metrics(text, event)

    return SlackObservation(
        source_event_id=source_event_id(team_id, channel, "message", str(ts)),
        team_id=team_id,
        event_type="message",
        channel_id=channel,
        channel_type=event.get("channel_type"),
        slack_user_id=user,
        message_ts=str(ts),
        thread_ts=str(thread_ts) if thread_ts else None,
        is_thread_reply=is_reply,
        reaction=None,
        item_user_id=None,
        text_length=length,
        word_count=words,
        has_link=link,
        has_attachment=attachment,
        text=text if store_text else None,
        event_time_utc=ts_to_utc(ts),
        raw=_raw_subset(event, retry_num=retry_num),
    )


def _parse_edit(
    event: dict[str, Any], team_id: str, channel: str, *, store_text: bool, retry_num: int | None
) -> ParseResult:
    inner = event.get("message") or {}
    user = inner.get("user")
    original_ts = inner.get("ts")
    edited_ts = (inner.get("edited") or {}).get("ts") or event.get("ts")
    if not user or not original_ts:
        return Skipped("edit without user or original ts", "message", "message_changed")
    if inner.get("bot_id"):
        return Skipped("edit of a bot message", "message", "message_changed")

    text = inner.get("text") or ""
    length, words, link, attachment = _text_metrics(text, inner)
    thread_ts = inner.get("thread_ts")

    return SlackObservation(
        source_event_id=source_event_id(
            team_id, channel, "message_changed", str(original_ts), str(edited_ts)
        ),
        team_id=team_id,
        event_type="message_changed",
        channel_id=channel,
        channel_type=event.get("channel_type"),
        slack_user_id=user,
        message_ts=str(original_ts),
        thread_ts=str(thread_ts) if thread_ts else None,
        is_thread_reply=bool(thread_ts) and thread_ts != original_ts,
        reaction=None,
        item_user_id=None,
        text_length=length,
        word_count=words,
        has_link=link,
        has_attachment=attachment,
        text=text if store_text else None,
        event_time_utc=ts_to_utc(edited_ts),
        raw=_raw_subset(event, retry_num=retry_num, original_ts=str(original_ts)),
    )


def _parse_deletion(
    event: dict[str, Any], team_id: str, channel: str, *, retry_num: int | None
) -> ParseResult:
    previous = event.get("previous_message") or {}
    user = previous.get("user")
    deleted_ts = event.get("deleted_ts") or previous.get("ts")
    if not user or not deleted_ts:
        return Skipped("deletion without user or ts", "message", "message_deleted")

    return SlackObservation(
        source_event_id=source_event_id(team_id, channel, "message_deleted", str(deleted_ts)),
        team_id=team_id,
        event_type="message_deleted",
        channel_id=channel,
        channel_type=event.get("channel_type"),
        slack_user_id=user,
        message_ts=str(deleted_ts),
        thread_ts=None,
        is_thread_reply=False,
        reaction=None,
        item_user_id=None,
        text_length=None,
        word_count=None,
        has_link=None,
        has_attachment=None,
        text=None,
        event_time_utc=ts_to_utc(event.get("ts") or event.get("event_ts") or deleted_ts),
        raw=_raw_subset(event, retry_num=retry_num),
    )


def _parse_reaction(event: dict[str, Any], team_id: str, *, retry_num: int | None) -> ParseResult:
    etype = event["type"]
    user = event.get("user")
    reaction = event.get("reaction")
    item = event.get("item") or {}
    channel = item.get("channel")
    item_ts = item.get("ts")
    if not (user and reaction and channel and item_ts):
        return Skipped("reaction missing user, name, channel or item ts", etype)
    if item.get("type") not in (None, "message"):
        return Skipped("reaction to a non-message item", etype)

    event_ts = event.get("event_ts") or item_ts
    return SlackObservation(
        source_event_id=source_event_id(team_id, channel, etype, str(item_ts), user, reaction),
        team_id=team_id,
        event_type=etype,
        channel_id=channel,
        channel_type=None,
        slack_user_id=user,
        message_ts=str(item_ts),
        thread_ts=None,
        is_thread_reply=False,
        reaction=reaction,
        item_user_id=event.get("item_user"),
        text_length=None,
        word_count=None,
        has_link=None,
        has_attachment=None,
        text=None,
        event_time_utc=ts_to_utc(event_ts),
        raw=_raw_subset(event, retry_num=retry_num),
    )


def _parse_membership(event: dict[str, Any], team_id: str, *, retry_num: int | None) -> ParseResult:
    etype = event["type"]
    user = event.get("user")
    channel = event.get("channel")
    event_ts = event.get("event_ts")
    if not (user and channel and event_ts):
        return Skipped("membership event missing user, channel or ts", etype)

    return SlackObservation(
        source_event_id=source_event_id(team_id, channel, etype, user, str(event_ts)),
        team_id=team_id,
        event_type=etype,
        channel_id=channel,
        channel_type=event.get("channel_type"),
        slack_user_id=user,
        message_ts=None,
        thread_ts=None,
        is_thread_reply=False,
        reaction=None,
        item_user_id=None,
        text_length=None,
        word_count=None,
        has_link=None,
        has_attachment=None,
        text=None,
        event_time_utc=ts_to_utc(event_ts),
        raw=_raw_subset(event, retry_num=retry_num, inviter=event.get("inviter")),
    )


def history_message_to_event(message: dict[str, Any], channel_id: str) -> dict[str, Any]:
    """Reshape a ``conversations.history`` message into an Events API message.

    History messages omit ``channel`` (you asked for one channel) and
    ``channel_type``. Adding the channel back means the backfill and the live
    bot feed the SAME parser and produce the SAME idempotency key — which is
    the whole point.
    """
    shaped = dict(message)
    shaped.setdefault("type", "message")
    shaped["channel"] = channel_id
    return shaped


def history_reactions_to_events(message: dict[str, Any], channel_id: str) -> list[dict[str, Any]]:
    """Expand the ``reactions`` block on a history message into reaction events.

    History returns reactions aggregated on the message — a list of
    ``{name, users, count}`` — rather than as separate events. This is the only
    way a backfill can see reactions at all, and the Director's definition
    names reacting explicitly, so they are worth reconstructing. The event_ts
    is unknown; the message ts is used, which is the best available lower bound.
    """
    events: list[dict[str, Any]] = []
    ts = message.get("ts")
    if not ts:
        return events
    for block in message.get("reactions") or []:
        name = block.get("name")
        for user in block.get("users") or []:
            events.append(
                {
                    "type": "reaction_added",
                    "user": user,
                    "reaction": name,
                    "item": {"type": "message", "channel": channel_id, "ts": ts},
                    "item_user": message.get("user"),
                    "event_ts": ts,
                }
            )
    return events
