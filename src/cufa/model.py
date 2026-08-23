"""Canonical data model.

Every source is normalised into the same shape before anything is computed:
a RawEvent from a source system, resolved into an Event against a Fellow.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field

# Event kinds the pipeline understands. A source emitting anything else is a
# bug in that adapter, not a new feature.
KINDS = (
    "live_lesson_attended",
    "live_lesson_absent",
    "slack_message",
    "slack_reaction",
    "assignment_submitted",
    "assignment_missed",
)

SOURCES = ("zoom", "slack", "sheets", "hubspot", "manual")


def week_id(when: dt.datetime) -> str:
    """ISO year-week label, e.g. '2026-W35'. Weeks start Monday."""
    iso = when.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def week_start(wid: str) -> dt.date:
    """First day (Monday) of an ISO week label."""
    year, week = wid.split("-W")
    return dt.date.fromisocalendar(int(year), int(week), 1)


@dataclass(frozen=True)
class Fellow:
    """A person in the cohort.

    `fellow_id` is internal and stable. It is what the rest of the system
    joins on, so that a Fellow changing their Slack handle or Zoom display
    name does not fracture their history.
    """
    fellow_id: str
    display_name: str
    cohort: str
    joined_on: dt.date
    status: str = "active"          # active | withdrawn | completed

    @property
    def initials(self) -> str:
        parts = [p for p in self.display_name.split() if p]
        return "".join(p[0].upper() for p in parts[:2]) or "?"


@dataclass(frozen=True)
class Identity:
    """Maps a per-system user id onto a Fellow.

    Zoom ids, Slack member ids and spreadsheet emails are three different
    namespaces. This is the seam where they are reconciled, and the only
    place that reconciliation happens.
    """
    source: str
    source_user_id: str
    fellow_id: str


@dataclass(frozen=True)
class RawEvent:
    """What a source adapter emits, before identity resolution."""
    source: str
    source_user_id: str
    kind: str
    occurred_at: dt.datetime
    value: float = 1.0
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            raise ValueError(f"unknown source {self.source!r}")
        if self.kind not in KINDS:
            raise ValueError(f"unknown event kind {self.kind!r}")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware (store UTC)")


@dataclass(frozen=True)
class Event:
    """A RawEvent successfully attached to a Fellow."""
    event_id: str
    fellow_id: str
    source: str
    kind: str
    occurred_at: dt.datetime
    week: str
    value: float
    meta: dict = field(default_factory=dict)

    @staticmethod
    def make(fellow_id: str, raw: RawEvent) -> "Event":
        # Deterministic id: re-ingesting the same export twice is a no-op
        # rather than a doubling of everyone's score.
        digest = hashlib.sha256(
            "|".join([
                fellow_id, raw.source, raw.kind,
                raw.occurred_at.isoformat(),
                json.dumps(raw.meta, sort_keys=True, default=str),
            ]).encode()
        ).hexdigest()[:16]
        return Event(
            event_id=digest,
            fellow_id=fellow_id,
            source=raw.source,
            kind=raw.kind,
            occurred_at=raw.occurred_at,
            week=week_id(raw.occurred_at),
            value=raw.value,
            meta=dict(raw.meta),
        )


@dataclass(frozen=True)
class Unresolved:
    """A RawEvent whose source_user_id matched no Fellow.

    Kept rather than dropped. A rising unresolved count is the earliest
    signal that the identity map has drifted from reality.
    """
    source: str
    source_user_id: str
    kind: str
    occurred_at: dt.datetime
    reason: str = "no identity mapping"
