"""Cohort assignments used by Slack reminders and weekly digests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg

from .db import execute, fetch_all, fetch_one
from .timeutil import get_zone, to_utc
from .urls import optional_http_url


@dataclass(frozen=True)
class AssignmentInput:
    cohort_id: str
    title: str
    due_at_local: datetime
    timezone: str
    description: str | None = None
    url: str | None = None
    status: str = "active"

    def due_at_utc(self) -> datetime:
        local = self.due_at_local
        if local.tzinfo is None:
            local = local.replace(tzinfo=get_zone(self.timezone))
        return to_utc(local)

    def cleaned_url(self) -> str | None:
        return optional_http_url(self.url, label="Assignment URL")

    def validate(self) -> None:
        if not self.cohort_id.strip():
            raise ValueError("Cohort is required.")
        if not self.title.strip():
            raise ValueError("Assignment title is required.")
        if self.status not in {"active", "cancelled"}:
            raise ValueError("Assignment status must be active or cancelled.")
        get_zone(self.timezone)
        self.cleaned_url()


def create_assignment(conn: psycopg.Connection, data: AssignmentInput) -> str:
    data.validate()
    row = fetch_one(
        conn,
        """
        insert into assignment (
            cohort_id, title, description, url, due_at_local, timezone,
            due_at_utc, status
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        returning assignment_id
        """,
        (
            data.cohort_id.strip(),
            data.title.strip(),
            (data.description or "").strip() or None,
            data.cleaned_url(),
            data.due_at_local.replace(tzinfo=None),
            data.timezone,
            data.due_at_utc(),
            data.status,
        ),
    )
    assert row is not None
    return str(row["assignment_id"])


def update_assignment(
    conn: psycopg.Connection, assignment_id: str, data: AssignmentInput
) -> bool:
    data.validate()
    return bool(
        execute(
            conn,
            """
            update assignment
               set cohort_id = %s,
                   title = %s,
                   description = %s,
                   url = %s,
                   due_at_local = %s,
                   timezone = %s,
                   due_at_utc = %s,
                   status = %s,
                   updated_at = now()
             where assignment_id = %s
            """,
            (
                data.cohort_id.strip(),
                data.title.strip(),
                (data.description or "").strip() or None,
                data.cleaned_url(),
                data.due_at_local.replace(tzinfo=None),
                data.timezone,
                data.due_at_utc(),
                data.status,
                assignment_id,
            ),
        )
    )


def get_assignment(
    conn: psycopg.Connection, assignment_id: str
) -> dict[str, Any] | None:
    return fetch_one(
        conn, "select * from assignment where assignment_id = %s", (assignment_id,)
    )


def list_assignments(
    conn: psycopg.Connection,
    cohort_id: str | None = None,
    *,
    include_cancelled: bool = False,
) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        """
        select *
          from assignment
         where (%s::text is null or cohort_id = %s::text)
           and (%s or status = 'active')
         order by due_at_utc, title
        """,
        (cohort_id, cohort_id, include_cancelled),
    )
