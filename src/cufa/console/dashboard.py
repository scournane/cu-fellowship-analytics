"""Two dashboards, rendered on the server with no build step.

* ``/dashboard`` — **staff**. Overall attendance, the attention list with the
  "reached out" toggle, this week's most active fellows, open check-in
  requests, roster alerts, badges and ranks, assignment scores (with a form to
  enter them), the funnel, and when data last arrived. Behind the console
  allowlist like every other staff screen.
* ``/me/<token>`` — **one fellow**. Their own attendance, exit tickets, Slack
  activity, badges, assignments, what is connected, their reminder
  preferences, and an export button. The token is signed and expiring and is
  handed out by the bot's ``/dashboard`` command; there is no fellow login.

Both are plain Jinja templates rather than React screens on purpose: they
are read-mostly, they must keep working the day the front-end toolchain
does not, and a fellow's page must not require the staff bundle.

Neither page reads ``help_request``. Nothing about the help checkbox appears
on a fellow's own page either — it is routed to a named person and is not a
"stat".
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from ..assignments import list_assignments, record_score, submissions_for_assignment, submissions_for_fellow
from ..config import get_settings
from ..db import connection, fetch_all, fetch_one
from ..engagement import cohort_attendance, cohort_engagement, fellow_engagement, most_active
from ..errors import CufaError
from ..funnel import cohort_summary, fellow_funnel, STAGE_LABELS
from ..interventions import clear_reached_out, for_fellow as interventions_for, mark_reached_out, open_requests, resolve as resolve_intervention
from ..slack.badges import RANK_KEYS, badges_for, leaderboard
from ..slack.dashboard_links import read_token
from ..slack.identity import open_alerts
from ..slack.identity import list_aliases
from ..slack.preferences import DEFAULT_OFFSETS, get_preferences, set_all_reminders, set_gamification, set_reminder
from ..slack.sync import last_data_received
from ..zoom import speaking_share


def _cohorts(conn: Any) -> list[str]:
    return [r["cohort_id"] for r in fetch_all(conn, "select cohort_id from cohort order by cohort_id")]


def _pick_cohort(conn: Any, requested: str | None) -> str | None:
    cohorts = _cohorts(conn)
    if requested and requested in cohorts:
        return requested
    settings = get_settings()
    if settings.slack_cohort and settings.slack_cohort in cohorts:
        return settings.slack_cohort
    return cohorts[0] if cohorts else None


def staff_context(conn: Any, cohort_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    engagement = cohort_engagement(conn, cohort_id, now=now)
    assignments = list_assignments(conn, cohort_id)
    for a in assignments:
        a["rows"] = submissions_for_assignment(conn, str(a["assignment_id"]))
    return {
        "cohort_id": cohort_id,
        "cohorts": _cohorts(conn),
        "now": now,
        "attendance": cohort_attendance(conn, cohort_id, now=now),
        "engagement": [e.to_dict() for e in engagement],
        "most_active": most_active(conn, cohort_id, now=now),
        "requests": open_requests(conn, cohort_id),
        "alerts": open_alerts(conn),
        "ranks": {key: leaderboard(conn, cohort_id, by=key, now=now) for key in RANK_KEYS},
        "rank_labels": {key: label for key, (label, _) in RANK_KEYS.items()},
        "assignments": assignments,
        "funnel": cohort_summary(conn, cohort_id),
        "stage_labels": STAGE_LABELS,
        "received": last_data_received(conn, cohort_id),
        "fellows": fetch_all(conn, "select fellow_id, full_name from fellow where cohort_id = %s and status = 'active' order by full_name", (cohort_id,)),
    }


def fellow_context(conn: Any, fellow_id: str, *, now: datetime | None = None) -> dict[str, Any] | None:
    now = now or datetime.now(timezone.utc)
    fellow = fetch_one(conn, "select fellow_id, cohort_id, full_name, status, primary_email from fellow where fellow_id = %s", (fellow_id,))
    if fellow is None:
        return None
    slack = fetch_all(
        conn,
        "select slack_user_id, display_name, real_name, tz, match_method from v_slack_user_resolved where fellow_id = %s and not is_bot and not deleted",
        (fellow_id,),
    )
    prefs = get_preferences(conn, slack[0]["slack_user_id"]).to_dict() if slack else None
    sessions = fetch_all(
        conn,
        """
        select s.session_id, s.title, s.scheduled_at_utc, s.timezone,
               bool_or(a.status = 'attended') as attended,
               bool_or(a.status = 'needs_review') as under_review,
               bool_or(b.checkin_b_id is not null) as exit_ticket,
               max(b.confidence_raw) as confidence
          from "session" s
          left join v_checkin_resolved a on a.session_id = s.session_id and a.fellow_id = %s
          left join v_checkin_b_resolved b on b.session_id = s.session_id and b.fellow_id = %s
         where s.cohort_id = %s and s.scheduled_at_utc <= %s
         group by s.session_id order by s.scheduled_at_utc
        """,
        (fellow_id, fellow_id, fellow["cohort_id"], now),
    )
    engagement = fellow_engagement(conn, fellow_id, now=now)
    return {
        "fellow": fellow,
        "now": now,
        "slack": slack,
        "preferences": prefs,
        "sessions": sessions,
        "engagement": engagement.to_dict() if engagement else None,
        "badges": badges_for(conn, fellow_id),
        "assignments": submissions_for_fellow(conn, fellow_id),
        "funnel": fellow_funnel(conn, fellow_id),
        "stage_labels": STAGE_LABELS,
        "connected": {
            "slack": bool(slack),
            "forms_part_a": any(s["attended"] or s["under_review"] for s in sessions),
            "forms_part_b": any(s["exit_ticket"] for s in sessions),
        },
    }


def fellow_detail_context(conn: Any, fellow_id: str, *, now: datetime | None = None) -> dict[str, Any] | None:
    """Everything the staff see for one fellow: the fellow page plus staff-only rows."""
    base = fellow_context(conn, fellow_id, now=now)
    if base is None:
        return None
    airtime = []
    for s in base["sessions"]:
        for share in speaking_share(conn, str(s["session_id"])):
            if share.fellow_id == fellow_id:
                airtime.append({"session": s["title"], "share": share.share_of_seconds, "turns": share.turns, "words": share.words})
    base.update(
        {
            "aliases": list_aliases(conn, fellow_id),
            "interventions": interventions_for(conn, fellow_id),
            "airtime": airtime,
        }
    )
    return base


def _csv(rows: list[dict[str, Any]], fieldnames: list[str]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: ("" if v is None else v) for k, v in row.items()})
    return buffer.getvalue()


def fellow_export_csv(conn: Any, fellow_id: str) -> str:
    """Everything on the fellow's page, as one CSV. No help-request data."""
    ctx = fellow_context(conn, fellow_id)
    if ctx is None:
        raise CufaError(f"No fellow with id {fellow_id}")
    rows = [
        {
            "session": s["title"],
            "scheduled_at_utc": s["scheduled_at_utc"].isoformat(),
            "attended": bool(s["attended"]),
            "under_review": bool(s["under_review"]),
            "exit_ticket": bool(s["exit_ticket"]),
            "confidence": s["confidence"],
        }
        for s in ctx["sessions"]
    ]
    return _csv(rows, ["session", "scheduled_at_utc", "attended", "under_review", "exit_ticket", "confidence"])


def staff_export_csv(conn: Any, cohort_id: str) -> str:
    rows = [e.to_dict() for e in cohort_engagement(conn, cohort_id)]
    for r in rows:
        r["flags"] = "; ".join(r["flags"])
    fields = [
        "fellow_id", "full_name", "status", "messages", "messages_7d", "words", "cohort_mean_messages", "slack_share",
        "sessions_held", "attended", "needs_review", "attendance_rate", "forms_submitted", "forms_expected",
        "form_completeness", "attention_index", "flags", "reached_out", "open_check_in_requests", "last_message_at",
    ]
    return _csv(rows, fields)


def register(app: FastAPI, templates: Jinja2Templates, require_user: Callable[..., Any]) -> None:
    """Mount the routes. Called once from ``console.app``."""

    @app.get("/dashboard", response_class=HTMLResponse)
    def staff_dashboard(request: Request, cohort: str | None = None, user: Any = Depends(require_user)) -> Response:
        with connection() as conn:
            cohort_id = _pick_cohort(conn, cohort)
            if cohort_id is None:
                return HTMLResponse("<p>No cohort yet. Load a roster first.</p>", status_code=200)
            context = staff_context(conn, cohort_id)
        context.update({"request": request, "user": user, "title": "Staff dashboard"})
        return templates.TemplateResponse(request, "dashboard.html", context)

    @app.get("/dashboard/export.csv")
    def staff_export(cohort: str | None = None, user: Any = Depends(require_user)) -> Response:
        with connection() as conn:
            cohort_id = _pick_cohort(conn, cohort)
            body = staff_export_csv(conn, cohort_id) if cohort_id else ""
        return Response(body, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="engagement-{cohort_id}.csv"'})

    @app.post("/dashboard/fellow/{fellow_id}/outreach")
    def toggle_outreach(
        fellow_id: str, request: Request, action: str = Form(...), note: str = Form(""), cohort: str = Form(""), user: Any = Depends(require_user)
    ) -> Response:
        with connection() as conn:
            if action == "clear":
                clear_reached_out(conn, fellow_id, by_email=user.email)
            else:
                mark_reached_out(conn, fellow_id, by_email=user.email, note=note.strip() or None, source="console")
                for r in open_requests(conn):
                    if r["fellow_id"] == fellow_id:
                        resolve_intervention(conn, str(r["intervention_id"]), by_email=user.email)
        return RedirectResponse(f"/dashboard?cohort={cohort}#attention", status_code=303)

    @app.post("/dashboard/score")
    def enter_score(
        request: Request,
        assignment_id: str = Form(...),
        fellow_id: str = Form(...),
        score: str = Form(...),
        note: str = Form(""),
        cohort: str = Form(""),
        user: Any = Depends(require_user),
    ) -> Response:
        try:
            value = Decimal(score.strip())
        except InvalidOperation:
            return HTMLResponse(f"<p>{score!r} is not a number. <a href='/dashboard?cohort={cohort}'>Back</a></p>", status_code=400)
        with connection() as conn:
            try:
                record_score(conn, assignment_id, fellow_id, score=value, by=user.email, note=note.strip() or None)
            except CufaError as exc:
                return HTMLResponse(f"<p>{exc} <a href='/dashboard?cohort={cohort}'>Back</a></p>", status_code=400)
        return RedirectResponse(f"/dashboard?cohort={cohort}#assignments", status_code=303)

    @app.get("/dashboard/fellow/{fellow_id}", response_class=HTMLResponse)
    def staff_fellow_detail(fellow_id: str, request: Request, user: Any = Depends(require_user)) -> Response:
        with connection() as conn:
            context = fellow_detail_context(conn, fellow_id)
        if context is None:
            return HTMLResponse("<p>No fellow with that id.</p>", status_code=404)
        context.update({"request": request, "user": user, "title": context["fellow"]["full_name"], "staff_view": True, "token": None})
        return templates.TemplateResponse(request, "me.html", context)

    @app.post("/me/{token}/prefs")
    def fellow_prefs(
        token: str,
        kind: str = Form(...),
        offset: str = Form(""),
        enabled: str = Form(...),
    ) -> Response:
        fellow_id = read_token(get_settings(), token)
        if fellow_id is None:
            return Response("expired", status_code=403)
        on = enabled == "on"
        with connection() as conn:
            slack = fetch_one(
                conn,
                "select slack_user_id from v_slack_user_resolved where fellow_id = %s and not is_bot and not deleted order by last_seen_at desc limit 1",
                (fellow_id,),
            )
            if slack is None:
                return HTMLResponse("<p>Join the Slack workspace first; preferences live on your Slack account.</p>", status_code=400)
            uid = slack["slack_user_id"]
            try:
                if kind == "gamification":
                    set_gamification(conn, uid, on)
                elif kind in ("session", "assignment") and offset:
                    set_reminder(conn, uid, kind=kind, offset=int(offset), enabled=on)
                elif kind in ("session", "assignment", "all"):
                    set_all_reminders(conn, uid, kind=kind, enabled=on)
                else:
                    return HTMLResponse("<p>Unknown preference.</p>", status_code=400)
            except (CufaError, ValueError) as exc:
                return HTMLResponse(f"<p>{exc}</p>", status_code=400)
        return RedirectResponse(f"/me/{token}#prefs", status_code=303)

    @app.get("/me/{token}", response_class=HTMLResponse)
    def fellow_dashboard(token: str, request: Request) -> Response:
        fellow_id = read_token(get_settings(), token)
        if fellow_id is None:
            return HTMLResponse("<p>This link has expired or is not valid. Ask the bot for a new one with <code>/dashboard</code>.</p>", status_code=403)
        with connection() as conn:
            context = fellow_context(conn, fellow_id)
        if context is None:
            return HTMLResponse("<p>No record found.</p>", status_code=404)
        context.update({"request": request, "token": token, "title": "My dashboard", "staff_view": False, "offsets": list(DEFAULT_OFFSETS)})
        return templates.TemplateResponse(request, "me.html", context)

    @app.get("/me/{token}/export.csv")
    def fellow_export(token: str) -> Response:
        fellow_id = read_token(get_settings(), token)
        if fellow_id is None:
            return Response("expired", status_code=403)
        with connection() as conn:
            body = fellow_export_csv(conn, fellow_id)
        return Response(body, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="my-fellowship-{fellow_id}.csv"'})


__all__ = ["fellow_context", "fellow_detail_context", "fellow_export_csv", "register", "staff_context", "staff_export_csv"]
