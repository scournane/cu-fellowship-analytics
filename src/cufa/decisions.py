"""Writing decisions: always append, never edit.

Invariant 2 and 4 in code. Superseding is two statements — set ``superseded_at``
on the row that was current, insert the new one — inside one transaction. The
partial unique index on ``checkin_id WHERE superseded_at IS NULL`` means a bug
that forgets the first statement fails immediately with a constraint violation
rather than producing two live decisions that disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg

from .db import execute, fetch_one
from .logging_setup import get_logger, mask_email

log = get_logger(__name__)

STATUS_ATTENDED = "attended"
STATUS_NOT_ATTENDED = "not_attended"
STATUS_NEEDS_REVIEW = "needs_review"

_ATTENDED_BY_STATUS: dict[str, bool | None] = {
    STATUS_ATTENDED: True,
    STATUS_NOT_ATTENDED: False,
    # needs_review is NULL, not False. Absent evidence is not evidence of
    # absence, and collapsing unknown into "did not attend" is exactly the
    # mistake this column exists to prevent.
    STATUS_NEEDS_REVIEW: None,
}


@dataclass(frozen=True)
class Decision:
    """A decision row as the console and CLI display it."""

    decision_id: str
    checkin_id: str
    status: str
    attended: bool | None
    confidence: float | None
    decided_by: str
    rule_name: str | None
    ai_model: str | None
    ai_prompt_version: str | None
    ai_reasoning: str | None
    human_email: str | None
    note: str | None
    created_at: Any


def current_decision(conn: psycopg.Connection, checkin_id: str) -> dict[str, Any] | None:
    return fetch_one(
        conn,
        """
        select * from attendance_decision
         where checkin_id = %s and superseded_at is null
        """,
        (checkin_id,),
    )


def record_decision(
    conn: psycopg.Connection,
    checkin_id: str,
    *,
    status: str,
    decided_by: str,
    confidence: float | None = None,
    rule_name: str | None = None,
    ai_model: str | None = None,
    ai_prompt_version: str | None = None,
    ai_reasoning: str | None = None,
    human_email: str | None = None,
    note: str | None = None,
) -> str:
    """Supersede whatever is current and insert this decision as the new current."""
    if status not in _ATTENDED_BY_STATUS:
        raise ValueError(f"unknown status {status!r}")

    execute(
        conn,
        """
        update attendance_decision
           set superseded_at = now()
         where checkin_id = %s and superseded_at is null
        """,
        (checkin_id,),
    )

    row = fetch_one(
        conn,
        """
        insert into attendance_decision (
            checkin_id, attended, status, confidence, decided_by,
            rule_name, ai_model, ai_prompt_version, ai_reasoning, human_email, note
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        returning decision_id
        """,
        (
            checkin_id,
            _ATTENDED_BY_STATUS[status],
            status,
            confidence,
            decided_by,
            rule_name,
            ai_model,
            ai_prompt_version,
            ai_reasoning,
            (human_email or "").strip().lower() or None,
            note,
        ),
    )
    assert row is not None
    return str(row["decision_id"])


def human_override(
    conn: psycopg.Connection,
    checkin_id: str,
    *,
    status: str,
    by_email: str,
    note: str | None = None,
) -> str:
    """Tier 3. A person looked at it; their answer is the answer.

    Confidence is 1.0 by construction — not because humans are infallible, but
    because a human decision is not a probabilistic estimate of anything. It is
    the ground truth this system is trying to approximate.
    """
    decision_id = record_decision(
        conn,
        checkin_id,
        status=status,
        decided_by="human",
        confidence=1.0,
        human_email=by_email,
        note=note,
    )
    log.info(
        "human decision checkin=%s status=%s by=%s", checkin_id, status, mask_email(by_email)
    )
    return decision_id


def decision_history(conn: psycopg.Connection, checkin_id: str) -> list[dict[str, Any]]:
    """Every decision ever made about one check-in, newest first."""
    from .db import fetch_all

    return fetch_all(
        conn,
        """
        select * from attendance_decision
         where checkin_id = %s
         order by created_at desc, superseded_at desc nulls first
        """,
        (checkin_id,),
    )
