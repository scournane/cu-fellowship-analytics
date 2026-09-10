"""An in-memory Slack workspace, and a WebClient that answers from it.

Two front doors, one model:

* the test suite constructs ``FakeWorkspace`` directly and hands
  ``FakeSlackWebClient`` to the code under test;
* ``scripts/fake_slack_server.py`` wraps the same two classes in an HTTP
  server, so the *real* Bolt app — real signature check, real
  ``slack_sdk.WebClient`` pointed at ``http://localhost:3001/api/`` — can be
  driven from a browser with no Slack account anywhere.

The fake raises ``SlackApiError`` exactly where the real client would, so a
caller that handles ``users_not_found`` correctly against the fake handles it
correctly against Slack.

Deliberate limits: it knows nothing about permissions, rate limits or scopes.
Those are documented in docs/setup/slack-bot.md; a fake that pretended to
enforce them would only prove that the fake enforces them.
"""

from __future__ import annotations

import csv
import itertools
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from slack_sdk.errors import SlackApiError

from ..text import normalize_email

TEAM_ID = "T0DEMO000"
TEAM_NAME = "CIF Demo Workspace"
BOT_USER_ID = "U0DEMOBOT"

DEFAULT_CHANNELS = (
    ("general", False),
    ("announcements", False),
    ("help-desk", False),
    ("project-teams", False),
    ("q-and-a", False),
    ("cohort-private", True),
)

WORKSPACE_URL = "https://demo.slack.invalid/"


def _err(method: str, error: str) -> SlackApiError:
    return SlackApiError(f"{method}: {error}", {"ok": False, "error": error})


@dataclass
class FakeWorkspace:
    """The state a Slack workspace would hold. Mutated by the demo UI."""

    team_id: str = TEAM_ID
    team_name: str = TEAM_NAME
    bot_user_id: str = BOT_USER_ID
    users: dict[str, dict[str, Any]] = field(default_factory=dict)
    channels: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: channel_id -> TOP-LEVEL messages, oldest first. What conversations.history
    #: reads. Thread replies are not here — as on Slack, they only come back
    #: through conversations.replies.
    history: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    #: channel_id -> thread ts -> replies, oldest first.
    replies: dict[str, dict[str, list[dict[str, Any]]]] = field(default_factory=dict)
    #: Everything chat.postMessage was asked to send.
    posted: list[dict[str, Any]] = field(default_factory=list)
    _counter: itertools.count = field(default_factory=lambda: itertools.count(1), repr=False)
    _ts_base: float = field(default_factory=lambda: float(int(time.time())), repr=False)
    _ts_seq: itertools.count = field(default_factory=lambda: itertools.count(1), repr=False)

    def __post_init__(self) -> None:
        self.users[self.bot_user_id] = {
            "id": self.bot_user_id,
            "name": "cufa-bot",
            "real_name": "CUFA participation bot",
            "is_bot": True,
            "deleted": False,
            "profile": {"display_name": "cufa-bot", "email": None},
        }
        for name, private in DEFAULT_CHANNELS:
            self.add_channel(name, private=private)

    # -- construction ---------------------------------------------------------

    def next_id(self, prefix: str) -> str:
        return f"{prefix}0DEMO{next(self._counter):04d}"

    def next_ts(self, *, offset_seconds: float = 0.0) -> str:
        """A monotonic Slack ts. Real ones are epoch seconds plus a sequence."""
        seq = next(self._ts_seq)
        return f"{self._ts_base + offset_seconds + seq * 0.001:.6f}"

    def add_user(
        self,
        *,
        email: str | None,
        real_name: str,
        display_name: str | None = None,
        is_bot: bool = False,
        deleted: bool = False,
        user_id: str | None = None,
    ) -> str:
        uid = user_id or self.next_id("U")
        handle = (display_name or real_name.split()[0]).lower()
        self.users[uid] = {
            "id": uid,
            "name": handle,
            "real_name": real_name,
            "is_bot": is_bot,
            "deleted": deleted,
            "profile": {
                "display_name": display_name or handle,
                "real_name": real_name,
                "email": normalize_email(email) if email else None,
            },
        }
        return uid

    def add_channel(self, name: str, *, private: bool = False, channel_id: str | None = None) -> str:
        cid = channel_id or self.next_id("G" if private else "C")
        self.channels[cid] = {
            "id": cid,
            "name": name,
            "is_private": private,
            "is_member": True,
            "is_channel": not private,
            "is_group": private,
        }
        self.history.setdefault(cid, [])
        self.replies.setdefault(cid, {})
        return cid

    def find_message(self, channel_id: str, ts: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None]:
        """A stored message and the list it lives in — top level or a thread."""
        for msg in self.history.get(channel_id, []):
            if msg.get("ts") == ts:
                return msg, self.history[channel_id]
        for thread in self.replies.get(channel_id, {}).values():
            for msg in thread:
                if msg.get("ts") == ts:
                    return msg, thread
        return None, None

    def channel_id(self, name: str) -> str:
        for cid, ch in self.channels.items():
            if ch["name"] == name:
                return cid
        raise KeyError(name)

    def user_for_email(self, email: str) -> str | None:
        wanted = normalize_email(email)
        for uid, user in self.users.items():
            if (user.get("profile") or {}).get("email") == wanted:
                return uid
        return None

    def seed_from_roster(self, path: str | Path) -> list[str]:
        """Give every fellow on a roster CSV a Slack account.

        Same file `cufa load-roster` reads, so the demo's Slack identities line
        up with the demo's roster by construction rather than by copying names
        into a second place.
        """
        ids: list[str] = []
        with Path(path).open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                email = row.get("primary_email") or row.get("email") or ""
                name = row.get("full_name") or row.get("name") or email
                if not email:
                    continue
                ids.append(self.add_user(email=email, real_name=name))
        return ids

    # -- event payloads -------------------------------------------------------

    def message_event(
        self,
        user_id: str,
        channel_id: str,
        text: str,
        *,
        ts: str | None = None,
        thread_ts: str | None = None,
        files: list[dict[str, Any]] | None = None,
        record: bool = True,
    ) -> dict[str, Any]:
        """A ``message`` event as Slack would deliver it.

        ``record=True`` also appends it to the channel's history, so a later
        backfill reads back the same message the live bot saw — which is the
        idempotency case the design most needs to hold.
        """
        ts = ts or self.next_ts()
        channel = self.channels[channel_id]
        event = {
            "type": "message",
            "user": user_id,
            "text": text,
            "ts": ts,
            "channel": channel_id,
            "channel_type": "group" if channel["is_private"] else "channel",
            "event_ts": ts,
            "team": self.team_id,
        }
        if thread_ts:
            event["thread_ts"] = thread_ts
        if files:
            event["files"] = files
        if record:
            stored = {k: v for k, v in event.items() if k not in ("channel", "channel_type", "event_ts")}
            if thread_ts and thread_ts != ts:
                self.replies.setdefault(channel_id, {}).setdefault(thread_ts, []).append(stored)
                parent, _ = self.find_message(channel_id, thread_ts)
                if parent is not None:
                    # What Slack puts on a parent once it has replies.
                    parent["thread_ts"] = parent["ts"]
                    parent["reply_count"] = len(self.replies[channel_id][thread_ts])
                    parent["latest_reply"] = ts
                    users = parent.setdefault("reply_users", [])
                    if user_id not in users:
                        users.append(user_id)
            else:
                self.history[channel_id].append(stored)
        return event

    def bot_message_event(self, channel_id: str, text: str) -> dict[str, Any]:
        ts = self.next_ts()
        return {
            "type": "message",
            "subtype": "bot_message",
            "bot_id": "B0DEMOBOT",
            "text": text,
            "ts": ts,
            "channel": channel_id,
            "channel_type": "channel",
            "event_ts": ts,
        }

    def edit_event(self, original: dict[str, Any], new_text: str) -> dict[str, Any]:
        edited_ts = self.next_ts()
        inner = {k: v for k, v in original.items() if k not in ("channel", "channel_type", "event_ts")}
        inner["text"] = new_text
        inner["edited"] = {"user": original["user"], "ts": edited_ts}
        msg, _ = self.find_message(original["channel"], original["ts"])
        if msg is not None:
            msg["text"] = new_text
            msg["edited"] = inner["edited"]
        return {
            "type": "message",
            "subtype": "message_changed",
            "channel": original["channel"],
            "channel_type": original.get("channel_type"),
            "ts": edited_ts,
            "event_ts": edited_ts,
            "message": inner,
            "previous_message": {k: v for k, v in original.items() if k not in ("channel", "channel_type", "event_ts")},
        }

    def delete_event(self, original: dict[str, Any]) -> dict[str, Any]:
        ts = self.next_ts()
        msg, container = self.find_message(original["channel"], original["ts"])
        if msg is not None and container is not None:
            container.remove(msg)
        return {
            "type": "message",
            "subtype": "message_deleted",
            "channel": original["channel"],
            "channel_type": original.get("channel_type"),
            "ts": ts,
            "event_ts": ts,
            "deleted_ts": original["ts"],
            "previous_message": {k: v for k, v in original.items() if k not in ("channel", "channel_type", "event_ts")},
        }

    def reaction_event(
        self,
        user_id: str,
        channel_id: str,
        item_ts: str,
        reaction: str,
        *,
        removed: bool = False,
        item_user: str | None = None,
        record: bool = True,
    ) -> dict[str, Any]:
        target, _ = self.find_message(channel_id, item_ts)
        if item_user is None and target is not None:
            item_user = target.get("user")
        if record and target is not None:
            blocks = target.setdefault("reactions", [])
            for block in blocks:
                if block["name"] == reaction:
                    if removed and user_id in block["users"]:
                        block["users"].remove(user_id)
                    elif not removed and user_id not in block["users"]:
                        block["users"].append(user_id)
                    block["count"] = len(block["users"])
                    break
            else:
                if not removed:
                    blocks.append({"name": reaction, "users": [user_id], "count": 1})
            target["reactions"] = [b for b in blocks if b["users"]]
        return {
            "type": "reaction_removed" if removed else "reaction_added",
            "user": user_id,
            "reaction": reaction,
            "item": {"type": "message", "channel": channel_id, "ts": item_ts},
            "item_user": item_user,
            "event_ts": self.next_ts(),
        }

    def mention_event(self, user_id: str, channel_id: str, text: str, *, thread_ts: str | None = None) -> dict[str, Any]:
        """An ``app_mention`` event. Slack sends this IN ADDITION to the
        ``message`` event for the same post; the fake server delivers both."""
        ts = self.next_ts()
        event = {
            "type": "app_mention",
            "user": user_id,
            "text": f"<@{self.bot_user_id}> {text}",
            "ts": ts,
            "channel": channel_id,
            "event_ts": ts,
            "team": self.team_id,
        }
        if thread_ts:
            event["thread_ts"] = thread_ts
        return event

    def join_event(self, user_id: str, channel_id: str, *, left: bool = False) -> dict[str, Any]:
        channel = self.channels[channel_id]
        return {
            "type": "member_left_channel" if left else "member_joined_channel",
            "user": user_id,
            "channel": channel_id,
            "channel_type": "G" if channel["is_private"] else "C",
            "team": self.team_id,
            "event_ts": self.next_ts(),
        }

    def envelope(self, event: dict[str, Any], *, event_id: str | None = None) -> dict[str, Any]:
        """The Events API wrapper Slack POSTs to the request URL."""
        return {
            "token": "demo-verification-token",
            "team_id": self.team_id,
            "api_app_id": "A0DEMOAPP",
            "event": event,
            "type": "event_callback",
            "event_id": event_id or f"Ev{next(self._counter):08d}",
            "event_time": int(float(event.get("event_ts") or event.get("ts") or time.time())),
            "authorizations": [
                {"team_id": self.team_id, "user_id": self.bot_user_id, "is_bot": True}
            ],
        }


class FakeSlackWebClient:
    """The subset of ``slack_sdk.WebClient`` this package calls.

    Responses are plain dicts. The real client returns ``SlackResponse``, which
    also supports ``resp["key"]`` and ``resp.get(...)``, so code written against
    this fake runs unchanged against Slack — as long as it uses mapping access
    and never ``resp.data``.
    """

    is_fake = True

    def __init__(self, workspace: FakeWorkspace, *, page_size: int = 100) -> None:
        self.ws = workspace
        self.page_size = page_size
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, method: str, **kwargs: Any) -> None:
        self.calls.append((method, kwargs))

    def call_count(self, method: str) -> int:
        return sum(1 for m, _ in self.calls if m == method)

    # -- auth -----------------------------------------------------------------

    def auth_test(self, **kwargs: Any) -> dict[str, Any]:
        self._record("auth.test")
        return {
            "ok": True,
            "url": WORKSPACE_URL,
            "team": self.ws.team_name,
            "team_id": self.ws.team_id,
            "user": "cufa-bot",
            "user_id": self.ws.bot_user_id,
            "bot_id": "B0DEMOBOT",
        }

    # -- users ----------------------------------------------------------------

    def users_info(self, *, user: str, **kwargs: Any) -> dict[str, Any]:
        self._record("users.info", user=user)
        record = self.ws.users.get(user)
        if record is None:
            raise _err("users.info", "user_not_found")
        return {"ok": True, "user": record}

    def users_list(self, *, cursor: str | None = None, limit: int | None = None, **kwargs: Any) -> dict[str, Any]:
        self._record("users.list", cursor=cursor)
        return self._page(list(self.ws.users.values()), "members", cursor, limit)

    # -- conversations --------------------------------------------------------

    def conversations_list(self, *, cursor: str | None = None, limit: int | None = None, types: str | None = None, **kwargs: Any) -> dict[str, Any]:
        self._record("conversations.list", cursor=cursor, types=types)
        wanted = set((types or "public_channel").split(","))
        rows = [
            ch for ch in self.ws.channels.values()
            if ("private_channel" in wanted and ch["is_private"])
            or ("public_channel" in wanted and not ch["is_private"])
        ]
        return self._page(rows, "channels", cursor, limit)

    def conversations_info(self, *, channel: str, **kwargs: Any) -> dict[str, Any]:
        self._record("conversations.info", channel=channel)
        record = self.ws.channels.get(channel)
        if record is None:
            raise _err("conversations.info", "channel_not_found")
        return {"ok": True, "channel": record}

    def conversations_history(
        self,
        *,
        channel: str,
        oldest: str | None = None,
        latest: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
        inclusive: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self._record("conversations.history", channel=channel, oldest=oldest, cursor=cursor)
        if channel not in self.ws.channels:
            raise _err("conversations.history", "channel_not_found")
        rows = list(self.ws.history.get(channel, []))
        if oldest is not None:
            bound = float(oldest)
            rows = [m for m in rows if (float(m["ts"]) >= bound if inclusive else float(m["ts"]) > bound)]
        if latest is not None:
            bound = float(latest)
            rows = [m for m in rows if (float(m["ts"]) <= bound if inclusive else float(m["ts"]) < bound)]
        # Slack returns newest first.
        rows.sort(key=lambda m: float(m["ts"]), reverse=True)
        return self._page(rows, "messages", cursor, limit)

    def conversations_replies(
        self,
        *,
        channel: str,
        ts: str,
        cursor: str | None = None,
        limit: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """The parent first, then its replies oldest first — Slack's order."""
        self._record("conversations.replies", channel=channel, ts=ts, cursor=cursor)
        if channel not in self.ws.channels:
            raise _err("conversations.replies", "channel_not_found")
        parent, _ = self.ws.find_message(channel, ts)
        if parent is None:
            raise _err("conversations.replies", "thread_not_found")
        rows = [parent, *self.ws.replies.get(channel, {}).get(ts, [])]
        return self._page(rows, "messages", cursor, limit)

    def conversations_members(self, *, channel: str, cursor: str | None = None, limit: int | None = None, **kwargs: Any) -> dict[str, Any]:
        self._record("conversations.members", channel=channel)
        if channel not in self.ws.channels:
            raise _err("conversations.members", "channel_not_found")
        return self._page([uid for uid in self.ws.users if uid != self.ws.bot_user_id], "members", cursor, limit)

    # -- chat -----------------------------------------------------------------

    def chat_postMessage(self, *, channel: str, text: str = "", **kwargs: Any) -> dict[str, Any]:
        self._record("chat.postMessage", channel=channel)
        if channel not in self.ws.channels:
            raise _err("chat.postMessage", "channel_not_found")
        ts = self.ws.next_ts()
        self.ws.posted.append({"channel": channel, "text": text, "ts": ts, **{k: v for k, v in kwargs.items() if k in ("blocks", "thread_ts")}})
        return {"ok": True, "channel": channel, "ts": ts, "message": {"text": text, "ts": ts}}

    def chat_getPermalink(self, *, channel: str, message_ts: str, **kwargs: Any) -> dict[str, Any]:
        self._record("chat.getPermalink", channel=channel, message_ts=message_ts)
        if channel not in self.ws.channels:
            raise _err("chat.getPermalink", "channel_not_found")
        msg, _ = self.ws.find_message(channel, message_ts)
        if msg is None:
            raise _err("chat.getPermalink", "message_not_found")
        link = f"{WORKSPACE_URL}archives/{channel}/p{message_ts.replace('.', '')}"
        thread = msg.get("thread_ts")
        if thread and thread != message_ts:
            link += f"?thread_ts={thread}&cid={channel}"
        return {"ok": True, "channel": channel, "permalink": link}

    # -- pagination -----------------------------------------------------------

    def _page(self, rows: list[Any], key: str, cursor: str | None, limit: int | None) -> dict[str, Any]:
        # page_size is the SERVER-side cap, as on Slack: a caller may ask for
        # fewer, never more. This is what lets a test force pagination.
        size = min(int(limit), self.page_size) if limit else self.page_size
        start = int(cursor) if cursor else 0
        chunk = rows[start:start + size]
        has_more = start + size < len(rows)
        return {
            "ok": True,
            key: chunk,
            "has_more": has_more,
            "response_metadata": {"next_cursor": str(start + size) if has_more else ""},
        }

    # -- HTTP dispatch (used by the demo server) -------------------------------

    METHODS = {
        "auth.test": "auth_test",
        "users.info": "users_info",
        "users.list": "users_list",
        "conversations.list": "conversations_list",
        "conversations.info": "conversations_info",
        "conversations.history": "conversations_history",
        "conversations.replies": "conversations_replies",
        "conversations.members": "conversations_members",
        "chat.postMessage": "chat_postMessage",
        "chat.getPermalink": "chat_getPermalink",
    }

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Route ``/api/<method>`` to the matching method. Errors become {ok:false}."""
        attr = self.METHODS.get(method)
        if attr is None:
            return {"ok": False, "error": "unknown_method"}
        clean = {}
        for k, v in params.items():
            if k in ("token",):
                continue
            if k == "inclusive":
                v = str(v).lower() in ("1", "true")
            if k == "limit" and v not in (None, ""):
                v = int(v)
            clean[k] = v
        try:
            return getattr(self, attr)(**clean)
        except SlackApiError as exc:
            return {"ok": False, "error": exc.response.get("error", "unknown_error")}
        except TypeError as exc:
            return {"ok": False, "error": f"invalid_arguments: {exc}"}


def demo_workspace(roster_path: str | Path | None = None, *, extra_users: Iterable[tuple[str, str | None]] = ()) -> FakeWorkspace:
    """The workspace the demo runs against: the fixture roster plus a few strangers.

    The strangers matter. A guest speaker with no roster email, a deactivated
    account, and someone whose email is not on the roster are the cases the
    identity path has to handle *without* dropping their events.
    """
    ws = FakeWorkspace()
    if roster_path and Path(roster_path).exists():
        ws.seed_from_roster(roster_path)
    ws.add_user(email="guest.speaker@example.invalid", real_name="Guest Speaker", display_name="guest")
    ws.add_user(email=None, real_name="No Email On Profile", display_name="noemail")
    ws.add_user(email="former.fellow@example.invalid", real_name="Former Fellow", display_name="former", deleted=True)
    for real_name, email in extra_users:
        ws.add_user(email=email, real_name=real_name)
    return ws
