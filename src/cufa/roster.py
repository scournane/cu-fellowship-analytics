"""Bulk CSV loading for the roster and for sessions.

The console is the primary way staff create sessions, but everything the
console does has to be doable from the CLI: it keeps the system scriptable, it
keeps it testable, and it keeps it usable on the day the web app breaks.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg

from .db import execute, fetch_all, fetch_one
from .logging_setup import get_logger, summarize
from .sessions import SessionInput, create_session
from .text import normalize_email

log = get_logger(__name__)


@dataclass(frozen=True)
class LoadSummary:
    read: int
    written: int
    skipped: int
    #: Said once per file, not once per row: a column the loader ignores is a
    #: fact about the file, and thirty copies of it would bury the one line
    #: that matters.
    warnings: tuple[str, ...] = ()

    def __str__(self) -> str:  # pragma: no cover - display only
        return summarize(read=self.read, written=self.written, skipped=self.skipped)


def _headers(row: dict[str, Any]) -> dict[str, str]:
    """Map lowercased/stripped header -> original, so column order and case vary freely."""
    return {(k or "").strip().lower(): k for k in row}


def _pick(row: dict[str, Any], headers: dict[str, str], *names: str) -> str:
    for name in names:
        key = headers.get(name)
        if key is not None and row.get(key) not in (None, ""):
            return str(row[key]).strip()
    return ""


def ensure_cohort(conn: psycopg.Connection, cohort_id: str, label: str | None = None) -> None:
    """Create the cohort if it does not exist, so a load never fails on FK."""
    execute(
        conn,
        """
        insert into cohort (cohort_id, label)
        values (%s, %s)
        on conflict (cohort_id) do nothing
        """,
        (cohort_id, label or cohort_id),
    )


@dataclass(frozen=True)
class RosterCheck:
    """What a CSV would do, worked out before anything is written.

    `load_roster` upserts row by row, so a file with a good first half and a
    bad second half half-applies and reports a count. That is fine at a
    terminal, where the person who typed the command can read a traceback and
    re-run. It is not fine on a web form, where the person may have picked the
    wrong spreadsheet out of a folder and has no way to see what went in. So
    the file is read twice: once to say what it contains, and only then to
    write it.
    """

    rows: int
    usable: int
    problems: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.problems and self.usable > 0


def inspect_roster_csv(path: str | Path) -> RosterCheck:
    """Read a roster CSV and describe it, without touching the database.

    Every message here is addressed to somebody who has a spreadsheet open,
    not to somebody who has the schema in their head: it names the column that
    is missing in the words the column would have, and it counts rather than
    raising on the first bad line, because "row 14 is missing an email" is
    only useful alongside "the other 23 are fine".
    """
    problems: list[str] = []
    warnings: list[str] = []
    rows = usable = 0

    try:
        with Path(path).open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            field_names = reader.fieldnames or []
            if not field_names:
                return RosterCheck(0, 0, ["This file has no header row, so there is nothing to read."], [])

            present = {(name or "").strip().lower() for name in field_names}
            if not present & {"fellow_id", "id"}:
                problems.append(
                    "No column named 'fellow_id' (or 'id'). That is the "
                    "identifier the rest of the system uses for a person."
                )
            if not present & {"primary_email", "email"}:
                problems.append(
                    "No column named 'primary_email' (or 'email'). Without an "
                    "address, nothing can be matched to a Google form or a "
                    "Slack account."
                )
            if not present & {"full_name", "name"}:
                warnings.append(
                    "No column named 'full_name' (or 'name'). Everyone will be "
                    "shown by their fellow id until that is corrected."
                )

            for row in reader:
                rows += 1
                headers = _headers(row)
                fellow_id = _pick(row, headers, "fellow_id", "id")
                email = normalize_email(_pick(row, headers, "primary_email", "email"))
                timezone = _pick(row, headers, "timezone", "tz") or None

                if not fellow_id or not email:
                    missing = "id" if not fellow_id else "email address"
                    warnings.append(f"Row {rows} has no {missing}, so it will be skipped.")
                    continue

                if timezone:
                    # The same refusal `load_roster` makes, made early. A typo
                    # here sends somebody's reminders to the wrong hour, and
                    # the loader raises on it mid-file; better to say so now.
                    from .timeutil import get_zone

                    try:
                        get_zone(timezone)
                    except Exception:
                        problems.append(
                            f"Row {rows} has the time zone {timezone!r}, which is not a "
                            "zone name. Use something like 'America/New_York'."
                        )
                        continue

                usable += 1
    except UnicodeDecodeError:
        return RosterCheck(
            0, 0,
            ["This file is not readable as text. Export it from the spreadsheet as CSV and try again."],
            [],
        )
    except csv.Error as exc:
        return RosterCheck(0, 0, [f"This file is not valid CSV ({exc})."], [])

    if rows and not usable and not problems:
        problems.append("Every row is missing an id or an email address, so there is nothing to load.")
    if not rows:
        problems.append("This file has a header row but no people in it.")

    # A long file with a long tail of bad rows produces an unreadable wall.
    if len(warnings) > 12:
        warnings = warnings[:12] + [f"...and {len(warnings) - 12} more rows like this."]

    return RosterCheck(rows, usable, problems, warnings)


def load_roster(conn: psycopg.Connection, path: str | Path, cohort_id: str) -> LoadSummary:
    """Upsert fellows from a CSV.

    Upsert rather than insert: correcting a roster typo and re-running is the
    normal way a mis-attributed check-in gets fixed, and because identity
    resolves at read time, the correction re-attributes history immediately.
    """
    ensure_cohort(conn, cohort_id)
    read = written = skipped = 0

    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            read += 1
            headers = _headers(row)
            fellow_id = _pick(row, headers, "fellow_id", "id")
            email = normalize_email(_pick(row, headers, "primary_email", "email"))
            name = _pick(row, headers, "full_name", "name")
            status = _pick(row, headers, "status") or "active"
            timezone = _pick(row, headers, "timezone", "tz") or None

            if timezone:
                # Refuse a typo at the roster boundary. Quiet hours in a guessed
                # zone are worse than an explicit fallback visible in config.
                from .timeutil import get_zone

                get_zone(timezone)

            if not fellow_id or not email:
                skipped += 1
                log.warning("roster row %d skipped: missing fellow_id or email", read)
                continue

            execute(
                conn,
                """
                insert into fellow (
                    fellow_id, cohort_id, full_name, primary_email, status, timezone
                )
                values (%s, %s, %s, %s, %s, %s)
                on conflict (fellow_id) do update
                   set cohort_id = excluded.cohort_id,
                       full_name = excluded.full_name,
                       primary_email = excluded.primary_email,
                       status = excluded.status,
                       timezone = coalesce(excluded.timezone, fellow.timezone),
                       updated_at = now()
                """,
                (fellow_id, cohort_id, name or fellow_id, email, status, timezone),
            )
            written += 1

    log.info("roster loaded cohort=%s %s", cohort_id, summarize(read=read, written=written, skipped=skipped))
    return LoadSummary(read, written, skipped)


#: Part A had a spoken passphrase until the exit ticket replaced it. Schedules
#: exported from the old template still carry the column, and silently dropping
#: a value someone typed looks like a bug, so both the check and the load say
#: out loud that it is ignored.
PASSPHRASE_COLUMN_IGNORED = (
    "The 'passphrase' column is ignored. Part A no longer uses a passphrase: "
    "attendance is a verified address submitting inside the session window."
)


def inspect_sessions_csv(path: str | Path) -> RosterCheck:
    """The same read-before-write as `inspect_roster_csv`, for a session file.

    `load_sessions` has the same shape of danger and one more besides: a time it
    cannot parse raises out of `_parse_local` partway down the file, and a
    session half-created is a schedule with a hole in it that nobody notices
    until a form does not go out. A schedule is also the thing most likely to be
    typed by hand rather than exported, so the times are the part to check.
    """
    problems: list[str] = []
    warnings: list[str] = []
    rows = usable = 0

    required = (
        ("cohort_id", ("cohort_id", "cohort")),
        ("title", ("title",)),
        ("scheduled_at_local", ("scheduled_at_local", "scheduled_at", "starts_at")),
        ("timezone", ("timezone", "tz")),
        ("duration_minutes", ("duration_minutes", "duration")),
    )

    try:
        with Path(path).open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            field_names = reader.fieldnames or []
            if not field_names:
                return RosterCheck(0, 0, ["This file has no header row, so there is nothing to read."], [])

            present = {(name or "").strip().lower() for name in field_names}
            for label, accepted in required:
                if not present & set(accepted):
                    other = " (or " + ", ".join(repr(a) for a in accepted[1:]) + ")" if len(accepted) > 1 else ""
                    problems.append(f"No column named {label!r}{other}. Every session needs one.")
            if "passphrase" in present:
                warnings.append(PASSPHRASE_COLUMN_IGNORED)

            for row in reader:
                rows += 1
                headers = _headers(row)
                title = _pick(row, headers, "title")
                cohort_id = _pick(row, headers, "cohort_id", "cohort")
                local_raw = _pick(row, headers, "scheduled_at_local", "scheduled_at", "starts_at")
                zone = _pick(row, headers, "timezone", "tz")
                duration = _pick(row, headers, "duration_minutes", "duration")
                where = f"Row {rows}" + (f" ({title})" if title else "")

                if not (cohort_id and title and local_raw and zone and duration):
                    warnings.append(f"{where} is missing something it needs, so it will be skipped.")
                    continue

                try:
                    _parse_local(local_raw)
                except ValueError:
                    problems.append(
                        f"{where} has the start time {local_raw!r}, which cannot be read. "
                        "Write it like 2026-09-15 19:00."
                    )
                    continue

                from .timeutil import get_zone

                try:
                    get_zone(zone)
                except Exception:
                    problems.append(
                        f"{where} has the time zone {zone!r}, which is not a zone name. "
                        "Use something like 'America/New_York'."
                    )
                    continue

                try:
                    if int(duration) <= 0:
                        raise ValueError
                except ValueError:
                    problems.append(
                        f"{where} has a length of {duration!r}. It should be a number of minutes, like 60."
                    )
                    continue

                usable += 1
    except UnicodeDecodeError:
        return RosterCheck(
            0, 0,
            ["This file is not readable as text. Export it from the spreadsheet as CSV and try again."],
            [],
        )
    except csv.Error as exc:
        return RosterCheck(0, 0, [f"This file is not valid CSV ({exc})."], [])

    if not rows:
        problems.append("This file has a header row but no sessions in it.")
    elif not usable and not problems:
        problems.append("Every row is missing something it needs, so there is nothing to create.")

    if len(warnings) > 12:
        warnings = warnings[:12] + [f"...and {len(warnings) - 12} more rows like this."]

    return RosterCheck(rows, usable, problems, warnings)


def load_sessions(conn: psycopg.Connection, path: str | Path) -> LoadSummary:
    """Create sessions from a CSV. Existing (cohort, title, time) rows are skipped.

    A ``passphrase`` column is accepted and ignored, and the returned summary's
    ``warnings`` says so once for the file.
    """
    read = written = skipped = 0
    warnings: list[str] = []

    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if "passphrase" in {(name or "").strip().lower() for name in reader.fieldnames or []}:
            warnings.append(PASSPHRASE_COLUMN_IGNORED)
        for row in reader:
            read += 1
            headers = _headers(row)
            cohort_id = _pick(row, headers, "cohort_id", "cohort")
            title = _pick(row, headers, "title")
            local_raw = _pick(row, headers, "scheduled_at_local", "scheduled_at", "starts_at")
            zone = _pick(row, headers, "timezone", "tz")
            duration = _pick(row, headers, "duration_minutes", "duration")
            grace = _pick(row, headers, "grace_minutes", "grace") or "15"
            week_raw = _pick(row, headers, "week_index", "week")
            teacher_question = _pick(row, headers, "teacher_question")
            zoom_url = _pick(row, headers, "zoom_url", "zoom", "meeting_url")
            agenda = _pick(row, headers, "agenda")
            slack_channel_id = _pick(
                row, headers, "slack_channel_id", "slack_channel"
            )

            if not (cohort_id and title and local_raw and zone and duration):
                skipped += 1
                log.warning(
                    "session row %d skipped: needs cohort_id, title, "
                    "scheduled_at_local, timezone and duration_minutes",
                    read,
                )
                continue

            ensure_cohort(conn, cohort_id)
            local = _parse_local(local_raw)

            week: int | None = None
            if week_raw:
                try:
                    week = int(week_raw)
                except ValueError:
                    # A bad week number is not worth losing the session over —
                    # Part A does not need it, and Part B refuses loudly later
                    # rather than guessing.
                    log.warning(
                        "session row %d has week_index=%r, which is not a whole "
                        "number; the session is created without one",
                        read,
                        week_raw,
                    )

            existing = fetch_one(
                conn,
                """
                select session_id from "session"
                 where cohort_id = %s and title = %s and scheduled_at_local = %s
                """,
                (cohort_id, title, local.replace(tzinfo=None)),
            )
            if existing:
                skipped += 1
                continue

            create_session(
                conn,
                SessionInput(
                    cohort_id=cohort_id,
                    title=title,
                    scheduled_at_local=local,
                    timezone=zone,
                    duration_minutes=int(duration),
                    grace_minutes=int(grace),
                    week_index=week,
                    teacher_question=teacher_question or None,
                    zoom_url=zoom_url or None,
                    agenda=agenda or None,
                    slack_channel_id=slack_channel_id or None,
                ),
            )
            written += 1

    log.info("sessions loaded %s", summarize(read=read, written=written, skipped=skipped))
    return LoadSummary(read, written, skipped, tuple(warnings))


def list_fellows(
    conn: psycopg.Connection, cohort_id: str | None = None
) -> list[dict[str, Any]]:
    """The roster, for a screen to show. Ordered by name so it reads as a list."""
    return fetch_all(
        conn,
        """
        select fellow_id, cohort_id, full_name, primary_email, status, timezone
          from fellow
         where (%s::text is null or cohort_id = %s::text)
         order by full_name, fellow_id
        """,
        (cohort_id, cohort_id),
    )


def set_fellow_timezone(
    conn: psycopg.Connection, fellow_id: str, timezone: str | None
) -> bool:
    """Set (or clear) one fellow's IANA zone.

    The same validation the CSV path applies, in the same place, so a zone typed
    into the console cannot be one the loader would have rejected. A blank value
    clears it — a fellow with no zone is legal, and reminders fall back to the
    session's zone.
    """
    zone = (timezone or "").strip() or None
    if zone is not None:
        from .timeutil import get_zone

        get_zone(zone)  # raises TimezoneError on an unknown name
    return bool(
        execute(
            conn,
            "update fellow set timezone = %s, updated_at = now() where fellow_id = %s",
            (zone, fellow_id),
        )
    )


def _parse_local(value: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"could not parse session time {value!r}; expected '2026-09-15 19:00'")
