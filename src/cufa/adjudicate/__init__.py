"""Three tiers: deterministic rules, then a model, then a person."""

from .rules import RULES, RuleOutcome, apply_rules
from .engine import AdjudicationResult, adjudicate_cohort

__all__ = ["RULES", "RuleOutcome", "apply_rules", "AdjudicationResult", "adjudicate_cohort"]
