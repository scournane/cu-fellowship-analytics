"""slack_user_id → email, cached.

The event stream carries only a workspace-scoped user id. ``users.info`` turns
that into the profile email (with the ``users:read.email`` scope), which is
what joins to the roster. Cached in ``slack_user`` so a busy channel does not
cost one API call per message, and refreshed on a timer so a fellow who
changes their email is picked up within a day.

Identity never blocks ingest. A user with no email on their profile, a bot, a
deactivated account, an id ``users.info`` refuses to return — every one of
those still produces an event row, with ``user_email`` NULL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from slack_sdk.errors import SlackApiError

from ..db import execute, fetch_one
from ..logging_setup import get_logger, mask_email
from ..text import normalize_email

log = get_logger(__name__)

DEFAULT_MAX_AGE = timedelta(hours=24)


@dataclass(frozen=True)
class SlackUserRecord:
    team_id: str
    slack_user_id: str
    email: str | None
    display_name: str | None
    real_name: str | None
    is_bot: bool
    is_deleted: bool
    fetched_at: datetime

    @property
    def resolvable(self) -> bool:
        """Whether there is an address worth trying to match to the roster."""
        return bool(self.email) and not self.is_bot


def _from_profile(team_id: str, payload: dict[str, Any]) -> SlackUserRecord:
    profile = payload.get("profile") or {}
    email = normalize_email(profile.get("email")) or None
    return SlackUserRecord(
        team_id=team_id,
        slack_user_id=payload["id"],
        email=email,
        display_name=profile.get("display_name") or payload.get("name"),
        real_name=profile.get("real_name") or payload.get("real_name"),
        is_bot=bool(payload.get("is_bot")) or payload["id"] == "USLACKBOT",
        is_deleted=bool(payload.get("deleted")),
        fetched_at=datetime.now(timezone.utc),
    )


def _upsert(conn: psycopg.Connection, record: SlackUserRecord) -> None:
    execute(
        conn,
        """
        insert into slack_user
            (team_id, slack_user_id, email, display_name, real_name, is_bot, is_deleted, fetched_at)
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (team_id, slack_user_id) do update
           set email = excluded.email,
               display_name = excluded.display_name,
               real_name = excluded.real_name,
               is_bot = excluded.is_bot,
               is_deleted = excluded.is_deleted,
               fetched_at = excluded.fetched_at
        """,
        (
            record.team_id, record.slack_user_id, record.email, record.display_name,
            record.real_name, record.is_bot, record.is_deleted, record.fetched_at,
        ),
    )


def _cached(conn: psycopg.Connection, team_id: str, user_id: str) -> SlackUserRecord | None:
    row = fetch_one(
        conn,
        "select * from slack_user where team_id = %s and slack_user_id = %s",
        (team_id, user_id),
    )
    if not row:
        return None
    return SlackUserRecord(
        team_id=row["team_id"],
        slack_user_id=row["slack_user_id"],
        email=row["email"],
        display_name=row["display_name"],
        real_name=row["real_name"],
        is_bot=bool(row["is_bot"]),
        is_deleted=bool(row["is_deleted"]),
        fetched_at=row["fetched_at"],
    )


def resolve_user(
    conn: psycopg.Connection,
    client: Any,
    team_id: str,
    user_id: str,
    *,
    max_age: timedelta = DEFAULT_MAX_AGE,
    force: bool = False,
) -> SlackUserRecord:
    """The cached record for a user, refreshed from ``users.info`` when stale.

    Never raises for a user Slack will not describe: the record comes back
    with no email and the event is still written.
    """
    cached = None if force else _cached(conn, team_id, user_id)
    if cached is not None:
        age = datetime.now(timezone.utc) - cached.fetched_at
        if age <= max_age:
            return cached

    try:
        response = client.users_info(user=user_id)
        record = _from_profile(team_id, response["user"])
    except SlackApiError as exc:
        error = (exc.response or {}).get("error", "unknown") if hasattr(exc, "response") else "unknown"
        log.warning("users.info failed for %s: %s — event will be unattributed", user_id, error)
        record = SlackUserRecord(
            team_id=team_id, slack_user_id=user_id, email=cached.email if cached else None,
            display_name=cached.display_name if cached else None,
            real_name=cached.real_name if cached else None,
            is_bot=cached.is_bot if cached else False,
            is_deleted=cached.is_deleted if cached else False,
            fetched_at=datetime.now(timezone.utc),
        )

    _upsert(conn, record)
    log.debug(
        "user %s resolved email=%s bot=%s deleted=%s",
        user_id, mask_email(record.email) if record.email else "-", record.is_bot, record.is_deleted,
    )
    return record


def sync_users(conn: psycopg.Connection, client: Any, team_id: str) -> int:
    """Refresh every member via ``users.list``. Returns how many were written.

    Run once on connect and then occasionally; the per-event path only refreshes
    the users it actually sees.
    """
    written = 0
    cursor: str | None = None
    while True:
        response = client.users_list(cursor=cursor, limit=200)
        for payload in response.get("members") or []:
            _upsert(conn, _from_profile(team_id, payload))
            written += 1
        cursor = ((response.get("response_metadata") or {}).get("next_cursor") or "").strip()
        if not cursor:
            break
    log.info("slack users synced team=%s written=%d", team_id, written)
    return written
