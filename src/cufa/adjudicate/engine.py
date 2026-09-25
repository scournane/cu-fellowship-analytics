"""Running the rules, then a person, over a cohort.

Ordering rules that are not negotiable:

* A **human decision is never superseded** by a rule. Re-running adjudication
  skips any check-in whose current decision has ``decided_by='human'``.
  ``--force`` overrides that and says out loud exactly what it is about to
  overwrite.
* **No model takes part.** The rules read the source, the session match and
  the submission time; the exit-ticket answers are never read here. The
  passphrase era's model tier is gone, and nothing in this package imports a
  model client.
* **Passphrase-era decisions are frozen.** A check-in with a
  ``passphrase_match`` was judged under the old definition, and a staff member
  may have acted on it. When it already has a current decision it is left
  alone unless ``--redecide-legacy`` asks for it to be re-judged by timing.
  One without a decision is judged by the rules like any other row.
* **needs_review only ever comes from evidence the rules cannot read** — an
  unverified address, overlapping windows — and re-running over that same
  evidence yields needs_review again. What can change a pending decision is a
  changed schedule, which is a changed input: the new decision's note names
  the window it was judged against, so the history shows why it moved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg

from ..db import fetch_all, fetch_one
from ..decisions import current_decision, record_decision
from ..latency import recompute_for_cohort
from ..logging_setup import get_logger
from ..timeutil import iso_utc, session_window, to_utc
from .rules import apply_rules

log = get_logger(__name__)


@dataclass
class AdjudicationResult:
    """Counts for one adjudication pass."""

    examined: int = 0
    decided_by_rule: int = 0
    needs_review: int = 0
    outside_window: int = 0
    unchanged: int = 0
    human_preserved: int = 0
    human_overwritten: int = 0
    #: Passphrase-era check-ins left on their existing decision.
    legacy_frozen: int = 0
    #: Passphrase-era check-ins re-judged because --redecide-legacy asked.
    legacy_redecided: int = 0
    warnings: list[str] = field(default_factory=list)

    def __str__(self) -> str:  # pragma: no cover - display only
        from ..logging_setup import summarize

        counts: dict[str, int] = dict(
            examined=self.examined,
            rule=self.decided_by_rule,
            needs_review=self.needs_review,
            outside_window=self.outside_window,
            unchanged=self.unchanged,
            human_kept=self.human_preserved,
        )
        if self.human_overwritten:
            counts["human_overwritten"] = self.human_overwritten
        if self.legacy_frozen:
            counts["legacy_frozen"] = self.legacy_frozen
        if self.legacy_redecided:
            counts["legacy_redecided"] = self.legacy_redecided
        return summarize(**counts)


def _checkins_for_cohort(conn: psycopg.Connection, cohort_id: str) -> list[dict[str, Any]]:
    """Every check-in belonging to a cohort, including unmatched ones.

    A check-in with no session has no cohort of its own, so the load run's
    cohort carries it. Without that, exactly the rows that failed to match a
    session — the ones worth looking at — would be invisible to adjudication.

    The schedule is read from ``session`` as it is *now*, not as it was at
    ingest: fixing a session's time is how a wrongly-entered schedule gets
    corrected, and the decisions should follow.
    """
    return fetch_all(
        conn,
        """
        select c.checkin_id, c.source, c.session_match, c.submitted_at_utc,
               c.passphrase_match, s.title as session_title,
               s.scheduled_at_utc, s.duration_minutes, s.grace_minutes
          from checkin c
          left join "session" s on s.session_id = c.session_id
          left join load_run lr on lr.load_id = c.load_id
         where s.cohort_id = %s or lr.cohort_id = %s
         order by c.submitted_at_utc, c.checkin_id
        """,
        (cohort_id, cohort_id),
    )


def window_for(row: dict[str, Any]) -> tuple[datetime, datetime] | None:
    """The matched session's window, or None when there is no session."""
    if row.get("scheduled_at_utc") is None:
        return None
    return session_window(
        row["scheduled_at_utc"], row["duration_minutes"], row["grace_minutes"]
    )


def window_note(window: tuple[datetime, datetime]) -> str:
    """``window 2026-09-15T22:45:00Z..2026-09-16T00:45:00Z``.

    Written on every decision that was judged against a window, so a decision
    that moved after a reschedule says which window moved it.
    """
    start, end = window
    return f"window {iso_utc(start)}..{iso_utc(end)}"


def legacy_counts(conn: psycopg.Connection, cohort_id: str) -> dict[str, int]:
    """Passphrase-era check-ins with a current decision, by who decided.

    What ``--redecide-legacy`` is about to touch, so the CLI can say so before
    it does anything — the same courtesy ``--force`` extends to human
    decisions. Human decisions are counted separately because the flag alone
    does not reach them: a person's call on a passphrase-era row moves only
    with ``--force`` as well.
    """
    row = fetch_one(
        conn,
        """
        select count(*)                                       as total,
               count(*) filter (where d.decided_by = 'rule')  as rule,
               count(*) filter (where d.decided_by = 'ai')    as ai,
               count(*) filter (where d.decided_by = 'human') as human
          from checkin c
          join v_current_decision d on d.checkin_id = c.checkin_id
          left join "session" s on s.session_id = c.session_id
          left join load_run lr on lr.load_id = c.load_id
         where (s.cohort_id = %s or lr.cohort_id = %s)
           and c.passphrase_match is not null
        """,
        (cohort_id, cohort_id),
    ) or {}
    return {key: int(row.get(key) or 0) for key in ("total", "rule", "ai", "human")}


def _is_same_decision(current: dict[str, Any] | None, **candidate: Any) -> bool:
    """True when re-deciding would produce a byte-identical judgment.

    Without this, every re-run would append a new row that says exactly what the
    previous row said, and the decision history — the thing that makes an
    override auditable — would fill with noise. The note is compared too: it
    names the window, so an unchanged window is a no-op and a moved one is not.
    """
    if current is None:
        return False
    for key, value in candidate.items():
        existing = current.get(key)
        if key == "confidence":
            a = None if existing is None else round(float(existing), 3)
            b = None if value is None else round(float(value), 3)
            if a != b:
                return False
        elif existing != value:
            return False
    return True


def adjudicate_cohort(
    conn: psycopg.Connection,
    cohort_id: str,
    *,
    force: bool = False,
    redecide_legacy: bool = False,
) -> AdjudicationResult:
    """Decide (or re-decide) every check-in in a cohort."""
    result = AdjudicationResult()

    recompute_for_cohort(conn, cohort_id)

    for row in _checkins_for_cohort(conn, cohort_id):
        result.examined += 1
        checkin_id = str(row["checkin_id"])
        current = current_decision(conn, checkin_id)

        legacy = row["passphrase_match"] is not None
        human = current is not None and current["decided_by"] == "human"

        # Each flag opens one door. --force reaches human decisions and
        # --redecide-legacy reaches passphrase-era ones; a person's decision on
        # a passphrase-era row needs both.
        if human and not force:
            result.human_preserved += 1
            continue
        if legacy and current is not None and not redecide_legacy:
            result.legacy_frozen += 1
            continue
        if human:
            warning = (
                f"--force is overwriting a HUMAN decision: checkin={checkin_id} "
                f"status={current['status']!r} "
                f"by={current.get('human_email') or '(unknown)'} "
                f"note={(current.get('note') or '')[:80]!r}"
            )
            result.warnings.append(warning)
            log.warning("%s", warning)
            result.human_overwritten += 1

        window = window_for(row) if row["session_match"] == "matched" else None
        in_window: bool | None = None
        if window is not None:
            stamp = to_utc(row["submitted_at_utc"])
            in_window = window[0] <= stamp <= window[1]

        outcome = apply_rules(row["source"], row["session_match"], in_window)
        candidate = {
            "status": outcome.status,
            "decided_by": "rule",
            "rule_name": outcome.rule_name,
            "confidence": outcome.confidence,
            "note": window_note(window) if window is not None else None,
        }

        if outcome.status == "needs_review":
            result.needs_review += 1
        elif outcome.status == "not_attended":
            result.outside_window += 1

        if _is_same_decision(current, **candidate):
            result.unchanged += 1
            continue
        record_decision(conn, checkin_id, **candidate)
        result.decided_by_rule += 1
        if legacy and current is not None:
            result.legacy_redecided += 1

    log.info("adjudicate cohort=%s %s", cohort_id, result)
    return result
