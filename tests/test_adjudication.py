"""Adjudication, latency and decision versioning.

Attendance is a Google-verified address submitting inside the session window.
The rule table is tested on its own, then through the engine against real
rows: the window edges, a reschedule, the passphrase-era rows that stay frozen,
and the proof that no model — and no answer — takes part.

Most rows here are inserted directly rather than pulled through the fake
Forms API. Adjudication reads four columns (source, session_match, the submit
time and the session's schedule), and a test about those should not also be a
test of provisioning. The CSV outcomes go through the real CSV ingest, because
there the ingest-time session match is the thing under test.
"""

from __future__ import annotations

import inspect
import subprocess
import sys
from datetime import datetime, timedelta

import psycopg
import pytest
from psycopg.types.json import Jsonb

from conftest import TEST_COHORT, TEST_TZ, count, make_session, write_csv

from cufa.adjudicate.engine import adjudicate_cohort, legacy_counts
from cufa.adjudicate.rules import OUTSIDE_WINDOW_RULES, RULES, apply_rules
from cufa.db import execute, fetch_all, fetch_one
from cufa.decisions import current_decision, decision_history, human_override, record_decision
from cufa.ingest.csv_path import ingest_csv
from cufa.latency import recompute_for_session, t0_for_session
from cufa.sessions import SessionInput, announce_now, get_session, update_session
from cufa.timeutil import UTC

# 19:00 America/New_York on 2026-09-15 is 23:00Z. With the conftest defaults
# (90 minutes, 15 minutes' grace) the window is 22:45Z..00:45Z.
LOCAL = datetime(2026, 9, 15, 19, 0)
WINDOW_START = datetime(2026, 9, 15, 22, 45, tzinfo=UTC)
WINDOW_END = datetime(2026, 9, 16, 0, 45, tzinfo=UTC)
WINDOW_NOTE = "window 2026-09-15T22:45:00Z..2026-09-16T00:45:00Z"

# A Part A sheet export: the two columns ingest reads, plus an exit-ticket
# question, which ingest keeps and adjudication never looks at.
HEADERS = ["Timestamp", "Email Address", "What stood out today?"]


def _rows(*triples):
    return [
        {"Timestamp": ts, "Email Address": email, "What stood out today?": answer}
        for ts, email, answer in triples
    ]


def _ingest(db, tmp_path, *triples, cohort=TEST_COHORT, tz=TEST_TZ, name="r.csv"):
    return ingest_csv(db, write_csv(tmp_path / name, _rows(*triples), HEADERS), cohort, tz)


def _checkin(
    db,
    session_id: str | None,
    at: datetime,
    *,
    email: str = "ada@example.invalid",
    source: str = "forms_api",
    session_match: str = "matched",
    passphrase_match: str | None = None,
    answers: dict | None = None,
) -> str:
    """One observation, written the way ingest writes it.

    ``passphrase_match`` set means a passphrase-era row. A row with no session
    is tied to the cohort through its load run, exactly as ingest does it.
    """
    load = fetch_one(
        db,
        "insert into load_run (source, origin, cohort_id) values (%s, 'test', %s) "
        "returning load_id",
        (source, TEST_COHORT),
    )
    stamp = at.astimezone(UTC)
    row = fetch_one(
        db,
        """
        insert into checkin (source_event_id, source, submitted_email, submitted_at_utc,
                             submitted_at_raw, session_id, session_match,
                             passphrase_match, answers, load_id)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        returning checkin_id
        """,
        (
            f"{source}:{email}:{stamp.isoformat()}",
            source,
            email,
            stamp,
            stamp.isoformat(),
            session_id,
            session_match,
            passphrase_match,
            Jsonb(answers or {}),
            load["load_id"],
        ),
    )
    return str(row["checkin_id"])


def _decision(db, checkin_id: str) -> tuple:
    current = current_decision(db, checkin_id)
    return current["status"], current["rule_name"], (
        None if current["confidence"] is None else float(current["confidence"])
    )


# --- the rule table ---------------------------------------------------------

def _rule(source: str, session_match: str, in_window: bool | None) -> tuple:
    outcome = apply_rules(source, session_match, in_window)
    return outcome.status, outcome.rule_name, outcome.confidence


def test_rules_map_each_observation_to_the_specified_decision():
    assert _rule("forms_api", "matched", True) == ("attended", "verified_email_in_window", 0.7)
    assert _rule("forms_api", "matched", False) == (
        "not_attended", "outside_session_window", 0.6
    )
    # A sheet export's address was typed, not verified. A person decides.
    assert _rule("csv", "matched", True) == ("needs_review", "unverified_email_in_window", None)
    assert _rule("csv", "matched", False) == ("not_attended", "outside_session_window", 0.6)
    assert _rule("csv", "none", None) == ("not_attended", "outside_all_windows", 0.6)
    # An overlapping window is a scheduling bug; answering it either way hides it.
    assert _rule("csv", "ambiguous", None) == ("needs_review", "ambiguous_session", None)


def test_rule_table_is_exactly_the_five_named_rules():
    assert set(RULES) == {
        "verified_email_in_window",
        "outside_session_window",
        "unverified_email_in_window",
        "outside_all_windows",
        "ambiguous_session",
    }
    assert set(OUTSIDE_WINDOW_RULES) == {"outside_session_window", "outside_all_windows"}
    # needs_review never carries a confidence: it is the absence of a judgment.
    for outcome in RULES.values():
        assert (outcome.status == "needs_review") == (outcome.confidence is None)


def test_an_unknown_observation_goes_to_a_person_not_to_a_guess():
    assert _rule("carrier_pigeon", "matched", True)[0:2] == (
        "needs_review", "unhandled_observation"
    )


# --- the window, through the engine ----------------------------------------

def test_window_edges_are_inclusive_and_agree_with_the_view(db):
    """Three definitions of the edge would disagree at exactly the minute
    someone asks about. The engine and ``v_checkin_resolved`` must agree."""
    session_id = make_session(db, local=LOCAL)
    second = timedelta(seconds=1)
    cases = {
        "early@example.invalid": (WINDOW_START - second, False),
        "start@example.invalid": (WINDOW_START, True),
        "end@example.invalid": (WINDOW_END, True),
        "late@example.invalid": (WINDOW_END + second, False),
    }
    ids = {
        email: _checkin(db, session_id, at, email=email)
        for email, (at, _inside) in cases.items()
    }

    adjudicate_cohort(db, TEST_COHORT)

    view = {
        row["submitted_email"]: row
        for row in fetch_all(
            db,
            "select submitted_email, in_session_window, window_start_utc, window_end_utc, "
            "rule_name, note from v_checkin_resolved",
        )
    }
    for email, (_at, inside) in cases.items():
        expected = (
            ("attended", "verified_email_in_window", 0.7)
            if inside
            else ("not_attended", "outside_session_window", 0.6)
        )
        assert _decision(db, ids[email]) == expected, email
        assert view[email]["in_session_window"] is inside, email
        assert view[email]["window_start_utc"] == WINDOW_START
        assert view[email]["window_end_utc"] == WINDOW_END
        assert view[email]["note"] == WINDOW_NOTE


def test_a_verified_response_long_after_the_lesson_is_not_attendance(db):
    """The form stays open; the window does not. Opening the link the next
    morning is a response, and not evidence of being in the room."""
    session_id = make_session(db, local=LOCAL)
    checkin_id = _checkin(db, session_id, WINDOW_END + timedelta(hours=10))

    result = adjudicate_cohort(db, TEST_COHORT)

    assert _decision(db, checkin_id) == ("not_attended", "outside_session_window", 0.6)
    assert result.outside_window == 1


def test_csv_rows_through_real_ingest(db, tmp_path):
    make_session(db, title="Main", local=LOCAL)
    make_session(db, title="Overlap", local=datetime(2026, 9, 22, 19, 0))
    make_session(db, title="Overlap 2", local=datetime(2026, 9, 22, 19, 30))

    _ingest(
        db,
        tmp_path,
        ("2026-09-15 19:20:00", "in@example.invalid", "budgets are values"),
        ("2026-09-30 19:20:00", "none@example.invalid", "budgets are values"),
        ("2026-09-22 19:45:00", "amb@example.invalid", "budgets are values"),
    )
    adjudicate_cohort(db, TEST_COHORT)

    decided = {
        row["submitted_email"]: (row["status"], row["rule_name"])
        for row in fetch_all(db, "select submitted_email, status, rule_name from v_checkin_resolved")
    }
    assert decided == {
        "in@example.invalid": ("needs_review", "unverified_email_in_window"),
        "none@example.invalid": ("not_attended", "outside_all_windows"),
        "amb@example.invalid": ("needs_review", "ambiguous_session"),
    }


# --- a reschedule re-judges -------------------------------------------------

def test_a_reschedule_rejudges_and_the_note_names_the_new_window(db):
    session_id = make_session(db, local=LOCAL)
    checkin_id = _checkin(db, session_id, datetime(2026, 9, 15, 23, 20, tzinfo=UTC))

    adjudicate_cohort(db, TEST_COHORT)
    assert _decision(db, checkin_id) == ("attended", "verified_email_in_window", 0.7)
    assert current_decision(db, checkin_id)["note"] == WINDOW_NOTE

    # Staff typed the wrong day; the lesson was the 16th.
    existing = get_session(db, session_id)
    update_session(
        db,
        session_id,
        SessionInput(
            cohort_id=existing["cohort_id"],
            title=existing["title"],
            scheduled_at_local=datetime(2026, 9, 16, 19, 0),
            timezone=existing["timezone"],
            duration_minutes=existing["duration_minutes"],
            grace_minutes=existing["grace_minutes"],
        ),
    )

    result = adjudicate_cohort(db, TEST_COHORT)

    assert result.decided_by_rule == 1
    assert _decision(db, checkin_id) == ("not_attended", "outside_session_window", 0.6)
    assert current_decision(db, checkin_id)["note"] == (
        "window 2026-09-16T22:45:00Z..2026-09-17T00:45:00Z"
    )
    # The earlier judgment is superseded, not erased: the history says why it moved.
    history = decision_history(db, checkin_id)
    assert [row["note"] for row in history] == [
        "window 2026-09-16T22:45:00Z..2026-09-17T00:45:00Z",
        WINDOW_NOTE,
    ]


def test_rerunning_over_the_same_evidence_writes_nothing(db, tmp_path):
    session_id = make_session(db, local=LOCAL)
    _checkin(db, session_id, datetime(2026, 9, 15, 23, 20, tzinfo=UTC))
    _checkin(db, session_id, WINDOW_END + timedelta(hours=1), email="late@example.invalid")
    _ingest(db, tmp_path, ("2026-09-15 19:21:00", "csv@example.invalid", "x"))

    adjudicate_cohort(db, TEST_COHORT)
    before = count(db, "attendance_decision")
    result = adjudicate_cohort(db, TEST_COHORT)

    assert count(db, "attendance_decision") == before
    assert result.decided_by_rule == 0
    assert result.unchanged == 3


# --- passphrase-era rows ----------------------------------------------------

def test_legacy_decided_rows_are_frozen_unless_asked(db):
    session_id = make_session(db, local=LOCAL)
    in_window = datetime(2026, 9, 15, 23, 20, tzinfo=UTC)
    exact = _checkin(db, session_id, in_window, email="exact@example.invalid",
                     passphrase_match="exact")
    record_decision(db, exact, status="attended", decided_by="rule",
                    rule_name="exact_match", confidence=1.0)
    by_ai = _checkin(db, session_id, WINDOW_END + timedelta(hours=2),
                     email="ai@example.invalid", passphrase_match="mismatch")
    record_decision(db, by_ai, status="attended", decided_by="ai",
                    ai_model="gemini-2.5-flash", ai_prompt_version="v1", confidence=0.9)
    # Ingested under the passphrase but never adjudicated: judged like any row.
    undecided = _checkin(db, session_id, in_window, email="undecided@example.invalid",
                         passphrase_match="mismatch")

    result = adjudicate_cohort(db, TEST_COHORT)

    assert result.legacy_frozen == 2
    assert _decision(db, exact) == ("attended", "exact_match", 1.0)
    assert current_decision(db, by_ai)["decided_by"] == "ai"
    assert _decision(db, undecided) == ("attended", "verified_email_in_window", 0.7)

    assert legacy_counts(db, TEST_COHORT) == {"total": 3, "rule": 2, "ai": 1, "human": 0}

    result = adjudicate_cohort(db, TEST_COHORT, redecide_legacy=True)

    assert result.legacy_frozen == 0
    assert result.legacy_redecided == 2
    assert _decision(db, exact) == ("attended", "verified_email_in_window", 0.7)
    assert _decision(db, by_ai) == ("not_attended", "outside_session_window", 0.6)


def test_a_human_decision_on_a_legacy_row_needs_both_flags(db):
    session_id = make_session(db, local=LOCAL)
    checkin_id = _checkin(db, session_id, WINDOW_END + timedelta(hours=2),
                          passphrase_match="exact")
    human_override(db, checkin_id, status="attended", by_email="staff@cu.invalid",
                   note="Was in the room; phone died")

    assert adjudicate_cohort(db, TEST_COHORT, redecide_legacy=True).human_preserved == 1
    assert adjudicate_cohort(db, TEST_COHORT, force=True).legacy_frozen == 1
    assert current_decision(db, checkin_id)["decided_by"] == "human"

    result = adjudicate_cohort(db, TEST_COHORT, force=True, redecide_legacy=True)
    assert result.human_overwritten == 1
    assert "HUMAN" in result.warnings[0]
    assert _decision(db, checkin_id) == ("not_attended", "outside_session_window", 0.6)


# --- no model, and no answer, takes part ------------------------------------

def test_adjudication_never_imports_a_model():
    """Checked in a fresh interpreter, so nothing another test imported counts."""
    code = (
        "import sys, cufa.adjudicate, cufa.adjudicate.engine, cufa.report\n"
        "banned = ('google.genai', 'anthropic', 'openai', 'cufa.themes', "
        "'cufa.slack.qa', 'cufa.adjudicate.ai')\n"
        "loaded = [m for m in sys.modules if m.startswith(banned)]\n"
        "print(','.join(loaded))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == "", f"adjudication pulled in {out}"

    import importlib.util

    assert importlib.util.find_spec("cufa.adjudicate.ai") is None
    params = inspect.signature(adjudicate_cohort).parameters
    assert "use_ai" not in params and "adjudicator" not in params

    import cufa.adjudicate.engine as engine
    import cufa.adjudicate.rules as rules

    for module in (engine, rules):
        source = inspect.getsource(module).lower()
        for needle in ("genai", "gemini", "anthropic", "openai"):
            assert needle not in source, (module.__name__, needle)


def test_the_answers_never_change_a_decision(db):
    """A blank exit ticket, a full one and a one-word one from someone in the
    room are the same attendance."""
    session_id = make_session(db, local=LOCAL)
    at = datetime(2026, 9, 15, 23, 20, tzinfo=UTC)
    ids = [
        _checkin(db, session_id, at, email="blank@example.invalid", answers={}),
        _checkin(db, session_id, at, email="full@example.invalid", answers={
            "1a2b": {"values": ["A budget is a statement of values."], "title": "Takeaway"},
            "3c4d": {"values": ["5"], "title": "How clear was today?"},
        }),
        _checkin(db, session_id, at, email="terse@example.invalid", answers={
            "1a2b": {"values": ["idk"], "title": "Takeaway"},
        }),
    ]

    adjudicate_cohort(db, TEST_COHORT)

    assert {_decision(db, checkin_id) for checkin_id in ids} == {
        ("attended", "verified_email_in_window", 0.7)
    }


# --- latency ----------------------------------------------------------------

def test_latency_derived_t0_makes_the_first_submitter_zero(db, tmp_path):
    session_id = make_session(db, local=LOCAL)
    _ingest(
        db,
        tmp_path,
        ("2026-09-15 19:20:00", "a@example.invalid", "x"),
        ("2026-09-15 19:22:00", "b@example.invalid", "x"),
        ("2026-09-15 19:25:00", "c@example.invalid", "x"),
    )

    _t0, source = t0_for_session(db, session_id)
    assert source == "derived"

    latencies = {
        row["submitted_email"]: row["latency_seconds"]
        for row in fetch_all(db, "select submitted_email, latency_seconds from checkin")
    }
    # Documented as expected, not a bug: with no announcement stamp the first
    # arrival is the only evidence of when the form went out.
    assert latencies["a@example.invalid"] == 0
    assert latencies["b@example.invalid"] == 120
    assert latencies["c@example.invalid"] == 300


def test_explicit_announced_at_wins_and_recomputes(db, tmp_path):
    session_id = make_session(db, local=LOCAL)
    _ingest(
        db,
        tmp_path,
        ("2026-09-15 19:20:00", "a@example.invalid", "x"),
        ("2026-09-15 19:22:00", "b@example.invalid", "x"),
    )
    assert fetch_one(
        db, "select latency_seconds from checkin where submitted_email = 'a@example.invalid'"
    )["latency_seconds"] == 0

    # The teacher presses "Announce now" after the first fellow already submitted.
    announce_now(db, session_id, datetime(2026, 9, 15, 23, 18, tzinfo=UTC))
    recompute_for_session(db, session_id)

    _t0, source = t0_for_session(db, session_id)
    assert source == "announced"
    latencies = {
        row["submitted_email"]: row["latency_seconds"]
        for row in fetch_all(db, "select submitted_email, latency_seconds from checkin")
    }
    assert latencies["a@example.invalid"] == 120
    assert latencies["b@example.invalid"] == 240


def test_latency_is_null_when_no_session_matched(db, tmp_path):
    make_session(db, local=LOCAL)
    _ingest(db, tmp_path, ("2026-11-30 09:00:00", "a@example.invalid", "x"))

    row = fetch_one(db, "select session_id, latency_seconds from checkin")
    assert row["session_id"] is None
    assert row["latency_seconds"] is None


def test_a_submission_before_the_announcement_keeps_its_negative_latency(db, tmp_path):
    """Latency is stored, not interpreted — including when it is negative.

    A teacher who presses "Announce now" a minute after the first fellow has
    already submitted produces exactly this. Clamping it to zero would be an
    interpretation, and would hide the case worth noticing.
    """
    session_id = make_session(db, local=LOCAL)
    _ingest(db, tmp_path, ("2026-09-15 19:20:00", "early@example.invalid", "x"))

    # Announced a minute AFTER that submission landed.
    announce_now(db, session_id, datetime(2026, 9, 15, 23, 21, tzinfo=UTC))
    recompute_for_session(db, session_id)

    assert fetch_one(db, "select latency_seconds from checkin")["latency_seconds"] == -60


# --- decision versioning ----------------------------------------------------

def test_override_supersedes_and_leaves_exactly_one_current(db):
    session_id = make_session(db, local=LOCAL)
    checkin_id = _checkin(db, session_id, datetime(2026, 9, 15, 23, 20, tzinfo=UTC))

    adjudicate_cohort(db, TEST_COHORT)
    assert current_decision(db, checkin_id)["status"] == "attended"

    human_override(
        db, checkin_id, status="not_attended", by_email="staff@cu.invalid",
        note="Fellow says they were not there.",
    )

    history = decision_history(db, checkin_id)
    assert len(history) == 2
    current = [row for row in history if row["superseded_at"] is None]
    assert len(current) == 1
    assert current[0]["decided_by"] == "human"
    assert current[0]["status"] == "not_attended"
    assert current[0]["attended"] is False


def test_partial_unique_index_actually_enforces_one_current(db):
    session_id = make_session(db, local=LOCAL)
    checkin_id = _checkin(db, session_id, datetime(2026, 9, 15, 23, 20, tzinfo=UTC))

    record_decision(
        db, checkin_id, status="attended", decided_by="rule",
        rule_name="verified_email_in_window",
    )

    # Insert a second live decision directly, bypassing the supersede step.
    with pytest.raises(psycopg.errors.UniqueViolation):
        execute(
            db,
            """
            insert into attendance_decision
                (checkin_id, attended, status, decided_by, rule_name)
            values (%s, true, 'attended', 'rule', 'verified_email_in_window')
            """,
            (checkin_id,),
        )


def test_superseded_rows_may_coexist_freely(db):
    """The index is partial: history is unlimited, only 'current' is unique."""
    session_id = make_session(db, local=LOCAL)
    checkin_id = _checkin(db, session_id, datetime(2026, 9, 15, 23, 20, tzinfo=UTC))

    for index in range(4):
        human_override(
            db, checkin_id,
            status="attended" if index % 2 == 0 else "not_attended",
            by_email="staff@cu.invalid",
        )

    assert count(db, "attendance_decision") == 4
    assert count(db, "attendance_decision", "superseded_at is null") == 1


# --- a human always wins ----------------------------------------------------

def test_rerunning_adjudicate_does_not_overwrite_a_human(db):
    session_id = make_session(db, local=LOCAL)
    checkin_id = _checkin(db, session_id, datetime(2026, 9, 15, 23, 20, tzinfo=UTC))

    adjudicate_cohort(db, TEST_COHORT)
    human_override(
        db, checkin_id, status="not_attended", by_email="staff@cu.invalid", note="Confirmed absent",
    )

    result = adjudicate_cohort(db, TEST_COHORT)

    assert result.human_preserved == 1
    current = current_decision(db, checkin_id)
    assert current["decided_by"] == "human"
    assert current["note"] == "Confirmed absent"


def test_force_overwrites_and_warns_naming_what_it_destroys(db):
    session_id = make_session(db, local=LOCAL)
    checkin_id = _checkin(db, session_id, datetime(2026, 9, 15, 23, 20, tzinfo=UTC))

    adjudicate_cohort(db, TEST_COHORT)
    human_override(
        db, checkin_id, status="not_attended", by_email="staff@cu.invalid", note="Confirmed absent",
    )

    result = adjudicate_cohort(db, TEST_COHORT, force=True)

    assert result.human_overwritten == 1
    assert len(result.warnings) == 1
    warning = result.warnings[0]
    assert "HUMAN" in warning
    assert checkin_id in warning
    assert "not_attended" in warning
    assert "staff@cu.invalid" in warning
    assert "Confirmed absent" in warning

    assert current_decision(db, checkin_id)["decided_by"] == "rule"


# --- needs_review is never resolved downward by the same evidence ----------

def test_needs_review_is_not_resolved_downward_by_a_rerun(db, tmp_path):
    make_session(db, title="Main", local=LOCAL)
    make_session(db, title="Overlap", local=datetime(2026, 9, 15, 19, 30))

    _ingest(
        db,
        tmp_path,
        ("2026-09-15 19:45:00", "amb@example.invalid", "x"),  # both windows
        ("2026-09-15 18:50:00", "csv@example.invalid", "x"),  # one window, unverified
    )

    adjudicate_cohort(db, TEST_COHORT)
    adjudicate_cohort(db, TEST_COHORT)

    rows = {
        row["submitted_email"]: row
        for row in fetch_all(
            db, "select submitted_email, status, attended from v_checkin_resolved"
        )
    }
    assert rows["amb@example.invalid"]["status"] == "needs_review"
    assert rows["csv@example.invalid"]["status"] == "needs_review"

    # attended is NULL for every needs_review row, never False.
    assert count(db, "attendance_decision", "status = 'needs_review' and attended is not null") == 0
    assert count(db, "v_current_decision", "status = 'not_attended'") == 0


def test_database_rejects_a_needs_review_row_claiming_attendance(db):
    session_id = make_session(db, local=LOCAL)
    checkin_id = _checkin(db, session_id, datetime(2026, 9, 15, 23, 20, tzinfo=UTC))

    with pytest.raises(psycopg.errors.CheckViolation):
        execute(
            db,
            """
            insert into attendance_decision
                (checkin_id, attended, status, decided_by, rule_name)
            values (%s, false, 'needs_review', 'rule', 'x')
            """,
            (checkin_id,),
        )


# --- immutability -----------------------------------------------------------

def test_checkin_observations_are_immutable(db):
    session_id = make_session(db, local=LOCAL)
    checkin_id = _checkin(
        db, session_id, datetime(2026, 9, 15, 23, 20, tzinfo=UTC),
        answers={"1a2b": {"values": ["as submitted"], "title": "Takeaway"}},
    )

    for statement in (
        "update checkin set answers = '{}'::jsonb where checkin_id = %s",
        "update checkin set submitted_email = 'someone@else.invalid' where checkin_id = %s",
        "delete from checkin where checkin_id = %s",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation):
            execute(db, statement, (checkin_id,))

    # The one derived column is allowed to be recomputed.
    execute(db, "update checkin set latency_seconds = 42 where checkin_id = %s", (checkin_id,))
    assert fetch_one(db, "select latency_seconds from checkin")["latency_seconds"] == 42
