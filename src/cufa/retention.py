"""Retention: are the early frameworks still in use later on?

The fellowship's first weeks teach big-picture frameworks; the later weeks are
topic-specific. The question staff asked is whether fellows keep *using* the
early ideas. This answers it the only way that fits the project's rules:

* **Deterministically.** ``config/retention_rubric.json`` names each concept
  and the terms that signal it. A later free-text answer that uses one of the
  terms counts as a mention. No model reads a fellow's words to decide
  anything about them (design invariant 12).
* **Counted, never graded.** A mention is a mention. There is no score for
  how *well* a concept was applied, because that would be rating writing
  (invariant 13).
* **Compared with the cohort**, so the output is "this fellow references the
  week-1 frameworks less than most" rather than an absolute verdict.

The midpoint and end-of-fellowship surveys the Director suggested are
ordinary Part B rotating questions — the rotation config already supports a
teacher question on any week, so no new form is needed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg

from .db import fetch_all
from .errors import CufaError

DEFAULT_RUBRIC_PATH = "config/retention_rubric.json"


class RubricError(CufaError):
    """The rubric file exists but cannot be used."""


@dataclass(frozen=True)
class Concept:
    key: str
    label: str
    taught_week: int
    terms: tuple[str, ...]

    def pattern(self) -> re.Pattern[str]:
        alternatives = "|".join(re.escape(t) for t in self.terms if t)
        return re.compile(rf"\b(?:{alternatives})\b", re.IGNORECASE)


@dataclass(frozen=True)
class Rubric:
    concepts: tuple[Concept, ...] = ()
    source: str = DEFAULT_RUBRIC_PATH

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "concepts": [
                {"key": c.key, "label": c.label, "taught_week": c.taught_week, "terms": list(c.terms)}
                for c in self.concepts
            ],
        }


def parse_rubric(payload: Any, *, source: str = DEFAULT_RUBRIC_PATH) -> Rubric:
    if not isinstance(payload, dict) or not isinstance(payload.get("concepts"), list):
        raise RubricError(f'{source} must be a JSON object with a "concepts" list.')
    concepts: list[Concept] = []
    seen: set[str] = set()
    for entry in payload["concepts"]:
        if not isinstance(entry, dict):
            raise RubricError(f"{source}: every concept must be an object.")
        key = str(entry.get("key") or "").strip()
        terms = tuple(str(t).strip() for t in (entry.get("terms") or []) if str(t).strip())
        try:
            week = int(entry.get("taught_week"))
        except (TypeError, ValueError):
            raise RubricError(f"{source}: concept {key or '?'} needs an integer taught_week.") from None
        if not key or not terms:
            raise RubricError(f"{source}: every concept needs a key and at least one term.")
        if key in seen:
            raise RubricError(f"{source}: concept {key} is listed twice.")
        seen.add(key)
        concepts.append(Concept(key=key, label=str(entry.get("label") or key), taught_week=week, terms=terms))
    return Rubric(concepts=tuple(concepts), source=source)


def load_rubric(path: str | Path | None = None) -> Rubric:
    file = Path(path or DEFAULT_RUBRIC_PATH)
    if not file.is_absolute():
        file = Path(__file__).resolve().parents[2] / file
    if not file.exists():
        return Rubric(source=str(file))
    try:
        payload = json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RubricError(f"{file} is not valid JSON: {exc}") from exc
    return parse_rubric(payload, source=str(file))


@dataclass
class FellowRetention:
    fellow_id: str
    full_name: str
    later_answers: int
    mentions: dict[str, int] = field(default_factory=dict)
    concepts_reused: int = 0
    cohort_mean_reused: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "fellow_id": self.fellow_id,
            "full_name": self.full_name,
            "later_answers": self.later_answers,
            "mentions": dict(self.mentions),
            "concepts_reused": self.concepts_reused,
            "cohort_mean_reused": self.cohort_mean_reused,
        }


def cohort_retention(
    conn: psycopg.Connection, cohort_id: str, *, rubric: Rubric | None = None
) -> list[FellowRetention]:
    """For each fellow, how many of the early concepts they referenced in
    answers given *after* the week each concept was taught."""
    rubric = rubric if rubric is not None else load_rubric()
    if not rubric.concepts:
        return []
    patterns = {c.key: c.pattern() for c in rubric.concepts}
    rows = fetch_all(
        conn,
        """
        select fellow_id, full_name, week_index,
               coalesce(takeaway_text, '') || ' ' || coalesce(rotating_text, '') as answer
          from v_checkin_b_resolved
         where cohort_id = %s and fellow_id is not null and week_index is not null
        """,
        (cohort_id,),
    )
    fellows = {
        r["fellow_id"]: r["full_name"]
        for r in fetch_all(conn, "select fellow_id, full_name from fellow where cohort_id = %s and status = 'active'", (cohort_id,))
    }
    per: dict[str, FellowRetention] = {
        fid: FellowRetention(fellow_id=fid, full_name=name, later_answers=0) for fid, name in fellows.items()
    }
    for row in rows:
        item = per.get(row["fellow_id"])
        if item is None:
            continue
        later_for_any = False
        for concept in rubric.concepts:
            if row["week_index"] > concept.taught_week:
                later_for_any = True
                if patterns[concept.key].search(row["answer"]):
                    item.mentions[concept.key] = item.mentions.get(concept.key, 0) + 1
        if later_for_any:
            item.later_answers += 1
    out = list(per.values())
    for item in out:
        item.concepts_reused = len(item.mentions)
    mean = (sum(i.concepts_reused for i in out) / len(out)) if out else 0.0
    for item in out:
        item.cohort_mean_reused = round(mean, 2)
    out.sort(key=lambda i: (i.concepts_reused, i.full_name))
    return out


def render_text(cohort_id: str, rubric: Rubric, rows: list[FellowRetention]) -> str:
    lines = [f"Retention — cohort {cohort_id}", ""]
    if not rubric.concepts:
        lines.append(f"  No rubric at {rubric.source}. Add concepts and terms to enable this.")
        return "\n".join(lines)
    lines.append("  Concepts (from the rubric):")
    for c in rubric.concepts:
        lines.append(f"    week {c.taught_week}: {c.label} — {', '.join(c.terms)}")
    lines.append("")
    lines.append("  Concepts referenced in later answers, per fellow (counted, never graded):")
    for r in rows:
        detail = ", ".join(f"{k}×{v}" for k, v in sorted(r.mentions.items())) or "none"
        lines.append(f"    {r.full_name:<24} {r.concepts_reused}/{len(rubric.concepts)}  {detail}")
    if rows:
        lines.append("")
        lines.append(f"  Cohort mean: {rows[0].cohort_mean_reused} of {len(rubric.concepts)} concepts")
    return "\n".join(lines)


__all__ = [
    "DEFAULT_RUBRIC_PATH",
    "Concept",
    "FellowRetention",
    "Rubric",
    "RubricError",
    "cohort_retention",
    "load_rubric",
    "parse_rubric",
    "render_text",
]
