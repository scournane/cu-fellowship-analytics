"""The Slack Web API behind a small protocol, with a fake that records.

Same shape as :mod:`cufa.google`: the code that decides *what* to send never
imports anything network-facing, so it can be exercised end to end without a
workspace. ``FakeSlackClient`` keeps every posted message in an outbox and
lets a test stand up users, channels and history in memory.

``RealSlackClient`` talks to ``slack.com/api`` with the standard library only.
The Slack SDK is not a dependency of the core package — ``slack_bolt`` is
needed only by ``cufa slack serve`` and lives in the ``[slack]`` extra.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
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


class RealSlackClient:
    """``slack.com/api`` over ``urllib``. Paginates, retries on 429."""

    BASE = "https://slack.com/api/"

    def __init__(self, bot_token: str, *, sleep=time.sleep) -> None:
        if not bot_token:
            raise CufaError("SLACK_BOT_TOKEN is not set.")
        self._token = bot_token
        self._sleep = sleep

    def _call(self, method: str, **params: Any) -> dict[str, Any]:
        body = json.dumps({k: v for k, v in params.items() if v is not None}).encode()
        request = urllib.request.Request(
            self.BASE + method,
            data=body,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        for attempt in range(5):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    payload = json.loads(response.read().decode())
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    self._sleep(int(exc.headers.get("Retry-After", "1")))
                    continue
                raise SlackApiError(method, f"http_{exc.code}") from exc
            if payload.get("ok"):
                return payload
            if payload.get("error") == "ratelimited" and attempt < 4:
                self._sleep(1 + attempt)
                continue
            raise SlackApiError(method, str(payload.get("error")), payload)
        raise SlackApiError(method, "ratelimited")

    def _paginate(self, method: str, key: str, **params: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            payload = self._call(method, cursor=cursor, limit=200, **params)
            items.extend(payload.get(key) or [])
            cursor = ((payload.get("response_metadata") or {}).get("next_cursor") or "").strip()
            if not cursor:
                return items

    def list_users(self) -> list[SlackUser]:
        return [SlackUser.from_api(m) for m in self._paginate("users.list", "members")]

    def get_user(self, user_id: str) -> SlackUser | None:
        try:
            payload = self._call("users.info", user=user_id)
        except SlackApiError as exc:
            if exc.error == "user_not_found":
                return None
            raise
        return SlackUser.from_api(payload["user"])

    def list_channels(self) -> list[SlackChannel]:
        rows = self._paginate(
            "conversations.list", "channels", types="public_channel,private_channel", exclude_archived=True
        )
        return [
            SlackChannel(
                id=str(r["id"]),
                name=r.get("name") or "",
                is_private=bool(r.get("is_private")),
                is_member=bool(r.get("is_member")),
            )
            for r in rows
        ]

    def channel_history(
        self, channel_id: str, *, oldest: str | None = None, include_replies: bool = True
    ) -> list[SlackMessage]:
        rows = self._paginate("conversations.history", "messages", channel=channel_id, oldest=oldest)
        messages = [SlackMessage.from_api(channel_id, r) for r in rows]
        if include_replies:
            parents = [r for r in rows if r.get("reply_count")]
            for parent in parents:
                replies = self._paginate(
                    "conversations.replies", "messages", channel=channel_id, ts=parent["ts"], oldest=oldest
                )
                for reply in replies:
                    if reply.get("ts") != parent["ts"]:
                        messages.append(SlackMessage.from_api(channel_id, reply))
        return sorted(messages, key=lambda m: float(m.ts))

    def open_dm(self, user_id: str) -> str:
        payload = self._call("conversations.open", users=user_id)
        return str(payload["channel"]["id"])

    def post_message(
        self, channel_id: str, text: str, *, blocks: list[dict[str, Any]] | None = None
    ) -> Posted:
        payload = self._call("chat.postMessage", channel=channel_id, text=text, blocks=blocks)
        return Posted(channel_id=str(payload["channel"]), ts=str(payload["ts"]))

    def post_ephemeral(self, channel_id: str, user_id: str, text: str) -> None:
        self._call("chat.postEphemeral", channel=channel_id, user=user_id, text=text)


def build_client(settings: Any | None = None) -> SlackClient:
    """The client the process should use, from settings."""
    from ..config import get_settings

    settings = settings or get_settings()
    if settings.fake_slack:
        return FakeSlackClient()
    return RealSlackClient(settings.slack_bot_token or "")


__all__ = [
    "FakeSlackClient",
    "OutboxItem",
    "Posted",
    "RealSlackClient",
    "SlackApiError",
    "SlackChannel",
    "SlackClient",
    "SlackMessage",
    "SlackUser",
    "build_client",
]
