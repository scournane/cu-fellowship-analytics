"""Metrics.

Every metric declares the terms it depends on. `Definitions.check` decides
whether it may run. A metric whose terms are undefined returns a `Withheld`
object that carries the open question and its owner -- so the report shows
the gap instead of hiding it behind a plausible default.

Deliberate omission: there is no 0-100 score here. A single normalised grade
would be an implicit definition of *performance*, which nobody has defined
yet. What is computed is participation points plus the component breakdown,
with the cohort median for context.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from .config import Definitions, Withheld
from .model import Fellow, week_start
from .store import Store


@dataclass
class FellowWeek:
    fellow_id: str
    week: str
    components: dict[str, float]     # kind -> raw count
    points: dict[str, float]         # kind -> weighted, capped points
    total: float
    enrolled: bool                   # was the Fellow in the cohort this week

    @property
    def silent(self) -> bool:
        """Enrolled, but no positive signal from any source."""
        return self.enrolled and self.total == 0.0


@dataclass
class CohortWeek:
    week: str
    active: int
    enrolled: int
    median_points: float
    total_points: float
    attendance_rate: float | None


@dataclass
class Result:
    weeks: list[str]
    fellows: list[Fellow]
    by_fellow_week: dict[tuple[str, str], FellowWeek]
    cohort: list[CohortWeek]
    withheld: list[Withheld] = field(default_factory=list)
    data_health: dict = field(default_factory=dict)

    def row(self, fellow_id: str, week: str) -> FellowWeek | None:
        return self.by_fellow_week.get((fellow_id, week))

    def fellow_total(self, fellow_id: str) -> float:
        return sum(fw.total for (fid, _), fw in self.by_fellow_week.items()
                   if fid == fellow_id)


# Metric id -> the terms it requires. This table is the contract between
# metrics.py and definitions.toml.
REQUIREMENTS = {
    "weekly_participation":      ["participation", "cohort"],
    "cohort_participation_trend": ["participation", "cohort"],
    "performance_index":         ["performance"],
    "cohort_performance_trend":  ["performance"],
    "at_risk_flag":              ["falling_behind", "fellow_support_responder"],
    "intervention_queue":        ["falling_behind", "fellow_support_responder"],
    "retention_enforcement":     ["retention"],
    "correction_log":            ["correction_process"],
}


def _points(counts: dict[str, float], defs: Definitions) -> dict[str, float]:
    out: dict[str, float] = {}
    for kind, weight in defs.weights.items():
        raw = counts.get(kind, 0.0)
        cap = defs.caps.get(kind)
        if cap is not None:
            raw = min(raw, cap)
        if raw:
            out[kind] = round(raw * weight, 2)
    return out


def compute(store: Store, defs: Definitions, cohort: str | None = None) -> Result:
    cohort = cohort or defs.meta.get("cohort_label")
    fellows = store.fellows(cohort)
    weeks = store.weeks(cohort)
    counts = store.weekly_counts(cohort)

    withheld: list[Withheld] = []
    for metric, requires in REQUIREMENTS.items():
        withheld.extend(defs.check(metric, requires))

    by_fellow_week: dict[tuple[str, str], FellowWeek] = {}
    # weekly_participation is gated too -- if `participation` were ever set
    # back to undefined, this block simply does not run.
    if not defs.check("weekly_participation", REQUIREMENTS["weekly_participation"]):
        for fellow in fellows:
            for week in weeks:
                enrolled = week_start(week) >= _monday_of(fellow)
                c = counts.get((fellow.fellow_id, week), {})
                pts = _points(c, defs)
                by_fellow_week[(fellow.fellow_id, week)] = FellowWeek(
                    fellow_id=fellow.fellow_id, week=week, components=c,
                    points=pts, total=round(sum(pts.values()), 2), enrolled=enrolled,
                )

    cohort_weeks: list[CohortWeek] = []
    for week in weeks:
        rows = [by_fellow_week[(f.fellow_id, week)] for f in fellows
                if (f.fellow_id, week) in by_fellow_week]
        enrolled_rows = [r for r in rows if r.enrolled]
        totals = [r.total for r in enrolled_rows]
        attended = sum(r.components.get("live_lesson_attended", 0) for r in enrolled_rows)
        absent = sum(r.components.get("live_lesson_absent", 0) for r in enrolled_rows)
        denom = attended + absent
        cohort_weeks.append(CohortWeek(
            week=week,
            active=sum(1 for t in totals if t > 0),
            enrolled=len(enrolled_rows),
            median_points=round(statistics.median(totals), 2) if totals else 0.0,
            total_points=round(sum(totals), 2),
            attendance_rate=round(attended / denom, 3) if denom else None,
        ))

    return Result(
        weeks=weeks, fellows=fellows, by_fellow_week=by_fellow_week,
        cohort=cohort_weeks, withheld=withheld,
        data_health=_health(store),
    )


def _monday_of(fellow: Fellow):
    d = fellow.joined_on
    return d.fromordinal(d.toordinal() - d.weekday())


def _health(store: Store) -> dict:
    unresolved = store.unresolved_summary()
    return {
        "unresolved_identities": len(unresolved),
        "unresolved_events": sum(r["n"] for r in unresolved),
        "unresolved_detail": [(r["source"], r["source_user_id"], r["n"])
                              for r in unresolved[:10]],
        "ingests": [(r["run_at"], r["source"], r["artifact"], r["accepted"], r["unresolved"])
                    for r in store.last_ingests(8)],
    }
