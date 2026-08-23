"""Assignment submissions from a Google Sheet exported as CSV.

Expected columns: email, assignment, due_on, submitted_at
A blank submitted_at past its due date is a miss, and is recorded as such.
"""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from ..model import RawEvent


def _date(raw: str) -> dt.datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(raw, fmt).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"unrecognised date: {raw!r}")


def read(path: Path | str, *, as_of: dt.datetime | None = None) -> list[RawEvent]:
    as_of = as_of or dt.datetime.now(dt.timezone.utc)
    events: list[RawEvent] = []
    with open(Path(path), newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            email = (row.get("email") or "").strip().lower()
            if not email:
                continue
            assignment = (row.get("assignment") or "").strip()
            submitted = _date(row.get("submitted_at"))
            due = _date(row.get("due_on"))
            if submitted:
                events.append(RawEvent("sheets", email, "assignment_submitted", submitted,
                                       meta={"assignment": assignment,
                                             "late": bool(due and submitted > due)}))
            elif due and due < as_of:
                events.append(RawEvent("sheets", email, "assignment_missed", due,
                                       meta={"assignment": assignment}))
    return events
