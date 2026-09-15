"""Two dashboards: one for the staff, one for one fellow.

* ``/dashboard`` — **staff**. Overall attendance, the attention list with the
  "reached out" toggle, this week's most active fellows, open check-in
  requests, roster alerts, badges and ranks, assignment scores (with a form to
  enter them), the funnel, and when data last arrived. Behind the console
  allowlist like every other staff screen.
* ``/me/<token>`` — **one fellow**. Their own attendance, exit tickets, Slack
  activity, badges, assignments, what is connected, their reminder
  preferences, and an export button. The token is signed and expiring and is
  handed out by the bot's ``/dashboard`` command; there is no fellow login.

Both are React screens drawn from the same Astryx components as the rest of
the console (https://astryx.atmeta.com), rendered through ``render_spa`` like
every other screen: one set of components, one build, one look. The cost is
real and is accepted here rather than hidden — both pages now need the bundle
that ``python tasks.py frontend`` builds, the fellow's page included, and
neither works with JavaScript switched off. What a fellow can still do without
it is Slack: ``/me``, ``/reminders``, ``/badges``.

The fellow's page is not a staff screen and is not framed as one. It carries
no console nav, and ``render_spa`` hands it the screen's own data only — the
allowlist and the rest of the staff configuration never reach a browser
holding nothing but a signed link.

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
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ..assignments import list_assignments, record_score, submissions_for_assignment, submissions_for_fellow
from ..config import get_settings
from ..db import connection, fetch_all, fetch_one
from ..engagement import cohort_attendance, cohort_engagement, fellow_engagement, most_active
from ..errors import CufaError
from ..funnel import cohort_summary, fellow_funnel, journey, STAGE_LABELS
from ..interventions import clear_reached_out, for_fellow as interventions_for, mark_reached_out, open_requests, resolve as resolve_intervention
from ..slack.badges import RANK_KEYS, badges_for, leaderboard
from ..slack.dashboard_links import read_token
from ..slack.identity import open_alerts
from ..slack.identity import list_aliases
from ..slack.preferences import DEFAULT_OFFSETS, get_preferences, set_all_reminders, set_gamification, set_reminder
from ..slack.sync import last_data_received
from ..zoom import speaking_share


def _cohorts(conn: Any) -> list[dict[str, Any]]:
    """Every cohort, with its label — what the cohort picker is built from."""
    return fetch_all(conn, "select cohort_id, label from cohort order by cohort_id")


def _pick_cohort(conn: Any, requested: str | None) -> str | None:
    ids = [c["cohort_id"] for c in _cohorts(conn)]
    if requested and requested in ids:
        return requested
    settings = get_settings()
    if settings.slack_cohort and settings.slack_cohort in ids:
        return settings.slack_cohort
    return ids[0] if ids else None


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
    funnel = fellow_funnel(conn, fellow_id)
    return {
        "fellow": fellow,
        "now": now,
        "slack": slack,
        "preferences": prefs,
        "sessions": sessions,
        "engagement": engagement.to_dict() if engagement else None,
        "badges": badges_for(conn, fellow_id),
        "assignments": submissions_for_fellow(conn, fellow_id),
        "funnel": funnel,
        # The stages in order, so the screen renders a journey without knowing
        # which column holds which stage.
        "journey": journey(funnel) if funnel else [],
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


# What a fellow is shown of their own engagement record: their counts, and
# nothing that was computed to rank them against anybody. The attention index,
# the flags behind it, their share of the cohort mean and whether a staffer has
# reached out are triage for staff (ADR-035) — and on a React screen the whole
# record would sit in the page source whether or not the screen drew it.
FELLOW_ENGAGEMENT_FIELDS = (
    "attended",
    "sessions_held",
    "needs_review",
    "forms_submitted",
    "forms_expected",
    "form_completeness",
    "messages",
    "messages_7d",
)


def fellow_facing(context: dict[str, Any]) -> dict[str, Any]:
    """The fellow's own context, with the staff-only numbers taken out."""
    engagement = context.get("engagement")
    if not engagement:
        return context
    return {
        **context,
        "engagement": {k: engagement[k] for k in FELLOW_ENGAGEMENT_FIELDS},
    }


EXPIRED_LINK = (
    "Dashboard links are good for 7 days and then stop working, so that a link "
    "forwarded or left open on a shared computer does not keep showing your "
    "record. Ask the bot for a new one in Slack with /dashboard."
)


def register(app: FastAPI, render: Callable[..., Response], require_user: Callable[..., Any]) -> None:
    """Mount the routes. Called once from ``console.app``.

    ``render`` is ``console.app.render_spa``, passed in rather than imported to
    keep the import one-way: this module is imported by ``console.app``.
    """

    def back_to_dashboard(cohort: str, notice: str) -> RedirectResponse:
        """Where every action on this page ends: the page it started on, with
        one line saying what happened. A 303 rather than a re-render, so a
        refresh cannot repeat the write."""
        query = urlencode({"cohort": cohort, "notice": notice})
        return RedirectResponse(f"/dashboard?{query}", status_code=303)

    @app.get("/dashboard", response_class=HTMLResponse)
    def staff_dashboard(
        request: Request,
        cohort: str | None = None,
        notice: str | None = None,
        user: Any = Depends(require_user),
    ) -> Response:
        with connection() as conn:
            cohort_id = _pick_cohort(conn, cohort)
            if cohort_id is None:
                return render(
                    request,
                    "message",
                    title="Staff dashboard",
                    heading="There is no cohort yet",
                    body=(
                        "Every number on this page is counted per cohort, and no "
                        "cohort exists. Load a roster first, from the command line:"
                    ),
                    code="cufa load-roster --csv <path> --cohort <id>",
                    link="/roster",
                    link_label="Roster",
                )
            context = staff_context(conn, cohort_id)
        return render(request, "dashboard", title="Staff dashboard", notice=notice, **context)

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
            row = fetch_one(conn, "select full_name from fellow where fellow_id = %s", (fellow_id,))
            who = (row or {}).get("full_name") or fellow_id
            if action == "clear":
                clear_reached_out(conn, fellow_id, by_email=user.email)
                notice = f"Cleared the reached-out flag on {who}."
            else:
                mark_reached_out(conn, fellow_id, by_email=user.email, note=note.strip() or None, source="console")
                closed = 0
                for r in open_requests(conn):
                    if r["fellow_id"] == fellow_id:
                        resolve_intervention(conn, str(r["intervention_id"]), by_email=user.email)
                        closed += 1
                notice = f"Recorded that {who} has been reached out to."
                if closed:
                    notice += f" {closed} check-in request(s) closed."
        return back_to_dashboard(cohort, notice)

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
        def refused(body: str) -> Response:
            # 400 with the reason in full, and the page to go back to. Nothing
            # was written, which is the part the person needs to be sure of.
            return render(
                request,
                "message",
                status_code=400,
                title="Score not saved",
                heading="That score was not saved",
                body=body,
                link=f"/dashboard?cohort={cohort}",
                link_label="Back to the dashboard",
            )

        try:
            value = Decimal(score.strip())
        except InvalidOperation:
            return refused(f"{score!r} is not a number. Scores are entered as digits, for example 88.5.")
        with connection() as conn:
            try:
                record_score(conn, assignment_id, fellow_id, score=value, by=user.email, note=note.strip() or None)
            except CufaError as exc:
                return refused(str(exc))
        return back_to_dashboard(cohort, f"Score {value} saved.")

    @app.get("/dashboard/fellow/{fellow_id}", response_class=HTMLResponse)
    def staff_fellow_detail(fellow_id: str, request: Request, user: Any = Depends(require_user)) -> Response:
        with connection() as conn:
            context = fellow_detail_context(conn, fellow_id)
        if context is None:
            return render(
                request,
                "message",
                status_code=404,
                title="No such fellow",
                heading="No fellow has that id",
                body=f"Nothing on the roster is {fellow_id}. Check the id on the dashboard.",
                link="/dashboard",
                link_label="Back to the dashboard",
            )
        return render(
            request,
            "me",
            title=context["fellow"]["full_name"],
            staff_view=True,
            token=None,
            **context,
        )

    @app.post("/me/{token}/prefs")
    def fellow_prefs(
        request: Request,
        token: str,
        kind: str = Form(...),
        offset: str = Form(""),
        enabled: str = Form(...),
    ) -> Response:
        def refused(status: int, heading: str, body: str) -> Response:
            return render(
                request,
                "message",
                status_code=status,
                title="Preferences",
                frame="fellow",
                heading=heading,
                body=body,
            )

        fellow_id = read_token(get_settings(), token)
        if fellow_id is None:
            return refused(403, "This link has expired", EXPIRED_LINK)
        on = enabled == "on"
        with connection() as conn:
            slack = fetch_one(
                conn,
                "select slack_user_id from v_slack_user_resolved where fellow_id = %s and not is_bot and not deleted order by last_seen_at desc limit 1",
                (fellow_id,),
            )
            if slack is None:
                return refused(
                    400,
                    "There is no Slack account to set this on",
                    "These preferences live on your Slack account. Join the workspace, "
                    "then open this page again.",
                )
            uid = slack["slack_user_id"]
            try:
                if kind == "gamification":
                    set_gamification(conn, uid, on)
                elif kind in ("session", "assignment") and offset:
                    set_reminder(conn, uid, kind=kind, offset=int(offset), enabled=on)
                elif kind in ("session", "assignment", "all"):
                    set_all_reminders(conn, uid, kind=kind, enabled=on)
                else:
                    return refused(400, "That is not a setting", f"No preference is called {kind!r}.")
            except (CufaError, ValueError) as exc:
                return refused(400, "That setting was not changed", str(exc))
        return RedirectResponse(f"/me/{token}", status_code=303)

    @app.get("/me/{token}", response_class=HTMLResponse)
    def fellow_dashboard(token: str, request: Request) -> Response:
        fellow_id = read_token(get_settings(), token)
        if fellow_id is None:
            return render(
                request,
                "message",
                status_code=403,
                title="Link expired",
                frame="fellow",
                heading="This link has expired",
                body=EXPIRED_LINK,
            )
        with connection() as conn:
            context = fellow_context(conn, fellow_id)
        if context is None:
            return render(
                request,
                "message",
                status_code=404,
                title="Nothing to show",
                frame="fellow",
                heading="There is no record to show",
                body=(
                    "The link is valid, but the roster entry it names is gone. Ask in "
                    "Slack and somebody will sort it out."
                ),
            )
        return render(
            request,
            "me",
            title="My dashboard",
            frame="fellow",
            staff_view=False,
            token=token,
            offsets=list(DEFAULT_OFFSETS),
            **fellow_facing(context),
        )

    @app.get("/me/{token}/export.csv")
    def fellow_export(token: str) -> Response:
        fellow_id = read_token(get_settings(), token)
        if fellow_id is None:
            return Response("expired", status_code=403)
        with connection() as conn:
            body = fellow_export_csv(conn, fellow_id)
        return Response(body, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="my-fellowship-{fellow_id}.csv"'})


__all__ = [
    "fellow_context",
    "fellow_detail_context",
    "fellow_export_csv",
    "fellow_facing",
    "register",
    "staff_context",
    "staff_export_csv",
]
