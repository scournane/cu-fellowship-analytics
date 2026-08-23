"""Deliverable 10, tests 18-25: adjudication, latency and decision versioning."""

from __future__ import annotations

from datetime import datetime, timedelta

import psycopg
import pytest

from conftest import (
    TEST_COHORT,
    TEST_TZ,
    ExplodingAdjudicator,
    StubAdjudicator,
    count,
    make_fellow,
    make_session,
    write_csv,
)

from cufa.adjudicate.ai import PROMPT_VERSION, judge_with_cache
from cufa.adjudicate.engine import adjudicate_cohort
from cufa.adjudicate.rules import apply_rules
from cufa.db import execute, fetch_all, fetch_one
from cufa.decisions import current_decision, decision_history, human_override, record_decision
from cufa.ingest.common import compare_passphrase
from cufa.ingest.csv_path import ingest_csv
from cufa.latency import recompute_for_session, t0_for_session
from cufa.sessions import announce_now
from cufa.text import normalize_answer
from cufa.timeutil import UTC

HEADERS = ["Timestamp", "Email Address", "Today's passphrase"]


def _rows(*triples):
    return [
        {"Timestamp": ts, "Email Address": email, "Today's passphrase": answer}
        for ts, email, answer in triples
    ]


def _ingest(db, tmp_path, *triples, cohort=TEST_COHORT, tz=TEST_TZ, name="r.csv"):
    return ingest_csv(db, write_csv(tmp_path / name, _rows(*triples), HEADERS), cohort, tz)


# --- 18. all five passphrase outcomes --------------------------------------

def test_18_all_five_passphrase_outcomes(db, tmp_path):
    make_session(db, title="Main", local=datetime(2026, 9, 15, 19, 0), passphrase="justice")
    make_session(
        db, title="No passphrase", local=datetime(2026, 9, 22, 19, 0), passphrase=None
    )
    make_session(db, title="Overlap", local=datetime(2026, 9, 15, 19, 30), passphrase="justice")

    _ingest(
        db,
        tmp_path,
        ("2026-09-15 18:50:00", "a@example.invalid", "justice"),    # exact
        ("2026-09-15 18:51:00", "b@example.invalid", "justise"),    # fuzzy (d=1)
        ("2026-09-15 18:52:00", "c@example.invalid", "committee"),  # mismatch
        ("2026-09-22 19:20:00", "d@example.invalid", "anything"),   # not_set
        ("2026-09-30 19:20:00", "e@example.invalid", "justice"),    # no_session
    )

    outcomes = {
        row["submitted_email"]: (row["passphrase_match"], row["edit_distance"])
        for row in fetch_all(
            db, "select submitted_email, passphrase_match, edit_distance from checkin"
        )
    }
    assert outcomes["a@example.invalid"] == ("exact", 0)
    assert outcomes["b@example.invalid"] == ("fuzzy", 1)
    assert outcomes["c@example.invalid"] == ("mismatch", None)
    assert outcomes["d@example.invalid"] == ("not_set", None)
    assert outcomes["e@example.invalid"] == ("no_session", None)


def _rule(passphrase_match: str, session_match: str) -> tuple:
    outcome = apply_rules(passphrase_match, session_match)
    return outcome.status, outcome.rule_name, outcome.confidence


def test_18b_rules_map_each_outcome_to_the_specified_decision():
    assert _rule("exact", "matched") == ("attended", "exact_match", 1.0)
    assert _rule("fuzzy", "matched") == ("attended", "fuzzy_match", 0.9)
    assert _rule("not_set", "matched") == ("attended", "no_passphrase_required", 0.7)
    assert _rule("no_session", "none") == ("not_attended", "outside_all_windows", 0.6)
    assert apply_rules("mismatch", "matched").escalates is True
    # An overlapping window is a scheduling bug; answering it either way hides it.
    assert _rule("no_session", "ambiguous") == ("needs_review", "ambiguous_session", None)


# --- 19. normalization ------------------------------------------------------

@pytest.mark.parametrize(
    "typed", ["  Justice ", "JUSTICE", "justice.", "Justice!", "  justice  ", "jUsTiCe,"]
)
def test_19_normalization_variants_all_read_as_exact(typed):
    match, distance = compare_passphrase(
        "justice", typed, max_edit_distance=1, session_matched=True
    )
    assert (match, distance) == ("exact", 0)


def test_19b_normalization_is_shared_with_the_ai_cache_key():
    """Tier 1 and the tier 2 cache must agree, or a cache hit means nothing."""
    assert normalize_answer("  Justice. ") == normalize_answer("JUSTICE")


# --- 20. latency ------------------------------------------------------------

def test_20_latency_derived_t0_makes_the_first_submitter_zero(db, tmp_path):
    session_id = make_session(db, local=datetime(2026, 9, 15, 19, 0))
    _ingest(
        db,
        tmp_path,
        ("2026-09-15 19:20:00", "a@example.invalid", "justice"),
        ("2026-09-15 19:22:00", "b@example.invalid", "justice"),
        ("2026-09-15 19:25:00", "c@example.invalid", "justice"),
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


def test_20b_explicit_announced_at_wins_and_recomputes(db, tmp_path):
    session_id = make_session(db, local=datetime(2026, 9, 15, 19, 0))
    _ingest(
        db,
        tmp_path,
        ("2026-09-15 19:20:00", "a@example.invalid", "justice"),
        ("2026-09-15 19:22:00", "b@example.invalid", "justice"),
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


def test_20c_latency_is_null_when_no_session_matched(db, tmp_path):
    make_session(db, local=datetime(2026, 9, 15, 19, 0))
    _ingest(db, tmp_path, ("2026-11-30 09:00:00", "a@example.invalid", "justice"))

    row = fetch_one(db, "select session_id, latency_seconds from checkin")
    assert row["session_id"] is None
    assert row["latency_seconds"] is None


# --- 21. decision versioning ------------------------------------------------

def test_21_override_supersedes_and_leaves_exactly_one_current(db, tmp_path):
    make_session(db)
    _ingest(db, tmp_path, ("2026-09-15 19:20:00", "a@example.invalid", "justice"))
    checkin_id = fetch_one(db, "select checkin_id from checkin")["checkin_id"]

    adjudicate_cohort(db, TEST_COHORT, use_ai=False)
    assert current_decision(db, checkin_id)["status"] == "attended"

    human_override(
        db, str(checkin_id), status="not_attended", by_email="staff@cu.invalid",
        note="Fellow says they were not there.",
    )

    history = decision_history(db, str(checkin_id))
    assert len(history) == 2
    current = [row for row in history if row["superseded_at"] is None]
    assert len(current) == 1
    assert current[0]["decided_by"] == "human"
    assert current[0]["status"] == "not_attended"
    assert current[0]["attended"] is False


def test_21b_partial_unique_index_actually_enforces_one_current(db, tmp_path):
    make_session(db)
    _ingest(db, tmp_path, ("2026-09-15 19:20:00", "a@example.invalid", "justice"))
    checkin_id = str(fetch_one(db, "select checkin_id from checkin")["checkin_id"])

    record_decision(db, checkin_id, status="attended", decided_by="rule", rule_name="exact_match")

    # Insert a second live decision directly, bypassing the supersede step.
    with pytest.raises(psycopg.errors.UniqueViolation):
        execute(
            db,
            """
            insert into attendance_decision
                (checkin_id, attended, status, decided_by, rule_name)
            values (%s, true, 'attended', 'rule', 'exact_match')
            """,
            (checkin_id,),
        )


def test_21c_superseded_rows_may_coexist_freely(db, tmp_path):
    """The index is partial: history is unlimited, only 'current' is unique."""
    make_session(db)
    _ingest(db, tmp_path, ("2026-09-15 19:20:00", "a@example.invalid", "justice"))
    checkin_id = str(fetch_one(db, "select checkin_id from checkin")["checkin_id"])

    for index in range(4):
        human_override(
            db, checkin_id,
            status="attended" if index % 2 == 0 else "not_attended",
            by_email="staff@cu.invalid",
        )

    assert count(db, "attendance_decision") == 4
    assert count(db, "attendance_decision", "superseded_at is null") == 1


# --- 22. a human always wins ------------------------------------------------

def test_22_rerunning_adjudicate_does_not_overwrite_a_human(db, tmp_path):
    make_session(db)
    _ingest(db, tmp_path, ("2026-09-15 19:20:00", "a@example.invalid", "justice"))
    checkin_id = str(fetch_one(db, "select checkin_id from checkin")["checkin_id"])

    adjudicate_cohort(db, TEST_COHORT, use_ai=False)
    human_override(
        db, checkin_id, status="not_attended", by_email="staff@cu.invalid", note="Confirmed absent",
    )

    result = adjudicate_cohort(db, TEST_COHORT, use_ai=False)

    assert result.human_preserved == 1
    current = current_decision(db, checkin_id)
    assert current["decided_by"] == "human"
    assert current["note"] == "Confirmed absent"


def test_22b_force_overwrites_and_warns_naming_what_it_destroys(db, tmp_path):
    make_session(db)
    _ingest(db, tmp_path, ("2026-09-15 19:20:00", "a@example.invalid", "justice"))
    checkin_id = str(fetch_one(db, "select checkin_id from checkin")["checkin_id"])

    adjudicate_cohort(db, TEST_COHORT, use_ai=False)
    human_override(
        db, checkin_id, status="not_attended", by_email="staff@cu.invalid", note="Confirmed absent",
    )

    result = adjudicate_cohort(db, TEST_COHORT, use_ai=False, force=True)

    assert result.human_overwritten == 1
    assert len(result.warnings) == 1
    warning = result.warnings[0]
    assert "HUMAN" in warning
    assert checkin_id in warning
    assert "not_attended" in warning
    assert "staff@cu.invalid" in warning
    assert "Confirmed absent" in warning

    assert current_decision(db, checkin_id)["decided_by"] == "rule"


# --- 23. AI cache -----------------------------------------------------------

def test_23_repeated_string_pair_makes_exactly_one_api_call(db, tmp_path):
    make_session(db, local=datetime(2026, 9, 15, 19, 0), passphrase="justice")
    make_session(db, title="Week 2", local=datetime(2026, 9, 22, 19, 0), passphrase="justice")
    _ingest(
        db,
        tmp_path,
        ("2026-09-15 19:20:00", "a@example.invalid", "the word was justice"),
        ("2026-09-22 19:20:00", "b@example.invalid", "The word was Justice."),
    )

    stub = StubAdjudicator(verdict=True)
    result = adjudicate_cohort(db, TEST_COHORT, adjudicator=stub)

    assert result.escalated == 2
    assert len(stub.calls) == 1, "the normalized pair is identical, so one call"
    assert result.ai_cache_hits == 1
    assert count(db, "ai_adjudication_cache") == 1

    # A second run makes zero calls at all.
    stub2 = StubAdjudicator(verdict=True)
    adjudicate_cohort(db, TEST_COHORT, adjudicator=stub2)
    assert stub2.calls == []


def test_23b_cache_key_includes_prompt_version_and_model(db):
    stub = StubAdjudicator()
    judge_with_cache(db, stub, "justice", "the word was justice")
    row = fetch_one(db, "select * from ai_adjudication_cache")
    assert row["prompt_version"] == PROMPT_VERSION
    assert row["model"] == "stub-model"
    assert row["expected_normalized"] == "justice"
    assert row["submitted_normalized"] == "the word was justice"


# --- 24. AI unavailable -----------------------------------------------------

def test_24_no_key_completes_and_lands_in_needs_review(db, tmp_path):
    make_session(db, passphrase="justice")
    _ingest(
        db,
        tmp_path,
        ("2026-09-15 19:20:00", "a@example.invalid", "justice"),
        ("2026-09-15 19:21:00", "b@example.invalid", "the word was justice"),
    )

    # No GEMINI_API_KEY in the environment (conftest removes it), so
    # build_adjudicator returns None and the run must still complete.
    result = adjudicate_cohort(db, TEST_COHORT, use_ai=True)

    assert result.ai_unavailable == 1
    statuses = {
        row["submitted_email"]: (row["status"], row["decided_by"], row["rule_name"])
        for row in fetch_all(
            db,
            "select submitted_email, status, decided_by, rule_name from v_checkin_resolved",
        )
    }
    assert statuses["a@example.invalid"] == ("attended", "rule", "exact_match")
    assert statuses["b@example.invalid"] == ("needs_review", "rule", "ai_unavailable")


def test_24b_mid_run_ai_failure_degrades_rather_than_crashing(db, tmp_path):
    make_session(db, passphrase="justice")
    _ingest(
        db,
        tmp_path,
        ("2026-09-15 19:20:00", "a@example.invalid", "the word was justice"),
        ("2026-09-15 19:21:00", "b@example.invalid", "justice i think"),
    )

    result = adjudicate_cohort(db, TEST_COHORT, adjudicator=ExplodingAdjudicator())

    assert result.ai_unavailable == 2
    assert result.needs_review == 2
    assert count(db, "attendance_decision", "rule_name = 'ai_unavailable'") == 2


def test_24c_no_ai_flag_skips_tier_two_entirely(db, tmp_path):
    make_session(db, passphrase="justice")
    _ingest(db, tmp_path, ("2026-09-15 19:20:00", "a@example.invalid", "the word was justice"))

    stub = StubAdjudicator()
    adjudicate_cohort(db, TEST_COHORT, use_ai=False, adjudicator=stub)
    assert stub.calls == []


def test_24d_call_cap_is_respected(db, tmp_path, settings):
    import dataclasses

    make_session(db, local=datetime(2026, 9, 15, 19, 0), passphrase="justice")
    _ingest(
        db,
        tmp_path,
        ("2026-09-15 19:20:00", "a@example.invalid", "the word was justice"),
        ("2026-09-15 19:21:00", "b@example.invalid", "justice maybe"),
        ("2026-09-15 19:22:00", "c@example.invalid", "it was justice right"),
    )

    capped = dataclasses.replace(settings, ai_max_calls_per_run=1)
    stub = StubAdjudicator()
    result = adjudicate_cohort(db, TEST_COHORT, adjudicator=stub, settings=capped)

    assert len(stub.calls) == 1
    assert result.ai_unavailable == 2, "the rest go to needs_review, not to a guess"


# --- 25. needs_review is never not_attended --------------------------------

def test_25_needs_review_never_becomes_not_attended(db, tmp_path):
    make_session(db, local=datetime(2026, 9, 15, 19, 0), passphrase="justice")
    make_session(db, title="Overlap", local=datetime(2026, 9, 15, 19, 30), passphrase="justice")

    _ingest(
        db,
        tmp_path,
        ("2026-09-15 19:45:00", "amb@example.invalid", "justice"),   # ambiguous window
        ("2026-09-15 18:50:00", "mis@example.invalid", "committee"),  # mismatch -> tier 2
    )

    # A tier 2 verdict of "did not hear it" is still not proof of absence.
    stub = StubAdjudicator(verdict=False, confidence=0.95)
    adjudicate_cohort(db, TEST_COHORT, adjudicator=stub)

    rows = {
        row["submitted_email"]: row
        for row in fetch_all(
            db, "select submitted_email, status, attended, decided_by from v_checkin_resolved"
        )
    }
    assert rows["amb@example.invalid"]["status"] == "needs_review"
    assert rows["mis@example.invalid"]["status"] == "needs_review"

    # attended is NULL for every needs_review row, never False.
    assert count(db, "attendance_decision", "status = 'needs_review' and attended is not null") == 0

    # And a further pass does not quietly resolve them downward.
    adjudicate_cohort(db, TEST_COHORT, use_ai=False)
    assert count(db, "v_current_decision", "status = 'not_attended'") == 0


def test_25b_database_rejects_a_needs_review_row_claiming_attendance(db, tmp_path):
    make_session(db)
    _ingest(db, tmp_path, ("2026-09-15 19:20:00", "a@example.invalid", "justice"))
    checkin_id = str(fetch_one(db, "select checkin_id from checkin")["checkin_id"])

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

def test_checkin_observations_are_immutable(db, tmp_path):
    make_session(db)
    _ingest(db, tmp_path, ("2026-09-15 19:20:00", "a@example.invalid", "justice"))
    checkin_id = str(fetch_one(db, "select checkin_id from checkin")["checkin_id"])

    with pytest.raises(psycopg.errors.RestrictViolation):
        execute(
            db, "update checkin set passphrase_raw = 'edited' where checkin_id = %s", (checkin_id,)
        )
    with pytest.raises(psycopg.errors.RestrictViolation):
        execute(db, "delete from checkin where checkin_id = %s", (checkin_id,))

    # The one derived column is allowed to be recomputed.
    execute(db, "update checkin set latency_seconds = 42 where checkin_id = %s", (checkin_id,))
    assert fetch_one(db, "select latency_seconds from checkin")["latency_seconds"] == 42


def test_gemini_response_schema_is_valid_for_the_sdk():
    """Catch a malformed schema here rather than as a 400 during a live run.

    This does not call Gemini — it only asks the SDK to validate the schema and
    the generation config, which is exactly the part that would otherwise fail
    opaquely and only when a key is present.
    """
    from google.genai import types

    from cufa.adjudicate.ai import PROMPT_TEMPLATE, RESPONSE_SCHEMA

    schema = types.Schema(**RESPONSE_SCHEMA)
    assert schema.type == types.Type.OBJECT
    assert set(schema.required) == {"heard_the_passphrase", "confidence", "reasoning"}

    config = types.GenerateContentConfig(
        temperature=0, response_mime_type="application/json", response_schema=schema
    )
    assert config.temperature == 0, "tier 2 must be reproducible"

    # The prompt carries both strings and nothing else about the person.
    rendered = PROMPT_TEMPLATE.format(expected="justice", submitted="the word was justice")
    assert "justice" in rendered
    for leak in ("@", "fellow_id", "cohort", "CU-"):
        assert leak not in rendered, f"tier 2 prompt must not carry {leak!r}"


def test_20d_a_submission_before_the_announcement_keeps_its_negative_latency(db, tmp_path):
    """Latency is stored, not interpreted — including when it is negative.

    A teacher who presses "Announce now" a minute after the first fellow has
    already submitted produces exactly this. Clamping it to zero would be an
    interpretation, and would hide the case worth noticing.
    """
    session_id = make_session(db, local=datetime(2026, 9, 15, 19, 0))
    _ingest(db, tmp_path, ("2026-09-15 19:20:00", "early@example.invalid", "justice"))

    # Announced a minute AFTER that submission landed.
    announce_now(db, session_id, datetime(2026, 9, 15, 23, 21, tzinfo=UTC))
    recompute_for_session(db, session_id)

    assert fetch_one(db, "select latency_seconds from checkin")["latency_seconds"] == -60
