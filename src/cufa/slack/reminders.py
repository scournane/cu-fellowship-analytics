"""Idempotent outbound Slack reminders, digests, nudges, and agendas.

This is the one engine that DMs fellows about sessions and assignments. The
reminder/badge half of the bot (``digest.tick``, ``cufa slack tick`` from
cron, the ``/reminders`` command) reaches it through :func:`run_reminders`
at the bottom of this module, so there is one dedupe table, one quiet-hours
rule and one timezone rule however a reminder is triggered.

Two preference commands, both honoured: ``/cufa-reminders`` sets the cadence
(all / fewer / later / none), the timezone and the quiet hours;
``/reminders session 10m off`` switches a single interval off. An interval
goes out only when the cadence includes it *and* the fellow has not switched
it off.

The scheduler polls; the database decides whether a message may be sent. That
is important operationally: restarting the bot, running a manual tick, or
briefly running two bot processes must not duplicate a DM. Part B nudges have a
second, independent database constraint that makes a third nudge impossible.
"""

from __future__ import annotations

import threading
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import psycopg

from dataclasses import dataclass, field

from ..config import Settings, get_settings
from ..db import connection, execute, fetch_all, fetch_one
from ..errors import CufaError
from ..logging_setup import get_logger
from ..timeutil import get_zone, to_utc
from .preferences import Preferences, get_preferences
from .users import resolve_user, sync_users

log = get_logger(__name__)

SESSION_OFFSETS: dict[str, tuple[int, ...]] = {
    "all": (24 * 60, 60, 10),
    "fewer": (60,),
    "later": (10,),
    "none": (),
}
ASSIGNMENT_OFFSETS: dict[str, tuple[int, ...]] = {
    "all": (24 * 60, 60, 10),
    "fewer": (24 * 60,),
    "later": (60,),
    "none": (),
}
# (nudge number, minutes after the end of the session + grace)
NUDGE_OFFSETS: dict[str, tuple[tuple[int, int], ...]] = {
    "all": ((1, 30), (2, 24 * 60)),
    "fewer": ((1, 24 * 60),),
    "later": ((1, 4 * 60), (2, 24 * 60)),
    "none": (),
}
NUDGE_EXPIRY_MINUTES = 48 * 60
DELIVERY_RETRY_AFTER = timedelta(minutes=5)


def parse_clock(value: str) -> time:
    """Parse a configured local clock time without accepting ambiguous forms."""
    try:
        return datetime.strptime(value.strip(), "%H:%M").time()
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"Expected a 24-hour time like 21:00, got {value!r}.") from exc


def is_quiet_time(
    instant: datetime, timezone_name: str, quiet_start: time, quiet_end: time
) -> bool:
    """Whether ``instant`` falls in an overnight local quiet-hours interval."""
    local_clock = to_utc(instant).astimezone(get_zone(timezone_name)).time().replace(
        tzinfo=None
    )
    if quiet_start == quiet_end:
        return False
    if quiet_start < quiet_end:
        return quiet_start <= local_clock < quiet_end
    return local_clock >= quiet_start or local_clock < quiet_end


def _slack_text(value: object) -> str:
    return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _slack_link(url: str, label: str) -> str:
    clean = url.replace("|", "%7C").replace(">", "%3E")
    return f"<{clean}|{_slack_text(label)}>"


def _first_name(full_name: str) -> str:
    return _slack_text((full_name or "").strip().split(maxsplit=1)[0] or "there")


def _local_when(instant: datetime, zone: ZoneInfo) -> str:
    local = to_utc(instant).astimezone(zone)
    hour = local.strftime("%I").lstrip("0") or "0"
    return (
        f"{local:%a, %b} {local.day} at {hour}:{local:%M %p} {local:%Z}"
    )


def _before_window(
    target: datetime, offsets: tuple[int, ...], now: datetime
) -> tuple[int, datetime] | None:
    """Return the one active reminder stage before ``target``, if any."""
    for index, offset in enumerate(offsets):
        trigger = target - timedelta(minutes=offset)
        expiry = (
            target - timedelta(minutes=offsets[index + 1])
            if index + 1 < len(offsets)
            else target
        )
        if trigger <= now < expiry:
            return offset, trigger
    return None


def _after_window(
    target: datetime,
    offsets: tuple[tuple[int, int], ...],
    now: datetime,
) -> tuple[int, int, datetime] | None:
    for index, (number, offset) in enumerate(offsets):
        trigger = target + timedelta(minutes=offset)
        expiry_offset = (
            offsets[index + 1][1]
            if index + 1 < len(offsets)
            else NUDGE_EXPIRY_MINUTES
        )
        expiry = target + timedelta(minutes=expiry_offset)
        if trigger <= now < expiry:
            return number, offset, trigger
    return None


def _offset_label(minutes: int) -> str:
    if minutes == 24 * 60:
        return "24 hours"
    if minutes == 60:
        return "1 hour"
    return f"{minutes} minutes"


def _zoom_line(session: dict[str, Any]) -> str:
    if session.get("zoom_url"):
        return _slack_link(session["zoom_url"], "Join Zoom")
    return "_No Zoom link has been added yet; check the channel._"


def session_reminder_text(
    fellow_name: str, session: dict[str, Any], minutes: int, zone: ZoneInfo
) -> str:
    return (
        f"Hi {_first_name(fellow_name)}, reminder: *{_slack_text(session['title'])}* "
        f"starts in {_offset_label(minutes)} ({_local_when(session['scheduled_at_utc'], zone)}). "
        f"{_zoom_line(session)}"
    )


def assignment_reminder_text(
    fellow_name: str, assignment: dict[str, Any], minutes: int, zone: ZoneInfo
) -> str:
    link = (
        " " + _slack_link(assignment["url"], "Open assignment")
        if assignment.get("url")
        else ""
    )
    return (
        f"Hi {_first_name(fellow_name)}, *{_slack_text(assignment['title'])}* is due "
        f"in {_offset_label(minutes)} ({_local_when(assignment['due_at_utc'], zone)}).{link}"
    )


def nudge_text(
    fellow_name: str, session: dict[str, Any], nudge_number: int
) -> str:
    title = _slack_text(session["title"])
    link = _slack_link(session["form_url"], "Complete Part B")
    if nudge_number == 1:
        return (
            f"Hi {_first_name(fellow_name)}, I couldn't find a Part B check-in for "
            f"*{title}*. If you still want to add yours, here's the form: {link}. "
            "If you already sent it, you're all set; responses can take a moment to sync."
        )
    return (
        f"Hi {_first_name(fellow_name)}, one last reminder for *{title}* Part B: "
        f"{link}. If you already submitted, no action is needed. I won't send "
        "another reminder for this session."
    )


class ReminderEngine:
    """Compute and deliver every automation that is due at one UTC instant."""

    def __init__(
        self,
        settings: Settings,
        client: Any,
        *,
        team_id: str,
        cohort_id: str,
    ) -> None:
        self.settings = settings
        self.client = client
        self.team_id = team_id
        self.cohort_id = cohort_id
        self.default_zone = get_zone(settings.slack_default_fellow_timezone)
        self.default_quiet_start = parse_clock(settings.slack_quiet_start)
        self.default_quiet_end = parse_clock(settings.slack_quiet_end)
        if settings.slack_digest_weekday not in range(7):
            raise ValueError("CUFA_SLACK_DIGEST_WEEKDAY must be between 0 and 6.")
        if settings.slack_digest_hour not in range(24):
            raise ValueError("CUFA_SLACK_DIGEST_HOUR must be between 0 and 23.")
        self._channel_cache: dict[str, str | None] = {}
        #: fellow_id -> (quiet_start, quiet_end) learned from when they are
        #: actually around. Filled per run; consulted only for fellows who set
        #: no quiet hours of their own.
        self._observed_quiet: dict[str, tuple[time, time]] = {}
        #: slack_user_id -> the per-interval switches from ``/reminders``.
        self._interval_prefs: dict[str, Preferences] = {}

    def run_once(
        self, conn: psycopg.Connection, *, now: datetime | None = None
    ) -> dict[str, int]:
        instant = to_utc(now or datetime.now(timezone.utc))
        counts: Counter[str] = Counter()
        self._interval_prefs = {}
        self._dispatch_agendas(conn, instant, counts)
        fellows = self._fellows(conn)
        self._learn_quiet_hours(conn, fellows, counts)
        self._dispatch_session_reminders(conn, fellows, instant, counts)
        self._dispatch_assignment_reminders(conn, fellows, instant, counts)
        self._dispatch_part_b_nudges(conn, fellows, instant, counts)
        self._dispatch_weekly_digests(conn, fellows, instant, counts)
        return dict(counts)

    def run_reminders_only(
        self, conn: psycopg.Connection, *, now: datetime | None = None
    ) -> dict[str, int]:
        """Session and assignment reminders only — what ``digest.tick`` asks for.

        Nudges, agendas and the fellow digests stay with :meth:`run_once`,
        which the automation loop runs.
        """
        instant = to_utc(now or datetime.now(timezone.utc))
        counts: Counter[str] = Counter()
        self._interval_prefs = {}
        fellows = self._fellows(conn)
        self._learn_quiet_hours(conn, fellows, counts)
        self._dispatch_session_reminders(conn, fellows, instant, counts)
        self._dispatch_assignment_reminders(conn, fellows, instant, counts)
        return dict(counts)

    def _fellows(self, conn: psycopg.Connection) -> list[dict[str, Any]]:
        return fetch_all(
            conn,
            """
            select f.fellow_id, f.full_name, f.primary_email, f.cohort_id,
                   coalesce(p.mode, 'all') as reminder_mode,
                   coalesce(p.timezone, f.timezone, su.tz, %s::text) as reminder_timezone,
                   coalesce(p.quiet_start_local, %s::time) as quiet_start_local,
                   coalesce(p.quiet_end_local, %s::time) as quiet_end_local,
                   (p.quiet_start_local is not null) as has_quiet_preference,
                   su.slack_user_id
              from fellow f
              left join fellow_reminder_preference p on p.fellow_id = f.fellow_id
              left join lateral (
                  -- The same resolution the staff commands use: a manual
                  -- link, then any address on the roster record (aliases
                  -- included), never a guess.
                  select r.slack_user_id, r.tz
                    from v_slack_user_resolved r
                   where r.team_id = %s
                     and r.fellow_id = f.fellow_id
                     and not r.is_bot and not r.deleted
                   order by r.last_seen_at desc nulls last
                   limit 1
              ) su on true
             where f.cohort_id = %s and f.status = 'active'
             order by f.fellow_id
            """,
            (
                self.settings.slack_default_fellow_timezone,
                self.default_quiet_start,
                self.default_quiet_end,
                self.team_id,
                self.cohort_id,
            ),
        )

    def _zone(self, fellow: dict[str, Any]) -> ZoneInfo:
        try:
            return get_zone(fellow["reminder_timezone"])
        except ValueError:
            log.warning(
                "invalid fellow timezone fellow=%s; using configured fallback",
                fellow["fellow_id"],
            )
            return self.default_zone

    def _learn_quiet_hours(
        self, conn: psycopg.Connection, fellows: list[dict[str, Any]], counts: Counter[str]
    ) -> None:
        """Replace the DEFAULT quiet hours with observed ones, per fellow.

        A fellow who set their own quiet hours is left alone — a stated
        preference beats an inference every time. For everyone else, if the
        last few weeks show a clear stretch when they are never on Slack, that
        stretch is their quiet time, whatever the deployment default says. A
        night owl gets the 22:00 reminder and is spared the 08:00 one.
        """
        self._observed_quiet = {}
        if not self.settings.slack_rhythm_enabled:
            return
        from .insights import observed_quiet_hours

        for fellow in fellows:
            if fellow.get("has_quiet_preference") or not fellow.get("primary_email"):
                continue
            window = observed_quiet_hours(
                conn,
                self.team_id,
                fellow["primary_email"],
                self._zone(fellow).key,
                days=self.settings.slack_rhythm_days,
                min_acts=self.settings.slack_rhythm_min_acts,
            )
            if window is not None:
                self._observed_quiet[fellow["fellow_id"]] = (window.start, window.end)
                counts["quiet_hours_observed"] += 1

    def _quiet_window(self, fellow: dict[str, Any]) -> tuple[time, time]:
        observed = self._observed_quiet.get(fellow["fellow_id"])
        if observed is not None and not fellow.get("has_quiet_preference"):
            return observed
        return fellow["quiet_start_local"], fellow["quiet_end_local"]

    def _dm_allowed(
        self, fellow: dict[str, Any], now: datetime, counts: Counter[str]
    ) -> bool:
        if fellow["reminder_mode"] == "none":
            counts["suppressed_none"] += 1
            return False
        if not fellow.get("slack_user_id"):
            counts["skipped_no_slack_user"] += 1
            return False
        zone = self._zone(fellow)
        quiet_start, quiet_end = self._quiet_window(fellow)
        if is_quiet_time(now, zone.key, quiet_start, quiet_end):
            counts["suppressed_quiet_hours"] += 1
            return False
        return True

    def _interval_wanted(
        self,
        conn: psycopg.Connection,
        fellow: dict[str, Any],
        kind: str,
        minutes: int,
        counts: Counter[str],
    ) -> bool:
        """The per-interval switches from ``/reminders`` (``slack_preference``)."""
        user_id = fellow.get("slack_user_id")
        if not user_id:
            return True  # _dm_allowed reports the missing account
        prefs = self._interval_prefs.get(user_id)
        if prefs is None:
            prefs = get_preferences(conn, user_id)
            self._interval_prefs[user_id] = prefs
        wanted = prefs.session_reminders if kind == "session" else prefs.assignment_reminders
        if minutes not in wanted:
            counts["suppressed_interval_pref"] += 1
            return False
        return True

    def _post(self, target: str, text: str) -> tuple[str, str | None]:
        """Send one message; return ``(channel, ts)``.

        The automation loop hands this a ``slack_sdk.WebClient`` (or the
        fake's duck-typed one). ``digest.tick`` and the tests for that half
        of the bot hand it a ``SlackClient`` — the protocol in
        :mod:`cufa.slack.client`, whose DMs go through ``open_dm``.
        """
        client = self.client
        if hasattr(client, "chat_postMessage"):
            response = client.chat_postMessage(
                channel=target,
                text=text,
                unfurl_links=False,
                unfurl_media=False,
            )
            return response.get("channel") or target, response.get("ts")
        channel = client.open_dm(target) if target[:1] in ("U", "W") else target
        posted = client.post_message(channel, text)
        return posted.channel_id, posted.ts

    def _claim_and_send(
        self,
        conn: psycopg.Connection,
        counts: Counter[str],
        *,
        dedupe_key: str,
        kind: str,
        target: str,
        text: str,
        scheduled_for: datetime,
        now: datetime,
        fellow_id: str | None = None,
        session_id: str | None = None,
        assignment_id: str | None = None,
        nudge_number: int | None = None,
    ) -> bool:
        claimed = fetch_one(
            conn,
            """
            insert into bot_delivery (
                dedupe_key, kind, team_id, fellow_id, session_id, assignment_id,
                nudge_number, target_channel, scheduled_for_utc, attempted_at
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (dedupe_key) do update
               set status = 'pending',
                   attempt_count = bot_delivery.attempt_count + 1,
                   attempted_at = excluded.attempted_at,
                   target_channel = excluded.target_channel,
                   error = null
             where bot_delivery.status = 'failed'
               and bot_delivery.attempt_count < 3
               and bot_delivery.attempted_at <= %s
            returning delivery_id
            """,
            (
                dedupe_key,
                kind,
                self.team_id,
                fellow_id,
                session_id,
                assignment_id,
                nudge_number,
                target,
                scheduled_for,
                now,
                now - DELIVERY_RETRY_AFTER,
            ),
        )
        if claimed is None:
            counts["deduplicated"] += 1
            return False

        counts["attempted"] += 1
        try:
            slack_channel, slack_ts = self._post(target, text)
            execute(
                conn,
                """
                update bot_delivery
                   set status = 'sent', delivered_at = %s, slack_ts = %s,
                       target_channel = %s, error = null
                 where delivery_id = %s
                """,
                (now, slack_ts, slack_channel, claimed["delivery_id"]),
            )
        except Exception as exc:  # Slack SDK and test doubles share no base error
            execute(
                conn,
                """
                update bot_delivery
                   set status = 'failed', error = %s
                 where delivery_id = %s
                """,
                (f"{type(exc).__name__}: {str(exc)[:500]}", claimed["delivery_id"]),
            )
            counts["failed"] += 1
            log.warning("Slack delivery failed kind=%s error=%s", kind, type(exc).__name__)
            return False

        counts["sent"] += 1
        counts[f"sent_{kind}"] += 1
        return True

    def _dispatch_session_reminders(
        self,
        conn: psycopg.Connection,
        fellows: list[dict[str, Any]],
        now: datetime,
        counts: Counter[str],
    ) -> None:
        sessions = fetch_all(
            conn,
            """
            select session_id, cohort_id, title, scheduled_at_utc, zoom_url
              from "session"
             where cohort_id = %s
               and scheduled_at_utc > %s
               and scheduled_at_utc <= %s
             order by scheduled_at_utc
            """,
            (self.cohort_id, now, now + timedelta(hours=24)),
        )
        for session in sessions:
            for fellow in fellows:
                mode = fellow["reminder_mode"]
                due = _before_window(
                    session["scheduled_at_utc"], SESSION_OFFSETS[mode], now
                )
                if due is None:
                    continue
                counts["candidates"] += 1
                minutes, scheduled_for = due
                if not self._interval_wanted(conn, fellow, "session", minutes, counts):
                    continue
                if not self._dm_allowed(fellow, now, counts):
                    continue
                zone = self._zone(fellow)
                self._claim_and_send(
                    conn,
                    counts,
                    dedupe_key=(
                        f"session-reminder:{self.team_id}:{session['session_id']}:"
                        f"{fellow['fellow_id']}:{minutes}"
                    ),
                    kind="session_reminder",
                    target=fellow["slack_user_id"],
                    text=session_reminder_text(
                        fellow["full_name"], session, minutes, zone
                    ),
                    scheduled_for=scheduled_for,
                    now=now,
                    fellow_id=fellow["fellow_id"],
                    session_id=str(session["session_id"]),
                )

    def _dispatch_assignment_reminders(
        self,
        conn: psycopg.Connection,
        fellows: list[dict[str, Any]],
        now: datetime,
        counts: Counter[str],
    ) -> None:
        assignments = fetch_all(
            conn,
            """
            select assignment_id, cohort_id, title, description, link as url, due_at_utc
              from assignment
             where cohort_id = %s and status = 'active'
               and due_at_utc > %s and due_at_utc <= %s
             order by due_at_utc
            """,
            (self.cohort_id, now, now + timedelta(hours=24)),
        )
        for assignment in assignments:
            for fellow in fellows:
                mode = fellow["reminder_mode"]
                due = _before_window(
                    assignment["due_at_utc"], ASSIGNMENT_OFFSETS[mode], now
                )
                if due is None:
                    continue
                counts["candidates"] += 1
                minutes, scheduled_for = due
                if not self._interval_wanted(conn, fellow, "assignment", minutes, counts):
                    continue
                if not self._dm_allowed(fellow, now, counts):
                    continue
                self._claim_and_send(
                    conn,
                    counts,
                    dedupe_key=(
                        f"assignment-reminder:{self.team_id}:"
                        f"{assignment['assignment_id']}:{fellow['fellow_id']}:{minutes}"
                    ),
                    kind="assignment_reminder",
                    target=fellow["slack_user_id"],
                    text=assignment_reminder_text(
                        fellow["full_name"], assignment, minutes, self._zone(fellow)
                    ),
                    scheduled_for=scheduled_for,
                    now=now,
                    fellow_id=fellow["fellow_id"],
                    assignment_id=str(assignment["assignment_id"]),
                )

    def _dispatch_part_b_nudges(
        self,
        conn: psycopg.Connection,
        fellows: list[dict[str, Any]],
        now: datetime,
        counts: Counter[str],
    ) -> None:
        sessions = fetch_all(
            conn,
            """
            select s.session_id, s.title, s.scheduled_at_utc, s.duration_minutes,
                   s.grace_minutes, sf.form_url, sf.last_successful_poll_at
              from "session" s
              join session_form sf
                on sf.session_id = s.session_id and sf.part = 'b'
             where s.cohort_id = %s
               and sf.publish_verified_at is not null
               and s.scheduled_at_utc <= %s
               and s.scheduled_at_utc >= %s
            """,
            (self.cohort_id, now, now - timedelta(days=4)),
        )
        for session in sessions:
            closes_at = session["scheduled_at_utc"] + timedelta(
                minutes=session["duration_minutes"] + session["grace_minutes"]
            )
            submitted = {
                str(row["submitted_email"]).strip().lower()
                for row in fetch_all(
                    conn,
                    "select distinct submitted_email from checkin_b where session_id = %s",
                    (session["session_id"],),
                )
            }
            for fellow in fellows:
                if str(fellow["primary_email"]).strip().lower() in submitted:
                    continue
                mode = fellow["reminder_mode"]
                due = _after_window(closes_at, NUDGE_OFFSETS[mode], now)
                if due is None:
                    continue
                counts["candidates"] += 1
                number, _offset, scheduled_for = due
                # A fresh successful pull after this stage opened is required.
                # Stale response data must never become a false personal nudge.
                if (
                    session.get("last_successful_poll_at") is None
                    or session["last_successful_poll_at"] < scheduled_for
                ):
                    counts["skipped_stale_part_b"] += 1
                    continue
                if not self._dm_allowed(fellow, now, counts):
                    continue
                still_missing = fetch_one(
                    conn,
                    """
                    select not exists (
                        select 1 from checkin_b
                         where session_id = %s and lower(submitted_email) = lower(%s)
                    ) as missing
                    """,
                    (session["session_id"], fellow["primary_email"]),
                )
                if not still_missing or not still_missing["missing"]:
                    continue
                self._claim_and_send(
                    conn,
                    counts,
                    dedupe_key=(
                        f"part-b-nudge:{self.team_id}:{session['session_id']}:"
                        f"{fellow['fellow_id']}:{number}"
                    ),
                    kind="part_b_nudge",
                    target=fellow["slack_user_id"],
                    text=nudge_text(fellow["full_name"], session, number),
                    scheduled_for=scheduled_for,
                    now=now,
                    fellow_id=fellow["fellow_id"],
                    session_id=str(session["session_id"]),
                    nudge_number=number,
                )

    def _resolve_channel(self, configured: str) -> str | None:
        wanted = (configured or "").strip().lstrip("#")
        if not wanted:
            return None
        if wanted in self._channel_cache:
            return self._channel_cache[wanted]
        if wanted.startswith(("C", "G")):
            self._channel_cache[wanted] = wanted
            return wanted

        if not hasattr(self.client, "conversations_list") and hasattr(self.client, "list_channels"):
            for known in self.client.list_channels():
                if known.name == wanted:
                    self._channel_cache[wanted] = known.id
                    return known.id
            return None

        cursor: str | None = None
        while True:
            response = self.client.conversations_list(
                cursor=cursor,
                limit=200,
                types="public_channel,private_channel",
            )
            for channel in response.get("channels") or []:
                if channel.get("name") == wanted:
                    resolved = channel.get("id")
                    self._channel_cache[wanted] = resolved
                    return resolved
            cursor = (
                (response.get("response_metadata") or {}).get("next_cursor") or ""
            ).strip()
            if not cursor:
                break
        return None

    def _dispatch_agendas(
        self, conn: psycopg.Connection, now: datetime, counts: Counter[str]
    ) -> None:
        sessions = fetch_all(
            conn,
            """
            select session_id, title, agenda, zoom_url, slack_channel_id,
                   scheduled_at_utc
              from "session"
             where cohort_id = %s and agenda is not null
               and scheduled_at_utc <= %s
               and scheduled_at_utc > %s
             order by scheduled_at_utc
            """,
            (self.cohort_id, now, now - timedelta(minutes=15)),
        )
        for session in sessions:
            counts["candidates"] += 1
            configured = (
                session.get("slack_channel_id")
                or self.settings.slack_announcement_channel
            )
            channel = self._resolve_channel(configured)
            if not channel:
                counts["skipped_missing_agenda_channel"] += 1
                continue
            zoom = (
                "\n" + _slack_link(session["zoom_url"], "Join Zoom")
                if session.get("zoom_url")
                else ""
            )
            text = (
                f"*{_slack_text(session['title'])} agenda*\n"
                f"{session['agenda'].strip()}{zoom}"
            )
            self._claim_and_send(
                conn,
                counts,
                dedupe_key=f"session-agenda:{self.team_id}:{session['session_id']}",
                kind="session_agenda",
                target=channel,
                text=text,
                scheduled_for=session["scheduled_at_utc"],
                now=now,
                session_id=str(session["session_id"]),
            )

    def _digest_schedule(
        self, now: datetime, zone: ZoneInfo
    ) -> tuple[datetime, datetime, datetime, str] | None:
        local = now.astimezone(zone)
        if local.weekday() != self.settings.slack_digest_weekday:
            return None
        scheduled_local = datetime.combine(
            local.date(), time(self.settings.slack_digest_hour), tzinfo=zone
        )
        if not scheduled_local <= local < scheduled_local + timedelta(days=1):
            return None
        monday: date = local.date() - timedelta(days=local.weekday())
        week_start_local = datetime.combine(monday, time.min, tzinfo=zone)
        week_end_local = week_start_local + timedelta(days=7)
        iso = local.isocalendar()
        return (
            scheduled_local.astimezone(timezone.utc),
            week_start_local.astimezone(timezone.utc),
            week_end_local.astimezone(timezone.utc),
            f"{iso.year}-W{iso.week:02d}",
        )

    def _dispatch_weekly_digests(
        self,
        conn: psycopg.Connection,
        fellows: list[dict[str, Any]],
        now: datetime,
        counts: Counter[str],
    ) -> None:
        for fellow in fellows:
            schedule = self._digest_schedule(now, self._zone(fellow))
            if schedule is None:
                continue
            counts["candidates"] += 1
            scheduled_for, week_start, week_end, week_key = schedule
            if not self._dm_allowed(fellow, now, counts):
                continue
            sessions = fetch_all(
                conn,
                """
                select title, scheduled_at_utc, zoom_url
                  from "session"
                 where cohort_id = %s
                   and scheduled_at_utc >= %s and scheduled_at_utc < %s
                 order by scheduled_at_utc
                """,
                (self.cohort_id, week_start, week_end),
            )
            assignments = fetch_all(
                conn,
                """
                select title, due_at_utc, link as url
                  from assignment
                 where cohort_id = %s and status = 'active'
                   and due_at_utc >= %s and due_at_utc < %s
                 order by due_at_utc
                """,
                (self.cohort_id, week_start, week_end),
            )
            changed_since = week_start - timedelta(days=7)
            changes = fetch_all(
                conn,
                """
                select 'session' as item_type, title, created_at, updated_at
                  from "session"
                 where cohort_id = %s and updated_at >= %s
                union all
                select 'assignment' as item_type, title, created_at, updated_at
                  from assignment
                 where cohort_id = %s and updated_at >= %s
                order by updated_at desc
                limit 12
                """,
                (self.cohort_id, changed_since, self.cohort_id, changed_since),
            )
            text = self._digest_text(fellow, sessions, assignments, changes)
            self._claim_and_send(
                conn,
                counts,
                dedupe_key=(
                    f"weekly-digest:{self.team_id}:{fellow['fellow_id']}:{week_key}"
                ),
                kind="weekly_digest",
                target=fellow["slack_user_id"],
                text=text,
                scheduled_for=scheduled_for,
                now=now,
                fellow_id=fellow["fellow_id"],
            )

    def _digest_text(
        self,
        fellow: dict[str, Any],
        sessions: Iterable[dict[str, Any]],
        assignments: Iterable[dict[str, Any]],
        changes: Iterable[dict[str, Any]],
    ) -> str:
        zone = self._zone(fellow)
        lines = [f"Hi {_first_name(fellow['full_name'])}, here's your week.", "", "*Sessions*"]
        session_rows = list(sessions)
        if session_rows:
            for row in session_rows:
                link = (
                    " - " + _slack_link(row["zoom_url"], "Join Zoom")
                    if row.get("zoom_url")
                    else " - Zoom link not set yet"
                )
                lines.append(
                    f"• {_slack_text(row['title'])}: "
                    f"{_local_when(row['scheduled_at_utc'], zone)}{link}"
                )
        else:
            lines.append("• No session scheduled.")

        lines.extend(["", "*Due*"])
        assignment_rows = list(assignments)
        if assignment_rows:
            for row in assignment_rows:
                link = (
                    " - " + _slack_link(row["url"], "Open")
                    if row.get("url")
                    else ""
                )
                lines.append(
                    f"• {_slack_text(row['title'])}: "
                    f"{_local_when(row['due_at_utc'], zone)}{link}"
                )
        else:
            lines.append("• Nothing due this week.")

        lines.extend(["", "*Changed*"])
        change_rows = list(changes)
        if change_rows:
            for row in change_rows:
                created = row["created_at"]
                updated = row["updated_at"]
                action = "Added" if abs((updated - created).total_seconds()) < 2 else "Updated"
                lines.append(
                    f"• {action} {_slack_text(row['item_type'])}: "
                    f"{_slack_text(row['title'])}"
                )
        else:
            lines.append("• No schedule or assignment changes.")
        return "\n".join(lines)


def _preference_row(
    conn: psycopg.Connection,
    client: Any,
    *,
    team_id: str,
    cohort_id: str,
    user_id: str,
    default_timezone: str,
    default_quiet_start: str,
    default_quiet_end: str,
) -> tuple[dict[str, Any] | None, str | None]:
    slack_user = resolve_user(conn, client, team_id, user_id)
    if not slack_user.email:
        return None, "I couldn't match your Slack profile to a roster email. Ask staff to check your Slack email."
    fellow = fetch_one(
        conn,
        """
        select f.fellow_id, f.full_name,
               coalesce(p.mode, 'all') as mode,
               coalesce(p.timezone, f.timezone, %s::text) as timezone,
               coalesce(p.quiet_start_local, %s::time) as quiet_start_local,
               coalesce(p.quiet_end_local, %s::time) as quiet_end_local
          from fellow f
          left join fellow_reminder_preference p on p.fellow_id = f.fellow_id
         where f.cohort_id = %s and lower(f.primary_email) = lower(%s)
           and f.status = 'active'
        """,
        (
            default_timezone,
            parse_clock(default_quiet_start),
            parse_clock(default_quiet_end),
            cohort_id,
            slack_user.email,
        ),
    )
    if not fellow:
        return None, "I found your Slack email, but it isn't on the active fellowship roster. Ask staff to update the roster."
    return fellow, None


def preference_command(
    conn: psycopg.Connection,
    client: Any,
    *,
    team_id: str,
    cohort_id: str,
    user_id: str,
    text: str,
    default_timezone: str,
    default_quiet_start: str = "21:00",
    default_quiet_end: str = "08:00",
) -> str:
    """Apply one `/cufa-reminders` command and return its ephemeral response."""
    fellow, error = _preference_row(
        conn,
        client,
        team_id=team_id,
        cohort_id=cohort_id,
        user_id=user_id,
        default_timezone=default_timezone,
        default_quiet_start=default_quiet_start,
        default_quiet_end=default_quiet_end,
    )
    if error or fellow is None:
        return error or "Your preference could not be loaded."

    words = (text or "").strip().split()
    action = words[0].lower() if words else "status"
    modes = {"all", "fewer", "later", "none"}
    if action in modes:
        execute(
            conn,
            """
            insert into fellow_reminder_preference (fellow_id, mode)
            values (%s, %s)
            on conflict (fellow_id) do update
               set mode = excluded.mode, updated_at = now()
            """,
            (fellow["fellow_id"], action),
        )
        explanations = {
            "all": "session reminders at 24h, 1h, and 10min; assignment reminders at 24h and 1h",
            "fewer": "one session reminder at 1h, one assignment reminder at 24h, and at most one Part B nudge",
            "later": "one session reminder at 10min, one assignment reminder at 1h, and later Part B nudges",
            "none": "no personal reminders, nudges, or weekly digests",
        }
        return f"Reminder preference set to *{action}*: {explanations[action]}."

    if action == "timezone" and len(words) == 2:
        try:
            get_zone(words[1])
        except ValueError as exc:
            return str(exc)
        execute(
            conn,
            """
            insert into fellow_reminder_preference (fellow_id, timezone)
            values (%s, %s)
            on conflict (fellow_id) do update
               set timezone = excluded.timezone, updated_at = now()
            """,
            (fellow["fellow_id"], words[1]),
        )
        return f"Timezone set to *{_slack_text(words[1])}*. Quiet hours use this timezone."

    if action == "quiet" and len(words) == 3:
        try:
            start, end = parse_clock(words[1]), parse_clock(words[2])
        except ValueError as exc:
            return str(exc)
        execute(
            conn,
            """
            insert into fellow_reminder_preference (
                fellow_id, quiet_start_local, quiet_end_local
            )
            values (%s, %s, %s)
            on conflict (fellow_id) do update
               set quiet_start_local = excluded.quiet_start_local,
                   quiet_end_local = excluded.quiet_end_local,
                   updated_at = now()
            """,
            (fellow["fellow_id"], start, end),
        )
        return f"Quiet hours set to *{start:%H:%M}-{end:%H:%M}* in your timezone."

    if action == "status":
        return (
            f"Your reminders are *{fellow['mode']}*. Timezone: *{_slack_text(fellow['timezone'])}*. "
            f"Quiet hours: *{fellow['quiet_start_local']:%H:%M}-"
            f"{fellow['quiet_end_local']:%H:%M}*."
        )

    return (
        "Use `/cufa-reminders all`, `fewer`, `later`, or `none`. "
        "You can also use `status`, `timezone America/Chicago`, or `quiet 21:00 08:00`."
    )


class AutomationLoop:
    """Long-lived polling loop owned by the Slack bot process."""

    def __init__(
        self,
        settings: Settings,
        client: Any,
        *,
        team_id: str,
        cohort_id: str,
    ) -> None:
        self.settings = settings
        self.client = client
        self.team_id = team_id
        self.cohort_id = cohort_id
        self.engine = ReminderEngine(
            settings, client, team_id=team_id, cohort_id=cohort_id
        )
        self.counts: Counter[str] = Counter()
        self.last_tick_at: datetime | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tick_lock = threading.Lock()
        self._next_user_sync: datetime | None = None
        self._next_part_b_sync: datetime | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="cufa-slack-automations", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # a failed tick must not kill the collector
                self.counts["tick_failed"] += 1
                log.exception("Slack automation tick failed: %s", type(exc).__name__)
            self._stop.wait(max(1, self.settings.slack_automation_interval_seconds))

    def tick(self, *, now: datetime | None = None) -> dict[str, int]:
        instant = to_utc(now or datetime.now(timezone.utc))
        if not self._tick_lock.acquire(blocking=False):
            return {"tick_already_running": 1}
        try:
            self._sync_slack_users(instant)
            if self.settings.slack_sync_part_b:
                self._sync_due_part_b(instant)
            with connection(self.settings, autocommit=True) as conn:
                result = self.engine.run_once(conn, now=instant)
            self.counts.update(result)
            self.counts["ticks"] += 1
            self.last_tick_at = instant
            return result
        finally:
            self._tick_lock.release()

    def _sync_slack_users(self, now: datetime) -> None:
        if self._next_user_sync is not None and now < self._next_user_sync:
            return
        with connection(self.settings, autocommit=True) as conn:
            written = sync_users(conn, self.client, self.team_id)
        self.counts["users_synced"] += written
        self._next_user_sync = now + timedelta(hours=24)

    def _sync_due_part_b(self, now: datetime) -> None:
        if self._next_part_b_sync is not None and now < self._next_part_b_sync:
            return
        self._next_part_b_sync = now + timedelta(
            minutes=max(1, self.settings.slack_part_b_poll_minutes)
        )
        with connection(self.settings, autocommit=True) as conn:
            rows = fetch_all(
                conn,
                """
                select distinct s.session_id
                  from "session" s
                  join session_form sf
                    on sf.session_id = s.session_id and sf.part = 'b'
                 where s.cohort_id = %s
                   and sf.publish_verified_at is not null
                   and s.scheduled_at_utc
                       + make_interval(mins => s.duration_minutes + s.grace_minutes)
                       + interval '30 minutes' <= %s
                   and s.scheduled_at_utc
                       + make_interval(mins => s.duration_minutes + s.grace_minutes)
                       + interval '48 hours' > %s
                   and exists (
                       select 1 from fellow f
                        where f.cohort_id = s.cohort_id and f.status = 'active'
                          and not exists (
                              select 1 from checkin_b b
                               where b.session_id = s.session_id
                                 and lower(b.submitted_email) = lower(f.primary_email)
                          )
                   )
                """,
                (self.cohort_id, now, now),
            )
        if not rows:
            return

        from ..google.factory import get_client
        from ..ingest.forms_b import pull_session_b

        for row in rows:
            try:
                with connection(self.settings) as conn:
                    google = get_client(conn, self.settings)
                    pull_session_b(conn, google, str(row["session_id"]), self.settings)
                self.counts["part_b_forms_synced"] += 1
            except Exception as exc:
                self.counts["part_b_sync_failed"] += 1
                log.warning(
                    "Part B sync before nudge failed session=%s error=%s",
                    row["session_id"],
                    type(exc).__name__,
                )

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "last_tick_at": self.last_tick_at.isoformat() if self.last_tick_at else None,
            "counts": dict(self.counts),
        }


# ---------------------------------------------------------------------------
# The reminder/badge bot's API, over the same engine
# ---------------------------------------------------------------------------
#
# ``digest.tick``, ``cufa slack tick`` and the staff commands were written
# against these names. They stay, so that half of the bot needs no scheduler
# of its own — and so a reminder can never go out twice because two engines
# both thought it was due.


@dataclass
class ReminderRun:
    considered: int = 0
    sent: int = 0
    skipped_pref: int = 0
    skipped_quiet: int = 0
    skipped_dup: int = 0
    failed: int = 0
    counts: dict[str, int] = field(default_factory=dict)


def run_reminders(
    conn: psycopg.Connection,
    client: Any,
    *,
    cohort_id: str,
    now: datetime | None = None,
    settings: Settings | None = None,
    team_id: str | None = None,
) -> ReminderRun:
    """Send every session and assignment reminder that is due. Safe to repeat."""
    settings = settings or get_settings()
    team = team_id or getattr(client, "team_id", None)
    if not team:
        raise CufaError("run_reminders needs a team id: the client has none and none was given")
    engine = ReminderEngine(settings, client, team_id=str(team), cohort_id=cohort_id)
    counts = engine.run_reminders_only(conn, now=now)
    return ReminderRun(
        considered=counts.get("candidates", 0),
        sent=counts.get("sent", 0),
        skipped_pref=counts.get("suppressed_none", 0) + counts.get("suppressed_interval_pref", 0),
        skipped_quiet=counts.get("suppressed_quiet_hours", 0),
        skipped_dup=counts.get("deduplicated", 0),
        failed=counts.get("failed", 0),
        counts=counts,
    )


def _zone_or_utc(tz: str | None) -> ZoneInfo:
    try:
        return get_zone(tz or "UTC")
    except ValueError:
        return get_zone("UTC")


def format_when(at: datetime, tz: str | None) -> str:
    """``Tue Sep 15, 7:00 PM EDT``. Built by hand: ``%-d`` is not portable to Windows."""
    local = to_utc(at).astimezone(_zone_or_utc(tz))
    hour12 = local.hour % 12 or 12
    ampm = "AM" if local.hour < 12 else "PM"
    return f"{local:%a %b} {local.day}, {hour12}:{local:%M} {ampm} {local.tzname() or (tz or 'UTC')}"


def in_quiet_hours(now: datetime, tz: str | None, settings: Settings | None = None) -> bool:
    """The deployment's default quiet window, in ``tz``. Per-fellow windows live in the engine."""
    settings = settings or get_settings()
    return is_quiet_time(
        now,
        _zone_or_utc(tz).key,
        parse_clock(settings.slack_quiet_start),
        parse_clock(settings.slack_quiet_end),
    )


def set_zoom_link(conn: psycopg.Connection, session_id: str, link: str | None) -> None:
    """``/zoom`` — the same column the console and the reminders read."""
    execute(
        conn,
        'update "session" set zoom_url = %s, updated_at = now() where session_id = %s',
        ((link or "").strip() or None, session_id),
    )


__all__ = [
    "ASSIGNMENT_OFFSETS",
    "AutomationLoop",
    "NUDGE_OFFSETS",
    "ReminderEngine",
    "ReminderRun",
    "SESSION_OFFSETS",
    "assignment_reminder_text",
    "format_when",
    "in_quiet_hours",
    "is_quiet_time",
    "nudge_text",
    "parse_clock",
    "preference_command",
    "run_reminders",
    "session_reminder_text",
    "set_zoom_link",
]
