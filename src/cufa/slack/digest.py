"""What the bot posts to the staff channel on a schedule, and the tick that
drives everything scheduled.

* **Session summary** — once, after a session's end time (scheduled time plus
  duration plus grace): who checked in, who filled in the exit ticket, who
  attended but said nothing on the recording (if a transcript was ingested),
  and anything queued for a human.
* **Weekly digest** — Monday mornings: who has been quiet, what is due this
  week, open check-in requests and roster alerts, the most active fellows.
* **Roster alerts** and **check-in pings** — posted the moment they arise.

Every post is recorded in ``digest_log`` under a key that makes it unique, so
a tick that runs twice posts once. ``tick`` is the one function a scheduler
calls; run it every few minutes from cron, a systemd timer, or the ``serve``
loop, and it does whatever is due.

Nothing here reads ``help_request``. A fellow asking for help is routed by
the safeguarding path to its own recipient, and does not appear in a digest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg

from ..config import Settings, get_settings
from ..db import execute, fetch_all, fetch_one
from ..errors import CufaError
from ..logging_setup import get_logger
from ..timeutil import short_date
from ..assignments import list_assignments
from ..engagement import most_active, quiet_fellows
from ..interventions import open_requests
from ..zoom import silent_fellows, speaking_share
from .badges import award_badges, notify_new_awards
from .client import SlackApiError, SlackClient
from .identity import open_alerts
from .reminders import format_when, run_reminders
from .sync import sync_all
from .welcome import send_welcomes

log = get_logger(__name__)


#: What a Slack channel id looks like. C public, G private, D direct message.
_CHANNEL_ID = re.compile(r"[CGD][A-Z0-9]{2,}")


def resolve_channel(conn: psycopg.Connection, client: SlackClient, target: str | None) -> str | None:
    """A channel id, from an id or a name.

    Staff write ``#staff`` in a configuration file, not ``C0123456789``, and the
    Q&A channels already accept either. The staff channel does too, so the two
    halves of the bot are configured the same way. Resolved from the synced
    channel table first, then from the API, so a name works on the first run
    before anything has been synced.
    """
    if not target:
        return None
    wanted = target.strip().lstrip("#")
    if not wanted:
        return None
    row = fetch_one(
        conn,
        "select channel_id from slack_channel where channel_id = %s or lower(name) = lower(%s) "
        "order by (channel_id = %s) desc limit 1",
        (wanted, wanted, wanted),
    )
    if row:
        return row["channel_id"]
    for channel in client.list_channels():
        if channel.name.lower() == wanted.lower() or channel.id == wanted:
            return channel.id
    # Nothing matched by name. If it is shaped like an id, use it anyway: the
    # bot can post to a channel it cannot list, and refusing would turn a
    # permissions quirk into a silently missing digest.
    return wanted if _CHANNEL_ID.fullmatch(wanted) else None


def _already(conn: psycopg.Connection, kind: str, key: str) -> bool:
    return fetch_one(conn, "select 1 from digest_log where kind = %s and target_key = %s", (kind, key)) is not None


def _log(conn: psycopg.Connection, kind: str, key: str, channel_id: str | None, body: str) -> bool:
    row = fetch_one(
        conn,
        """
        insert into digest_log (kind, target_key, channel_id, body) values (%s, %s, %s, %s)
        on conflict (kind, target_key) do nothing returning digest_id
        """,
        (kind, key, channel_id, body),
    )
    return row is not None


def _post(conn: psycopg.Connection, client: SlackClient, channel_id: str, *, kind: str, key: str, text: str) -> bool:
    """Post once. The log row is claimed first so a crash mid-post cannot double up."""
    if not _log(conn, kind, key, channel_id, text):
        return False
    try:
        client.post_message(channel_id, text)
    except SlackApiError as exc:
        execute(conn, "delete from digest_log where kind = %s and target_key = %s", (kind, key))
        log.warning("post %s/%s failed: %s", kind, key, exc.error)
        return False
    return True


# ---------------------------------------------------------------------------
# session summary
# ---------------------------------------------------------------------------


def session_summary_text(conn: psycopg.Connection, session_id: str) -> str:
    s = fetch_one(conn, 'select * from "session" where session_id = %s', (session_id,))
    if s is None:
        raise CufaError(f"No session with id {session_id}")
    roster = fetch_all(
        conn,
        "select fellow_id, full_name from fellow where cohort_id = %s and status = 'active' order by full_name",
        (s["cohort_id"],),
    )
    a = fetch_all(
        conn,
        """
        select fellow_id, full_name, status, submitted_email
          from v_checkin_resolved where session_id = %s
        """,
        (session_id,),
    )
    b = fetch_all(
        conn,
        "select fellow_id, full_name, confidence_raw, has_takeaway from v_checkin_b_resolved where session_id = %s",
        (session_id,),
    )
    attended = {r["fellow_id"] for r in a if r["status"] == "attended" and r["fellow_id"]}
    review = {r["fellow_id"] for r in a if r["status"] == "needs_review" and r["fellow_id"]}
    unknown = sorted({r["submitted_email"] for r in a if not r["fellow_id"]})
    ticketed = {r["fellow_id"] for r in b if r["fellow_id"]}
    missing = [r for r in roster if r["fellow_id"] not in attended and r["fellow_id"] not in review]
    no_ticket = [r for r in roster if r["fellow_id"] in attended and r["fellow_id"] not in ticketed]

    lines = [f"*Session summary — {s['title']}* ({format_when(s['scheduled_at_utc'], s['timezone'])})"]
    lines.append(
        f"Checked in: *{len(attended)}/{len(roster)}*"
        + (f" · needs review: {len(review)}" if review else "")
        + (f" · unknown addresses: {len(unknown)}" if unknown else "")
    )
    lines.append(f"Exit tickets: *{len(ticketed)}/{len(roster)}*")
    if missing:
        lines.append("No check-in: " + ", ".join(r["full_name"] for r in missing[:25]) + (" …" if len(missing) > 25 else ""))
    if no_ticket:
        lines.append("Attended, no exit ticket: " + ", ".join(r["full_name"] for r in no_ticket[:25]))
    if review:
        lines.append(f"{len(review)} check-in{'s' if len(review) > 1 else ''} waiting for a human decision — `cufa review`.")
    if unknown:
        lines.append(f"{len(unknown)} address{'es' if len(unknown) > 1 else ''} not on the roster — link with `/alias` or `/link`.")
    shares = speaking_share(conn, session_id)
    if shares:
        top = [x for x in shares if x.fellow_id][:3]
        if top:
            lines.append("Most airtime: " + ", ".join(f"{x.full_name} {x.share_of_seconds * 100:.0f}%" for x in top))
        quiet = silent_fellows(conn, session_id)
        if quiet:
            lines.append("Attended but not heard on the recording: " + ", ".join(r["full_name"] for r in quiet[:15]))
        unmatched = [x.speaker_name for x in shares if not x.fellow_id]
        if unmatched:
            lines.append("Zoom names nobody could match: " + ", ".join(unmatched[:10]))
    return "\n".join(lines)


def post_session_summaries(
    conn: psycopg.Connection, client: SlackClient, *, cohort_id: str, channel_id: str, now: datetime | None = None
) -> int:
    """Post a summary for every session that has ended and has none yet."""
    now = now or datetime.now(timezone.utc)
    rows = fetch_all(
        conn,
        """
        select session_id from "session"
         where cohort_id = %s and summary_posted_at is null
           and scheduled_at_utc + (duration_minutes + grace_minutes) * interval '1 minute' <= %s
           and scheduled_at_utc >= %s - interval '14 days'
         order by scheduled_at_utc
        """,
        (cohort_id, now, now),
    )
    posted = 0
    for r in rows:
        sid = str(r["session_id"])
        if _post(conn, client, channel_id, kind="session_summary", key=sid, text=session_summary_text(conn, sid)) or _already(conn, "session_summary", sid):
            posted += 1
            execute(conn, 'update "session" set summary_posted_at = now() where session_id = %s', (sid,))
    return posted


# ---------------------------------------------------------------------------
# weekly digest
# ---------------------------------------------------------------------------


def weekly_digest_text(conn: psycopg.Connection, cohort_id: str, *, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    week_end = now + timedelta(days=7)
    quiet = quiet_fellows(conn, cohort_id, days=7, now=now)
    active = most_active(conn, cohort_id, days=7, now=now)
    due = [a for a in list_assignments(conn, cohort_id, due_after=now) if a["due_at_utc"] <= week_end]
    sessions = fetch_all(
        conn,
        'select title, scheduled_at_utc, timezone, zoom_link from "session" where cohort_id = %s and scheduled_at_utc between %s and %s order by scheduled_at_utc',
        (cohort_id, now, week_end),
    )
    requests = open_requests(conn, cohort_id)
    alerts = open_alerts(conn)
    from ..engagement import cohort_engagement

    attention = [e for e in cohort_engagement(conn, cohort_id, now=now) if e.attention_index >= 60 and not e.reached_out][:8]

    lines = [f"*Weekly digest — {short_date(now)}*"]
    if sessions:
        lines.append("*This week:* " + " · ".join(
            f"{s['title']} ({format_when(s['scheduled_at_utc'], s['timezone'])}{'' if s['zoom_link'] else ' — no Zoom link yet'})"
            for s in sessions
        ))
    if due:
        lines.append("*Due:* " + " · ".join(f"{a['title']} ({format_when(a['due_at_utc'], a.get('timezone'))}, {a['submitted']} submitted)" for a in due))
    lines.append(
        "*Quiet for 7+ days:* " + (", ".join(q["full_name"] for q in quiet[:20]) + (" …" if len(quiet) > 20 else "") if quiet else "nobody 🎉")
    )
    if active:
        lines.append("*Most active:* " + ", ".join(f"{a['full_name']} ({a['messages']})" for a in active))
    if attention:
        lines.append("*Worth a human look* (attention index ≥ 60, nobody has reached out yet): " + ", ".join(
            f"{e.full_name} [{e.attention_index}: {'; '.join(e.flags)}]" for e in attention
        ))
    if requests:
        lines.append(f"*Check-in requests waiting:* " + ", ".join(r["full_name"] for r in requests))
    if alerts:
        lines.append(f"*Unrostered Slack accounts:* " + ", ".join(a["real_name"] or a["display_name"] or a["slack_user_id"] for a in alerts))
    lines.append("_`/report` for the full picture · mark outreach with `/outreach <name>`._")
    return "\n".join(lines)


def post_weekly_digest(
    conn: psycopg.Connection, client: SlackClient, *, cohort_id: str, channel_id: str, now: datetime | None = None, force: bool = False
) -> bool:
    """Post on Mondays, once per ISO week. ``force`` posts now regardless."""
    now = now or datetime.now(timezone.utc)
    if not force and now.weekday() != 0:
        return False
    year, week, _ = now.isocalendar()
    key = f"{cohort_id}:{year}-W{week:02d}"
    return _post(conn, client, channel_id, kind="weekly", key=key, text=weekly_digest_text(conn, cohort_id, now=now))


# ---------------------------------------------------------------------------
# alerts and pings
# ---------------------------------------------------------------------------


def post_roster_alerts(conn: psycopg.Connection, client: SlackClient, *, channel_id: str) -> int:
    n = 0
    for a in open_alerts(conn):
        if a["posted_at"] is not None:
            continue
        who = a["real_name"] or a["display_name"] or a["slack_user_id"]
        # The Slack mention, not the address. Staff can click the mention to see
        # the profile, so the address adds nothing they cannot already reach —
        # and a Slack channel keeps its history, is searchable, can be exported,
        # and is readable by whoever is added to it next year. The address stays
        # where access is gated: `cufa slack alerts` and the staff dashboard.
        text = (
            f"👋 *{who}* (<@{a['slack_user_id']}>) joined the workspace but is not on the roster.\n"
            + ("" if a["email"] else "Their profile carries no email, so nothing can be matched automatically.\n")
            + f"If they are a fellow: `/link <@{a['slack_user_id']}> <fellow id or name>`. "
            f"Staff or a guest: `/alerts resolve <@{a['slack_user_id']}> staff` or `… ignored`.\n"
            "_`/alerts` lists these with the address on them._"
        )
        if _post(conn, client, channel_id, kind="roster_alert", key=a["slack_user_id"], text=text):
            execute(conn, "update roster_alert set posted_at = now() where alert_id = %s", (a["alert_id"],))
            n += 1
    return n


def post_check_in_ping(
    conn: psycopg.Connection, client: SlackClient, *, channel_id: str, intervention_id: str, fellow_name: str, note: str | None
) -> bool:
    text = f"🙋 *{fellow_name}* pressed *check in with me*." + (f"\n> {note}" if note else "") + (
        f"\nPick it up with `/outreach {fellow_name}` once you have."
    )
    return _post(conn, client, channel_id, kind="check_in_ping", key=intervention_id, text=text)


# ---------------------------------------------------------------------------
# the tick
# ---------------------------------------------------------------------------


@dataclass
class TickResult:
    synced: bool = False
    welcomed: int = 0
    reminders_sent: int = 0
    summaries_posted: int = 0
    weekly_posted: bool = False
    alerts_posted: int = 0
    badges_awarded: int = 0
    badges_notified: int = 0
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:  # pragma: no cover - display only
        return (
            f"synced={self.synced} welcomed={self.welcomed} reminders={self.reminders_sent} summaries={self.summaries_posted} "
            f"weekly={self.weekly_posted} alerts={self.alerts_posted} badges={self.badges_awarded}/{self.badges_notified}"
            + (f" errors={len(self.errors)}" if self.errors else "")
        )


def tick(
    conn: psycopg.Connection,
    client: SlackClient,
    *,
    settings: Settings | None = None,
    cohort_id: str | None = None,
    staff_channel: str | None = None,
    now: datetime | None = None,
    sync: bool = True,
    sync_messages: bool = True,
) -> TickResult:
    """Do everything that is due. Each step is independent; one failing does not stop the rest."""
    settings = settings or get_settings()
    cohort_id = cohort_id or settings.slack_cohort
    now = now or datetime.now(timezone.utc)
    result = TickResult()
    if not cohort_id:
        raise CufaError("No cohort configured: set CUFA_SLACK_COHORT or pass --cohort.")

    def step(name: str, fn) -> Any:
        try:
            return fn()
        except CufaError as exc:
            result.errors.append(f"{name}: {exc}")
            log.warning("tick step %s failed: %s", name, exc)
            return None

    configured_channel = staff_channel or settings.slack_staff_channel
    if sync:
        result.synced = step("sync", lambda: sync_all(conn, client, staff_channel=staff_channel, staff_emails=settings.slack_admins, store_text=settings.slack_store_text, cohort_id=cohort_id, messages=sync_messages)) is not None
    result.welcomed = step("welcome", lambda: send_welcomes(conn, client, cohort_id=cohort_id)) or 0
    run = step("reminders", lambda: run_reminders(conn, client, cohort_id=cohort_id, now=now))
    result.reminders_sent = run.sent if run else 0
    awards = step("badges", lambda: award_badges(conn, cohort_id, now=now))
    if awards:
        result.badges_awarded = len(awards.new_awards)
        notified = step("badge_dms", lambda: notify_new_awards(conn, client, awards, cohort_id=cohort_id))
        result.badges_notified = notified.notified if notified else 0
    # Resolved after the sync, so a channel named by name is already in the table.
    staff_channel = step("staff_channel", lambda: resolve_channel(conn, client, configured_channel))
    if configured_channel and not staff_channel:
        result.errors.append(
            f"staff channel {configured_channel!r} not found, or the bot is not in it; "
            "summaries, alerts and digests were not posted"
        )
    if staff_channel:
        result.alerts_posted = step("roster_alerts", lambda: post_roster_alerts(conn, client, channel_id=staff_channel)) or 0
        result.summaries_posted = step(
            "session_summaries",
            lambda: post_session_summaries(conn, client, cohort_id=cohort_id, channel_id=staff_channel, now=now),
        ) or 0
        result.weekly_posted = bool(
            step("weekly", lambda: post_weekly_digest(conn, client, cohort_id=cohort_id, channel_id=staff_channel, now=now))
        )
    elif not configured_channel:
        result.errors.append("no staff channel configured (CUFA_SLACK_STAFF_CHANNEL); summaries, alerts and digests not posted")
    log.info("tick %s", result)
    return result


__all__ = [
    "TickResult",
    "resolve_channel",
    "post_check_in_ping",
    "post_roster_alerts",
    "post_session_summaries",
    "post_weekly_digest",
    "session_summary_text",
    "tick",
    "weekly_digest_text",
]
