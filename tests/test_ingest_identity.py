"""Deliverable 10, tests 1-10: ingest and identity.

The common thread: a submission is an observation, and an observation is never
discarded. Every test here that describes a "bad" input asserts that the row was
still written.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from conftest import TEST_COHORT, TEST_TZ, count, make_fellow, make_session, write_csv

from cufa.ingest.common import assign_session, source_event_id
from cufa.ingest.csv_path import MissingTimezone, ingest_csv
from cufa.ingest.forms_api import pull_session
from cufa.provisioning import provision_session
from cufa.timeutil import UTC, iso_utc, parse_local_naive, parse_rfc3339

HEADERS = ["Timestamp", "Email Address", "Today's passphrase"]


def _rows(*triples: tuple[str, str, str]) -> list[dict[str, str]]:
    return [
        {"Timestamp": ts, "Email Address": email, "Today's passphrase": answer}
        for ts, email, answer in triples
    ]


# --- 1. idempotency --------------------------------------------------------

def test_1_same_input_twice_writes_no_second_row(db, tmp_path):
    make_session(db)
    make_fellow(db)
    path = write_csv(
        tmp_path / "r.csv",
        _rows(
            ("2026-09-15 19:20:00", "ada@example.invalid", "justice"),
            ("2026-09-15 19:21:00", "bob@example.invalid", "justice"),
        ),
        HEADERS,
    )

    first = ingest_csv(db, path, TEST_COHORT, TEST_TZ)
    second = ingest_csv(db, path, TEST_COHORT, TEST_TZ)

    assert first.rows_written == 2
    assert second.rows_written == 0, "a re-run must write zero rows"
    assert second.rows_skipped == 2
    assert count(db, "checkin") == 2


# --- 2. cross-path idempotency --------------------------------------------

def test_2_api_then_csv_does_not_duplicate(db, fake, verified_template, tmp_path):
    """The same response through both doors collides on one key.

    This is why source_event_id hashes the FORM ID rather than the file name,
    and truncates to whole seconds rather than keeping the API's milliseconds.
    """
    session_id = make_session(db, local=datetime(2026, 9, 15, 19, 0))
    make_fellow(db)
    result = provision_session(db, fake, session_id)

    # 19:20 America/New_York == 23:20Z on 2026-09-15 (EDT, UTC-4).
    fake.seed_responses(
        result.form_id, [("ada@example.invalid", "2026-09-15T23:20:00.482Z", "justice")]
    )
    pull_session(db, fake, session_id)
    assert count(db, "checkin") == 1

    path = write_csv(
        tmp_path / "export.csv",
        _rows(("2026-09-15 19:20:00", "ada@example.invalid", "justice")),
        HEADERS,
    )
    csv_result = ingest_csv(db, path, TEST_COHORT, TEST_TZ)

    assert csv_result.rows_written == 0
    assert csv_result.rows_skipped == 1
    assert count(db, "checkin") == 1


# --- 3. row order ----------------------------------------------------------

_ROWS_3 = (
    ("2026-09-15 19:20:00", "ada@example.invalid", "justice"),
    ("2026-09-15 19:21:00", "bob@example.invalid", "justice"),
    ("2026-09-15 19:22:00", "cy@example.invalid", "justice"),
)


def test_3_row_reordering_yields_identical_keys(db, tmp_path):
    """Reordering the same export must not change any row's identity.

    Keying on the row number would make a re-sorted export look like three new
    submissions.
    """
    make_session(db)
    path = write_csv(tmp_path / "r.csv", _rows(*_ROWS_3), HEADERS)
    ingest_csv(db, path, TEST_COHORT, TEST_TZ)
    keys_forward = {
        row["source_event_id"] for row in _all(db, "select source_event_id from checkin")
    }

    write_csv(tmp_path / "r.csv", list(reversed(_rows(*_ROWS_3))), HEADERS)
    second = ingest_csv(db, tmp_path / "r.csv", TEST_COHORT, TEST_TZ)

    assert second.rows_written == 0, "row order must not change the identity of a row"
    assert count(db, "checkin") == 3
    keys_reversed = {
        row["source_event_id"] for row in _all(db, "select source_event_id from checkin")
    }
    assert keys_forward == keys_reversed
    assert len(keys_forward) == 3


def test_3b_renaming_the_export_does_not_duplicate(db, tmp_path):
    """Downloading the same export twice yields `responses (1).csv`.

    That is the commonest real duplicate-import path, so the key must not
    depend on the file name when there is no provisioned form to key on.
    """
    make_session(db)
    first = write_csv(tmp_path / "responses.csv", _rows(*_ROWS_3), HEADERS)
    ingest_csv(db, first, TEST_COHORT, TEST_TZ)

    renamed = write_csv(tmp_path / "responses (1).csv", _rows(*_ROWS_3), HEADERS)
    second = ingest_csv(db, renamed, TEST_COHORT, TEST_TZ)

    assert second.rows_written == 0
    assert count(db, "checkin") == 3
    # Provenance is still per-file, even though identity is not.
    origins = {row["origin"] for row in _all(db, "select origin from load_run")}
    assert len(origins) == 2


def _all(conn, query: str):
    from cufa.db import fetch_all

    return fetch_all(conn, query)


# --- 4. timezone conversion ------------------------------------------------

def test_4_csv_timezone_conversion_is_exact():
    _local, utc = parse_local_naive("2026-09-15 13:05:00", "America/New_York")
    assert iso_utc(utc) == "2026-09-15T17:05:00Z"


def test_4b_conversion_is_recorded_for_audit(db, tmp_path):
    make_session(db, local=datetime(2026, 9, 15, 13, 0))
    path = write_csv(
        tmp_path / "r.csv",
        _rows(("2026-09-15 13:05:00", "ada@example.invalid", "justice")),
        HEADERS,
    )
    ingest_csv(db, path, TEST_COHORT, "America/New_York")

    row = _all(db, "select submitted_at_raw, source_timezone, submitted_at_utc from checkin")[0]
    assert row["submitted_at_raw"] == "2026-09-15 13:05:00"
    assert row["source_timezone"] == "America/New_York"
    assert iso_utc(row["submitted_at_utc"]) == "2026-09-15T17:05:00Z"


# --- 5. DST boundary -------------------------------------------------------

def test_5_dst_boundary_both_sides_correct():
    """2026-11-01 is the US fall-back. 01:30 is EDT (-4), 03:30 is EST (-5)."""
    _, before = parse_local_naive("2026-11-01 01:30:00", "America/New_York")
    _, after = parse_local_naive("2026-11-01 03:30:00", "America/New_York")

    assert iso_utc(before) == "2026-11-01T05:30:00Z"
    assert iso_utc(after) == "2026-11-01T08:30:00Z"
    # Two hours of wall clock, three hours of real time — the whole reason the
    # zone cannot be guessed.
    assert (after - before) == timedelta(hours=3)


def test_5b_dst_rows_both_ingest(db, tmp_path):
    make_session(db, local=datetime(2026, 11, 1, 1, 0), duration=240, grace=60)
    path = write_csv(
        tmp_path / "dst.csv",
        _rows(
            ("2026-11-01 01:30:00", "ada@example.invalid", "justice"),
            ("2026-11-01 03:30:00", "bob@example.invalid", "justice"),
        ),
        HEADERS,
    )
    result = ingest_csv(db, path, TEST_COHORT, "America/New_York")
    assert result.rows_written == 2

    stamps = sorted(
        iso_utc(row["submitted_at_utc"])
        for row in _all(db, "select submitted_at_utc from checkin")
    )
    assert stamps == ["2026-11-01T05:30:00Z", "2026-11-01T08:30:00Z"]


# --- 6. missing --sheet-timezone -------------------------------------------

def test_6_missing_sheet_timezone_errors_and_names_the_flag(db, tmp_path):
    path = write_csv(
        tmp_path / "r.csv",
        _rows(("2026-09-15 19:20:00", "ada@example.invalid", "justice")),
        HEADERS,
    )

    for missing in (None, "", "   "):
        with pytest.raises(MissingTimezone) as excinfo:
            ingest_csv(db, path, TEST_COHORT, missing)
        message = str(excinfo.value)
        assert "--sheet-timezone" in message
        assert "no default" in message

    assert count(db, "checkin") == 0, "nothing may be written on a refused run"


# --- 7. session assignment --------------------------------------------------

def test_7_session_assignment_matched_none_ambiguous_all_write_rows(db, tmp_path):
    make_session(db, title="Main", local=datetime(2026, 9, 15, 19, 0))
    make_session(db, title="Overlapping", local=datetime(2026, 9, 15, 19, 30))
    make_session(db, title="Elsewhere", local=datetime(2026, 10, 20, 19, 0))

    path = write_csv(
        tmp_path / "r.csv",
        _rows(
            # 18:50 is inside Main's grace window only.
            ("2026-09-15 18:50:00", "one@example.invalid", "justice"),
            # 19:45 is inside both Main and Overlapping.
            ("2026-09-15 19:45:00", "two@example.invalid", "justice"),
            # The following afternoon is inside nothing.
            ("2026-09-16 15:00:00", "three@example.invalid", "justice"),
        ),
        HEADERS,
    )
    result = ingest_csv(db, path, TEST_COHORT, TEST_TZ)

    assert result.rows_written == 3, "all three outcomes still write a row"
    matches = {
        row["session_match"]: row["n"]
        for row in _all(
            db, "select session_match, count(*) as n from checkin group by session_match"
        )
    }
    assert matches == {"matched": 1, "ambiguous": 1, "none": 1}

    # Ambiguous and unmatched rows carry no session, rather than a guess.
    assert count(db, "checkin", "session_match <> 'matched' and session_id is not null") == 0
    assert any("overlapping" in w.lower() for w in result.warnings)


# --- 8. unknown email -------------------------------------------------------

def test_8_unknown_email_queues_for_review_and_still_writes(db, tmp_path):
    make_session(db)
    make_fellow(db, email="ada@example.invalid")

    path = write_csv(
        tmp_path / "r.csv",
        _rows(
            ("2026-09-15 19:20:00", "ada@example.invalid", "justice"),
            ("2026-09-15 19:21:00", "stranger@example.invalid", "justice"),
        ),
        HEADERS,
    )
    ingest_csv(db, path, TEST_COHORT, TEST_TZ)

    assert count(db, "checkin") == 2, "identity never blocks ingest"
    unresolved = _all(db, "select email, occurrence_count from identity_unresolved")
    assert [r["email"] for r in unresolved] == ["stranger@example.invalid"]

    # Re-ingesting the same file increments the sighting without duplicating.
    ingest_csv(db, path, TEST_COHORT, TEST_TZ)
    assert _all(db, "select occurrence_count from identity_unresolved")[0]["occurrence_count"] == 2
    assert count(db, "identity_unresolved") == 1


# --- 9. gmail dots ----------------------------------------------------------

def test_9_gmail_dots_and_plus_suffixes_are_preserved(db, tmp_path):
    make_session(db)
    make_fellow(db, fellow_id="CU-A", email="ab@gmail.com")

    path = write_csv(
        tmp_path / "r.csv",
        _rows(
            ("2026-09-15 19:20:00", "a.b@gmail.com", "justice"),
            ("2026-09-15 19:21:00", "ab+fellowship@gmail.com", "justice"),
            ("2026-09-15 19:22:00", "AB@gmail.com", "justice"),
        ),
        HEADERS,
    )
    ingest_csv(db, path, TEST_COHORT, TEST_TZ)

    resolved = {
        row["submitted_email"]: row["fellow_id"]
        for row in _all(db, "select submitted_email, fellow_id from v_checkin_resolved")
    }
    # Only the case-folded exact address matches. The dotted and +suffixed
    # variants stay unmatched rather than being collapsed into someone else.
    assert resolved["ab@gmail.com"] == "CU-A"
    assert resolved["a.b@gmail.com"] is None
    assert resolved["ab+fellowship@gmail.com"] is None
    assert count(db, "identity_unresolved") == 2


# --- 10. extra columns ------------------------------------------------------

def test_10_unexpected_column_is_preserved_not_dropped(db, tmp_path):
    make_session(db)
    path = write_csv(
        tmp_path / "r.csv",
        [
            {
                # Deliberately not the order the parser expects.
                "Today's passphrase": "justice",
                "Device": "iPhone 14",
                "Score": "7",
                "Timestamp": "2026-09-15 19:20:00",
                "Email Address": "ada@example.invalid",
            }
        ],
        ["Today's passphrase", "Device", "Score", "Timestamp", "Email Address"],
    )
    result = ingest_csv(db, path, TEST_COHORT, TEST_TZ)

    assert result.rows_written == 1
    extra = _all(db, "select extra_fields from checkin")[0]["extra_fields"]
    assert extra["Device"] == "iPhone 14"
    assert extra["Score"] == "7"


# --- supporting unit checks -------------------------------------------------

def test_source_event_id_is_stable_across_subsecond_precision():
    stamp_ms = parse_rfc3339("2026-09-15T23:20:00.482Z")
    stamp_s = parse_rfc3339("2026-09-15T23:20:00Z")
    assert source_event_id("form-1", "Ada@Example.Invalid", stamp_ms) == source_event_id(
        "form-1", "ada@example.invalid", stamp_s
    )


def test_source_event_id_separator_prevents_collision():
    stamp = parse_rfc3339("2026-09-15T23:20:00Z")
    assert source_event_id("ab", "c@x.invalid", stamp) != source_event_id(
        "a", "bc@x.invalid", stamp
    )


def test_assign_session_window_edges_are_inclusive():
    session = {
        "session_id": "s1",
        "scheduled_at_utc": datetime(2026, 9, 15, 23, 0, tzinfo=UTC),
        "duration_minutes": 90,
        "grace_minutes": 15,
    }
    start = datetime(2026, 9, 15, 22, 45, tzinfo=UTC)
    end = datetime(2026, 9, 16, 0, 45, tzinfo=UTC)

    assert assign_session([session], start).match == "matched"
    assert assign_session([session], end).match == "matched"
    assert assign_session([session], start - timedelta(seconds=1)).match == "none"
    assert assign_session([session], end + timedelta(seconds=1)).match == "none"
