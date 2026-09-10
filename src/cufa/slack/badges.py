"""Badges, streaks and ranks — the gamification a fellow can switch off.

Every award is computed from observations already in the database and stored
as a row with the evidence it came from. Rules live in ``RULES``; a rule is a
badge key, a label, a list of levels, and a function from a fellow's
``Evidence`` to the level they have earned. Adding a badge is adding a rule.

Three constraints, and all three follow from ADRs already in this repo:

* **Everything here is DM'd to the person it is about, never posted publicly.**
  A public leaderboard is a popularity contest; a private "you've checked in
  five sessions running" is encouragement.
* **Opt-out is one command** (``/badges off``) and is honoured before anything
  is sent. Awards are still computed — the staff view needs them — but the
  fellow hears nothing.
* **Shoutouts are ranked by giving, not receiving** (ADR-028). The
  "recognised" badge exists, but the rank is for recognising others.
* **The help checkbox is not evidence for anything.** ``help_request`` is not
  read here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import psycopg

from ..db import execute, fetch_all, fetch_one
from ..logging_setup import get_logger
from .client import SlackApiError, SlackClient
from .preferences import get_preferences

log = get_logger(__name__)


@dataclass
class Evidence:
    """What one fellow has done, as counts."""

    fellow_id: str
    full_name: str
    checkins: int = 0            # sessions with a Part A attended decision
    exit_tickets: int = 0        # Part B forms submitted
    messages: int = 0            # fellow-facing Slack messages
    thread_replies: int = 0
    shoutouts_given: int = 0
    shoutouts_received: int = 0
    streak: int = 0              # consecutive sessions attended, ending at the latest held
    sessions_held: int = 0

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class Rule:
    key: str
    label: str
    description: str
    thresholds: tuple[int, ...]          # level 1, 2, 3 ... earned at each value
    measure: Callable[[Evidence], int]
    emoji: str = "🏅"

    def level_for(self, evidence: Evidence) -> int:
        value = self.measure(evidence)
        return sum(1 for t in self.thresholds if value >= t)


RULES: tuple[Rule, ...] = (
    Rule("first_checkin", "First check-in", "Checked in to a live session", (1,), lambda e: e.checkins, "✅"),
    Rule("regular", "Regular", "Checked in to 3, 6 and 9 sessions", (3, 6, 9), lambda e: e.checkins, "📅"),
    Rule("streak", "On a streak", "3, 5 and 8 sessions in a row", (3, 5, 8), lambda e: e.streak, "🔥"),
    Rule("reflective", "Reflective", "Filled in 3, 6 and 9 exit tickets", (3, 6, 9), lambda e: e.exit_tickets, "📝"),
    Rule("voice", "Finding your voice", "10, 50 and 150 Slack messages", (10, 50, 150), lambda e: e.messages, "💬"),
    Rule("thread_weaver", "Thread weaver", "Replied in 10 and 40 threads", (10, 40), lambda e: e.thread_replies, "🧵"),
    Rule("recogniser", "Recogniser", "Gave 1, 3 and 6 shoutouts", (1, 3, 6), lambda e: e.shoutouts_given, "🙌"),
    Rule("recognised", "Recognised", "Named in a shoutout", (1,), lambda e: e.shoutouts_received, "⭐"),
)

RULES_BY_KEY = {r.key: r for r in RULES}


# ---------------------------------------------------------------------------
# evidence
# ---------------------------------------------------------------------------


def _streak(attended: list[bool]) -> int:
    n = 0
    for hit in reversed(attended):
        if not hit:
            break
        n += 1
    return n


def collect_evidence(conn: psycopg.Connection, cohort_id: str, *, now: datetime | None = None) -> list[Evidence]:
    now = now or datetime.now(timezone.utc)
    fellows = fetch_all(
        conn,
        "select fellow_id, full_name from fellow where cohort_id = %s and status = 'active' order by full_name",
        (cohort_id,),
    )
    if not fellows:
        return []
    sessions = [
        str(r["session_id"])
        for r in fetch_all(
            conn,
            'select session_id from "session" where cohort_id = %s and scheduled_at_utc <= %s order by scheduled_at_utc',
            (cohort_id, now),
        )
    ]
    attended_pairs = {
        (r["fellow_id"], str(r["session_id"]))
        for r in fetch_all(
            conn,
            "select distinct fellow_id, session_id from v_checkin_resolved where cohort_id = %s and status = 'attended' and fellow_id is not null and session_id is not null",
            (cohort_id,),
        )
    }
    exit_tickets = {
        r["fellow_id"]: int(r["n"])
        for r in fetch_all(
            conn,
            "select fellow_id, count(distinct session_id) as n from v_checkin_b_resolved where cohort_id = %s and fellow_id is not null group by fellow_id",
            (cohort_id,),
        )
    }
    slack = {
        r["fellow_id"]: r
        for r in fetch_all(
            conn,
            """
            select fellow_id, count(*) as messages, count(*) filter (where is_thread_reply) as replies
              from v_slack_message_resolved
             where cohort_id = %s and fellow_id is not null and not staff_channel
             group by fellow_id
            """,
            (cohort_id,),
        )
    }
    given = {
        r["fellow_id"]: int(r["n"])
        for r in fetch_all(
            conn,
            """
            select b.fellow_id, count(*) as n
              from peer_shoutout p join v_checkin_b_resolved b on b.checkin_b_id = p.checkin_b_id
             where b.cohort_id = %s and b.fellow_id is not null
             group by b.fellow_id
            """,
            (cohort_id,),
        )
    }
    received = {
        r["named_fellow_id"]: int(r["n"])
        for r in fetch_all(
            conn,
            """
            select p.named_fellow_id, count(*) as n
              from peer_shoutout p join fellow f on f.fellow_id = p.named_fellow_id
             where f.cohort_id = %s and p.named_fellow_id is not null
             group by p.named_fellow_id
            """,
            (cohort_id,),
        )
    }
    out: list[Evidence] = []
    for f in fellows:
        fid = f["fellow_id"]
        attended = [(fid, s) in attended_pairs for s in sessions]
        s = slack.get(fid, {})
        out.append(
            Evidence(
                fellow_id=fid,
                full_name=f["full_name"],
                checkins=sum(attended),
                exit_tickets=exit_tickets.get(fid, 0),
                messages=int(s.get("messages") or 0),
                thread_replies=int(s.get("replies") or 0),
                shoutouts_given=given.get(fid, 0),
                shoutouts_received=received.get(fid, 0),
                streak=_streak(attended),
                sessions_held=len(sessions),
            )
        )
    return out


# ---------------------------------------------------------------------------
# awarding
# ---------------------------------------------------------------------------


@dataclass
class AwardRun:
    computed: int = 0
    new_awards: list[tuple[str, str, int]] = field(default_factory=list)  # fellow, badge, level
    notified: int = 0
    skipped_opt_out: int = 0
    failed: int = 0


def award_badges(conn: psycopg.Connection, cohort_id: str, *, now: datetime | None = None) -> AwardRun:
    """Compute and store every badge level earned so far. Never revokes."""
    run = AwardRun()
    for evidence in collect_evidence(conn, cohort_id, now=now):
        run.computed += 1
        for rule in RULES:
            for level in range(1, rule.level_for(evidence) + 1):
                row = fetch_one(
                    conn,
                    """
                    insert into badge_award (fellow_id, badge_key, level, evidence)
                    values (%s, %s, %s, %s::jsonb)
                    on conflict (fellow_id, badge_key, level) do nothing
                    returning award_id
                    """,
                    (evidence.fellow_id, rule.key, level, _json(evidence.to_dict())),
                )
                if row is not None:
                    run.new_awards.append((evidence.fellow_id, rule.key, level))
    return run


def _json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, default=str)


def badges_for(conn: psycopg.Connection, fellow_id: str) -> list[dict[str, Any]]:
    """Highest level of each badge the fellow holds, with labels attached."""
    rows = fetch_all(
        conn,
        """
        select badge_key, max(level) as level, min(awarded_at) as first_awarded_at, max(awarded_at) as awarded_at
          from badge_award where fellow_id = %s group by badge_key
        """,
        (fellow_id,),
    )
    out = []
    for r in rows:
        rule = RULES_BY_KEY.get(r["badge_key"])
        if rule is None:
            continue
        out.append(
            {
                "badge_key": r["badge_key"],
                "label": rule.label,
                "emoji": rule.emoji,
                "description": rule.description,
                "level": int(r["level"]),
                "max_level": len(rule.thresholds),
                "awarded_at": r["awarded_at"],
            }
        )
    out.sort(key=lambda b: (-b["level"], b["label"]))
    return out


def render_badges(full_name: str, badges: list[dict[str, Any]], evidence: Evidence | None) -> str:
    lines = [f"*Your badges, {full_name}*"]
    if not badges:
        lines.append("None yet — check in to a session and one appears.")
    for b in badges:
        stars = "★" * b["level"] + "☆" * (b["max_level"] - b["level"])
        lines.append(f"{b['emoji']} *{b['label']}* {stars} — {b['description']}")
    if evidence:
        lines.append("")
        lines.append(
            f"Streak: {evidence.streak} session{'s' if evidence.streak != 1 else ''} in a row · "
            f"Check-ins: {evidence.checkins}/{evidence.sessions_held} · Exit tickets: {evidence.exit_tickets} · "
            f"Messages: {evidence.messages} · Shoutouts given: {evidence.shoutouts_given}"
        )
    lines.append("_`/badges off` stops these messages._")
    return "\n".join(lines)


def notify_new_awards(
    conn: psycopg.Connection, client: SlackClient, run: AwardRun, *, cohort_id: str
) -> AwardRun:
    """DM each fellow about awards not yet announced, honouring opt-out."""
    pending = fetch_all(
        conn,
        """
        select a.award_id, a.fellow_id, a.badge_key, a.level, u.slack_user_id, f.full_name
          from badge_award a
          join fellow f on f.fellow_id = a.fellow_id
          left join v_slack_user_resolved u on u.fellow_id = a.fellow_id and not u.is_bot and not u.deleted
         where a.notified_at is null and f.cohort_id = %s
         order by a.fellow_id, a.awarded_at
        """,
        (cohort_id,),
    )
    by_fellow: dict[str, list[dict[str, Any]]] = {}
    for row in pending:
        by_fellow.setdefault(row["fellow_id"], []).append(row)
    for fellow_id, rows in by_fellow.items():
        slack_user_id = next((r["slack_user_id"] for r in rows if r["slack_user_id"]), None)
        if slack_user_id is None:
            continue  # not on Slack yet; the award waits
        if not get_preferences(conn, slack_user_id).gamification:
            run.skipped_opt_out += len(rows)
            execute(
                conn,
                "update badge_award set notified_at = now() where award_id = any(%s)",
                ([r["award_id"] for r in rows],),
            )
            continue
        lines = [f"🎉 New badge{'s' if len(rows) > 1 else ''}, {rows[0]['full_name']}!"]
        for r in rows:
            rule = RULES_BY_KEY[r["badge_key"]]
            stars = "★" * int(r["level"]) + "☆" * (len(rule.thresholds) - int(r["level"]))
            lines.append(f"{rule.emoji} *{rule.label}* {stars} — {rule.description}")
        lines.append("_`/badges` shows them all · `/badges off` stops these messages._")
        try:
            dm = client.open_dm(slack_user_id)
            client.post_message(dm, "\n".join(lines))
        except SlackApiError as exc:
            run.failed += 1
            log.warning("badge DM to %s failed: %s", slack_user_id, exc.error)
            continue
        execute(
            conn,
            "update badge_award set notified_at = now() where award_id = any(%s)",
            ([r["award_id"] for r in rows],),
        )
        run.notified += len(rows)
    return run


# ---------------------------------------------------------------------------
# ranks, for staff
# ---------------------------------------------------------------------------

RANK_KEYS = {
    "checkins": ("Most check-ins", lambda e: e.checkins),
    "streak": ("Longest streak", lambda e: e.streak),
    "messages": ("Most active on Slack", lambda e: e.messages),
    "shoutouts_given": ("Most shoutouts given", lambda e: e.shoutouts_given),
    "exit_tickets": ("Most exit tickets", lambda e: e.exit_tickets),
}


def leaderboard(
    conn: psycopg.Connection, cohort_id: str, *, by: str = "checkins", limit: int = 5, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Staff-only ranking. Never posted to a channel by the bot."""
    if by not in RANK_KEYS:
        raise ValueError(f"rank by one of {', '.join(RANK_KEYS)}")
    _, measure = RANK_KEYS[by]
    rows = sorted(collect_evidence(conn, cohort_id, now=now), key=lambda e: (-measure(e), e.full_name))
    out = []
    for i, e in enumerate(rows[:limit], start=1):
        out.append({"rank": i, "fellow_id": e.fellow_id, "full_name": e.full_name, "value": measure(e), "badges": len(badges_for(conn, e.fellow_id))})
    return out


def render_leaderboard(by: str, rows: list[dict[str, Any]]) -> str:
    label = RANK_KEYS[by][0]
    lines = [f"*{label}* (staff view — not shown to fellows)"]
    if not rows:
        lines.append("No data yet.")
    for r in rows:
        lines.append(f"{r['rank']}. {r['full_name']} — {r['value']}  ({r['badges']} badge{'s' if r['badges'] != 1 else ''})")
    return "\n".join(lines)


__all__ = [
    "RANK_KEYS",
    "RULES",
    "RULES_BY_KEY",
    "AwardRun",
    "Evidence",
    "Rule",
    "award_badges",
    "badges_for",
    "collect_evidence",
    "leaderboard",
    "notify_new_awards",
    "render_badges",
    "render_leaderboard",
]
