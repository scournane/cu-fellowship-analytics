"""Reading the confidence field: raw values in, median and IQR out.

Three rules, all of them about not over-claiming what a self-rating means:

* **Stored raw, 1-7, always.** Never rescaled, never normalised, never turned
  into a percentage on write. A percentage implies a ratio scale — that 6 is
  twice 3 — which a Likert scale does not have.
* **Median, not mean.** Seven-point Likert data is ordinal. The distance between
  3 and 4 is not known to equal the distance between 6 and 7, so a mean of these
  values is a number with no defined meaning, however comfortable it looks.
* **The signal is the trend and the dip, not the level.** Absolute self-rated
  confidence is noisy and weakly calibrated: some people never say 7 and some
  never say below 4. A fellow moving 6 → 3 across two sessions is informative; a
  fellow sitting flat at 4 mostly is not. That sentence belongs next to every
  chart drawn from this data, which is why it is a constant here rather than a
  comment.
"""

from __future__ import annotations

from typing import Any

import psycopg

from .db import fetch_all

INTERPRETATION = (
    "Read the trend, not the level. Self-rated confidence is noisy and weakly "
    "calibrated — some fellows never say 7 and some never say below 4 — so a "
    "single low score is not a finding. A fellow moving 6 to 3 across two "
    "sessions is worth a conversation; a fellow sitting flat at 4 mostly is not. "
    "The median is shown rather than the mean because a 7-point scale is ordinal "
    "and the mean of ordinal data is not a meaningful quantity."
)

STRAIGHTLINE_NOTE = (
    "Straight-lining is a DATA QUALITY flag on the responses, not a judgment "
    "about the person. Fatigued respondents repeat the same answer roughly a "
    "third more often, which is a fact about the survey. It never enters any "
    "participation signal, count or rate."
)


def trend(conn: psycopg.Connection, cohort_id: str) -> list[dict[str, Any]]:
    """Median and IQR per week for one cohort, in week order.

    Sessions with no week number sort last rather than being dropped: a session
    somebody forgot to number still has responses, and hiding them would make the
    trend look complete when it is not.
    """
    return fetch_all(
        conn,
        """
        select week_index, session_id, session_title, scheduled_at_utc,
               responses, fellows, median, q1, q3, iqr, lowest, highest
          from v_confidence_trend
         where cohort_id = %s
         order by week_index nulls last, scheduled_at_utc
        """,
        (cohort_id,),
    )


def by_fellow(
    conn: psycopg.Connection, cohort_id: str, fellow_id: str | None = None
) -> list[dict[str, Any]]:
    """Every raw value, per fellow per session."""
    return fetch_all(
        conn,
        """
        select fellow_id, full_name, session_id, session_title, week_index,
               scheduled_at_utc, confidence_raw, submitted_at_utc
          from v_confidence_by_fellow
         where cohort_id = %s
           and (%s::text is null or fellow_id = %s::text)
         order by full_name, scheduled_at_utc
        """,
        (cohort_id, fellow_id, fellow_id),
    )


def for_session(conn: psycopg.Connection, session_id: str) -> dict[str, Any]:
    """The distribution for one session, for the console's response view.

    A count per point on the scale, not a summary statistic — seven numbers is
    small enough to show whole, and a distribution answers "is this bimodal?"
    which a median cannot.
    """
    rows = fetch_all(
        conn,
        """
        select confidence_raw, count(*) as n
          from checkin_b
         where session_id = %s and confidence_raw is not null
         group by confidence_raw
         order by confidence_raw
        """,
        (session_id,),
    )
    counts = {int(row["confidence_raw"]): int(row["n"]) for row in rows}
    total = sum(counts.values())
    summary = fetch_all(
        conn,
        """
        select median, q1, q3, iqr, responses
          from v_confidence_trend
         where session_id = %s
        """,
        (session_id,),
    )
    stats = summary[0] if summary else {}
    return {
        "distribution": [{"value": v, "count": counts.get(v, 0)} for v in range(1, 8)],
        "responses": total,
        "median": stats.get("median"),
        "q1": stats.get("q1"),
        "q3": stats.get("q3"),
        "iqr": stats.get("iqr"),
        "interpretation": INTERPRETATION,
    }


def straightliners(
    conn: psycopg.Connection, cohort_id: str | None = None
) -> list[dict[str, Any]]:
    """Fellows who gave the same value four or more sessions running.

    Surfaced in the review screen and nowhere else. Nothing reads this while
    computing a count, a rate or a score — see ``docs/decisions.md`` ADR-027.
    """
    return fetch_all(
        conn,
        """
        select cohort_id, fellow_id, full_name, confidence_raw, run_length,
               first_session_at, last_session_at, session_titles
          from v_confidence_straightline
         where (%s::text is null or cohort_id = %s::text)
         order by run_length desc, full_name
        """,
        (cohort_id, cohort_id),
    )


def render_trend_text(cohort_id: str, rows: list[dict[str, Any]]) -> str:
    """The `cufa report --confidence` block.

    Draws the IQR as a bar across the 1-7 scale with the median marked, because
    "median 5, IQR 2" and a picture of where the middle half of the cohort
    actually sits are different amounts of understanding for the same data.
    """
    lines: list[str] = []
    add = lines.append

    add(f"Confidence trend — cohort {cohort_id}")
    add("=" * 62)
    add("")
    if not rows:
        add("  (no Part B confidence responses yet)")
        add("")
        add(INTERPRETATION)
        return "\n".join(lines)

    add(f"  {'wk':>3} {'session':<28}{'n':>4}{'med':>5}{'IQR':>5}  1───────7")
    for row in rows:
        week = row["week_index"]
        title = (row["session_title"] or "")[:27]
        median = row["median"]
        q1, q3 = row["q1"], row["q3"]
        add(
            f"  {(week if week is not None else '—'):>3} {title:<28}"
            f"{row['responses']:>4}{(median if median is not None else '—'):>5}"
            f"{(row['iqr'] if row['iqr'] is not None else '—'):>5}  {_bar(q1, q3, median)}"
        )
    add("")
    add(_wrap(INTERPRETATION, "  "))
    return "\n".join(lines)


def _bar(q1: int | None, q3: int | None, median: int | None) -> str:
    """Seven cells: '-' outside the IQR, '=' inside it, '#' at the median."""
    if q1 is None or q3 is None:
        return "".join("-" for _ in range(7))
    cells = []
    for value in range(1, 8):
        if median is not None and value == median:
            cells.append("#")
        elif q1 <= value <= q3:
            cells.append("=")
        else:
            cells.append("-")
    return "".join(cells)


def _wrap(text: str, indent: str = "", width: int = 76) -> str:
    import textwrap

    return "\n".join(
        textwrap.fill(text, width=width, initial_indent=indent, subsequent_indent=indent)
        for _ in [0]
    )


__all__ = [
    "INTERPRETATION",
    "STRAIGHTLINE_NOTE",
    "by_fellow",
    "for_session",
    "render_trend_text",
    "straightliners",
    "trend",
]
