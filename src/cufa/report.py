"""HTML report.

One self-contained file, no external assets, no network. It opens from a
shared drive on any machine and keeps working after the contract ends.

A note on what is deliberately absent: nothing here is styled as an alert.
Where a Fellow has no recorded signal, the report says exactly that, in
neutral type, and points at the undefined term that would be needed to call
it anything stronger. Red badges are a definition of *falling behind* drawn
in CSS, and that decision is not the tool's to make.
"""
from __future__ import annotations

import datetime as dt
import html
from .config import Definitions
from .metrics import Result
from .model import week_start

# Categorical palette for the four participation components. Chosen to stay
# distinguishable in stacked bars and to survive greyscale printing.
COMPONENT_STYLE = {
    "live_lesson_attended": ("#1f6f78", "Live lesson"),
    "assignment_submitted": ("#b4553f", "Assignment"),
    "slack_message":        ("#5b6ab0", "Slack message"),
    "slack_reaction":       ("#c99a2e", "Slack reaction"),
}

CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --bg:#fbfaf8; --surface:#fff; --ink:#1b1f23; --muted:#5f6b76;
  --line:#e3e0da; --accent:#1f6f78; --warn-bg:#fdf6e9; --warn-line:#e2c98a;
}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1060px;margin:0 auto;padding:40px 24px 80px}
header{border-bottom:2px solid var(--ink);padding-bottom:18px;margin-bottom:32px}
h1{font-size:26px;margin:0 0 4px;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:14px}
h2{font-size:17px;margin:40px 0 6px;letter-spacing:-.005em}
h2 .n{color:var(--muted);font-weight:400;margin-right:8px}
.lede{color:var(--muted);font-size:13.5px;margin:0 0 16px;max-width:68ch}
.card{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:18px 20px}
.gap{background:var(--warn-bg);border:1px solid var(--warn-line);border-radius:8px;padding:18px 20px}
.gap h3{margin:0 0 2px;font-size:14.5px}
.gap .term{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;
  background:rgba(0,0,0,.05);padding:1px 6px;border-radius:4px}
.gap p{margin:6px 0 0;font-size:13.5px;color:#4a4034;white-space:pre-line}
.gap .who{margin-top:8px;font-size:12.5px;color:var(--muted)}
.gap+.gap{margin-top:12px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{text-align:left;font-weight:600;font-size:11.5px;text-transform:uppercase;
  letter-spacing:.06em;color:var(--muted);border-bottom:1px solid var(--line);
  padding:0 8px 8px;white-space:nowrap}
td{padding:9px 8px;border-bottom:1px solid var(--line);vertical-align:middle}
tbody tr:last-child td{border-bottom:none}
.num{text-align:right;font-variant-numeric:tabular-nums}
.name{font-weight:600}
.pill{display:inline-block;width:26px;height:26px;border-radius:50%;background:#eceae5;
  color:var(--muted);font-size:11px;font-weight:600;text-align:center;line-height:26px;margin-right:9px}
.chart{display:flex;align-items:flex-end;justify-content:space-between;
  gap:20px;height:150px;padding:12px 0 0}
.col{flex:1 1 0;max-width:118px;display:flex;flex-direction:column;
  justify-content:flex-end;height:100%}
.stack{display:flex;flex-direction:column-reverse;border-radius:3px 3px 0 0;overflow:hidden}
.seg{width:100%}
.xlab{text-align:center;font-size:10.5px;line-height:1.45;color:var(--muted);
  margin-top:7px;padding-top:7px;border-top:1px solid var(--line)}
.xlab b{display:block;color:var(--ink);font-weight:600;font-size:11.5px;
  margin-bottom:2px}
.xlab span{display:block;white-space:nowrap}
.spark{display:flex;gap:3px;align-items:flex-end;height:26px}
.spark i{width:9px;background:var(--accent);border-radius:1px;opacity:.85;display:block}
.spark i.zero{height:2px!important;background:var(--line);opacity:1}
.spark i.pre{height:2px!important;background:transparent;
  border-bottom:2px dotted var(--line);opacity:1}
.scale{font-size:11px;color:var(--muted);text-align:right;
  border-bottom:1px dashed var(--line);padding-bottom:3px}
.share{display:flex;height:7px;border-radius:4px;overflow:hidden;margin:14px 0 8px}
.share div{height:100%}
.legend{display:flex;flex-wrap:wrap;gap:16px;font-size:12.5px;color:var(--muted);margin-top:14px}
.legend span{display:flex;align-items:center;gap:6px}
.sw{width:10px;height:10px;border-radius:2px;display:inline-block}
.quiet{color:var(--muted);font-size:12.5px;font-style:italic}
.note{font-size:12.5px;color:var(--muted);margin-top:12px;padding-top:12px;
  border-top:1px solid var(--line);max-width:72ch}
.ledger{font-size:13px}
.ledger dt{font-weight:600;margin-top:12px}
.ledger dd{margin:2px 0 0;color:var(--muted);white-space:pre-line}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
footer{margin-top:56px;padding-top:16px;border-top:1px solid var(--line);
  font-size:12px;color:var(--muted)}
@media print{body{background:#fff}.wrap{max-width:none}}
"""


def _e(text) -> str:
    return html.escape(str(text))


def _sparkline(result: Result, fellow_id: str, peak: float) -> str:
    """One bar per week. Dotted = not yet enrolled. Flat grey = enrolled, no signal."""
    bars = []
    for week in result.weeks:
        row = result.row(fellow_id, week)
        if row is None or not row.enrolled:
            bars.append('<i class="pre" title="not yet enrolled"></i>')
        elif row.total == 0:
            bars.append(f'<i class="zero" title="{week}: no recorded signal"></i>')
        else:
            h = max(3, round(26 * row.total / peak)) if peak else 3
            bars.append(f'<i style="height:{h}px" title="{week}: {row.total:g} pts"></i>')
    return f'<div class="spark">{"".join(bars)}</div>'


def render(result: Result, defs: Definitions) -> str:
    generated = dt.datetime.now(dt.timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    cohort = _e(defs.meta.get("cohort_label", ""))
    program = _e(defs.meta.get("program", "Fellowship"))

    parts: list[str] = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>{program} — participation report</title>",
        f"<style>{CSS}</style></head><body><div class='wrap'>",
        f"<header><h1>{program} — participation</h1>",
        f"<div class='sub'>Cohort {cohort} &middot; {len(result.fellows)} fellows &middot; "
        f"{len(result.weeks)} weeks &middot; generated {generated}</div></header>",
    ]

    # -- 1. what this report cannot say ------------------------------------
    parts.append("<h2><span class='n'>1</span>What this report does not say</h2>")
    if result.withheld:
        parts.append(
            "<p class='lede'>These measures are withheld because the term they rest on "
            "has not been defined. They are not missing data, and not a limitation of "
            "the tool. Each needs a decision from a named person; once that decision is "
            "recorded in <code>definitions.toml</code>, the measure appears here "
            "automatically.</p>")
        seen: set[str] = set()
        for w in result.withheld:
            if w.term in seen:
                continue
            seen.add(w.term)
            blocked = sorted({x.metric for x in result.withheld if x.term == w.term})
            parts.append(
                f"<div class='gap'><h3>{_e(w.term.replace('_',' ').title())} "
                f"<span class='term'>{_e(w.term)}</span></h3>"
                f"<p>{_e(w.question)}</p>"
                + (f"<p>{_e(w.note)}</p>" if w.note else "")
                + f"<div class='who'>Decision needed from: <b>{_e(w.needed_from)}</b>"
                f" &middot; withholds: <code>{_e(', '.join(blocked))}</code></div></div>")
    else:
        parts.append("<div class='card'>Every term this report depends on is defined.</div>")

    # -- 2. cohort trend ----------------------------------------------------
    parts.append("<h2><span class='n'>2</span>Cohort participation by week</h2>")
    parts.append(
        "<p class='lede'>Weighted participation points, stacked by component. "
        "Weights are set in <code>definitions.toml</code> and are a presentation "
        "choice, not a ranking of what matters.</p>")
    peak_week = max((c.total_points for c in result.cohort), default=0) or 1
    cols = []
    for cw in result.cohort:
        agg: dict[str, float] = {}
        for f in result.fellows:
            row = result.row(f.fellow_id, cw.week)
            if row and row.enrolled:
                for kind, pts in row.points.items():
                    agg[kind] = agg.get(kind, 0.0) + pts
        segs = []
        for kind, (color, _) in COMPONENT_STYLE.items():
            pts = agg.get(kind, 0.0)
            if pts <= 0:
                continue
            h = 120 * pts / peak_week
            segs.append(f"<div class='seg' style='height:{h:.1f}px;background:{color}' "
                        f"title='{_e(COMPONENT_STYLE[kind][1])}: {pts:g} pts'></div>")
        rate = f"{cw.attendance_rate:.0%}" if cw.attendance_rate is not None else "—"
        cols.append(
            f"<div class='col'><div class='stack'>{''.join(segs)}</div>"
            f"<div class='xlab'><b>{_e(week_start(cw.week).strftime('%d %b'))}</b>"
            f"<span>{cw.active}/{cw.enrolled} active</span>"
            f"<span>{rate} attended</span></div></div>")
    parts.append(f"<div class='card'><div class='scale'>peak week: "
                 f"{peak_week:g} pts</div><div class='chart'>{''.join(cols)}</div>")
    parts.append("<div class='legend'>" + "".join(
        f"<span><i class='sw' style='background:{c}'></i>{_e(label)}</span>"
        for c, label in COMPONENT_STYLE.values()) + "</div>")
    # Share of all points by component. Printed because the weighting is a
    # choice with consequences: if one channel accounts for most of the score,
    # whoever set the weights should be able to see that at a glance.
    overall: dict[str, float] = {}
    for row in result.by_fellow_week.values():
        if row.enrolled:
            for kind, pts in row.points.items():
                overall[kind] = overall.get(kind, 0.0) + pts
    grand = sum(overall.values()) or 1.0
    bar, shares = [], []
    for kind, (color, label) in COMPONENT_STYLE.items():
        pct = 100 * overall.get(kind, 0.0) / grand
        if pct <= 0:
            continue
        bar.append(f"<div style='width:{pct:.2f}%;background:{color}' "
                   f"title='{_e(label)}: {pct:.0f}%'></div>")
        shares.append(f"{_e(label)} {pct:.0f}%")
    parts.append(f"<div class='share'>{''.join(bar)}</div>")
    parts.append(
        "<p class='note'>&ldquo;Active&rdquo; counts fellows with at least one recorded "
        "signal that week, among those enrolled by then. Attendance is live-lesson "
        "attendances over attendances plus recorded absences.<br>"
        "Share of all points under the current weights: <b>" + _e(" &middot; ".join(shares))
        .replace("&amp;middot;", "&middot;") +
        "</b>. If one channel dominates, that is the weighting talking, not the cohort — "
        "the weights are set in <code>definitions.toml</code> and are not yet owned by "
        "a named decision.</p></div>")

    # -- 3. per fellow ------------------------------------------------------
    parts.append("<h2><span class='n'>3</span>By fellow</h2>")
    parts.append(
        "<p class='lede'>Sorted by total participation points. Dotted marks are weeks "
        "before the fellow joined; flat grey marks are enrolled weeks with no recorded "
        "signal from any source.</p>")
    peak_fellow = max((row.total for row in result.by_fellow_week.values()), default=0) or 1
    ranked = sorted(result.fellows, key=lambda f: -result.fellow_total(f.fellow_id))

    head = ("<tr><th>Fellow</th><th>By week</th><th class='num'>Lessons</th>"
            "<th class='num'>Assign.</th><th class='num'>Msgs</th>"
            "<th class='num'>React.</th><th class='num'>Points</th>"
            "<th>Latest week</th></tr>")
    rows = []
    for f in ranked:
        weekly = [result.row(f.fellow_id, w) for w in result.weeks]
        enrolled_rows = [r for r in weekly if r and r.enrolled]
        tally = lambda kind: sum(r.components.get(kind, 0) for r in enrolled_rows)
        last = enrolled_rows[-1] if enrolled_rows else None
        if last is None:
            latest = "<span class='quiet'>not yet enrolled</span>"
        elif last.total == 0:
            latest = "<span class='quiet'>no recorded signal</span>"
        else:
            latest = f"{last.total:g} pts"
        rows.append(
            f"<tr><td class='name'><span class='pill'>{_e(f.initials)}</span>"
            f"{_e(f.display_name)}</td>"
            f"<td>{_sparkline(result, f.fellow_id, peak_fellow)}</td>"
            f"<td class='num'>{tally('live_lesson_attended'):g}</td>"
            f"<td class='num'>{tally('assignment_submitted'):g}</td>"
            f"<td class='num'>{tally('slack_message'):g}</td>"
            f"<td class='num'>{tally('slack_reaction'):g}</td>"
            f"<td class='num'><b>{result.fellow_total(f.fellow_id):g}</b></td>"
            f"<td>{latest}</td></tr>")
    parts.append(f"<div class='card'><table><thead>{head}</thead>"
                 f"<tbody>{''.join(rows)}</tbody></table>"
                 "<p class='note'>Raw counts are shown uncapped; the points column applies "
                 "the per-week Slack caps. No fellow is ranked, flagged, or compared against "
                 "a threshold here — that would require the undefined terms in section 1.</p></div>")

    # -- 4. data health -----------------------------------------------------
    h = result.data_health
    parts.append("<h2><span class='n'>4</span>Data health</h2>")
    parts.append(
        "<p class='lede'>Every number above is only as good as the identity map. "
        "Unresolved events came from a real account that matches no fellow on the "
        "roster — usually a new joiner, a changed handle, or a staff member.</p>")
    parts.append("<div class='card'>")
    parts.append(
        f"<table><thead><tr><th>Check</th><th class='num'>Value</th></tr></thead><tbody>"
        f"<tr><td>Unresolved accounts</td><td class='num'>{h.get('unresolved_identities',0)}</td></tr>"
        f"<tr><td>Events not attributed to a fellow</td>"
        f"<td class='num'>{h.get('unresolved_events',0)}</td></tr></tbody></table>")
    if h.get("unresolved_detail"):
        parts.append("<p class='note'>Unmapped accounts: " + ", ".join(
            f"<code>{_e(s)}:{_e(u)}</code> ({n})" for s, u, n in h["unresolved_detail"]) + "</p>")
    if h.get("ingests"):
        parts.append("<p class='note'>Most recent ingests: " + " &middot; ".join(
            f"{_e(src)} <code>{_e(art)}</code> +{acc}"
            for _, src, art, acc, _ in h["ingests"][:5]) + "</p>")
    parts.append("</div>")

    # -- 5. definitions ledger ---------------------------------------------
    parts.append("<h2><span class='n'>5</span>Definitions in force</h2>")
    parts.append(
        "<p class='lede'>What each term means, who decided it, and when. This section "
        "is generated from <code>definitions.toml</code>, so the report can never drift "
        "from the definitions it was computed under.</p>")
    parts.append("<div class='card'><dl class='ledger'>")
    for t in defs.confirmed_terms():
        parts.append(f"<dt>{_e(t.name.replace('_',' ').title())}</dt>"
                     f"<dd>{_e(t.definition)}<br><small>— {_e(t.owner)}, "
                     f"{_e(t.decided_on)} ({_e(t.source)})</small></dd>")
    parts.append("</dl></div>")

    parts.append(
        f"<footer>Generated by <code>cufa</code> v0.1.0 from "
        f"<code>{_e(defs.meta.get('config_version','?'))}</code>-versioned definitions. "
        "Not shared with fellows: see <code>downstream_use</code> above.</footer>")
    parts.append("</div></body></html>")
    return "".join(parts)
