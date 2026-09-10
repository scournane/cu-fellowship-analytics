"""The Slack Web API behind a small protocol, with a fake that records.

Same shape as :mod:`cufa.google`: the code that decides *what* to send never
imports anything network-facing, so it can be exercised end to end without a
workspace. ``FakeSlackClient`` keeps every posted message in an outbox and
lets a test stand up users, channels and history in memory.

``WebClientAdapter`` wraps the ``slack_sdk.WebClient`` the participation bot
already uses (``cufa.slack.bot.make_web_client``), so both halves of the bot
share one HTTP client, one token, and — in the demo — one fake Slack server.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from ..errors import CufaError
from ..logging_setup import get_logger

log = get_logger(__name__)


class SlackApiError(CufaError):
    """Slack answered ``ok: false``. Carries the error code Slack gave."""

    def __init__(self, method: str, error: str, payload: dict[str, Any] | None = None):
        super().__init__(f"Slack {method} failed: {error}")
        self.method = method
        self.error = error
        self.payload = payload or {}


@dataclass(frozen=True)
class SlackUser:
    """The subset of ``users.info`` the bot uses."""

    id: str
    email: str | None
    display_name: str
    real_name: str
    tz: str | None
    tz_offset: int | None
    is_admin: bool = False
    is_bot: bool = False
    deleted: bool = False
    team_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, member: dict[str, Any]) -> "SlackUser":
        profile = member.get("profile") or {}
        return cls(
            id=str(member.get("id")),
            email=(profile.get("email") or "").strip().lower() or None,
            display_name=profile.get("display_name") or "",
            real_name=profile.get("real_name") or member.get("real_name") or "",
            tz=member.get("tz") or None,
            tz_offset=member.get("tz_offset"),
            is_admin=bool(member.get("is_admin") or member.get("is_owner")),
            is_bot=bool(member.get("is_bot") or member.get("id") == "USLACKBOT"),
            deleted=bool(member.get("deleted")),
            team_id=member.get("team_id"),
            raw=member,
        )


@dataclass(frozen=True)
class SlackChannel:
    id: str
    name: str
    is_private: bool = False
    is_member: bool = False


@dataclass(frozen=True)
class SlackMessage:
    """One entry of ``conversations.history``."""

    channel_id: str
    ts: str
    user: str | None
    text: str
    thread_ts: str | None = None
    subtype: str | None = None
    reactions: int = 0

    @property
    def posted_at(self) -> datetime:
        return datetime.fromtimestamp(float(self.ts), tz=timezone.utc)

    @classmethod
    def from_api(cls, channel_id: str, item: dict[str, Any]) -> "SlackMessage":
        reactions = sum(int(r.get("count") or 0) for r in item.get("reactions") or [])
        return cls(
            channel_id=channel_id,
            ts=str(item.get("ts")),
            user=item.get("user") or item.get("bot_id"),
            text=item.get("text") or "",
            thread_ts=item.get("thread_ts"),
            subtype=item.get("subtype"),
            reactions=reactions,
        )


@dataclass(frozen=True)
class Posted:
    """What ``post_message`` returned: enough to find the message again."""

    channel_id: str
    ts: str


class SlackClient(Protocol):
    """Everything the bot needs from Slack. Nothing more."""

    @property
    def team_id(self) -> str: ...

    def list_users(self) -> list[SlackUser]: ...

    def get_user(self, user_id: str) -> SlackUser | None: ...

    def list_channels(self) -> list[SlackChannel]: ...

    def channel_history(
        self, channel_id: str, *, oldest: str | None = None, include_replies: bool = True
    ) -> list[SlackMessage]: ...

    def open_dm(self, user_id: str) -> str: ...

    def post_message(
        self, channel_id: str, text: str, *, blocks: list[dict[str, Any]] | None = None
    ) -> Posted: ...

    def post_ephemeral(self, channel_id: str, user_id: str, text: str) -> None: ...


# ---------------------------------------------------------------------------
# the fake
# ---------------------------------------------------------------------------


@dataclass
class OutboxItem:
    channel_id: str
    text: str
    blocks: list[dict[str, Any]] | None
    ts: str
    ephemeral_to: str | None = None


class FakeSlackClient:
    """In-memory workspace. Tests seed it and read the outbox."""

    team_id = "TFAKE"

    def __init__(self) -> None:
        self.users: dict[str, SlackUser] = {}
        self.channels: dict[str, SlackChannel] = {}
        self.history: dict[str, list[SlackMessage]] = {}
        self.dm_channels: dict[str, str] = {}
        self.outbox: list[OutboxItem] = []
        self._ts = 1_700_000_000.0
        self.fail_post_for: set[str] = set()

    # --- seeding helpers -------------------------------------------------

    def add_user(
        self,
        user_id: str,
        *,
        email: str | None,
        name: str = "",
        tz: str | None = "America/New_York",
        is_admin: bool = False,
        is_bot: bool = False,
        deleted: bool = False,
    ) -> SlackUser:
        user = SlackUser(
            id=user_id,
            email=(email or "").lower() or None,
            display_name=name,
            real_name=name,
            tz=tz,
            tz_offset=None,
            is_admin=is_admin,
            is_bot=is_bot,
            deleted=deleted,
            team_id="TFAKE",
        )
        self.users[user_id] = user
        return user

    def add_channel(self, channel_id: str, name: str, *, private: bool = False) -> SlackChannel:
        channel = SlackChannel(id=channel_id, name=name, is_private=private, is_member=True)
        self.channels[channel_id] = channel
        self.history.setdefault(channel_id, [])
        return channel

    def add_message(
        self,
        channel_id: str,
        user_id: str,
        text: str,
        *,
        at: datetime | None = None,
        thread_ts: str | None = None,
        reactions: int = 0,
    ) -> SlackMessage:
        if at is None:
            self._ts += 1
            ts = f"{self._ts:.6f}"
        else:
            ts = f"{at.timestamp():.6f}"
        message = SlackMessage(
            channel_id=channel_id,
            ts=ts,
            user=user_id,
            text=text,
            thread_ts=thread_ts,
            reactions=reactions,
        )
        self.history.setdefault(channel_id, []).append(message)
        return message

    # --- the protocol ----------------------------------------------------

    def list_users(self) -> list[SlackUser]:
        return list(self.users.values())

    def get_user(self, user_id: str) -> SlackUser | None:
        return self.users.get(user_id)

    def list_channels(self) -> list[SlackChannel]:
        return list(self.channels.values())

    def channel_history(
        self, channel_id: str, *, oldest: str | None = None, include_replies: bool = True
    ) -> list[SlackMessage]:
        items = self.history.get(channel_id, [])
        if oldest is not None:
            items = [m for m in items if float(m.ts) > float(oldest)]
        if not include_replies:
            items = [m for m in items if not (m.thread_ts and m.thread_ts != m.ts)]
        return sorted(items, key=lambda m: float(m.ts))

    def open_dm(self, user_id: str) -> str:
        return self.dm_channels.setdefault(user_id, f"D-{user_id}")

    def post_message(
        self, channel_id: str, text: str, *, blocks: list[dict[str, Any]] | None = None
    ) -> Posted:
        if channel_id in self.fail_post_for:
            raise SlackApiError("chat.postMessage", "channel_not_found")
        self._ts += 1
        ts = f"{self._ts:.6f}"
        self.outbox.append(OutboxItem(channel_id=channel_id, text=text, blocks=blocks, ts=ts))
        return Posted(channel_id=channel_id, ts=ts)

    def post_ephemeral(self, channel_id: str, user_id: str, text: str) -> None:
        self._ts += 1
        self.outbox.append(
            OutboxItem(channel_id=channel_id, text=text, blocks=None, ts=f"{self._ts:.6f}", ephemeral_to=user_id)
        )

    # --- inspection ------------------------------------------------------

    def sent_to(self, channel_id: str) -> list[OutboxItem]:
        return [item for item in self.outbox if item.channel_id == channel_id]

    def dms_to(self, user_id: str) -> list[OutboxItem]:
        return self.sent_to(self.dm_channels.get(user_id, f"D-{user_id}"))


# ---------------------------------------------------------------------------
# the real one
# ---------------------------------------------------------------------------


class WebClientAdapter:
    """The protocol, over a ``slack_sdk.WebClient``. Paginates; the SDK retries 429s."""

    def __init__(self, web: Any) -> None:
        self._web = web
        self._team: str | None = None

    @property
    def team_id(self) -> str:
        if self._team is None:
            self._team = str(self._web.auth_test()["team_id"])
        return self._team

    def _paginate(self, method: str, key: str, **params: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            payload = getattr(self._web, method)(cursor=cursor, limit=200, **params)
            items.extend(payload.get(key) or [])
            cursor = ((payload.get("response_metadata") or {}).get("next_cursor") or "").strip()
            if not cursor:
                return items

    def list_users(self) -> list[SlackUser]:
        return [SlackUser.from_api(m) for m in self._paginate("users_list", "members")]

    def get_user(self, user_id: str) -> SlackUser | None:
        try:
            payload = self._web.users_info(user=user_id)
        except Exception as exc:  # noqa: BLE001 — SlackApiError from the SDK
            if "user_not_found" in str(exc):
                return None
            raise SlackApiError("users.info", str(exc)) from exc
        return SlackUser.from_api(payload["user"])

    def list_channels(self) -> list[SlackChannel]:
        rows = self._paginate("conversations_list", "channels", types="public_channel,private_channel", exclude_archived=True)
        return [
            SlackChannel(id=str(r["id"]), name=r.get("name") or "", is_private=bool(r.get("is_private")), is_member=bool(r.get("is_member")))
            for r in rows
        ]

    def channel_history(
        self, channel_id: str, *, oldest: str | None = None, include_replies: bool = True
    ) -> list[SlackMessage]:
        rows = self._paginate("conversations_history", "messages", channel=channel_id, oldest=oldest)
        messages = [SlackMessage.from_api(channel_id, r) for r in rows]
        if include_replies:
            for parent in [r for r in rows if r.get("reply_count")]:
                for reply in self._paginate("conversations_replies", "messages", channel=channel_id, ts=parent["ts"], oldest=oldest):
                    if reply.get("ts") != parent["ts"]:
                        messages.append(SlackMessage.from_api(channel_id, reply))
        return sorted(messages, key=lambda m: float(m.ts))

    def open_dm(self, user_id: str) -> str:
        payload = self._web.conversations_open(users=user_id)
        return str(payload["channel"]["id"])

    def post_message(
        self, channel_id: str, text: str, *, blocks: list[dict[str, Any]] | None = None
    ) -> Posted:
        try:
            payload = self._web.chat_postMessage(channel=channel_id, text=text, blocks=blocks)
        except Exception as exc:  # noqa: BLE001
            raise SlackApiError("chat.postMessage", str(exc)) from exc
        return Posted(channel_id=str(payload["channel"]), ts=str(payload["ts"]))

    def post_ephemeral(self, channel_id: str, user_id: str, text: str) -> None:
        self._web.chat_postEphemeral(channel=channel_id, user=user_id, text=text)


def build_client(settings: Any | None = None) -> SlackClient:
    """The client the process should use, from settings."""
    from ..config import get_settings

    settings = settings or get_settings()
    if settings.fake_slack:
        return FakeSlackClient()
    from .bot import make_web_client

    return WebClientAdapter(make_web_client(settings))


__all__ = [
    "FakeSlackClient",
    "OutboxItem",
    "Posted",
    "WebClientAdapter",
    "SlackApiError",
    "SlackChannel",
    "SlackClient",
    "SlackMessage",
    "SlackUser",
    "build_client",
]
