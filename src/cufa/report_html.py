"""The self-contained HTML report — the deliverable the contract actually names.

"An evergreen, auto-updating report … user-friendly and easily accessible to
all members of the CU Team." Everything before this module needs a terminal.
This produces one file that opens from disk, attaches to an email, and shows
the three participation signals side by side: attendance (Part A), the
end-of-session check-in (Part B), and Slack.

Three rules that shape it:

* **No addresses, anywhere.** The file goes to the whole team. Fellows appear
  by roster name; an address that matched nobody appears as a count. A test
  asserts the output contains no ``@``.
* **``help_request`` is never read.** The table takes no part in any number
  here (design invariant 1). Every data function in this module is listed in
  ``report.EXPORT_PATHS`` and exercised by the safeguarding suite, and the SQL
  each one executes is inspected at runtime for the table name.
* **No combined score.** The three signals are shown separately. Weighting
  them into one number is a decision the Director of Programs owns and has not
  taken, so the report does not take it for her.

Charts are inline SVG built here — no library, no CDN, nothing fetched. The
forms follow the data-viz method: stat tiles for the headline numbers, an
ordinal one-hue ramp for attendance states (more blue, more present), a
median-plus-IQR band for confidence (never a mean of ordinal data), and two
lines for Slack. Every chart has a table twin.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Any

import psycopg

from . import __version__
from .confidence import INTERPRETATION
from .db import fetch_all, fetch_one
from .report import CohortReport, cohort_report

# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

import re

#: "Session 3 — Reading a budget" already says W3 in the bar label; drop the echo.
_SESSION_PREFIX = re.compile(r"^\s*session\s+\d+\s*[—–-]\s*", re.IGNORECASE)

#: Attendance states, most present first. The order IS the ordinal ramp.
STATES = ("attended", "needs_review", "not_attended", "undecided", "none")
STATE_LABEL = {
    "attended": "Attended",
    "needs_review": "Needs review",
    "not_attended": "Not attended",
    "undecided": "Check-in, no decision yet",
    "none": "No check-in",
}
_RANK = {"attended": 4, "needs_review": 3, "not_attended": 2, "undecided": 1}


def fellow_grid(conn: psycopg.Connection, cohort_id: str) -> dict[str, Any]:
    """Every fellow × every session, with the current attendance state.

    A fellow with no check-in for a session is a row with state ``none`` —
    kept, because the empty cell is the one a programs director looks for.
    """
    sessions = fetch_all(
        conn,
        """
        select s.session_id, s.title, s.week_index, s.scheduled_at_utc,
               (s.scheduled_at_utc <= now()
                or exists (select 1 from checkin c where c.session_id = s.session_id)) as held
          from "session" s
         where s.cohort_id = %s
         order by s.scheduled_at_utc
        """,
        (cohort_id,),
    )
    fellows = fetch_all(
        conn,
        """
        select fellow_id, full_name, status
          from fellow
         where cohort_id = %s
         order by full_name
        """,
        (cohort_id,),
    )
    cells = fetch_all(
        conn,
        """
        select v.fellow_id, v.session_id,
               max(case v.status
                     when 'attended'     then 4
                     when 'needs_review' then 3
                     when 'not_attended' then 2
                     else 1 end)                     as rank
          from v_checkin_resolved v
         where v.cohort_id = %s
           and v.fellow_id is not null
           and v.session_id is not null
         group by v.fellow_id, v.session_id
        """,
        (cohort_id,),
    )
    part_b = fetch_all(
        conn,
        """
        select fellow_id, session_id, count(*) as n
          from v_checkin_b_resolved
         where cohort_id = %s and fellow_id is not null and session_id is not null
         group by fellow_id, session_id
        """,
        (cohort_id,),
    )
    slack = fetch_all(
        conn,
        """
        select f.fellow_id,
               count(*) filter (where e.event_type = 'message')        as messages,
               count(*) filter (where e.event_type = 'reaction_added') as reactions,
               count(distinct (e.event_time_utc at time zone 'UTC')::date)
                   filter (where e.event_type in ('message', 'reaction_added')) as active_days
          from slack_event e
          join slack_workspace w on w.team_id = e.team_id
          join fellow f on f.cohort_id = w.cohort_id
                       and lower(f.primary_email) = lower(e.user_email)
         where w.cohort_id = %s
         group by f.fellow_id
        """,
        (cohort_id,),
    )

    by_rank = {4: "attended", 3: "needs_review", 2: "not_attended", 1: "undecided"}
    state = {(c["fellow_id"], str(c["session_id"])): by_rank[int(c["rank"])] for c in cells}
    b_count = {(r["fellow_id"], str(r["session_id"])): int(r["n"]) for r in part_b}
    slack_by = {r["fellow_id"]: r for r in slack}

    rows = []
    for f in fellows:
        states = [state.get((f["fellow_id"], str(s["session_id"])), "none") for s in sessions]
        b = [b_count.get((f["fellow_id"], str(s["session_id"])), 0) for s in sessions]
        sk = slack_by.get(f["fellow_id"]) or {}
        rows.append(
            {
                "fellow_id": f["fellow_id"],
                "full_name": f["full_name"],
                "status": f["status"],
                "states": states,
                "part_b": b,
                "attended": sum(1 for x in states if x == "attended"),
                "needs_review": sum(1 for x in states if x == "needs_review"),
                "part_b_total": sum(b),
                "slack_messages": int(sk.get("messages") or 0),
                "slack_reactions": int(sk.get("reactions") or 0),
                "slack_active_days": int(sk.get("active_days") or 0),
            }
        )
    return {"sessions": sessions, "fellows": rows}


def slack_summary(conn: psycopg.Connection, cohort_id: str) -> dict[str, Any]:
    """Cohort-level Slack activity by week, plus who is active lately."""
    weekly = fetch_all(
        conn,
        """
        select date_trunc('week', e.event_time_utc)::date                  as week_start,
               count(*) filter (where e.event_type = 'message')           as messages,
               count(*) filter (where e.event_type = 'reaction_added')    as reactions,
               count(distinct e.slack_user_id)
                   filter (where e.event_type in ('message','reaction_added')) as active_people
          from slack_event e
          join slack_workspace w on w.team_id = e.team_id
         where w.cohort_id = %s
         group by 1
         order by 1
        """,
        (cohort_id,),
    )
    totals = fetch_one(
        conn,
        """
        select count(*) filter (where e.event_type = 'message')        as messages,
               count(*) filter (where e.event_type = 'reaction_added') as reactions,
               count(distinct f.fellow_id)                             as fellows_ever_active,
               count(distinct f.fellow_id)
                   filter (where e.event_time_utc >= now() - interval '7 days') as fellows_active_7d,
               count(distinct e.slack_user_id) filter (where f.fellow_id is null) as people_not_on_roster,
               max(e.event_time_utc)                                   as last_event,
               max(e.received_at)                                      as last_received
          from slack_event e
          join slack_workspace w on w.team_id = e.team_id
          left join fellow f on f.cohort_id = w.cohort_id
                            and lower(f.primary_email) = lower(e.user_email)
         where w.cohort_id = %s
           and e.event_type in ('message', 'reaction_added')
        """,
        (cohort_id,),
    ) or {}
    workspace = fetch_one(
        conn,
        "select team_name, connected_at, last_seen_at from slack_workspace where cohort_id = %s order by connected_at desc limit 1",
        (cohort_id,),
    )
    return {"weekly": weekly, "totals": totals, "workspace": workspace}


def provenance(conn: psycopg.Connection, cohort_id: str) -> dict[str, Any]:
    """When each source last delivered, and whether that run finished cleanly.

    This is what makes "evergreen" checkable: a report generated today from
    data that stopped arriving in October should say so on its face.
    """
    runs = fetch_all(
        conn,
        """
        select distinct on (source)
               source, status, started_at, finished_at, rows_read, rows_written, rows_skipped
          from load_run
         where cohort_id = %s or cohort_id is null
         order by source, started_at desc
        """,
        (cohort_id,),
    )
    latest = fetch_one(
        conn,
        """
        select (select max(submitted_at_utc) from v_checkin_resolved   where cohort_id = %s) as last_part_a,
               (select max(submitted_at_utc) from v_checkin_b_resolved where cohort_id = %s) as last_part_b,
               (select max(e.event_time_utc) from slack_event e
                  join slack_workspace w on w.team_id = e.team_id where w.cohort_id = %s) as last_slack
        """,
        (cohort_id, cohort_id, cohort_id),
    ) or {}
    review = fetch_one(
        conn,
        """
        select (select count(*) from v_checkin_resolved where cohort_id = %s and status = 'needs_review') as needs_review,
               (select count(*) from identity_unresolved where cohort_id = %s and resolved_at is null)   as unresolved_identities,
               (select count(*) from v_shoutout_review where cohort_id = %s)                              as unresolved_shoutouts
        """,
        (cohort_id, cohort_id, cohort_id),
    ) or {}
    return {"runs": runs, "latest": latest, "review": review}


# --------------------------------------------------------------------------
# rendering helpers
# --------------------------------------------------------------------------

def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _dt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return _e(value)


def _date(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%b %d")
    return _e(str(value)[:10])


def _pct(num: int | float | None, den: int | float | None) -> str:
    if not den:
        return "—"
    return f"{100 * (num or 0) / den:.0f}%"


def _n(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def _tile(label: str, value: str, note: str = "") -> str:
    return (
        f'<div class="tile"><div class="tile-value">{_e(value)}</div>'
        f'<div class="tile-label">{_e(label)}</div>'
        + (f'<div class="tile-note">{_e(note)}</div>' if note else "")
        + "</div>"
    )


def _rounded_right(x: float, y: float, w: float, h: float, r: float) -> str:
    """A rect whose right end is rounded: 4px data-end, square at the baseline."""
    r = min(r, w / 2, h / 2)
    return (
        f"M{x:.1f},{y:.1f} H{x + w - r:.1f} "
        f"A{r:.1f},{r:.1f} 0 0 1 {x + w:.1f},{y + r:.1f} "
        f"V{y + h - r:.1f} A{r:.1f},{r:.1f} 0 0 1 {x + w - r:.1f},{y + h:.1f} "
        f"H{x:.1f} Z"
    )


def _stacked_bars(rows: list[dict[str, Any]], *, keys: tuple[str, ...], classes: dict[str, str],
                  label_key: str, total_key: str | None = None, label_width: int = 230) -> str:
    """Horizontal stacked bars: ≤24px thick, 2px surface gap, rounded data-end.

    The attended count is direct-labelled at the bar end; the rest ride the
    tooltip and the table twin. One label per bar, never one per segment.
    """
    if not rows:
        return '<p class="empty">Nothing to draw yet.</p>'
    bar_h, gap_y, gap_x = 18, 10, 2
    width, right_pad = 760, 60
    plot_w = width - label_width - right_pad
    maximum = max((sum(int(r.get(k) or 0) for k in keys) for r in rows), default=0) or 1
    height = len(rows) * (bar_h + gap_y) + 8
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="Attendance by session">']
    for i, row in enumerate(rows):
        y = i * (bar_h + gap_y) + 4
        out.append(f'<text class="axis-label" x="{label_width - 10}" y="{y + bar_h - 5}" text-anchor="end">{_e(str(row[label_key])[:34])}</text>')
        x = float(label_width)
        segs = [(k, int(row.get(k) or 0)) for k in keys if int(row.get(k) or 0) > 0]
        for j, (k, v) in enumerate(segs):
            w = plot_w * v / maximum
            last = j == len(segs) - 1
            inner = max(0.0, w - (0 if last else gap_x))
            title = f"{STATE_LABEL.get(k, k)}: {v}"
            if last:
                out.append(f'<path class="{classes[k]}" d="{_rounded_right(x, y, inner, bar_h, 4)}"><title>{_e(title)}</title></path>')
            else:
                out.append(f'<rect class="{classes[k]}" x="{x:.1f}" y="{y}" width="{inner:.1f}" height="{bar_h}"><title>{_e(title)}</title></rect>')
            x += w
        total = int(row.get(total_key) or 0) if total_key else int(row.get(keys[0]) or 0)
        out.append(f'<text class="value-label" x="{x + 8:.1f}" y="{y + bar_h - 5}">{total}</text>')
    out.append("</svg>")
    return "".join(out)


def _band_line(points: list[dict[str, Any]]) -> str:
    """Median line with a Q1–Q3 wash, on a fixed 1–7 axis. One series: no legend."""
    pts = [p for p in points if p.get("median") is not None]
    if not pts:
        return '<p class="empty">No confidence responses yet.</p>'
    width, height = 760, 240
    left, right, top, bottom = 56, 96, 16, 40
    plot_w, plot_h = width - left - right, height - top - bottom
    n = len(pts)

    def x_of(i: int) -> float:
        return left + (plot_w * i / (n - 1) if n > 1 else plot_w / 2)

    def y_of(v: float) -> float:
        return top + plot_h * (7 - v) / 6

    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="Confidence by week, median and interquartile range">']
    for tick in range(1, 8):
        y = y_of(tick)
        out.append(f'<line class="grid" x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}"/>')
        out.append(f'<text class="axis-label" x="{left - 10}" y="{y + 4:.1f}" text-anchor="end">{tick}</text>')
    band = " ".join(f"{x_of(i):.1f},{y_of(p['q3']):.1f}" for i, p in enumerate(pts))
    band += " " + " ".join(f"{x_of(i):.1f},{y_of(p['q1']):.1f}" for i, p in reversed(list(enumerate(pts))))
    out.append(f'<polygon class="area s1" points="{band}"/>')
    line = " ".join(f"{x_of(i):.1f},{y_of(p['median']):.1f}" for i, p in enumerate(pts))
    out.append(f'<polyline class="line s1" points="{line}"/>')
    for i, p in enumerate(pts):
        x, y = x_of(i), y_of(p["median"])
        label = f"Week {p['week_index']}" if p.get("week_index") is not None else (p.get("session_title") or "")
        tip = f"{label}: median {p['median']}, IQR {p['q1']}–{p['q3']}, {p['responses']} responses"
        out.append(f'<g class="pt" data-i="{i}"><circle class="hit" cx="{x:.1f}" cy="{y:.1f}" r="14"><title>{_e(tip)}</title></circle>'
                   f'<circle class="dot s1" cx="{x:.1f}" cy="{y:.1f}" r="4"/></g>')
        out.append(f'<text class="axis-label" x="{x:.1f}" y="{height - 12}" text-anchor="middle">{_e(("W" + str(p["week_index"])) if p.get("week_index") is not None else str(i + 1))}</text>')
    lx, ly = x_of(n - 1), y_of(pts[-1]["median"])
    out.append(f'<text class="value-label" x="{lx + 10:.1f}" y="{ly + 4:.1f}">median {pts[-1]["median"]}</text>')
    out.append("</svg>")
    return "".join(out)


def _two_lines(rows: list[dict[str, Any]], *, x_key: str, series: tuple[tuple[str, str, str], ...]) -> str:
    """Two series over time. Legend rendered by the caller; end-labels here."""
    if not rows:
        return '<p class="empty">No Slack activity recorded yet.</p>'
    width, height = 760, 220
    left, right, top, bottom = 48, 90, 16, 36
    plot_w, plot_h = width - left - right, height - top - bottom
    n = len(rows)
    maximum = max((int(r.get(k) or 0) for r in rows for k, _, _ in series), default=0) or 1
    step = _nice_step(maximum)
    top_tick = ((maximum + step - 1) // step) * step or step

    def x_of(i: int) -> float:
        return left + (plot_w * i / (n - 1) if n > 1 else plot_w / 2)

    def y_of(v: float) -> float:
        return top + plot_h * (1 - v / top_tick)

    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="Slack activity by week">']
    for t in range(0, top_tick + 1, step):
        y = y_of(t)
        out.append(f'<line class="grid" x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}"/>')
        out.append(f'<text class="axis-label" x="{left - 8}" y="{y + 4:.1f}" text-anchor="end">{t:,}</text>')
    for k, cls, label in series:
        line = " ".join(f"{x_of(i):.1f},{y_of(int(r.get(k) or 0)):.1f}" for i, r in enumerate(rows))
        out.append(f'<polyline class="line {cls}" points="{line}"/>')
        for i, r in enumerate(rows):
            v = int(r.get(k) or 0)
            out.append(f'<circle class="dot {cls}" cx="{x_of(i):.1f}" cy="{y_of(v):.1f}" r="4"><title>{_e(f"{label}, week of {_date(r[x_key])}: {v}")}</title></circle>')
        lv = int(rows[-1].get(k) or 0)
        out.append(f'<text class="value-label" x="{x_of(n - 1) + 10:.1f}" y="{y_of(lv) + 4:.1f}">{_e(label)} {lv}</text>')
    shown = set()
    for i, r in enumerate(rows):
        if n <= 8 or i % max(1, n // 8) == 0 or i == n - 1:
            lbl = _date(r[x_key])
            if lbl not in shown:
                out.append(f'<text class="axis-label" x="{x_of(i):.1f}" y="{height - 10}" text-anchor="middle">{lbl}</text>')
                shown.add(lbl)
    out.append("</svg>")
    return "".join(out)


def _nice_step(maximum: int) -> int:
    for s in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000):
        if maximum / s <= 6:
            return s
    return 10000


def _table(headers: list[str], rows: list[list[Any]], *, cls: str = "") -> str:
    out = [f'<table class="{cls}"><thead><tr>' + "".join(f"<th>{_e(h)}</th>" for h in headers) + "</tr></thead><tbody>"]
    for row in rows:
        out.append("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in row) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def _legend(items: list[tuple[str, str]]) -> str:
    return '<div class="legend">' + "".join(f'<span class="key"><i class="sw {cls}"></i>{_e(label)}</span>' for cls, label in items) + "</div>"


# --------------------------------------------------------------------------
# the page
# --------------------------------------------------------------------------

def render_report_html(conn: psycopg.Connection, cohort_id: str) -> str:
    """The whole report for one cohort, as one self-contained HTML document."""
    report: CohortReport = cohort_report(conn, cohort_id)
    grid = fellow_grid(conn, cohort_id)
    slack = slack_summary(conn, cohort_id)
    prov = provenance(conn, cohort_id)
    label = (fetch_one(conn, "select label from cohort where cohort_id = %s", (cohort_id,)) or {}).get("label") or cohort_id
    generated = datetime.now(timezone.utc)

    sessions = grid["sessions"]
    fellows = grid["fellows"]
    held = [s for s in sessions if s["held"]]
    n_fellows = sum(1 for f in fellows if (f.get("status") or "active") == "active")
    n_held = len(held)
    denominator = n_fellows * n_held
    attended_cells = sum(f["attended"] for f in fellows)
    b_cells = sum(1 for f in fellows for c in f["part_b"] if c)
    st = slack["totals"] or {}
    rv = prov["review"] or {}
    awaiting = int(rv.get("needs_review") or 0) + int(rv.get("unresolved_identities") or 0) + int(rv.get("unresolved_shoutouts") or 0)

    # -- sessions block -----------------------------------------------------
    session_rows = []
    for s in sessions:
        sid = str(s["session_id"])
        idx = [str(x["session_id"]) for x in sessions].index(sid)
        counts = {k: sum(1 for f in fellows if f["states"][idx] == k) for k in STATES}
        short = s["title"] or ""
        if s.get("week_index") is not None:
            short = _SESSION_PREFIX.sub("", short)
        session_rows.append({
            "label": (f"W{s['week_index']} · " if s.get("week_index") is not None else "") + short,
            "title": s["title"], "week": s.get("week_index"), "date": s["scheduled_at_utc"], "held": s["held"],
            **counts,
            "part_b": sum(1 for f in fellows if f["part_b"][idx]),
        })

    bars = _stacked_bars(
        session_rows, keys=("attended", "needs_review", "not_attended", "undecided", "none"),
        classes={"attended": "att-3", "needs_review": "att-2", "not_attended": "att-1", "undecided": "att-u", "none": "att-0"},
        label_key="label", total_key="attended",
    )
    sessions_table = _table(
        ["Week", "Session", "Date", "Attended", "Needs review", "Not attended", "No decision", "No check-in", "Part B responses"],
        [[r["week"] if r["week"] is not None else "—", r["title"], _date(r["date"]), r["attended"], r["needs_review"],
          r["not_attended"], r["undecided"], r["none"], r["part_b"]] for r in session_rows],
    )

    # -- fellow grid ---------------------------------------------------------
    head = "".join(
        f'<th class="cell-h" title="{_e(s["title"])}">{_e("W" + str(s["week_index"]) if s.get("week_index") is not None else _date(s["scheduled_at_utc"]))}</th>'
        for s in sessions
    )
    def _cell(f: dict[str, Any], s: dict[str, Any], st_: str, b: int) -> str:
        tip = f"{f['full_name']} · {s['title']}: {STATE_LABEL[st_]}"
        if b:
            tip += f" · Part B ×{b}"
        badge = f"<b>{b}</b>" if b else ""
        return f'<td class="cell s-{st_}" title="{_e(tip)}"><i></i>{badge}</td>'

    body = []
    for f in fellows:
        cells = "".join(_cell(f, s, st_, b) for st_, b, s in zip(f["states"], f["part_b"], sessions))
        status = f.get("status") or "active"
        status_note = "" if status == "active" else f" <small>({_e(status)})</small>"
        name = _e(f["full_name"])
        body.append(
            f'<tr data-name="{name}" data-attended="{f["attended"]}" data-review="{f["needs_review"]}" '
            f'data-partb="{f["part_b_total"]}" data-msgs="{f["slack_messages"]}" data-days="{f["slack_active_days"]}">'
            f'<th class="name">{name}{status_note}</th>'
            f"{cells}"
            f'<td class="num">{f["attended"]}<span class="of">/{n_held}</span></td>'
            f'<td class="num">{f["needs_review"] or ""}</td>'
            f'<td class="num">{f["part_b_total"] or ""}</td>'
            f'<td class="num">{f["slack_messages"] or ""}</td>'
            f'<td class="num">{f["slack_reactions"] or ""}</td>'
            f'<td class="num">{f["slack_active_days"] or ""}</td></tr>'
        )
    grid_html = (
        '<div class="scroll"><table class="grid" id="grid"><thead><tr>'
        '<th class="name sortable" data-key="name">Fellow</th>'
        f"{head}"
        '<th class="num sortable" data-key="attended" title="Sessions attended, of those held">Att.</th>'
        '<th class="num sortable" data-key="review" title="Check-ins awaiting a decision">Rev.</th>'
        '<th class="num sortable" data-key="partb" title="Part B responses">B</th>'
        '<th class="num sortable" data-key="msgs" title="Slack messages">Msgs</th>'
        '<th class="num" title="Slack reactions given">Reacts</th>'
        '<th class="num sortable" data-key="days" title="Days with any Slack activity">Days</th>'
        "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>"
        if fellows else '<p class="empty">No fellows on the roster for this cohort.</p>'
    )

    # -- confidence ------------------------------------------------------------
    conf = report.confidence or []
    conf_chart = _band_line(conf)
    conf_table = _table(
        ["Week", "Session", "Responses", "Median", "Q1", "Q3", "Lowest", "Highest"],
        [[r.get("week_index") if r.get("week_index") is not None else "—", r.get("session_title"), r.get("responses"),
          r.get("median"), r.get("q1"), r.get("q3"), r.get("lowest"), r.get("highest")] for r in conf],
    )

    # -- slack --------------------------------------------------------------------
    weekly = slack["weekly"] or []
    slack_chart = _two_lines(weekly, x_key="week_start", series=(("messages", "s1", "Messages"), ("reactions", "s2", "Reactions")))
    slack_table = _table(
        ["Week of", "Messages", "Reactions", "People active"],
        [[_date(r["week_start"]), r["messages"], r["reactions"], r["active_people"]] for r in weekly],
    )
    ws = slack.get("workspace") or {}

    # -- provenance -----------------------------------------------------------------
    latest = prov["latest"] or {}
    run_rows = [[r["source"], r["status"], _dt(r["started_at"]), _dt(r["finished_at"]), r["rows_read"], r["rows_written"], r["rows_skipped"]] for r in prov["runs"]]
    prov_table = _table(["Source", "Last run", "Started", "Finished", "Read", "Written", "Skipped"], run_rows) if run_rows else '<p class="empty">No loads recorded yet.</p>'

    t = report.totals or {}
    pb = (report.part_b or {}).get("totals") or {}

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(label)} — participation report</title>
<style>
:root {{
  color-scheme: light;
  --surface:#fcfcfb; --plane:#f9f9f7; --ink:#0b0b0b; --ink-2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --axis:#c3c2b7; --ring:rgba(11,11,11,.10);
  --s1:#2a78d6; --s2:#eb6834;
  --att-3:#1c5cab; --att-2:#3987e5; --att-1:#86b6ef; --att-u:#c3c2b7; --att-0:#e1e0d9;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
    --surface:#1a1a19; --plane:#0d0d0d; --ink:#ffffff; --ink-2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --axis:#383835; --ring:rgba(255,255,255,.10);
    --s1:#3987e5; --s2:#d95926;
    --att-3:#6da7ec; --att-2:#2a78d6; --att-1:#184f95; --att-u:#383835; --att-0:#2c2c2a;
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --surface:#1a1a19; --plane:#0d0d0d; --ink:#ffffff; --ink-2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --axis:#383835; --ring:rgba(255,255,255,.10);
  --s1:#3987e5; --s2:#d95926;
  --att-3:#6da7ec; --att-2:#2a78d6; --att-1:#184f95; --att-u:#383835; --att-0:#2c2c2a;
}}
* {{ box-sizing:border-box }}
body {{ margin:0; background:var(--plane); color:var(--ink); font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif }}
main {{ max-width:72rem; margin:0 auto; padding:2rem 1.25rem 4rem }}
h1 {{ font-size:1.5rem; font-weight:600; margin:0 0 .25rem }}
h2 {{ font-size:1.05rem; font-weight:600; margin:2.25rem 0 .75rem }}
.sub {{ color:var(--ink-2); margin:0 }}
.fresh {{ display:flex; flex-wrap:wrap; gap:.4rem 1.25rem; color:var(--muted); font-size:.8rem; margin:.75rem 0 0 }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(9.5rem,1fr)); gap:.75rem; margin:1.5rem 0 0 }}
.tile {{ background:var(--surface); border:1px solid var(--ring); border-radius:.6rem; padding:.85rem 1rem }}
.tile-value {{ font-size:1.75rem; font-weight:600; line-height:1.1 }}
.tile-label {{ color:var(--ink-2); font-size:.85rem; margin-top:.2rem }}
.tile-note {{ color:var(--muted); font-size:.75rem; margin-top:.15rem }}
figure {{ background:var(--surface); border:1px solid var(--ring); border-radius:.6rem; padding:1rem 1.1rem; margin:0 }}
figcaption {{ color:var(--ink-2); font-size:.85rem; margin:0 0 .75rem }}
.chart {{ width:100%; height:auto; display:block }}
.chart .grid {{ stroke:var(--grid); stroke-width:1 }}
.chart .axis-label {{ fill:var(--muted); font-size:11px }}
.chart .value-label {{ fill:var(--ink-2); font-size:11px; font-variant-numeric:tabular-nums }}
.chart .line {{ fill:none; stroke-width:2; stroke-linejoin:round; stroke-linecap:round }}
.chart .line.s1 {{ stroke:var(--s1) }} .chart .line.s2 {{ stroke:var(--s2) }}
.chart .dot {{ stroke:var(--surface); stroke-width:2 }}
.chart .dot.s1 {{ fill:var(--s1) }} .chart .dot.s2 {{ fill:var(--s2) }}
.chart .area.s1 {{ fill:var(--s1); opacity:.10 }}
.chart .hit {{ fill:transparent }}
.chart .att-3 {{ fill:var(--att-3) }} .chart .att-2 {{ fill:var(--att-2) }} .chart .att-1 {{ fill:var(--att-1) }}
.chart .att-u {{ fill:var(--att-u) }} .chart .att-0 {{ fill:var(--att-0) }}
.legend {{ display:flex; flex-wrap:wrap; gap:.35rem 1rem; color:var(--ink-2); font-size:.8rem; margin:.5rem 0 0 }}
.key {{ display:inline-flex; align-items:center; gap:.4rem }}
.sw {{ display:inline-block; width:12px; height:12px; border-radius:3px }}
.sw.line {{ height:3px; width:16px; border-radius:2px }}
.sw.att-3 {{ background:var(--att-3) }} .sw.att-2 {{ background:var(--att-2) }} .sw.att-1 {{ background:var(--att-1) }}
.sw.att-u {{ background:var(--att-u) }} .sw.att-0 {{ background:var(--att-0); border:1px solid var(--axis) }}
.sw.s1 {{ background:var(--s1) }} .sw.s2 {{ background:var(--s2) }}
.note {{ color:var(--ink-2); font-size:.85rem; margin:.75rem 0 0 }}
.empty {{ color:var(--muted); font-style:italic; margin:.5rem 0 }}
details {{ margin-top:.75rem }} summary {{ cursor:pointer; color:var(--ink-2); font-size:.8rem }}
table {{ border-collapse:collapse; width:100%; font-size:.85rem; font-variant-numeric:tabular-nums }}
th,td {{ text-align:left; padding:.35rem .5rem; border-top:1px solid var(--grid); vertical-align:middle }}
thead th {{ border-top:0; color:var(--muted); font-weight:500; font-size:.75rem; white-space:nowrap }}
.scroll {{ overflow-x:auto }}
.grid th.name {{ position:sticky; left:0; background:var(--surface); min-width:11rem; z-index:1 }}
.grid td.cell {{ padding:.25rem .2rem; text-align:center; width:2rem }}
.grid td.cell i {{ display:inline-block; width:1.35rem; height:1.35rem; border-radius:4px; vertical-align:middle }}
.grid td.cell b {{ position:relative; margin-left:-1.2rem; font-size:.65rem; font-weight:600; color:var(--ink); vertical-align:middle; pointer-events:none }}
.grid td.s-attended i {{ background:var(--att-3) }} .grid td.s-needs_review i {{ background:var(--att-2) }}
.grid td.s-not_attended i {{ background:var(--att-1) }} .grid td.s-undecided i {{ background:var(--att-u) }}
.grid td.s-none i {{ background:var(--att-0) }}
.grid td.s-attended b, .grid td.s-needs_review b {{ color:#fff }}
.grid th.cell-h {{ text-align:center; font-size:.7rem }}
.grid .num {{ text-align:right; white-space:nowrap }} .grid .of {{ color:var(--muted); font-size:.75rem }}
.sortable {{ cursor:pointer }} .sortable:hover {{ color:var(--ink) }}
small {{ color:var(--muted); font-weight:400 }}
footer {{ margin-top:3rem; color:var(--muted); font-size:.8rem; border-top:1px solid var(--grid); padding-top:1rem }}
footer p {{ margin:.35rem 0 }}
.tip {{ position:fixed; pointer-events:none; background:var(--surface); color:var(--ink); border:1px solid var(--ring); border-radius:.4rem; padding:.4rem .6rem; font-size:.8rem; box-shadow:0 2px 8px rgba(0,0,0,.12); display:none; z-index:9 }}
@media print {{ body {{ background:#fff }} .tile,figure {{ break-inside:avoid }} }}
</style>
</head>
<body>
<main>
<h1>{_e(label)}</h1>
<p class="sub">Participation report · generated {_dt(generated)} · cufa {_e(__version__)}</p>
<div class="fresh">
  <span>Last check-in (Part A): <b>{_dt(latest.get("last_part_a"))}</b></span>
  <span>Last end-of-session (Part B): <b>{_dt(latest.get("last_part_b"))}</b></span>
  <span>Last Slack activity: <b>{_dt(latest.get("last_slack"))}</b></span>
</div>

<div class="tiles">
  {_tile("Fellows on roster", _n(n_fellows))}
  {_tile("Sessions held", f"{n_held} of {len(sessions)}", "in the past, or with any check-in")}
  {_tile("Attendance", _pct(attended_cells, denominator), "attended ÷ (fellows × sessions held)")}
  {_tile("End-of-session responses", _pct(b_cells, denominator), "Part B ÷ (fellows × sessions held)")}
  {_tile("Active in Slack, last 7 days", f"{_n(st.get('fellows_active_7d'))} of {_n(n_fellows)}")}
  {_tile("Awaiting a human", _n(awaiting), "decisions, identities, shoutouts")}
</div>

<h2>Attendance by session</h2>
<figure>
  <figcaption>One bar per session. Darker means more present. The number at the end is how many attended.</figcaption>
  {bars}
  {_legend([("att-3", "Attended"), ("att-2", "Needs review"), ("att-1", "Not attended"), ("att-u", "Check-in, no decision yet"), ("att-0", "No check-in")])}
  <details><summary>Table view</summary>{sessions_table}</details>
</figure>

<h2>Every fellow, every session</h2>
<figure>
  <figcaption>The intervention view. A blank cell is a fellow who did not check in for that session. A small number is how many Part B responses they submitted. Click a column heading to sort.</figcaption>
  {grid_html}
  {_legend([("att-3", "Attended"), ("att-2", "Needs review"), ("att-1", "Not attended"), ("att-u", "No decision yet"), ("att-0", "No check-in")])}
  <p class="note">Attendance, the end-of-session check-in and Slack are shown side by side and are not combined into a score. Weighting them is a decision the Director of Programs owns and has not yet taken.</p>
</figure>

<h2>Confidence by week</h2>
<figure>
  <figcaption>Median self-rated confidence, 1–7, with the interquartile band. Median rather than mean: the scale is ordinal.</figcaption>
  {conf_chart}
  <p class="note">{_e(INTERPRETATION)}</p>
  <details><summary>Table view</summary>{conf_table}</details>
</figure>

<h2>Slack activity by week</h2>
<figure>
  <figcaption>{_e(("Workspace: " + str(ws.get("team_name")) + ". ") if ws else "")}Messages sent and reactions given by people on this cohort's roster. Text is not stored; reactions received are not counted.</figcaption>
  {slack_chart}
  {_legend([("s1 line", "Messages"), ("s2 line", "Reactions")])}
  <p class="note">{_n(st.get("fellows_ever_active"))} fellows have posted or reacted at least once; {_n(st.get("people_not_on_roster"))} active people in the workspace are not on the roster.</p>
  <details><summary>Table view</summary>{slack_table}</details>
</figure>

<h2>Awaiting a human</h2>
<figure>
  {_table(["Queue", "Open items", "Where to act"], [
      ["Check-ins needing a decision", int(rv.get("needs_review") or 0), "cufa review --status needs_review, or the console's Review screen"],
      ["Addresses not on the roster", int(rv.get("unresolved_identities") or 0), "cufa review --status unresolved-identity"],
      ["Shoutout names to link", int(rv.get("unresolved_shoutouts") or 0), "cufa shoutouts review"],
  ])}
  <p class="note">Addresses are not shown in this report. It goes to the whole team; the review queues show them to the people doing the reviewing.</p>
</figure>

<h2>Where the numbers come from</h2>
<figure>
  {prov_table}
  <p class="note">A source whose last run is old, or not <b>succeeded</b>, is the first thing to check when a number looks wrong. Part A decisions: {_n(t.get("by_rule"))} by rule, {_n(t.get("by_ai"))} by the AI tier, {_n(t.get("by_human"))} by a person.</p>
</figure>

<footer>
  <p><b>What is counted.</b> Attendance is the current decision on each mid-session check-in: a rule, the AI tier for ambiguous passphrases, or a person, in that order of precedence, with a person's decision never overridden. Part B counts responses that arrived; free text is counted, never graded. Slack counts messages sent and reactions given; message text is not stored.</p>
  <p><b>What is not here.</b> The help checkbox appears nowhere in this report. It is excluded from every count, rate and aggregate, permanently. No email address appears here. Latency between announcement and check-in is recorded but not interpreted: no thresholds, no flags.</p>
  <p>Regenerate with <code>cufa report --cohort {_e(cohort_id)} --html</code>. Every number is computed from the database at that moment; nothing is cached.</p>
</footer>
</main>
<div class="tip" id="tip"></div>
<script>
(function(){{
  var tip=document.getElementById('tip');
  function show(e,text){{tip.textContent=text;tip.style.display='block';tip.style.left=(e.clientX+14)+'px';tip.style.top=(e.clientY+14)+'px';}}
  function hide(){{tip.style.display='none';}}
  document.querySelectorAll('.chart [title], .grid td[title], .chart title').forEach(function(el){{
    var host=el.tagName==='title'?el.parentNode:el; var t=el.tagName==='title'?el.textContent:el.getAttribute('title');
    host.addEventListener('pointermove',function(e){{show(e,t);}}); host.addEventListener('pointerleave',hide);
    host.addEventListener('focus',function(e){{show({{clientX:20,clientY:20}},t);}}); host.addEventListener('blur',hide);
  }});
  var grid=document.getElementById('grid'); if(!grid) return;
  var dir={{}};
  grid.querySelectorAll('th.sortable').forEach(function(th){{
    th.addEventListener('click',function(){{
      var key=th.dataset.key, rows=Array.prototype.slice.call(grid.tBodies[0].rows);
      var asc=(dir[key]=!dir[key]);
      rows.sort(function(a,b){{var x=a.dataset[key],y=b.dataset[key];
        if(key==='name') return asc?x.localeCompare(y):y.localeCompare(x);
        return asc?(+x)-(+y):(+y)-(+x);}});
      rows.forEach(function(r){{grid.tBodies[0].appendChild(r);}});
    }});
  }});
}})();
</script>
</body>
</html>
"""


def write_report_html(conn: psycopg.Connection, cohort_id: str, path: str) -> str:
    """Render and write. Returns the path written."""
    from pathlib import Path

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_report_html(conn, cohort_id), encoding="utf-8")
    return str(out)


__all__ = ["fellow_grid", "provenance", "render_report_html", "slack_summary", "write_report_html"]
