"""``cufa slack …``, ``cufa assignment …``, ``cufa fellow …``, ``cufa zoom …``.

Kept in its own module so ``cli.py`` stays about the forms pipeline. Every
command here is a thin call into the same functions the bot and the console
use — nothing is reimplemented, so the terminal is a complete alternative on
the day Slack, or the web app, is not.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .config import get_settings
from .db import connection
from .errors import CufaError


def _now(args: argparse.Namespace) -> datetime | None:
    raw = getattr(args, "now", None)
    if not raw:
        return None
    from .timeutil import parse_rfc3339

    return parse_rfc3339(raw)


def _client(args: argparse.Namespace) -> Any:
    from .slack.client import build_client

    settings = get_settings()
    if getattr(args, "fake", False) and not settings.fake_slack:
        from .slack.client import FakeSlackClient

        return FakeSlackClient()
    return build_client(settings)


def _cohort(args: argparse.Namespace) -> str:
    cohort = getattr(args, "cohort", None) or get_settings().slack_cohort
    if not cohort:
        raise CufaError("Give --cohort, or set CUFA_SLACK_COHORT.")
    return cohort


def _dump(value: Any) -> None:
    print(json.dumps(value, indent=2, default=str))


# --------------------------------------------------------------------------
# cufa slack
# --------------------------------------------------------------------------


def cmd_slack(args: argparse.Namespace) -> int:
    action = args.slack_action
    settings = get_settings()
    client = _client(args)
    with connection() as conn:
        if action == "sync":
            from .slack.sync import sync_all

            print(sync_all(conn, client, staff_channel=settings.slack_staff_channel, staff_emails=settings.slack_admins, store_text=settings.slack_store_text, cohort_id=settings.slack_cohort))
        elif action == "tick":
            from .slack.digest import tick

            result = tick(conn, client, settings=settings, cohort_id=getattr(args, "cohort", None), now=_now(args), sync=not args.no_sync)
            print(result)
            for err in result.errors:
                print(f"  ! {err}")
        elif action == "reminders":
            from .slack.reminders import run_reminders

            run = run_reminders(conn, client, cohort_id=_cohort(args), now=_now(args))
            print(f"sent={run.sent} skipped_pref={run.skipped_pref} quiet={run.skipped_quiet} dup={run.skipped_dup} failed={run.failed}")
        elif action == "digest":
            from .slack.digest import post_weekly_digest, weekly_digest_text

            if args.post:
                if not settings.slack_staff_channel:
                    raise CufaError("CUFA_SLACK_STAFF_CHANNEL is not set.")
                posted = post_weekly_digest(conn, client, cohort_id=_cohort(args), channel_id=settings.slack_staff_channel, now=_now(args), force=True)
                print("posted" if posted else "already posted this week")
            else:
                print(weekly_digest_text(conn, _cohort(args), now=_now(args)))
        elif action == "summary":
            from .slack.digest import session_summary_text

            print(session_summary_text(conn, args.session))
        elif action == "cmd":
            from .slack.commands import dispatch

            reply = dispatch(conn, client, command=args.name, text=" ".join(args.text), slack_user_id=args.as_user, settings=settings, now=_now(args))
            print(reply.text)
        elif action == "alerts":
            from .slack.identity import open_alerts

            for a in open_alerts(conn):
                print(f"{a['slack_user_id']:<14} {a['real_name'] or a['display_name']:<24} {a['email'] or '':<32} since {a['created_at']:%Y-%m-%d}")
        elif action == "link":
            from .slack.identity import link_slack_user

            link_slack_user(conn, args.slack_user, args.fellow, by=args.by)
            print(f"{args.slack_user} → {args.fellow}")
        elif action == "badges":
            from .slack.badges import award_badges, leaderboard, render_leaderboard

            run = award_badges(conn, _cohort(args), now=_now(args))
            print(f"computed={run.computed} new={len(run.new_awards)}")
            print(render_leaderboard(args.by, leaderboard(conn, _cohort(args), by=args.by, now=_now(args))))
        elif action == "engagement":
            from .engagement import cohort_engagement

            rows = cohort_engagement(conn, _cohort(args), now=_now(args))
            if args.json:
                _dump([r.to_dict() for r in rows])
            else:
                print(f"{'fellow':<26} {'idx':>4}  {'att':>7}  {'tickets':>7}  {'msgs':>5}  out  flags")
                for r in rows:
                    print(
                        f"{r.full_name:<26} {r.attention_index:>4}  {r.attended:>3}/{r.sessions_held:<3}  "
                        f"{r.forms_submitted:>3}/{r.forms_expected:<3}  {r.messages:>5}  {'yes' if r.reached_out else ' - '}  {'; '.join(r.flags)}"
                    )
        elif action == "outreach":
            from .interventions import clear_reached_out, mark_reached_out

            if args.clear:
                clear_reached_out(conn, args.fellow, by_email=args.by)
                print("cleared")
            else:
                mark_reached_out(conn, args.fellow, by_email=args.by, note=args.note, source="cli")
                print("marked")
        else:  # pragma: no cover - argparse restricts choices
            raise CufaError(f"unknown slack action {action}")
    return 0


# --------------------------------------------------------------------------
# cufa assignment
# --------------------------------------------------------------------------


def cmd_assignment(args: argparse.Namespace) -> int:
    from .assignments import (
        AssignmentInput,
        create_assignment,
        find_assignment,
        list_assignments,
        record_score,
        record_submission,
        set_link,
        submissions_for_assignment,
    )
    from .roster import _parse_local

    with connection() as conn:
        if args.assignment_action == "create":
            assignment_id = create_assignment(
                conn,
                AssignmentInput(
                    cohort_id=_cohort(args),
                    title=args.title,
                    due_at_local=_parse_local(args.due),
                    timezone=args.timezone,
                    kind=args.kind,
                    link=args.link,
                    max_score=Decimal(args.max_score) if args.max_score else None,
                    created_by=args.by,
                ),
            )
            print(assignment_id)
        elif args.assignment_action == "list":
            for a in list_assignments(conn, _cohort(args)):
                print(f"{a['assignment_id']}  {a['due_at_utc']:%Y-%m-%d %H:%M}Z  {a['kind']:<10} {a['title']}  submitted={a['submitted']} scored={a['scored']}")
        elif args.assignment_action == "link":
            a = find_assignment(conn, _cohort(args), args.which)
            set_link(conn, str(a["assignment_id"]), args.url)
            print("ok")
        elif args.assignment_action == "submitted":
            a = find_assignment(conn, _cohort(args), args.which)
            record_submission(conn, str(a["assignment_id"]), args.fellow)
            print("ok")
        elif args.assignment_action == "score":
            a = find_assignment(conn, _cohort(args), args.which)
            row = record_score(conn, str(a["assignment_id"]), args.fellow, score=Decimal(args.score), by=args.by, note=args.note)
            print(f"{row['score']} recorded by {row['graded_by']}")
        elif args.assignment_action == "show":
            a = find_assignment(conn, _cohort(args), args.which)
            print(f"{a['title']} ({a['kind']}) due {a['due_at_utc']:%Y-%m-%d %H:%M}Z  link={a['link'] or '-'}")
            for r in submissions_for_assignment(conn, str(a["assignment_id"])):
                score = f"{r['score']}" if r["score"] is not None else "-"
                print(f"  {r['full_name']:<26} submitted={'yes' if r['submitted_at_utc'] else 'no ':<3}  score={score}")
    return 0


# --------------------------------------------------------------------------
# cufa fellow
# --------------------------------------------------------------------------


def cmd_fellow(args: argparse.Namespace) -> int:
    with connection() as conn:
        action = args.fellow_action
        if action == "alias":
            from .slack.identity import add_alias, list_aliases, remove_alias

            if args.remove:
                print("removed" if remove_alias(conn, args.email) else "no such alias")
            elif args.email:
                add_alias(conn, args.fellow, args.email, by=args.by, kind=args.kind)
                print("ok")
            for a in list_aliases(conn, args.fellow):
                print(f"  {a['email']}  ({a['kind']}, added by {a['added_by'] or '?'})")
        elif action == "merge":
            from .slack.identity import merge

            merge(conn, args.keep, args.other_email, by=args.by, kind=args.kind)
            print(f"{args.other_email} now resolves to {args.keep}")
        elif action == "funnel":
            from .funnel import cohort_summary, fellow_funnel, render_fellow_text, render_text

            if args.fellow:
                f = fellow_funnel(conn, args.fellow)
                if f is None:
                    raise CufaError(f"No fellow with id {args.fellow}")
                print(json.dumps(f.to_dict(), default=str, indent=2) if args.json else render_fellow_text(f))
            else:
                summary = cohort_summary(conn, _cohort(args))
                print(json.dumps(summary, default=str, indent=2) if args.json else render_text(summary))
        elif action == "completed":
            from .funnel import mark_completed

            mark_completed(conn, args.fellow)
            print("ok")
        elif action == "retention":
            from .retention import cohort_retention, load_rubric, render_text

            rubric = load_rubric(args.rubric)
            rows = cohort_retention(conn, _cohort(args), rubric=rubric)
            print(json.dumps([r.to_dict() for r in rows], indent=2) if args.json else render_text(_cohort(args), rubric, rows))
        elif action == "card":
            from .slack.client import FakeSlackClient
            from .slack.commands import Context, cmd_fellow as card
            from .slack.permissions import Caller

            ctx = Context(
                caller=Caller(slack_user_id="cli", email=None, is_admin=True, fellow_id=None, full_name=None, cohort_id=_cohort(args)),
                settings=get_settings(),
                now=_now(args) or datetime.now(timezone.utc),
            )
            print(card(conn, FakeSlackClient(), ctx, [args.query]).text)
    return 0


# --------------------------------------------------------------------------
# cufa zoom
# --------------------------------------------------------------------------


def cmd_zoom(args: argparse.Namespace) -> int:
    from .zoom import ingest_transcript, render_text, silent_fellows, speaking_share

    with connection() as conn:
        if args.zoom_action == "ingest":
            result = ingest_transcript(conn, args.session, args.vtt)
            print(f"turns={result['turns']} written={result['written']}")
        title = (conn.execute('select title from "session" where session_id = %s', (args.session,)).fetchone() or {}).get("title", args.session)
        shares = speaking_share(conn, args.session)
        print(json.dumps([s.to_dict() for s in shares], indent=2) if getattr(args, "json", False) else render_text(title, shares))
        quiet = silent_fellows(conn, args.session)
        if quiet:
            print("\nAttended but not heard: " + ", ".join(r["full_name"] for r in quiet))
    return 0


# --------------------------------------------------------------------------
# parser wiring
# --------------------------------------------------------------------------


def add_parsers(sub: argparse._SubParsersAction, *, slack_sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Extend ``cufa slack`` (built in cli.py) and add ``assignment``, ``fellow``, ``zoom``.

    Each sub-command below sets its own ``func`` so it dispatches here rather
    than to the participation-capture handler that owns the ``slack`` parser.
    """
    sp = slack_sub

    for name, help_ in (
        ("sync", "pull members, channels and new messages"),
        ("tick", "do everything that is due: sync, reminders, badges, summaries, digest"),
        ("reminders", "send due reminders only"),
    ):
        q = sp.add_parser(name, help=help_)
        q.add_argument("--cohort")
        q.add_argument("--now", help="RFC3339 instant to pretend it is (testing)")
        q.add_argument("--fake", action="store_true", help="use the in-memory Slack client")
        if name == "tick":
            q.add_argument("--no-sync", action="store_true")

    q = sp.add_parser("digest", help="print (or --post) the weekly staff digest")
    q.add_argument("--cohort")
    q.add_argument("--post", action="store_true")
    q.add_argument("--now")
    q.add_argument("--fake", action="store_true")

    q = sp.add_parser("summary", help="print the post-session summary for one session")
    q.add_argument("--session", required=True)
    q.add_argument("--fake", action="store_true")

    q = sp.add_parser("cmd", help="run a slash command as a given Slack user, e.g. cufa slack cmd /fellow --as U123 ada")
    q.add_argument("name", help="/attendance, /fellow, /report, …")
    q.add_argument("text", nargs="*")
    q.add_argument("--as", dest="as_user", required=True, help="Slack user id to run as")
    q.add_argument("--now")
    q.add_argument("--fake", action="store_true")

    q = sp.add_parser("alerts", help="Slack accounts not on the roster")
    q.add_argument("--fake", action="store_true")

    q = sp.add_parser("link", help="attach a Slack account to a fellow")
    q.add_argument("--slack-user", required=True)
    q.add_argument("--fellow", required=True)
    q.add_argument("--by", required=True)
    q.add_argument("--fake", action="store_true")

    q = sp.add_parser("badges", help="compute badges and print a staff ranking")
    q.add_argument("--cohort")
    q.add_argument("--by", default="checkins", choices=["checkins", "streak", "messages", "shoutouts_given", "exit_tickets"])
    q.add_argument("--now")
    q.add_argument("--fake", action="store_true")

    q = sp.add_parser("engagement", help="per-fellow engagement and attention index")
    q.add_argument("--cohort")
    q.add_argument("--now")
    q.add_argument("--json", action="store_true")
    q.add_argument("--fake", action="store_true")

    q = sp.add_parser("outreach", help="mark (or --clear) that staff reached out to a fellow")
    q.add_argument("--fellow", required=True)
    q.add_argument("--by", required=True)
    q.add_argument("--note")
    q.add_argument("--clear", action="store_true")
    q.add_argument("--fake", action="store_true")
    for name in ("sync", "tick", "reminders", "digest", "summary", "cmd", "alerts", "link", "badges", "engagement", "outreach"):
        sp.choices[name].set_defaults(func=cmd_slack)

    # --- assignment ---
    p = sub.add_parser("assignment", help="assignments, submissions and hand-entered scores")
    sp = p.add_subparsers(dest="assignment_action", required=True)
    q = sp.add_parser("create")
    q.add_argument("--cohort")
    q.add_argument("--title", required=True)
    q.add_argument("--due", required=True, help="local time, e.g. '2026-10-01 18:00'")
    q.add_argument("--timezone", default="America/New_York")
    q.add_argument("--kind", default="other", choices=["solvathon", "case_brief", "other"])
    q.add_argument("--link")
    q.add_argument("--max-score")
    q.add_argument("--by")
    q = sp.add_parser("list")
    q.add_argument("--cohort")
    q = sp.add_parser("link")
    q.add_argument("--cohort")
    q.add_argument("which", help="id, kind or title fragment")
    q.add_argument("url")
    q = sp.add_parser("submitted")
    q.add_argument("--cohort")
    q.add_argument("which")
    q.add_argument("--fellow", required=True)
    q = sp.add_parser("score")
    q.add_argument("--cohort")
    q.add_argument("which")
    q.add_argument("--fellow", required=True)
    q.add_argument("--score", required=True)
    q.add_argument("--by", required=True)
    q.add_argument("--note")
    q = sp.add_parser("show")
    q.add_argument("--cohort")
    q.add_argument("which")
    p.set_defaults(func=cmd_assignment)

    # --- fellow ---
    p = sub.add_parser("fellow", help="aliases, merges, funnel, retention, profile card")
    sp = p.add_subparsers(dest="fellow_action", required=True)
    q = sp.add_parser("alias", help="list or add a second address on a roster record")
    q.add_argument("fellow")
    q.add_argument("email", nargs="?")
    q.add_argument("--kind", default="other", choices=["school", "personal", "other"])
    q.add_argument("--by", default="cli")
    q.add_argument("--remove", action="store_true")
    q = sp.add_parser("merge", help="the manual merge: attach the address one system saw to the record another knows")
    q.add_argument("--keep", required=True, help="the fellow id to keep")
    q.add_argument("--other-email", required=True)
    q.add_argument("--kind", default="other", choices=["school", "personal", "other"])
    q.add_argument("--by", required=True)
    q = sp.add_parser("funnel")
    q.add_argument("--cohort")
    q.add_argument("--fellow")
    q.add_argument("--json", action="store_true")
    q = sp.add_parser("completed", help="stamp completed_at on a fellow")
    q.add_argument("fellow")
    q = sp.add_parser("retention", help="early concepts referenced in later answers (deterministic rubric)")
    q.add_argument("--cohort")
    q.add_argument("--rubric", help="path to a rubric JSON; default config/retention_rubric.json")
    q.add_argument("--json", action="store_true")
    q = sp.add_parser("card", help="the same profile card /fellow shows in Slack")
    q.add_argument("query")
    q.add_argument("--cohort")
    q.add_argument("--now")
    p.set_defaults(func=cmd_fellow)

    # --- zoom ---
    p = sub.add_parser("zoom", help="speaking share from a Zoom transcript (.vtt)")
    sp = p.add_subparsers(dest="zoom_action", required=True)
    q = sp.add_parser("ingest")
    q.add_argument("--session", required=True)
    q.add_argument("--vtt", required=True)
    q.add_argument("--json", action="store_true")
    q = sp.add_parser("share")
    q.add_argument("--session", required=True)
    q.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_zoom)


__all__ = ["add_parsers", "cmd_assignment", "cmd_fellow", "cmd_slack", "cmd_zoom"]
