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
    # Slack reports a huddle per user (user_huddle_changed) with no channel.
    "huddle_joined",
    "huddle_left",
    # Canvases arrive as file events. file_change names no editor, so a
    # canvas_edited row may have no actor — see the migration.
    "canvas_created",
    "canvas_edited",
    "canvas_shared",
    "canvas_comment",
    # A vote on a poll the bot itself posted, delivered as a block_actions
    # interaction rather than an event.
    "poll_vote",
)

#: Slack file types that are canvases. "quip" is what older workspaces report.
CANVAS_FILETYPES = frozenset({"canvas", "quip"})

#: The action_id on every poll button the bot posts. A vote is a block_actions
#: payload whose action carries this id and a block_id of ``cufa_poll:<uuid>``.
POLL_ACTION_ID = "cufa_poll_vote"
POLL_BLOCK_PREFIX = "cufa_poll:"

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
        # The "X started a huddle" line. The joins themselves arrive as
        # user_huddle_changed, so counting the line too would double-count.
        "huddle_thread",
    }
)

_LINK_RE = re.compile(r"<https?://|https?://", re.IGNORECASE)
#: ``<@U0ABC>`` or ``<@U0ABC|name>``. Only user and workspace-user ids; ``<!here>``
#: and ``<#C…>`` are not mentions of a person.
_MENTION_RE = re.compile(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>")


def mentions_in(text: str | None) -> tuple[str, ...]:
    """User ids @-mentioned in ``text``, in order, duplicates kept.

    Pulled out at parse time because the text itself is not stored (ADR-031).
    Kept as a sequence of ids, never resolved to names here: who was mentioned
    is recorded so absence can be noticed, and it is never counted per person.
    """
    return tuple(_MENTION_RE.findall(text or ""))


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
    #: The huddle a huddle_* row refers to. Slack's call id, not a channel.
    call_id: str | None = None
    #: The file a canvas_* row refers to.
    file_id: str | None = None
    #: The bot-run poll a poll_vote row refers to, and the option chosen.
    poll_id: str | None = None
    poll_choice: str | None = None
    #: User ids @-mentioned in a message. ``None`` for non-message rows.
    mentions: tuple[str, ...] | None = None


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
    if etype == "user_huddle_changed":
        return _parse_huddle(event, team_id, retry_num=retry_num)
    if etype in ("file_created", "file_change", "file_shared"):
        return _parse_file(event, team_id, retry_num=retry_num)
    if etype == "file_comment_added":
        return _parse_file_comment(event, team_id, retry_num=retry_num)
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
        mentions=mentions_in(text),
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
        mentions=mentions_in(text),
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


def _blank(**overrides: Any) -> dict[str, Any]:
    """The all-NULL observation fields, for event kinds that carry no text."""
    base: dict[str, Any] = dict(
        channel_type=None, message_ts=None, thread_ts=None, is_thread_reply=False,
        reaction=None, item_user_id=None, text_length=None, word_count=None,
        has_link=None, has_attachment=None, text=None,
    )
    base.update(overrides)
    return base


def _parse_huddle(event: dict[str, Any], team_id: str, *, retry_num: int | None) -> ParseResult:
    """``user_huddle_changed``: one user's huddle state flipped.

    Slack sends the user object with ``profile.huddle_state`` of
    ``in_a_huddle`` or ``default`` and the call id. There is no channel on the
    event — Slack reports the huddle, not where it was started — so
    ``channel_id`` is NULL by design, not by omission.
    """
    user = event.get("user") or {}
    user_id = user.get("id") if isinstance(user, dict) else user
    profile = (user.get("profile") or {}) if isinstance(user, dict) else {}
    state = profile.get("huddle_state")
    call_id = profile.get("huddle_state_call_id")
    event_ts = event.get("event_ts")
    if not (user_id and event_ts):
        return Skipped("huddle event missing user or ts", "user_huddle_changed")
    if state == "in_a_huddle":
        etype = "huddle_joined"
    elif state in ("default", None):
        etype = "huddle_left"
    else:
        return Skipped(f"unknown huddle state {state!r}", "user_huddle_changed")

    return SlackObservation(
        source_event_id=source_event_id(team_id, etype, user_id, str(call_id or ""), str(event_ts)),
        team_id=team_id,
        event_type=etype,
        channel_id=None,
        slack_user_id=user_id,
        event_time_utc=ts_to_utc(event_ts),
        raw=_raw_subset(event, retry_num=retry_num, huddle_state=state),
        call_id=call_id,
        **_blank(),
    )


def _parse_file(event: dict[str, Any], team_id: str, *, retry_num: int | None) -> ParseResult:
    """``file_created`` / ``file_change`` / ``file_shared``, as a canvas act.

    The payload says which file, not what kind of file. Whether it is a canvas
    is decided at record time from ``files.info`` (cached in ``slack_file``);
    a non-canvas is skipped there. ``file_change`` names no editor at all, so
    ``canvas_edited`` is recorded with the actor honestly NULL rather than
    attributed to the canvas owner.
    """
    etype = event["type"]
    file_id = event.get("file_id") or (event.get("file") or {}).get("id")
    event_ts = event.get("event_ts")
    if not (file_id and event_ts):
        return Skipped("file event missing file id or ts", etype)

    if etype == "file_created":
        kind, user, channel = "canvas_created", event.get("user_id"), None
        if not user:
            return Skipped("file_created without a user", etype)
    elif etype == "file_shared":
        kind, user, channel = "canvas_shared", event.get("user_id"), event.get("channel_id")
        if not (user and channel):
            return Skipped("file_shared without user or channel", etype)
    else:
        kind, user, channel = "canvas_edited", None, None

    return SlackObservation(
        source_event_id=source_event_id(team_id, kind, file_id, str(user or ""), str(event_ts)),
        team_id=team_id,
        event_type=kind,
        channel_id=channel,
        slack_user_id=user,
        event_time_utc=ts_to_utc(event_ts),
        raw=_raw_subset(event, retry_num=retry_num),
        file_id=file_id,
        **_blank(),
    )


def _parse_file_comment(event: dict[str, Any], team_id: str, *, retry_num: int | None) -> ParseResult:
    """``file_comment_added``: a comment on a file, recorded as canvas_comment
    when the file turns out to be a canvas (decided at record time).

    Slack retired file comments for ordinary files years ago and has not
    published a dedicated canvas-comment event; this parser records whatever
    Slack does send in that shape, and nothing it does not.
    """
    comment = event.get("comment") or {}
    user = comment.get("user")
    file_id = event.get("file_id") or (event.get("file") or {}).get("id")
    event_ts = event.get("event_ts") or comment.get("created")
    if not (user and file_id and event_ts):
        return Skipped("file comment missing user, file or ts", "file_comment_added")
    return SlackObservation(
        source_event_id=source_event_id(
            team_id, "canvas_comment", file_id, user, str(comment.get("id") or event_ts)
        ),
        team_id=team_id,
        event_type="canvas_comment",
        channel_id=(event.get("file") or {}).get("channel") or None,
        slack_user_id=user,
        event_time_utc=ts_to_utc(event_ts),
        raw=_raw_subset(event, retry_num=retry_num),
        file_id=file_id,
        **_blank(text_length=len(comment.get("comment") or "") or None),
    )


def parse_interaction(body: dict[str, Any], team_id: str) -> ParseResult:
    """A ``block_actions`` payload: somebody pressed a button.

    The only button this bot posts is a poll option, so the only interaction it
    records is a vote. Changing your vote produces a NEW row — observations are
    never rewritten — and the latest one per person counts at read time.
    """
    if body.get("type") != "block_actions":
        return Skipped("not a block_actions payload", body.get("type"))
    user = (body.get("user") or {}).get("id")
    channel = (body.get("channel") or {}).get("id")
    message_ts = (body.get("message") or {}).get("ts")
    actions = body.get("actions") or []
    action = next((a for a in actions if a.get("action_id") == POLL_ACTION_ID), None)
    if action is None:
        return Skipped("interaction is not a poll vote", "block_actions")
    block_id = action.get("block_id") or ""
    if not block_id.startswith(POLL_BLOCK_PREFIX):
        return Skipped("poll button without a poll id", "block_actions")
    poll_id = block_id[len(POLL_BLOCK_PREFIX):]
    choice = action.get("value")
    action_ts = action.get("action_ts") or body.get("trigger_id") or message_ts
    if not (user and channel and poll_id and choice and action_ts):
        return Skipped("vote missing user, channel, poll, choice or ts", "block_actions")

    return SlackObservation(
        source_event_id=source_event_id(team_id, channel, "poll_vote", poll_id, user, str(action_ts)),
        team_id=team_id,
        event_type="poll_vote",
        channel_id=channel,
        slack_user_id=user,
        event_time_utc=ts_to_utc(action_ts),
        raw={"type": "block_actions", "action_id": POLL_ACTION_ID},
        poll_id=poll_id,
        poll_choice=str(choice),
        **_blank(message_ts=str(message_ts) if message_ts else None),
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
