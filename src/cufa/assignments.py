"""Assignments: the Solvathon, the case brief, and anything else with a due date.

Two things live here and they are kept apart on purpose:

* **The assignment** — a title, a due time, a link. Created by staff, from the
  console, the bot or the CLI, and what the reminder path reads. A cancelled
  assignment is kept rather than deleted, and stops appearing in reminders
  and digests.
* **A submission and its score** — recorded by hand. CU grades these against
  their own rubric; the system only stores what a staffer types in, with who
  typed it and when. Nothing here rates a piece of work.

A score is never part of any participation or attention metric. It is shown on
the fellow's profile card and the staff dashboard, and that is all.

The link column is ``link``. The console and the forms-pipeline CLI grew up
calling it ``url``; both names are accepted on the way in and both come back
on every row, so neither side has to change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import psycopg

from .db import execute, fetch_all, fetch_one
from .errors import CufaError
from .logging_setup import get_logger
from .timeutil import get_zone, to_utc
from .urls import optional_http_url

log = get_logger(__name__)

KINDS = ("solvathon", "case_brief", "other")
KIND_LABELS = {"solvathon": "Solvathon", "case_brief": "Case brief", "other": "Assignment"}
STATUSES = ("active", "cancelled")

_ROW = "a.*, a.link as url"


@dataclass(frozen=True)
class AssignmentInput:
    cohort_id: str
    title: str
    due_at_local: datetime
    timezone: str
    #: ``None`` means "leave as is" on an update and ``other`` on a create.
    kind: str | None = None
    link: str | None = None
    #: The console's and the CLI's name for ``link``; either may be given.
    url: str | None = None
    description: str | None = None
    status: str = "active"
    max_score: Decimal | float | None = None
    created_by: str | None = None

    def __post_init__(self) -> None:
        if self.url and not self.link:
            object.__setattr__(self, "link", self.url)
        elif self.link and not self.url:
            object.__setattr__(self, "url", self.link)

    def due_at_utc(self) -> datetime:
        local = self.due_at_local
        if local.tzinfo is not None:
            return to_utc(local)
        return to_utc(local.replace(tzinfo=get_zone(self.timezone)))

    def due_at_local_naive(self) -> datetime:
        """The wall-clock value that was typed, stored beside its zone."""
        local = self.due_at_local
        if local.tzinfo is not None:
            local = local.astimezone(get_zone(self.timezone))
        return local.replace(tzinfo=None)

    def cleaned_url(self) -> str | None:
        return optional_http_url(self.link, label="Assignment URL")

    def validate(self) -> None:
        if not self.cohort_id.strip():
            raise ValueError("Cohort is required.")
        if not self.title.strip():
            raise ValueError("Assignment title is required.")
        if self.status not in STATUSES:
            raise ValueError("Assignment status must be active or cancelled.")
        if self.kind is not None and self.kind not in KINDS:
            raise ValueError(f"Assignment kind must be one of {', '.join(KINDS)}.")
        if self.max_score is not None and Decimal(str(self.max_score)) < 0:
            raise ValueError("Maximum score cannot be negative.")
        get_zone(self.timezone)
        self.cleaned_url()


def create_assignment(conn: psycopg.Connection, data: AssignmentInput) -> str:
    data.validate()
    row = fetch_one(
        conn,
        """
        insert into assignment (
            cohort_id, title, kind, description, link, due_at_local, timezone,
            due_at_utc, status, max_score, created_by
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        returning assignment_id
        """,
        (
            data.cohort_id.strip(),
            data.title.strip(),
            data.kind or "other",
            (data.description or "").strip() or None,
            data.cleaned_url(),
            data.due_at_local_naive(),
            data.timezone,
            data.due_at_utc(),
            data.status,
            data.max_score,
            (data.created_by or "").strip().lower() or None,
        ),
    )
    assert row is not None
    assignment_id = str(row["assignment_id"])
    log.info("assignment created id=%s cohort=%s kind=%s", assignment_id, data.cohort_id, data.kind or "other")
    return assignment_id


def update_assignment(
    conn: psycopg.Connection, assignment_id: str, data: AssignmentInput
) -> bool:
    """Everything the form can change. ``kind`` and ``max_score`` are left alone when not given."""
    data.validate()
    return bool(
        execute(
            conn,
            """
            update assignment
               set cohort_id = %s,
                   title = %s,
                   kind = coalesce(%s, kind),
                   description = %s,
                   link = %s,
                   due_at_local = %s,
                   timezone = %s,
                   due_at_utc = %s,
                   status = %s,
                   max_score = coalesce(%s, max_score),
                   updated_at = now()
             where assignment_id = %s
            """,
            (
                data.cohort_id.strip(),
                data.title.strip(),
                data.kind,
                (data.description or "").strip() or None,
                data.cleaned_url(),
                data.due_at_local_naive(),
                data.timezone,
                data.due_at_utc(),
                data.status,
                data.max_score,
                assignment_id,
            ),
        )
    )


def set_link(conn: psycopg.Connection, assignment_id: str, link: str | None) -> None:
    execute(
        conn,
        "update assignment set link = %s, updated_at = now() where assignment_id = %s",
        ((link or "").strip() or None, assignment_id),
    )


def get_assignment(conn: psycopg.Connection, assignment_id: str) -> dict[str, Any] | None:
    return fetch_one(conn, f"select {_ROW} from assignment a where a.assignment_id = %s", (assignment_id,))


def list_assignments(
    conn: psycopg.Connection,
    cohort_id: str | None = None,
    *,
    include_cancelled: bool = False,
    due_after: datetime | None = None,
) -> list[dict[str, Any]]:
    """Every assignment, with how many fellows have submitted and been scored."""
    return fetch_all(
        conn,
        f"""
        select {_ROW},
               count(s.submission_id) filter (where s.submitted_at_utc is not null) as submitted,
               count(s.submission_id) filter (where s.score is not null) as scored
          from assignment a
          left join assignment_submission s on s.assignment_id = a.assignment_id
         where (%s::text is null or a.cohort_id = %s::text)
           and (%s or a.status = 'active')
           and (%s::timestamptz is null or a.due_at_utc >= %s::timestamptz)
         group by a.assignment_id
         order by a.due_at_utc, a.title
        """,
        (cohort_id, cohort_id, include_cancelled, due_after, due_after),
    )


def find_assignment(conn: psycopg.Connection, cohort_id: str, query: str) -> dict[str, Any]:
    """By id, by kind (``solvathon``), or by title fragment."""
    needle = (query or "").strip()
    if not needle:
        raise CufaError("Give an assignment id, kind or title.")
    try:
        import uuid

        uuid.UUID(needle)
        row = get_assignment(conn, needle)
        if row:
            return row
    except ValueError:
        pass
    if needle.lower().replace("-", "_").replace(" ", "_") in KINDS:
        rows = fetch_all(
            conn,
            f"select {_ROW} from assignment a where a.cohort_id = %s and a.kind = %s order by a.due_at_utc desc",
            (cohort_id, needle.lower().replace("-", "_").replace(" ", "_")),
        )
        if len(rows) == 1:
            return rows[0]
        if len(rows) > 1:
            raise CufaError(f"More than one {needle} assignment exists; use the id.")
    rows = fetch_all(
        conn,
        f"select {_ROW} from assignment a where a.cohort_id = %s and a.title ilike %s order by a.due_at_utc",
        (cohort_id, f"%{needle}%"),
    )
    if len(rows) == 1:
        return rows[0]
    if rows:
        raise CufaError(
            f"{needle!r} matches more than one assignment: "
            + ", ".join(f"{r['title']} ({r['assignment_id']})" for r in rows[:6])
        )
    raise CufaError(f"No assignment matches {needle!r}.")


# ---------------------------------------------------------------------------
# submissions and scores
# ---------------------------------------------------------------------------


def record_submission(
    conn: psycopg.Connection, assignment_id: str, fellow_id: str, *, submitted_at: datetime | None = None
) -> None:
    """Mark that a fellow submitted. Idempotent; the first timestamp wins."""
    execute(
        conn,
        """
        insert into assignment_submission (assignment_id, fellow_id, submitted_at_utc)
        values (%s, %s, coalesce(%s, now()))
        on conflict (assignment_id, fellow_id) do update
           set submitted_at_utc = coalesce(assignment_submission.submitted_at_utc, excluded.submitted_at_utc),
               updated_at = now()
        """,
        (assignment_id, fellow_id, submitted_at),
    )


def record_score(
    conn: psycopg.Connection,
    assignment_id: str,
    fellow_id: str,
    *,
    score: Decimal | float,
    by: str,
    note: str | None = None,
) -> dict[str, Any]:
    """Store the score a staffer gave. Marks the work submitted if it was not."""
    assignment = get_assignment(conn, assignment_id)
    if assignment is None:
        raise CufaError(f"No assignment with id {assignment_id}")
    value = Decimal(str(score))
    if value < 0:
        raise CufaError("a score cannot be negative")
    if assignment["max_score"] is not None and value > assignment["max_score"]:
        raise CufaError(f"{value} is above this assignment's maximum of {assignment['max_score']}")
    who = (by or "").strip().lower()
    if not who:
        raise CufaError("a score needs the address of the person who gave it")
    row = fetch_one(
        conn,
        """
        insert into assignment_submission
            (assignment_id, fellow_id, submitted_at_utc, score, graded_by, graded_at, note)
        values (%s, %s, now(), %s, %s, now(), %s)
        on conflict (assignment_id, fellow_id) do update
           set submitted_at_utc = coalesce(assignment_submission.submitted_at_utc, now()),
               score = excluded.score,
               graded_by = excluded.graded_by,
               graded_at = now(),
               note = coalesce(excluded.note, assignment_submission.note),
               updated_at = now()
        returning submission_id, score, graded_by, graded_at
        """,
        (assignment_id, fellow_id, value, who, note),
    )
    assert row is not None
    log.info("score recorded assignment=%s fellow=%s", assignment_id, fellow_id)
    return row


def submissions_for_fellow(conn: psycopg.Connection, fellow_id: str) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        """
        select a.assignment_id, a.title, a.kind, a.due_at_utc, a.max_score,
               s.submitted_at_utc, s.score, s.graded_by, s.graded_at, s.note
          from assignment a
          join fellow f on f.cohort_id = a.cohort_id and f.fellow_id = %s
          left join assignment_submission s on s.assignment_id = a.assignment_id and s.fellow_id = f.fellow_id
         order by a.due_at_utc
        """,
        (fellow_id,),
    )


def submissions_for_assignment(conn: psycopg.Connection, assignment_id: str) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        """
        select f.fellow_id, f.full_name, s.submitted_at_utc, s.score, s.graded_by, s.graded_at, s.note
          from assignment a
          join fellow f on f.cohort_id = a.cohort_id and f.status = 'active'
          left join assignment_submission s on s.assignment_id = a.assignment_id and s.fellow_id = f.fellow_id
         where a.assignment_id = %s
         order by f.full_name
        """,
        (assignment_id,),
    )


__all__ = [
    "KINDS",
    "KIND_LABELS",
    "STATUSES",
    "AssignmentInput",
    "create_assignment",
    "find_assignment",
    "get_assignment",
    "list_assignments",
    "record_score",
    "record_submission",
    "set_link",
    "submissions_for_assignment",
    "submissions_for_fellow",
    "update_assignment",
]
