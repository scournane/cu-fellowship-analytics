"""Running the three tiers over a cohort.

Ordering rules that are not negotiable:

* A **human decision is never superseded** by a rule or a model. Re-running
  adjudication skips any check-in whose current decision has
  ``decided_by='human'``. ``--force`` overrides that and says out loud exactly
  what it is about to overwrite.
* **Tier 2 never crashes the run.** No key, no network, quota exhausted — the
  case is written as ``needs_review`` with ``rule_name='ai_unavailable'`` and the
  pipeline finishes. A pipeline that stops because an optional model was
  unreachable would make the model mandatory in practice.
* **``needs_review`` is never turned into ``not_attended``.** Not by tier 1, not
  by tier 2, not by a later pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import psycopg

from ..config import Settings, get_settings
from ..db import fetch_all
from ..errors import AiUnavailable
from ..latency import recompute_for_cohort
from ..logging_setup import get_logger
from ..decisions import current_decision, record_decision
from .ai import PROMPT_VERSION, Adjudicator, build_adjudicator, judge_with_cache
from .rules import apply_rules

log = get_logger(__name__)


@dataclass
class AdjudicationResult:
    """Counts for one adjudication pass."""

    examined: int = 0
    decided_by_rule: int = 0
    decided_by_ai: int = 0
    escalated: int = 0
    needs_review: int = 0
    unchanged: int = 0
    human_preserved: int = 0
    human_overwritten: int = 0
    ai_calls: int = 0
    ai_cache_hits: int = 0
    ai_unavailable: int = 0
    warnings: list[str] = field(default_factory=list)

    def __str__(self) -> str:  # pragma: no cover - display only
        from ..logging_setup import summarize

        return summarize(
            examined=self.examined,
            rule=self.decided_by_rule,
            ai=self.decided_by_ai,
            needs_review=self.needs_review,
            unchanged=self.unchanged,
            human_kept=self.human_preserved,
            ai_calls=self.ai_calls,
            cache_hits=self.ai_cache_hits,
        )


def _checkins_for_cohort(conn: psycopg.Connection, cohort_id: str) -> list[dict[str, Any]]:
    """Every check-in belonging to a cohort, including unmatched ones.

    A check-in with no session has no cohort of its own, so the load run's
    cohort carries it. Without that, exactly the rows that failed to match a
    session — the ones worth looking at — would be invisible to adjudication.
    """
    return fetch_all(
        conn,
        """
        select c.checkin_id, c.passphrase_match, c.session_match, c.passphrase_raw,
               c.submitted_at_utc, s.passphrase as expected_passphrase, s.title as session_title
          from checkin c
          left join "session" s on s.session_id = c.session_id
          left join load_run lr on lr.load_id = c.load_id
         where s.cohort_id = %s or lr.cohort_id = %s
         order by c.submitted_at_utc, c.checkin_id
        """,
        (cohort_id, cohort_id),
    )


def _is_same_decision(current: dict[str, Any] | None, **candidate: Any) -> bool:
    """True when re-deciding would produce a byte-identical judgment.

    Without this, every re-run would append a new row that says exactly what the
    previous row said, and the decision history — the thing that makes an
    override auditable — would fill with noise.
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
    use_ai: bool = True,
    force: bool = False,
    adjudicator: Adjudicator | None = None,
    settings: Settings | None = None,
) -> AdjudicationResult:
    """Decide (or re-decide) every check-in in a cohort."""
    settings = settings or get_settings()
    result = AdjudicationResult()

    recompute_for_cohort(conn, cohort_id)

    tier2: Adjudicator | None = None
    if use_ai:
        tier2 = adjudicator if adjudicator is not None else build_adjudicator(settings)
        if tier2 is None:
            log.info(
                "tier 2 disabled for this run; mismatch cases will be written as "
                "needs_review with rule_name='ai_unavailable'"
            )

    ai_calls_remaining = settings.ai_max_calls_per_run

    for row in _checkins_for_cohort(conn, cohort_id):
        result.examined += 1
        checkin_id = str(row["checkin_id"])
        current = current_decision(conn, checkin_id)

        if current is not None and current["decided_by"] == "human":
            if not force:
                result.human_preserved += 1
                continue
            warning = (
                f"--force is overwriting a HUMAN decision: checkin={checkin_id} "
                f"status={current['status']!r} "
                f"by={current.get('human_email') or '(unknown)'} "
                f"note={(current.get('note') or '')[:80]!r}"
            )
            result.warnings.append(warning)
            log.warning("%s", warning)
            result.human_overwritten += 1

        outcome = apply_rules(row["passphrase_match"], row["session_match"])

        if not outcome.escalates:
            candidate = {
                "status": outcome.status,
                "decided_by": "rule",
                "rule_name": outcome.rule_name,
                "confidence": outcome.confidence,
            }
            if _is_same_decision(current, **candidate):
                result.unchanged += 1
                if outcome.status == "needs_review":
                    result.needs_review += 1
                continue
            record_decision(conn, checkin_id, **candidate)
            result.decided_by_rule += 1
            if outcome.status == "needs_review":
                result.needs_review += 1
            continue

        # --- tier 2 ---------------------------------------------------------
        result.escalated += 1
        expected = row["expected_passphrase"] or ""
        submitted = row["passphrase_raw"] or ""

        if tier2 is None or ai_calls_remaining <= 0:
            reason = (
                "tier 2 unavailable"
                if tier2 is None
                else f"tier 2 call cap of {settings.ai_max_calls_per_run} reached for this run"
            )
            candidate = {
                "status": "needs_review",
                "decided_by": "rule",
                "rule_name": "ai_unavailable",
                "confidence": None,
                "note": reason,
            }
            if _is_same_decision(
                current, status="needs_review", decided_by="rule", rule_name="ai_unavailable"
            ):
                result.unchanged += 1
            else:
                record_decision(conn, checkin_id, **candidate)
            result.needs_review += 1
            result.ai_unavailable += 1
            continue

        try:
            verdict = judge_with_cache(conn, tier2, expected, submitted)
        except AiUnavailable as exc:
            # Degrade for the rest of the run rather than retrying per row: if
            # the key is bad or quota is gone, every remaining call fails too.
            log.warning("tier 2 became unavailable mid-run: %s", exc)
            tier2 = None
            record_decision(
                conn,
                checkin_id,
                status="needs_review",
                decided_by="rule",
                rule_name="ai_unavailable",
                note=str(exc)[:500],
            )
            result.needs_review += 1
            result.ai_unavailable += 1
            continue

        if verdict.cached:
            result.ai_cache_hits += 1
        else:
            result.ai_calls += 1
            ai_calls_remaining -= 1

        if verdict.heard_the_passphrase:
            status = "attended"
        else:
            # Deliberately NOT not_attended. The model judged one thing — whether
            # the answer shows they heard the word — and a wrong answer from a
            # verified address inside the session window is not proof of absence.
            # A person decides this one.
            status = "needs_review"
            result.needs_review += 1

        candidate = {
            "status": status,
            "decided_by": "ai",
            "confidence": round(float(verdict.confidence), 3),
            "ai_model": tier2.model_name,
            "ai_prompt_version": PROMPT_VERSION,
            "ai_reasoning": verdict.reasoning,
        }
        if _is_same_decision(current, **candidate):
            result.unchanged += 1
            continue
        record_decision(conn, checkin_id, **candidate)
        result.decided_by_ai += 1

    log.info("adjudicate cohort=%s %s", cohort_id, result)
    return result
