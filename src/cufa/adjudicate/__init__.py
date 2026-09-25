"""Two tiers: the timing rules, then a person. No model judges attendance."""

from .rules import OUTSIDE_WINDOW_RULES, RULES, RuleOutcome, apply_rules
from .engine import AdjudicationResult, adjudicate_cohort, legacy_counts

__all__ = [
    "OUTSIDE_WINDOW_RULES",
    "RULES",
    "RuleOutcome",
    "apply_rules",
    "AdjudicationResult",
    "adjudicate_cohort",
    "legacy_counts",
]
