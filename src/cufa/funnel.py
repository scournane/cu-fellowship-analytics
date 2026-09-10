"""The per-fellow funnel: accepted → joined Slack → first message → first
check-in → completed.

Every stage but the first and last is derived from an observation and is
therefore never wrong in a way that has to be fixed by hand: linking an alias
moves a fellow's first message earlier, and nothing needs re-running. The
cohort view counts how many reached each stage and how long the median fellow
took between stages — the numbers behind the picture staff asked for.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from statistics import median
from typing import Any

import psycopg

from .db import execute, fetch_all, fetch_one
from .errors import CufaError

STAGES = ("accepted", "slack_joined", "first_message", "first_checkin", "completed")
STAGE_LABELS = {
    "accepted": "Accepted",
    "slack_joined": "Joined Slack",
    "first_message": "First message",
    "first_checkin": "First check-in",
    "completed": "Completed",
}


@dataclass
class FellowFunnel:
    fellow_id: str
    full_name: str
    status: str
    accepted_at: datetime | None
    slack_joined_at: datetime | None
    first_message_at: datetime | None
    first_checkin_at: datetime | None
    completed_at: datetime | None

    def reached(self) -> list[str]:
        return [s for s in STAGES if getattr(self, _column(s)) is not None]

    @property
    def furthest(self) -> str:
        reached = self.reached()
        return reached[-1] if reached else "none"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["reached"] = self.reached()
        data["furthest"] = self.furthest
        return data


def _column(stage: str) -> str:
    return {"accepted": "accepted_at"}.get(stage, f"{stage}_at")


def fellow_funnel(conn: psycopg.Connection, fellow_id: str) -> FellowFunnel | None:
    row = fetch_one(conn, "select * from v_fellow_funnel where fellow_id = %s", (fellow_id,))
    return _row(row) if row else None


def cohort_funnel(conn: psycopg.Connection, cohort_id: str) -> list[FellowFunnel]:
    return [_row(r) for r in fetch_all(conn, "select * from v_fellow_funnel where cohort_id = %s order by full_name", (cohort_id,))]


def _row(row: dict[str, Any]) -> FellowFunnel:
    return FellowFunnel(
        fellow_id=row["fellow_id"],
        full_name=row["full_name"],
        status=row["status"],
        accepted_at=row["accepted_at"],
        slack_joined_at=row["slack_joined_at"],
        first_message_at=row["first_message_at"],
        first_checkin_at=row["first_checkin_at"],
        completed_at=row["completed_at"],
    )


def cohort_summary(conn: psycopg.Connection, cohort_id: str) -> dict[str, Any]:
    """Stage counts and median days between stages."""
    rows = cohort_funnel(conn, cohort_id)
    counts = {stage: sum(1 for r in rows if getattr(r, _column(stage)) is not None) for stage in STAGES}
    gaps: dict[str, float | None] = {}
    for earlier, later in zip(STAGES, STAGES[1:]):
        deltas = [
            (getattr(r, _column(later)) - getattr(r, _column(earlier))).total_seconds() / 86400
            for r in rows
            if getattr(r, _column(later)) is not None and getattr(r, _column(earlier)) is not None
        ]
        gaps[f"{earlier}->{later}"] = round(median(deltas), 1) if deltas else None
    return {"cohort_id": cohort_id, "fellows": len(rows), "counts": counts, "median_days": gaps}


def mark_completed(conn: psycopg.Connection, fellow_id: str, *, at: datetime | None = None) -> None:
    n = execute(
        conn,
        "update fellow set completed_at = coalesce(%s, now()), updated_at = now() where fellow_id = %s",
        (at, fellow_id),
    )
    if n == 0:
        raise CufaError(f"No fellow with id {fellow_id}")


def set_accepted_on(conn: psycopg.Connection, fellow_id: str, accepted_on: datetime) -> None:
    n = execute(
        conn,
        "update fellow set accepted_on = %s, updated_at = now() where fellow_id = %s",
        (accepted_on.date() if isinstance(accepted_on, datetime) else accepted_on, fellow_id),
    )
    if n == 0:
        raise CufaError(f"No fellow with id {fellow_id}")


def render_text(summary: dict[str, Any]) -> str:
    """A terminal funnel: one bar per stage, scaled to the cohort size."""
    total = max(int(summary["fellows"]), 1)
    width = 30
    lines = [f"Funnel — cohort {summary['cohort_id']} ({summary['fellows']} fellows)", ""]
    for stage in STAGES:
        n = summary["counts"][stage]
        bar = "█" * round(width * n / total)
        lines.append(f"  {STAGE_LABELS[stage]:<15} {bar:<{width}} {n:>3}  ({100 * n // total:>3}%)")
    lines.append("")
    lines.append("  Median days between stages:")
    for key, days in summary["median_days"].items():
        a, b = key.split("->")
        lines.append(f"    {STAGE_LABELS[a]} → {STAGE_LABELS[b]}: {days if days is not None else '—'}")
    return "\n".join(lines)


def render_fellow_text(funnel: FellowFunnel) -> str:
    lines = [f"{funnel.full_name} ({funnel.fellow_id})"]
    for stage in STAGES:
        at = getattr(funnel, _column(stage))
        mark = "●" if at is not None else "○"
        when = at.strftime("%Y-%m-%d") if at is not None else "—"
        lines.append(f"  {mark} {STAGE_LABELS[stage]:<15} {when}")
    return "\n".join(lines)


__all__ = [
    "STAGES",
    "STAGE_LABELS",
    "FellowFunnel",
    "cohort_funnel",
    "cohort_summary",
    "fellow_funnel",
    "mark_completed",
    "render_fellow_text",
    "render_text",
    "set_accepted_on",
]
