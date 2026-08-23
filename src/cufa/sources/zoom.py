"""Zoom attendance.

Reads the participant CSV that Zoom's usage report exports. Attendance is
matched against a roster of who was expected, so that a Fellow who did not
appear produces a `live_lesson_absent` event rather than silence -- absence
has to be a fact in the data, not the absence of a fact.
"""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from ..model import RawEvent

# Zoom writes local wall-clock times without an offset in this export.
ZOOM_TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%b %d, %Y %I:%M:%S %p")

# Below this many minutes a join is treated as a drop-in, not attendance.
MIN_ATTENDANCE_MINUTES = 10.0


def _parse_time(raw: str, tz: dt.tzinfo) -> dt.datetime:
    raw = raw.strip()
    for fmt in ZOOM_TIME_FORMATS:
        try:
            return dt.datetime.strptime(raw, fmt).replace(tzinfo=tz).astimezone(dt.timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"unrecognised Zoom timestamp: {raw!r}")


def read(path: Path | str, *, tz: dt.tzinfo = dt.timezone.utc,
         expected: dict[str, dt.date] | None = None) -> list[RawEvent]:
    """Parse one Zoom participant export.

    `expected` maps Zoom user id (email) -> the date that person joined the
    cohort. Anyone expected by the session date with no qualifying join gets
    an absence event; anyone who had not joined yet is skipped, so the record
    never asserts that somebody missed a lesson they were not yet invited to.
    """
    path = Path(path)
    seen: dict[str, float] = {}
    first_join: dict[str, dt.datetime] = {}

    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            key = (row.get("User Email") or row.get("Email") or "").strip().lower()
            if not key:
                continue
            minutes = float(row.get("Duration (Minutes)") or row.get("Duration") or 0)
            joined = _parse_time(row.get("Join Time") or row.get("Join time"), tz)
            # Zoom emits one row per join; someone who drops and rejoins
            # appears repeatedly. Sum the minutes, keep the earliest join.
            seen[key] = seen.get(key, 0.0) + minutes
            if key not in first_join or joined < first_join[key]:
                first_join[key] = joined

    events: list[RawEvent] = []
    session = path.stem
    for key, minutes in seen.items():
        if minutes < MIN_ATTENDANCE_MINUTES:
            continue
        events.append(RawEvent("zoom", key, "live_lesson_attended", first_join[key],
                               meta={"session": session, "minutes": round(minutes, 1)}))

    if expected:
        attended = {e.source_user_id for e in events}
        # Absences are stamped at the session's start so they land in the
        # right week even when nobody from the cohort showed up at all.
        stamp = min(first_join.values()) if first_join else None
        if stamp is not None:
            session_date = stamp.astimezone(tz).date()
            for key, joined_on in expected.items():
                key = key.strip().lower()
                if not key or key in attended:
                    continue
                if joined_on is not None and joined_on > session_date:
                    continue      # not yet in the cohort; not an absence
                events.append(RawEvent("zoom", key, "live_lesson_absent", stamp,
                                       meta={"session": session}))
    return events
