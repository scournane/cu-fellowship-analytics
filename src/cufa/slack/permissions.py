"""Who may run which command.

Two tiers, no more. **Admins** are workspace admins (as Slack reports them)
plus any address in ``CUFA_SLACK_ADMINS``; they create sessions and
assignments, put in Zoom links, read attendance and profiles, and run reports.
**Fellows** — everyone else — receive reminders, toggle them, see their own
badges and dashboard, and press the check-in button. A fellow never sees another
fellow's data through the bot.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from ..config import Settings, get_settings
from ..db import fetch_one
from ..errors import CufaError

ADMIN_COMMANDS = frozenset(
    {
        "attendance",
        "fellow",
        "report",
        "assignment",
        "zoom",
        "leaderboard",
        "alias",
        "link",
        "alerts",
        "outreach",
        "digest",
        "sync",
        "score",
    }
)

FELLOW_COMMANDS = frozenset({"reminders", "badges", "checkin", "dashboard", "help", "me"})


class NotAllowed(CufaError):
    """The caller is not an admin and the command needs one."""


@dataclass(frozen=True)
class Caller:
    slack_user_id: str
    email: str | None
    is_admin: bool
    fellow_id: str | None
    full_name: str | None
    cohort_id: str | None


def identify(conn: psycopg.Connection, slack_user_id: str, settings: Settings | None = None) -> Caller:
    settings = settings or get_settings()
    row = fetch_one(
        conn,
        "select email, is_admin, fellow_id, full_name, cohort_id from v_slack_user_resolved where slack_user_id = %s",
        (slack_user_id,),
    ) or {}
    email = (row.get("email") or "").lower() or None
    is_admin = bool(row.get("is_admin")) or (email is not None and email in settings.slack_admins)
    return Caller(
        slack_user_id=slack_user_id,
        email=email,
        is_admin=is_admin,
        fellow_id=row.get("fellow_id"),
        full_name=row.get("full_name"),
        cohort_id=row.get("cohort_id") or settings.slack_cohort,
    )


def require_admin(caller: Caller, command: str) -> None:
    if not caller.is_admin:
        raise NotAllowed(
            f"`/{command}` is a staff command. Ask a workspace admin, or have your "
            f"address added to CUFA_SLACK_ADMINS."
        )


def is_admin_command(command: str) -> bool:
    return command in ADMIN_COMMANDS


__all__ = ["ADMIN_COMMANDS", "FELLOW_COMMANDS", "Caller", "NotAllowed", "identify", "is_admin_command", "require_admin"]
