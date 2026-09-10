"""Reminders: 24 hours, 1 hour and 10 minutes before a session or a due date.

Each reminder is a DM, rendered in the recipient's own time zone as Slack
reports it, carrying the Zoom link (sessions) or the assignment link. Three
rules keep this from being a nuisance:

* **Each person chooses.** ``/reminders`` toggles any interval off or on, per
  kind. A fellow who has already submitted can turn the 1-hour and 10-minute
  nudges off and keep the day-before one.
* **Never at 2am.** A reminder whose local send time falls between 22:00 and
  07:00 is skipped, not delayed — a 10-minute reminder sent at 8am for a
  session that ran overnight is worse than none.
* **Sent once.** ``reminder_sent`` records every (target, person, interval)
  before the DM goes out; a tick that runs twice, or restarts halfway, sends
  nothing twice.

A reminder is *due* when now is within the window ``[target − offset,
target − offset + tolerance)``. The tolerance is how often the tick runs;
a late tick still sends the reminder as long as the event has not started.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg

from ..db import execute, fetch_all, fetch_one
from ..logging_setup import get_logger
from ..timeutil import get_zone
from .client import SlackApiError, SlackClient
from .preferences import OFFSET_LABELS, get_preferences

log = get_logger(__name__)

QUIET_START_HOUR = 22
QUIET_END_HOUR = 7


@dataclass
class ReminderRun:
    considered: int = 0
    sent: int = 0
    skipped_pref: int = 0
    skipped_quiet: int = 0
    skipped_dup: int = 0
    failed: int = 0
    sent_to: list[tuple[str, str, int]] = field(default_factory=list)


def _local(now: datetime, tz: str | None) -> datetime:
    try:
        return now.astimezone(get_zone(tz or "UTC"))
    except Exception:  # noqa: BLE001 — an unparseable zone falls back to UTC
        return now.astimezone(timezone.utc)


def in_quiet_hours(now: datetime, tz: str | None) -> bool:
    hour = _local(now, tz).hour
    return hour >= QUIET_START_HOUR or hour < QUIET_END_HOUR


def format_when(at: datetime, tz: str | None) -> str:
    local = _local(at, tz)
    zone = local.tzname() or (tz or "UTC")
    return local.strftime("%a %b %-d, %-I:%M %p") + f" {zone}"


def _recipients(conn: psycopg.Connection, cohort_id: str) -> list[dict[str, Any]]:
    """Active fellows in the cohort who have a Slack account."""
    return fetch_all(
        conn,
        """
        select u.slack_user_id, u.tz, u.fellow_id, u.full_name
          from v_slack_user_resolved u
         where u.cohort_id = %s and u.fellow_status = 'active'
           and not u.is_bot and not u.deleted
         order by u.full_name
        """,
        (cohort_id,),
    )


def session_message(row: dict[str, Any], offset: int, tz: str | None) -> str:
    when = format_when(row["scheduled_at_utc"], tz)
    label = OFFSET_LABELS.get(offset, f"{offset} minutes")
    lines = [f"*{row['title']}* starts in {label} — {when}."]
    if row.get("zoom_link"):
        lines.append(f"Zoom: {row['zoom_link']}")
    else:
        lines.append("_No Zoom link has been added yet; check the channel._")
    lines.append("Please set your Zoom name to your real name so attendance is recorded.")
    lines.append("_Reply `/reminders` to change when you get these._")
    return "\n".join(lines)


def assignment_message(row: dict[str, Any], offset: int, tz: str | None) -> str:
    when = format_when(row["due_at_utc"], tz)
    label = OFFSET_LABELS.get(offset, f"{offset} minutes")
    lines = [f"*{row['title']}* is due in {label} — {when}."]
    if row.get("link"):
        lines.append(f"Submit here: {row['link']}")
    lines.append("_Already submitted? `/reminders assignment 1h off` and `/reminders assignment 10m off`._")
    return "\n".join(lines)


def _claim(conn: psycopg.Connection, kind: str, target_id: str, slack_user_id: str, offset: int) -> bool:
    row = fetch_one(
        conn,
        """
        insert into reminder_sent (target_kind, target_id, slack_user_id, offset_minutes)
        values (%s, %s, %s, %s)
        on conflict do nothing
        returning sent_at
        """,
        (kind, target_id, slack_user_id, offset),
    )
    return row is not None


def _release(conn: psycopg.Connection, kind: str, target_id: str, slack_user_id: str, offset: int) -> None:
    execute(
        conn,
        "delete from reminder_sent where target_kind = %s and target_id = %s and slack_user_id = %s and offset_minutes = %s",
        (kind, target_id, slack_user_id, offset),
    )


def due_targets(
    conn: psycopg.Connection, cohort_id: str, now: datetime, *, offsets: tuple[int, ...], tolerance: timedelta
) -> list[tuple[str, dict[str, Any], int]]:
    """(kind, row, offset) for every reminder whose window contains ``now``."""
    out: list[tuple[str, dict[str, Any], int]] = []
    horizon = now + timedelta(minutes=max(offsets) + 1)
    sessions = fetch_all(
        conn,
        """
        select session_id, title, scheduled_at_utc, zoom_link, timezone
          from "session" where cohort_id = %s and scheduled_at_utc > %s and scheduled_at_utc <= %s
        """,
        (cohort_id, now, horizon),
    )
    assignments = fetch_all(
        conn,
        "select assignment_id, title, due_at_utc, link from assignment where cohort_id = %s and due_at_utc > %s and due_at_utc <= %s",
        (cohort_id, now, horizon),
    )
    for kind, rows, key in (("session", sessions, "scheduled_at_utc"), ("assignment", assignments, "due_at_utc")):
        for row in rows:
            for offset in offsets:
                fire_at = row[key] - timedelta(minutes=offset)
                if fire_at <= now < min(fire_at + tolerance, row[key]):
                    out.append((kind, row, offset))
    return out


def run_reminders(
    conn: psycopg.Connection,
    client: SlackClient,
    *,
    cohort_id: str,
    now: datetime | None = None,
    tolerance: timedelta = timedelta(minutes=15),
) -> ReminderRun:
    """Send every reminder that is due. Safe to call as often as you like."""
    now = now or datetime.now(timezone.utc)
    run = ReminderRun()
    targets = due_targets(conn, cohort_id, now, offsets=(1440, 60, 10), tolerance=tolerance)
    if not targets:
        return run
    recipients = _recipients(conn, cohort_id)
    for kind, row, offset in targets:
        target_id = str(row["session_id"] if kind == "session" else row["assignment_id"])
        for person in recipients:
            run.considered += 1
            prefs = get_preferences(conn, person["slack_user_id"])
            wanted = prefs.session_reminders if kind == "session" else prefs.assignment_reminders
            if offset not in wanted:
                run.skipped_pref += 1
                continue
            if in_quiet_hours(now, person["tz"]):
                run.skipped_quiet += 1
                continue
            if not _claim(conn, kind, target_id, person["slack_user_id"], offset):
                run.skipped_dup += 1
                continue
            text = (
                session_message(row, offset, person["tz"])
                if kind == "session"
                else assignment_message(row, offset, person["tz"])
            )
            try:
                dm = client.open_dm(person["slack_user_id"])
                client.post_message(dm, text)
            except SlackApiError as exc:
                run.failed += 1
                _release(conn, kind, target_id, person["slack_user_id"], offset)
                log.warning("reminder to %s failed: %s", person["slack_user_id"], exc.error)
                continue
            run.sent += 1
            run.sent_to.append((person["slack_user_id"], target_id, offset))
    log.info(
        "reminders sent=%d skipped_pref=%d quiet=%d dup=%d failed=%d",
        run.sent, run.skipped_pref, run.skipped_quiet, run.skipped_dup, run.failed,
    )
    return run


def set_zoom_link(conn: psycopg.Connection, session_id: str, link: str | None) -> None:
    execute(
        conn,
        'update "session" set zoom_link = %s, updated_at = now() where session_id = %s',
        ((link or "").strip() or None, session_id),
    )


__all__ = [
    "ReminderRun",
    "assignment_message",
    "due_targets",
    "format_when",
    "in_quiet_hours",
    "run_reminders",
    "session_message",
    "set_zoom_link",
]
