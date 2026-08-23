"""Tier 1: deterministic rules over the observed comparison.

Every outcome here is reproducible from the row alone — no model, no clock, no
network. That matters more than it sounds: it means re-running adjudication over
history after changing the definition of "attended" produces the same answers
for the same inputs, so a changed definition is a real experiment rather than a
new roll of the dice.

The escalation rule is the interesting one. ``mismatch`` inside a session window
is *not* decided here, because edit distance genuinely cannot read
``"the word was justice"`` or ``"sorry I missed it"`` — it scores both as
nonsense, and a human reads both instantly.
"""

from __future__ import annotations

from dataclasses import dataclass

ESCALATE = "escalate"


@dataclass(frozen=True)
class RuleOutcome:
    """What tier 1 concluded, and under which name."""

    status: str  # 'attended' | 'not_attended' | 'needs_review' | 'escalate'
    rule_name: str | None
    confidence: float | None

    @property
    def escalates(self) -> bool:
        return self.status == ESCALATE


# (passphrase_match, in_window) -> outcome.
RULES: dict[tuple[str, bool], RuleOutcome] = {
    ("exact", True): RuleOutcome("attended", "exact_match", 1.0),
    ("fuzzy", True): RuleOutcome("attended", "fuzzy_match", 0.9),
    # A session with no passphrase set still had people in it. The timestamp is
    # weaker evidence on its own, which is what the 0.7 says.
    ("not_set", True): RuleOutcome("attended", "no_passphrase_required", 0.7),
    ("mismatch", True): RuleOutcome(ESCALATE, None, None),
}

# A submission that matched no session window is evidence of a submission, not
# of attendance at any particular lesson. 0.6 rather than 1.0 because the
# commonest cause is a misconfigured session time, not a fellow who was absent.
NO_SESSION_OUTCOME = RuleOutcome("not_attended", "outside_all_windows", 0.6)

# A timestamp inside two overlapping windows cannot be attributed to either.
# This is a scheduling bug, and answering it either way would hide the bug.
AMBIGUOUS_OUTCOME = RuleOutcome("needs_review", "ambiguous_session", None)


def apply_rules(passphrase_match: str, session_match: str) -> RuleOutcome:
    """Map an observation to a tier 1 outcome."""
    if session_match == "ambiguous":
        return AMBIGUOUS_OUTCOME
    if passphrase_match == "no_session" or session_match == "none":
        return NO_SESSION_OUTCOME

    outcome = RULES.get((passphrase_match, True))
    if outcome is not None:
        return outcome

    # Unreachable for the five defined passphrase outcomes; a new one added
    # later should surface for a human rather than default to a guess.
    return RuleOutcome("needs_review", "unhandled_observation", None)
