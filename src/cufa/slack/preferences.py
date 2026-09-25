"""What a fellow has asked the bot to do, and not do.

Reminders default on at 24 hours, 1 hour and 10 minutes; gamification DMs
default on. Every default can be switched off by the person it concerns, from
Slack, without asking anyone. A row is written only once someone changes
something, so "no row" means "the defaults".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg

from ..db import execute, fetch_one
from ..errors import CufaError

DEFAULT_OFFSETS: tuple[int, ...] = (1440, 60, 10)
OFFSET_LABELS = {1440: "24 hours", 60: "1 hour", 10: "10 minutes"}


@dataclass(frozen=True)
class Preferences:
    slack_user_id: str
    session_reminders: tuple[int, ...] = DEFAULT_OFFSETS
    assignment_reminders: tuple[int, ...] = DEFAULT_OFFSETS
    gamification: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_reminders": list(self.session_reminders),
            "assignment_reminders": list(self.assignment_reminders),
            "gamification": self.gamification,
        }

    def describe(self) -> str:
        def fmt(offsets: tuple[int, ...]) -> str:
            if not offsets:
                return "off"
            return ", ".join(OFFSET_LABELS.get(o, f"{o} min") for o in sorted(offsets, reverse=True))

        return (
            f"Session reminders: {fmt(self.session_reminders)}\n"
            f"Assignment reminders: {fmt(self.assignment_reminders)}\n"
            f"Badges and streaks: {'on' if self.gamification else 'off'}"
        )


def get_preferences(conn: psycopg.Connection, slack_user_id: str) -> Preferences:
    row = fetch_one(conn, "select * from slack_preference where slack_user_id = %s", (slack_user_id,))
    if row is None:
        return Preferences(slack_user_id=slack_user_id)
    return Preferences(
        slack_user_id=slack_user_id,
        session_reminders=tuple(int(o) for o in row["session_reminders"]),
        assignment_reminders=tuple(int(o) for o in row["assignment_reminders"]),
        gamification=bool(row["gamification"]),
    )


def _save(conn: psycopg.Connection, prefs: Preferences) -> Preferences:
    if fetch_one(conn, "select 1 from slack_user where slack_user_id = %s", (prefs.slack_user_id,)) is None:
        raise CufaError(
            f"Slack user {prefs.slack_user_id} is not known yet; the next sync will record them."
        )
    execute(
        conn,
        """
        insert into slack_preference (slack_user_id, session_reminders, assignment_reminders, gamification)
        values (%s, %s, %s, %s)
        on conflict (slack_user_id) do update
           set session_reminders = excluded.session_reminders,
               assignment_reminders = excluded.assignment_reminders,
               gamification = excluded.gamification,
               updated_at = now()
        """,
        (
            prefs.slack_user_id,
            list(prefs.session_reminders),
            list(prefs.assignment_reminders),
            prefs.gamification,
        ),
    )
    return prefs


def parse_offset(token: str) -> int:
    """``24h`` / ``1h`` / ``10m`` / ``1440`` → minutes."""
    raw = (token or "").strip().lower()
    aliases = {"24h": 1440, "1d": 1440, "1h": 60, "60m": 60, "10m": 10, "10min": 10}
    if raw in aliases:
        return aliases[raw]
    if raw.endswith("h") and raw[:-1].isdigit():
        return int(raw[:-1]) * 60
    if raw.endswith("m") and raw[:-1].isdigit():
        return int(raw[:-1])
    if raw.isdigit():
        return int(raw)
    raise CufaError(f"{token!r} is not a reminder interval. Use 24h, 1h or 10m.")


def set_reminder(
    conn: psycopg.Connection, slack_user_id: str, *, kind: str, offset: int, enabled: bool
) -> Preferences:
    prefs = get_preferences(conn, slack_user_id)
    if kind not in ("session", "assignment"):
        raise CufaError("reminder kind must be session or assignment")
    current = set(prefs.session_reminders if kind == "session" else prefs.assignment_reminders)
    if enabled:
        current.add(offset)
    else:
        current.discard(offset)
    updated = tuple(sorted(current, reverse=True))
    if kind == "session":
        prefs = Preferences(slack_user_id, updated, prefs.assignment_reminders, prefs.gamification)
    else:
        prefs = Preferences(slack_user_id, prefs.session_reminders, updated, prefs.gamification)
    return _save(conn, prefs)


def set_all_reminders(conn: psycopg.Connection, slack_user_id: str, *, kind: str, enabled: bool) -> Preferences:
    prefs = get_preferences(conn, slack_user_id)
    offsets = DEFAULT_OFFSETS if enabled else ()
    if kind == "session":
        prefs = Preferences(slack_user_id, offsets, prefs.assignment_reminders, prefs.gamification)
    elif kind == "assignment":
        prefs = Preferences(slack_user_id, prefs.session_reminders, offsets, prefs.gamification)
    else:
        prefs = Preferences(slack_user_id, offsets, offsets, prefs.gamification)
    return _save(conn, prefs)


def set_gamification(conn: psycopg.Connection, slack_user_id: str, enabled: bool) -> Preferences:
    prefs = get_preferences(conn, slack_user_id)
    return _save(conn, Preferences(slack_user_id, prefs.session_reminders, prefs.assignment_reminders, enabled))


__all__ = [
    "DEFAULT_OFFSETS",
    "OFFSET_LABELS",
    "Preferences",
    "get_preferences",
    "parse_offset",
    "set_all_reminders",
    "set_gamification",
    "set_reminder",
]
