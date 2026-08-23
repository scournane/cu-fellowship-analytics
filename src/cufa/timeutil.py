"""Time handling. UTC past the parser boundary, without exception.

The one place a local timezone is allowed to exist is the CSV parser, where the
spreadsheet's zone is supplied explicitly by the operator. Everything past that
line is timezone-aware UTC — the Google Sheets export writes wall-clock times
with no offset marker, and reading those as UTC shifts every check-in by hours
while looking entirely plausible.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc

_NAIVE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
)


class TimezoneError(ValueError):
    """The supplied IANA timezone name is not one zoneinfo knows."""


def get_zone(name: str) -> ZoneInfo:
    """Resolve an IANA name, with a message that names the flag that set it."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise TimezoneError(
            f"{name!r} is not a known IANA timezone. Use a name like "
            f"'America/New_York' or 'UTC' (see --sheet-timezone)."
        ) from exc


def to_utc(value: datetime) -> datetime:
    """Convert an aware datetime to UTC. Naive input is a programming error."""
    if value.tzinfo is None:
        raise ValueError(
            "Refusing to convert a naive datetime: the zone has to be supplied "
            "explicitly, because guessing it is how every timestamp silently shifts."
        )
    return value.astimezone(UTC)


def parse_rfc3339(value: str) -> datetime:
    """Parse an RFC3339 timestamp from the Forms API into aware UTC.

    The API returns e.g. ``2026-09-15T17:05:03.123Z``. Python's fromisoformat
    handles the offset form directly; the trailing ``Z`` is normalized first.
    """
    text = value.strip()
    if not text:
        raise ValueError("empty timestamp")
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        # An RFC3339 timestamp without an offset is malformed; the Forms API does
        # not emit one, so this means the input is not what we think it is.
        raise ValueError(f"timestamp {value!r} has no UTC offset")
    return parsed.astimezone(UTC)


def parse_local_naive(value: str, zone_name: str) -> tuple[datetime, datetime]:
    """Parse a spreadsheet wall-clock string in ``zone_name``.

    Returns ``(local_aware, utc)``. DST is handled by zoneinfo: a time in the
    spring-forward gap or the fall-back overlap resolves the way Python resolves
    it (fold=0, i.e. the first of two ambiguous instants), which is what a
    Sheets export of a real submission corresponds to.
    """
    zone = get_zone(zone_name)
    text = value.strip()

    parsed: datetime | None = None
    try:
        candidate = datetime.fromisoformat(text)
        parsed = candidate if candidate.tzinfo is None else None
        if candidate.tzinfo is not None:
            # The export already carried an offset. Trust it over the flag —
            # explicit beats supplied — but still return both halves.
            return candidate, candidate.astimezone(UTC)
    except ValueError:
        parsed = None

    if parsed is None:
        for fmt in _NAIVE_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue

    if parsed is None:
        raise ValueError(
            f"could not parse timestamp {value!r}; expected something like "
            f"'2026-09-15 13:05:00'"
        )

    local = parsed.replace(tzinfo=zone)
    return local, local.astimezone(UTC)


def iso_utc(value: datetime) -> str:
    """Render an aware datetime as ``...Z``, the form Google's filters expect."""
    return to_utc(value).isoformat().replace("+00:00", "Z")


def session_window(
    scheduled_at_utc: datetime, duration_minutes: int, grace_minutes: int
) -> tuple[datetime, datetime]:
    """The inclusive window a submission must land in to match a session.

    Grace widens both sides: a fellow who submits a few minutes before the
    scheduled start because the teacher began early is present, not absent.
    """
    start = to_utc(scheduled_at_utc) - timedelta(minutes=grace_minutes)
    end = to_utc(scheduled_at_utc) + timedelta(minutes=duration_minutes + grace_minutes)
    return start, end
