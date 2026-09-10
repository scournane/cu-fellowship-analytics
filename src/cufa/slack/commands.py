"""Slash commands: parse, check permission, do the thing, answer in text.

Every handler is ``(conn, client, ctx, args) -> Reply``. ``ctx`` says who is
asking and what they are allowed to do; the reply is Slack markdown, sent back
ephemerally so nobody else in the channel sees it. The Bolt adapter is a
thin loop over ``dispatch``; the CLI reaches the same handlers through
``cufa slack cmd``, so every command is testable with the fake client and no
socket.

Two tiers of command, enforced here and nowhere else:

* fellows: ``/reminders``, ``/badges``, ``/checkin``, ``/dashboard``, ``/me``, ``/help``
* staff:   ``/attendance``, ``/fellow``, ``/report``, ``/assignment``, ``/zoom``,
           ``/leaderboard``, ``/alias``, ``/link``, ``/alerts``, ``/outreach``,
           ``/score``, ``/digest``, ``/sync``

A fellow never sees another fellow's data through the bot.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

import psycopg

from ..assignments import (
    KIND_LABELS,
    AssignmentInput,
    create_assignment,
    find_assignment,
    list_assignments,
    record_score,
    set_link,
    submissions_for_fellow,
)
from ..config import Settings, get_settings
from ..engagement import cohort_attendance, cohort_engagement, fellow_engagement, most_active
from ..errors import CufaError
from ..funnel import fellow_funnel, render_fellow_text
from ..interventions import clear_reached_out, for_fellow as interventions_for, mark_reached_out, request_check_in
from ..db import fetch_all, fetch_one
from ..logging_setup import get_logger
from ..report import cohort_report
from .badges import badges_for, collect_evidence, leaderboard, render_badges, render_leaderboard, RANK_KEYS
from .client import SlackClient
from .dashboard_links import fellow_dashboard_url
from .digest import post_check_in_ping, post_weekly_digest, session_summary_text
from .identity import (
    AmbiguousFellow,
    UnknownFellow,
    add_alias,
    find_fellow,
    link_slack_user,
    list_aliases,
    open_alerts,
    resolve_alert,
)
from .permissions import Caller, NotAllowed, identify, is_admin_command, require_admin
from .preferences import get_preferences, parse_offset, set_all_reminders, set_gamification, set_reminder
from .reminders import format_when, set_zoom_link
from .sync import last_data_received, sync_all

log = get_logger(__name__)

_USER_MENTION = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")
_URL = re.compile(r"<(?:mailto:)?([^|>@<]+@[^|>]+|https?://[^|>]+)(?:\|[^>]*)?>")


@dataclass(frozen=True)
class Context:
    caller: Caller
    settings: Settings
    now: datetime
    channel_id: str | None = None

    @property
    def cohort_id(self) -> str:
        cohort = self.caller.cohort_id or self.settings.slack_cohort
        if not cohort:
            raise CufaError("No cohort configured. Set CUFA_SLACK_COHORT.")
        return cohort


@dataclass(frozen=True)
class Reply:
    text: str
    ephemeral: bool = True


Handler = Callable[[psycopg.Connection, SlackClient, Context, list[str]], Reply]


def _args(text: str) -> list[str]:
    cleaned = _URL.sub(lambda m: m.group(1), text or "")
    try:
        return shlex.split(cleaned)
    except ValueError:
        return cleaned.split()


def _mention(token: str) -> str | None:
    m = _USER_MENTION.match(token or "")
    return m.group(1) if m else None


def _find_session(conn: psycopg.Connection, cohort_id: str, query: str) -> dict[str, Any]:
    needle = (query or "").strip()
    if not needle:
        raise CufaError("Which session? Give part of the title, or `next`/`last`.")
    if needle.lower() == "next":
        row = fetch_one(
            conn,
            'select * from "session" where cohort_id = %s and scheduled_at_utc >= now() order by scheduled_at_utc limit 1',
            (cohort_id,),
        )
        if row:
            return row
        raise CufaError("There is no upcoming session.")
    if needle.lower() == "last":
        row = fetch_one(
            conn,
            'select * from "session" where cohort_id = %s and scheduled_at_utc < now() order by scheduled_at_utc desc limit 1',
            (cohort_id,),
        )
        if row:
            return row
        raise CufaError("No session has happened yet.")
    try:
        import uuid

        uuid.UUID(needle)
        row = fetch_one(conn, 'select * from "session" where session_id = %s', (needle,))
        if row:
            return row
    except ValueError:
        pass
    rows = fetch_all(
        conn,
        'select * from "session" where cohort_id = %s and title ilike %s order by scheduled_at_utc desc',
        (cohort_id, f"%{needle}%"),
    )
    if len(rows) == 1:
        return rows[0]
    if rows:
        raise CufaError(
            f"{needle!r} matches more than one session: " + ", ".join(f"{r['title']} ({r['scheduled_at_utc']:%b %-d})" for r in rows[:6])
        )
    raise CufaError(f"No session matches {needle!r}.")


def _parse_when(tokens: list[str]) -> datetime:
    raw = " ".join(tokens).strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %I:%M%p", "%Y-%m-%d %I%p"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise CufaError(f"Could not read the time {raw!r}. Use `2026-09-20 18:00` (local to the cohort, America/New_York).")


COHORT_TZ = "America/New_York"


# ---------------------------------------------------------------------------
# fellow commands
# ---------------------------------------------------------------------------


def cmd_help(conn, client, ctx: Context, args: list[str]) -> Reply:
    lines = [
        "*What I can do*",
        "`/reminders` — see or change when you get session and assignment reminders (`/reminders session 10m off`, `/reminders all off`)",
        "`/badges` — your badges and streak (`/badges off` to stop badge messages)",
        "`/checkin [note]` — ask a staff member to check in with you",
        "`/me` — your attendance, exit tickets and Slack activity",
        "`/dashboard` — a private link to the same, with an export button",
    ]
    if ctx.caller.is_admin:
        lines += [
            "",
            "*Staff*",
            "`/attendance <session|next|last>` — who checked in, exit tickets, airtime",
            "`/fellow <name|id|email>` — profile card: attendance, activity, interventions, scores",
            "`/report` — the cohort so far",
            "`/leaderboard [checkins|streak|messages|shoutouts_given|exit_tickets]` — staff-only ranking",
            "`/assignment create \"Title\" 2026-09-20 18:00 [solvathon|case_brief] [link]` · `/assignment list` · `/assignment link <which> <url>`",
            "`/score <assignment> <fellow> <score> [note]` — record a Solvathon or case-brief score",
            "`/zoom <session|next> <link>` — put the Zoom link on a session (goes out in reminders)",
            "`/outreach <fellow> [note]` · `/outreach clear <fellow>` — mark that someone has reached out",
            "`/alias <fellow> <email> [school|personal]` — a second address on one record",
            "`/link <@user> <fellow>` — attach a Slack account to a roster record",
            "`/alerts` · `/alerts resolve <@user> staff|ignored` — accounts that joined but are not on the roster",
            "`/digest` — post the weekly digest now · `/sync` — pull Slack now",
        ]
    return Reply("\n".join(lines))


def cmd_reminders(conn, client, ctx: Context, args: list[str]) -> Reply:
    uid = ctx.caller.slack_user_id
    if not args:
        return Reply("*Your reminders*\n" + get_preferences(conn, uid).describe() + "\n\n`/reminders session 10m off` · `/reminders assignment on` · `/reminders all off`")
    kind = args[0].lower()
    if kind not in ("session", "assignment", "all"):
        raise CufaError("Start with `session`, `assignment` or `all`.")
    rest = [a.lower() for a in args[1:]]
    if not rest:
        raise CufaError("Then say `on`, `off`, or an interval and on/off — e.g. `/reminders session 10m off`.")
    if rest[0] in ("on", "off"):
        prefs = set_all_reminders(conn, uid, kind=kind, enabled=rest[0] == "on")
        return Reply("Updated.\n" + prefs.describe())
    if kind == "all":
        raise CufaError("Use `session` or `assignment` with an interval.")
    offset = parse_offset(rest[0])
    if len(rest) < 2 or rest[1] not in ("on", "off"):
        raise CufaError("Say `on` or `off` after the interval.")
    prefs = set_reminder(conn, uid, kind=kind, offset=offset, enabled=rest[1] == "on")
    return Reply("Updated.\n" + prefs.describe())


def cmd_badges(conn, client, ctx: Context, args: list[str]) -> Reply:
    uid = ctx.caller.slack_user_id
    if args and args[0].lower() in ("on", "off"):
        set_gamification(conn, uid, args[0].lower() == "on")
        return Reply("Badge messages are now *" + args[0].lower() + "*." + (" You can turn them back on with `/badges on`." if args[0].lower() == "off" else ""))
    if not ctx.caller.fellow_id:
        return Reply("Your Slack account is not linked to the roster yet, so there is nothing to show. A staff member can fix that with `/link`.")
    evidence = next((e for e in collect_evidence(conn, ctx.cohort_id, now=ctx.now) if e.fellow_id == ctx.caller.fellow_id), None)
    return Reply(render_badges(ctx.caller.full_name or "there", badges_for(conn, ctx.caller.fellow_id), evidence))


def cmd_checkin(conn, client, ctx: Context, args: list[str]) -> Reply:
    if not ctx.caller.fellow_id:
        return Reply("Your Slack account is not linked to the roster yet. Message a staff member directly and they will sort it out.")
    note = " ".join(args).strip() or None
    intervention_id = request_check_in(conn, ctx.caller.fellow_id, by_slack_user=ctx.caller.slack_user_id, note=note)
    channel = ctx.settings.slack_staff_channel
    if channel:
        post_check_in_ping(conn, client, channel_id=channel, intervention_id=intervention_id, fellow_name=ctx.caller.full_name or ctx.caller.fellow_id, note=note)
        return Reply("Done — a staff member has been pinged and will reach out.")
    return Reply("Recorded. (No staff channel is configured yet, so nobody was pinged automatically — a staffer will see it on the dashboard.)")


def cmd_me(conn, client, ctx: Context, args: list[str]) -> Reply:
    if not ctx.caller.fellow_id:
        return Reply("Your Slack account is not linked to the roster yet. A staff member can fix that with `/link`.")
    e = fellow_engagement(conn, ctx.caller.fellow_id, now=ctx.now)
    if e is None:
        return Reply("Nothing recorded yet.")
    lines = [
        f"*{e.full_name}*",
        f"Sessions attended: {e.attended}/{e.sessions_held}" + (f" (+{e.needs_review} under review)" if e.needs_review else ""),
        f"Exit tickets: {e.forms_submitted}/{e.forms_expected}",
        f"Slack messages: {e.messages} (last 7 days: {e.messages_7d})",
    ]
    subs = [s for s in submissions_for_fellow(conn, e.fellow_id) if s["submitted_at_utc"] or s["score"] is not None]
    for s in subs:
        lines.append(f"{KIND_LABELS.get(s['kind'], 'Assignment')} — {s['title']}: " + (f"{s['score']}" + (f"/{s['max_score']}" if s["max_score"] else "") if s["score"] is not None else "submitted"))
    lines.append("_`/dashboard` for the full view and an export._")
    return Reply("\n".join(lines))


def cmd_dashboard(conn, client, ctx: Context, args: list[str]) -> Reply:
    if not ctx.caller.fellow_id:
        return Reply("Your Slack account is not linked to the roster yet. A staff member can fix that with `/link`.")
    url = fellow_dashboard_url(ctx.settings, ctx.caller.fellow_id)
    return Reply(f"Your dashboard (private link, valid for 7 days): {url}")


# ---------------------------------------------------------------------------
# staff commands
# ---------------------------------------------------------------------------


def cmd_attendance(conn, client, ctx: Context, args: list[str]) -> Reply:
    session = _find_session(conn, ctx.cohort_id, " ".join(args))
    return Reply(session_summary_text(conn, str(session["session_id"])))


def cmd_fellow(conn, client, ctx: Context, args: list[str]) -> Reply:
    f = find_fellow(conn, " ".join(args), cohort_id=ctx.cohort_id)
    e = fellow_engagement(conn, f["fellow_id"], now=ctx.now)
    lines = [f"*{f['full_name']}* · {f['fellow_id']} · {f['status']}"]
    aliases = list_aliases(conn, f["fellow_id"])
    lines.append(f"Email: {f['primary_email']}" + (" · also " + ", ".join(a["email"] for a in aliases) if aliases else ""))
    slack = fetch_all(conn, "select slack_user_id, match_method from v_slack_user_resolved where fellow_id = %s and not is_bot", (f["fellow_id"],))
    lines.append("Slack: " + (", ".join(f"<@{s['slack_user_id']}> ({s['match_method']})" for s in slack) if slack else "not found in the workspace"))
    if e:
        lines.append(
            f"Attendance: {e.attended}/{e.sessions_held}" + (f" (+{e.needs_review} under review)" if e.needs_review else "")
            + f" · Exit tickets: {e.forms_submitted}/{e.forms_expected} · Messages: {e.messages} (7d: {e.messages_7d}; cohort mean {e.cohort_mean_messages})"
        )
        lines.append(f"Attention index: *{e.attention_index}*" + (f" — {'; '.join(e.flags)}" if e.flags else ""))
        lines.append("Reached out: " + ("*yes*" if e.reached_out else "no") + (f" · open check-in requests: {e.open_check_in_requests}" if e.open_check_in_requests else ""))
    subs = submissions_for_fellow(conn, f["fellow_id"])
    if subs:
        lines.append("Assignments: " + " · ".join(
            f"{s['title']}: " + (f"{s['score']}" + (f"/{s['max_score']}" if s['max_score'] else "") if s["score"] is not None else ("submitted" if s["submitted_at_utc"] else "not submitted"))
            for s in subs
        ))
    recent = interventions_for(conn, f["fellow_id"])[:5]
    if recent:
        lines.append("Interventions: " + " · ".join(f"{i['kind']} {i['created_at']:%b %-d}" + (f" ({i['by_email']})" if i["by_email"] else "") for i in recent))
    badges = badges_for(conn, f["fellow_id"])
    if badges:
        lines.append("Badges: " + " ".join(f"{b['emoji']}{b['level']}" for b in badges))
    funnel = fellow_funnel(conn, f["fellow_id"])
    if funnel:
        lines.append("Funnel: " + " → ".join(
            ("✅ " if getattr(funnel, k) else "⬜ ") + label
            for k, label in (("accepted_at", "accepted"), ("slack_joined_at", "Slack"), ("first_message_at", "message"), ("first_checkin_at", "check-in"), ("completed_at", "completed"))
        ))
    return Reply("\n".join(lines))


def cmd_report(conn, client, ctx: Context, args: list[str]) -> Reply:
    cohort = ctx.cohort_id
    report = cohort_report(conn, cohort)
    att = cohort_attendance(conn, cohort, now=ctx.now)
    engagement = cohort_engagement(conn, cohort, now=ctx.now)
    received = last_data_received(conn, cohort)
    t = report.totals
    lines = [f"*Report — cohort {cohort}* (as of {ctx.now:%b %-d %H:%M} UTC)"]
    lines.append(
        f"Sessions held: {att.get('sessions_held', 0)} · active fellows: {att.get('active_fellows', 0)} · "
        f"overall attendance: {round(100 * (att.get('rate') or 0))}%"
    )
    lines.append(
        f"Check-ins: {t.get('checkins', 0)} (attended {t.get('attended', 0)}, needs review {t.get('needs_review', 0)}, "
        f"unknown address {t.get('unknown_email', 0)})"
    )
    pb = report.part_b.get("totals", {}) if isinstance(report.part_b, dict) else {}
    if pb:
        lines.append(f"Exit tickets: {pb.get('responses', 0)} from {pb.get('fellows', 0)} fellows · shoutouts given: {pb.get('shoutouts_given', 0)}")
    active = most_active(conn, cohort, now=ctx.now)
    if active:
        lines.append("Most active this week: " + ", ".join(f"{a['full_name']} ({a['messages']})" for a in active))
    watch = [e for e in engagement if e.attention_index >= 60][:8]
    if watch:
        lines.append("Highest attention index: " + ", ".join(f"{e.full_name} {e.attention_index}{' ✓' if e.reached_out else ''}" for e in watch))
    lines.append("Last data: " + " · ".join(f"{k} {v:%b %-d %H:%M}" if v else f"{k} never" for k, v in received.items()))
    lines.append("_✓ = someone has reached out. Full detail: the staff dashboard, or `cufa report`._")
    return Reply("\n".join(lines))


def cmd_leaderboard(conn, client, ctx: Context, args: list[str]) -> Reply:
    by = (args[0].lower() if args else "checkins")
    if by not in RANK_KEYS:
        raise CufaError("Rank by one of: " + ", ".join(RANK_KEYS))
    return Reply(render_leaderboard(by, leaderboard(conn, ctx.cohort_id, by=by, now=ctx.now)))


def cmd_assignment(conn, client, ctx: Context, args: list[str]) -> Reply:
    if not args or args[0].lower() == "list":
        rows = list_assignments(conn, ctx.cohort_id)
        if not rows:
            return Reply("No assignments yet. `/assignment create \"Solvathon deck\" 2026-10-01 18:00 solvathon https://…`")
        return Reply("\n".join(
            f"• *{r['title']}* ({KIND_LABELS.get(r['kind'])}) due {format_when(r['due_at_utc'], COHORT_TZ)} — {r['submitted']} submitted, {r['scored']} scored"
            + (f" — {r['link']}" if r["link"] else " — _no link_") + f"\n   `{r['assignment_id']}`"
            for r in rows
        ))
    action = args[0].lower()
    if action == "create":
        rest = args[1:]
        if len(rest) < 3:
            raise CufaError('Usage: `/assignment create "Title" 2026-09-20 18:00 [solvathon|case_brief|other] [link]`')
        title, date, time_ = rest[0], rest[1], rest[2]
        kind, link = "other", None
        for token in rest[3:]:
            t = token.lower().replace("-", "_")
            if t in KIND_LABELS:
                kind = t
            elif token.startswith("http"):
                link = token
        assignment_id = create_assignment(
            conn,
            AssignmentInput(
                cohort_id=ctx.cohort_id, title=title, due_at_local=_parse_when([date, time_]), timezone=COHORT_TZ,
                kind=kind, link=link, created_by=ctx.caller.email,
            ),
        )
        return Reply(f"Created *{title}* ({KIND_LABELS[kind]}), due {date} {time_} {COHORT_TZ}. Reminders go out 24h, 1h and 10m before.\n`{assignment_id}`" + ("" if link else "\nAdd the link with `/assignment link <title> <url>`."))
    if action == "link":
        if len(args) < 3:
            raise CufaError("Usage: `/assignment link <which> <url>`")
        a = find_assignment(conn, ctx.cohort_id, args[1])
        set_link(conn, str(a["assignment_id"]), args[2])
        return Reply(f"Link set on *{a['title']}*: {args[2]}")
    raise CufaError("Use `/assignment list`, `/assignment create …` or `/assignment link …`.")


def cmd_score(conn, client, ctx: Context, args: list[str]) -> Reply:
    if len(args) < 3:
        raise CufaError('Usage: `/score <assignment> <fellow> <score> [note]` — e.g. `/score solvathon "Ada Lovelace" 87`')
    a = find_assignment(conn, ctx.cohort_id, args[0])
    f = find_fellow(conn, args[1], cohort_id=ctx.cohort_id)
    try:
        value = Decimal(args[2])
    except InvalidOperation:
        raise CufaError(f"{args[2]!r} is not a number.") from None
    note = " ".join(args[3:]).strip() or None
    record_score(conn, str(a["assignment_id"]), f["fellow_id"], score=value, by=ctx.caller.email or ctx.caller.slack_user_id, note=note)
    return Reply(f"Recorded {value}" + (f"/{a['max_score']}" if a["max_score"] else "") + f" for *{f['full_name']}* on *{a['title']}*.")


def cmd_zoom(conn, client, ctx: Context, args: list[str]) -> Reply:
    if len(args) < 2:
        raise CufaError("Usage: `/zoom <session|next> <link>`")
    link = args[-1]
    if not link.startswith("http"):
        raise CufaError("The last argument should be the Zoom link.")
    session = _find_session(conn, ctx.cohort_id, " ".join(args[:-1]))
    set_zoom_link(conn, str(session["session_id"]), link)
    return Reply(f"Zoom link set on *{session['title']}* ({format_when(session['scheduled_at_utc'], session['timezone'])}). It will be in every reminder.")


def cmd_outreach(conn, client, ctx: Context, args: list[str]) -> Reply:
    if not args:
        raise CufaError("Usage: `/outreach <fellow> [note]` or `/outreach clear <fellow>`")
    if args[0].lower() == "clear":
        f = find_fellow(conn, " ".join(args[1:]), cohort_id=ctx.cohort_id)
        clear_reached_out(conn, f["fellow_id"], by_email=ctx.caller.email or ctx.caller.slack_user_id)
        return Reply(f"Cleared: *{f['full_name']}* is no longer marked as reached out.")
    f = find_fellow(conn, args[0], cohort_id=ctx.cohort_id)
    note = " ".join(args[1:]).strip() or None
    mark_reached_out(conn, f["fellow_id"], by_email=ctx.caller.email or ctx.caller.slack_user_id, note=note, source="slack")
    from ..interventions import resolve as resolve_intervention

    for i in interventions_for(conn, f["fellow_id"]):
        if i["kind"] == "check_in_request" and i["resolved_at"] is None:
            resolve_intervention(conn, str(i["intervention_id"]), by_email=ctx.caller.email or ctx.caller.slack_user_id)
    return Reply(f"Marked: someone has reached out to *{f['full_name']}*." + (f" Note: {note}" if note else ""))


def cmd_alias(conn, client, ctx: Context, args: list[str]) -> Reply:
    if len(args) < 2:
        raise CufaError("Usage: `/alias <fellow> <email> [school|personal]`")
    f = find_fellow(conn, args[0], cohort_id=ctx.cohort_id)
    email = args[1]
    kind = args[2].lower() if len(args) > 2 else "other"
    add_alias(conn, f["fellow_id"], email, by=ctx.caller.email or ctx.caller.slack_user_id, kind=kind)
    return Reply(f"*{f['full_name']}* now also resolves from {email}. Every check-in and message from that address is attributed to them from now on, past ones included.")


def cmd_link(conn, client, ctx: Context, args: list[str]) -> Reply:
    if len(args) < 2 or not _mention(args[0]):
        raise CufaError("Usage: `/link <@user> <fellow id or name>`")
    slack_user_id = _mention(args[0])
    assert slack_user_id
    f = find_fellow(conn, " ".join(args[1:]), cohort_id=ctx.cohort_id)
    link_slack_user(conn, slack_user_id, f["fellow_id"], by=ctx.caller.email or ctx.caller.slack_user_id)
    return Reply(f"<@{slack_user_id}> is now *{f['full_name']}* ({f['fellow_id']}).")


def cmd_alerts(conn, client, ctx: Context, args: list[str]) -> Reply:
    if args and args[0].lower() == "resolve":
        if len(args) < 3 or not _mention(args[1]) or args[2].lower() not in ("staff", "ignored"):
            raise CufaError("Usage: `/alerts resolve <@user> staff|ignored`")
        uid = _mention(args[1])
        assert uid
        if resolve_alert(conn, uid, by=ctx.caller.email or ctx.caller.slack_user_id, resolution=args[2].lower()):
            return Reply(f"Resolved: <@{uid}> marked as {args[2].lower()}.")
        return Reply(f"No open alert for <@{uid}>.")
    alerts = open_alerts(conn)
    if not alerts:
        return Reply("No unrostered accounts. 🎉")
    return Reply("*Joined but not on the roster:*\n" + "\n".join(
        f"• <@{a['slack_user_id']}> {a['real_name'] or a['display_name']}" + (f" — {a['email']}" if a["email"] else "") + f" (since {a['created_at']:%b %-d})"
        for a in alerts
    ) + "\n`/link <@user> <fellow>` · `/alerts resolve <@user> staff|ignored`")


def cmd_digest(conn, client, ctx: Context, args: list[str]) -> Reply:
    channel = ctx.settings.slack_staff_channel
    if not channel:
        raise CufaError("No staff channel configured (CUFA_SLACK_STAFF_CHANNEL).")
    if post_weekly_digest(conn, client, cohort_id=ctx.cohort_id, channel_id=channel, now=ctx.now, force=True):
        return Reply("Posted the weekly digest to the staff channel.")
    return Reply("This week's digest was already posted; it is in the staff channel.")


def cmd_sync(conn, client, ctx: Context, args: list[str]) -> Reply:
    summary = sync_all(conn, client, staff_channel=ctx.settings.slack_staff_channel, staff_emails=ctx.settings.slack_admins)
    return Reply(f"Synced: {summary}")


HANDLERS: dict[str, Handler] = {
    "help": cmd_help,
    "reminders": cmd_reminders,
    "badges": cmd_badges,
    "checkin": cmd_checkin,
    "me": cmd_me,
    "dashboard": cmd_dashboard,
    "attendance": cmd_attendance,
    "fellow": cmd_fellow,
    "report": cmd_report,
    "leaderboard": cmd_leaderboard,
    "assignment": cmd_assignment,
    "score": cmd_score,
    "zoom": cmd_zoom,
    "outreach": cmd_outreach,
    "alias": cmd_alias,
    "link": cmd_link,
    "alerts": cmd_alerts,
    "digest": cmd_digest,
    "sync": cmd_sync,
}


def dispatch(
    conn: psycopg.Connection,
    client: SlackClient,
    *,
    command: str,
    text: str,
    slack_user_id: str,
    channel_id: str | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> Reply:
    """Run one slash command. Errors come back as replies, never as tracebacks."""
    settings = settings or get_settings()
    name = command.lstrip("/").lower()
    handler = HANDLERS.get(name)
    if handler is None:
        return Reply(f"I don't know `/{name}`. Try `/help`.")
    caller = identify(conn, slack_user_id, settings)
    ctx = Context(caller=caller, settings=settings, now=now or datetime.now(timezone.utc), channel_id=channel_id)
    try:
        if is_admin_command(name):
            require_admin(caller, name)
        return handler(conn, client, ctx, _args(text))
    except NotAllowed as exc:
        return Reply(f"⛔ {exc}")
    except (UnknownFellow, AmbiguousFellow, CufaError) as exc:
        return Reply(f"⚠️ {exc}")


__all__ = ["Context", "HANDLERS", "Reply", "dispatch"]
