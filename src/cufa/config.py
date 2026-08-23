"""Loads config/definitions.toml and enforces the gate.

The gate is the whole idea: a metric declares which terms it depends on, and
if any of those terms is undefined the metric is withheld with a reason
naming the decision and its owner. Nothing silently defaults.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "definitions.toml"


@dataclass(frozen=True)
class Term:
    name: str
    status: str
    definition: str = ""
    owner: str = ""
    decided_on: str = ""
    source: str = ""
    question: str = ""
    needed_from: str = ""
    note: str = ""
    blocks: tuple[str, ...] = ()

    @property
    def confirmed(self) -> bool:
        return self.status == "confirmed"


@dataclass(frozen=True)
class Withheld:
    """Returned in place of a metric that depends on an undefined term."""
    metric: str
    term: str
    question: str
    needed_from: str
    note: str = ""


class Definitions:
    def __init__(self, raw: dict):
        self.meta = raw.get("meta", {})
        self.terms: dict[str, Term] = {}
        for name, body in raw.get("terms", {}).items():
            self.terms[name] = Term(
                name=name,
                status=body.get("status", "undefined"),
                definition=body.get("definition", "").strip(),
                owner=body.get("owner", ""),
                decided_on=body.get("decided_on", ""),
                source=body.get("source", ""),
                question=body.get("question", "").strip(),
                needed_from=body.get("needed_from", ""),
                note=body.get("note", "").strip(),
                blocks=tuple(body.get("blocks", [])),
            )
        part = raw.get("participation", {})
        self.weights: dict[str, float] = part.get("weights", {})
        self.caps: dict[str, float] = part.get("caps", {})
        self.window: dict = part.get("window", {})

    # -- gate ---------------------------------------------------------------

    def confirmed_terms(self) -> list[Term]:
        return [t for t in self.terms.values() if t.confirmed]

    def undefined_terms(self) -> list[Term]:
        return [t for t in self.terms.values() if not t.confirmed]

    def check(self, metric: str, requires: list[str]) -> list[Withheld]:
        """Reasons `metric` may not run. Empty list means it may.

        Returns *every* unmet requirement, not just the first. A metric can be
        blocked by two separate open decisions, and reporting only one of them
        would let the other stay invisible until the first was answered.
        """
        reasons: list[Withheld] = []
        for term_name in requires:
            term = self.terms.get(term_name)
            if term is None:
                reasons.append(Withheld(
                    metric=metric,
                    term=term_name,
                    question=f"Term {term_name!r} is referenced by this metric "
                             f"but does not appear in definitions.toml.",
                    needed_from="unassigned",
                ))
            elif not term.confirmed:
                reasons.append(Withheld(
                    metric=metric,
                    term=term.name,
                    question=term.question,
                    needed_from=term.needed_from or "unassigned",
                    note=term.note,
                ))
        return reasons

    def blocked_metrics(self) -> dict[str, str]:
        """metric id -> the undefined term blocking it."""
        out = {}
        for term in self.undefined_terms():
            for metric in term.blocks:
                out[metric] = term.name
        return out


def load(path: Path | str | None = None) -> Definitions:
    p = Path(path) if path else DEFAULT_PATH
    with open(p, "rb") as fh:
        return Definitions(tomllib.load(fh))
