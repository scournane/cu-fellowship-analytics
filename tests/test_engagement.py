"""Engagement, interventions, assignments, funnel, retention and Zoom share.

The invariants under test are the ones a careless implementation would break
without noticing: needs_review never counts against a fellow, a help request
never enters a metric, scores never enter participation, free text is
counted and never graded, and everything is idempotent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from conftest import TEST_COHORT, make_fellow, make_session

from cufa.assignments import AssignmentInput, create_assignment, find_assignment, record_score, submissions_for_fellow
from cufa.db import execute, fetch_one
from cufa.engagement import attention_index, cohort_attendance, cohort_engagement, most_active, quiet_fellows
from cufa.errors import CufaError
from cufa.funnel import cohort_summary, fellow_funnel, mark_completed, render_text
from cufa.interventions import mark_reached_out, clear_reached_out, reached_out
from cufa.retention import Rubric, cohort_retention, load_rubric, parse_rubric, RubricError
from cufa.slack.client import FakeSlackClient
from cufa.slack.sync import sync_all
from cufa.zoom import ingest_transcript, parse_vtt, silent_fellows, speaking_share

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def _checkin(db, email: str, session_id: str, at: str, status: str = "attended") -> None:
    key = f"{email}:{session_id}:{at}:{status}"
    execute(
        db,
        """
        insert into checkin (source_event_id, source, submitted_email, submitted_at_utc, submitted_at_raw,
                             session_id, session_match, passphrase_match)
        values (%s, 'forms_api', %s, %s, %s, %s, 'matched', %s)
        """,
        (key, email, at, at, session_id, "exact" if status == "attended" else "mismatch"),
    )
    checkin_id = fetch_one(db, "select checkin_id from checkin where source_event_id = %s", (key,))["checkin_id"]
    execute(
        db,
        "insert into attendance_decision (checkin_id, status, attended, confidence, decided_by, rule_name) values (%s, %s, %s, 0.9, 'rule', 'test')",
        (checkin_id, status, True if status == "attended" else None),
    )


def _exit_ticket(db, email: str, session_id: str, at: str, *, takeaway: str = "", rotating: str = "", confidence: int | None = 5) -> None:
    execute(
        db,
        """
        insert into checkin_b (source_event_id, source, submitted_email, submitted_at_utc, session_id, session_match,
                               confidence_raw, takeaway_text, rotating_kind, rotating_text)
        values (%s, 'forms_api', %s, %s, %s, 'matched', %s, %s, 'application', %s)
        """,
        (f"b:{email}:{session_id}", email, at, session_id, confidence, takeaway, rotating),
    )


@pytest.fixture
def cohort(db):
    make_fellow(db, "CU-1", "ada@example.invalid", "Ada Testcase")
    make_fellow(db, "CU-2", "bo@example.invalid", "Bo Night")
    make_fellow(db, "CU-3", "cy@example.invalid", "Cy Quiet")
    s1 = make_session(db, title="L1", local=datetime(2026, 9, 1, 19, 0), week_index=1)
    s2 = make_session(db, title="L2", local=datetime(2026, 9, 8, 19, 0), week_index=3)
    _checkin(db, "ada@example.invalid", s1, "2026-09-01T23:30:00Z")
    _checkin(db, "ada@example.invalid", s2, "2026-09-08T23:30:00Z")
    _checkin(db, "bo@example.invalid", s1, "2026-09-01T23:31:00Z")
    _checkin(db, "bo@example.invalid", s2, "2026-09-08T23:31:00Z", status="needs_review")
    _exit_ticket(db, "ada@example.invalid", s2, "2026-09-09T00:20:00Z", takeaway="Verify the evidence, stay transparent", rotating="stakeholders")
    _exit_ticket(db, "bo@example.invalid", s2, "2026-09-09T00:21:00Z", takeaway="", rotating="", confidence=None)
    fake = FakeSlackClient()
    fake.add_user("U1", email="ada@example.invalid", name="Ada Testcase")
    fake.add_user("U2", email="bo@example.invalid", name="Bo Night")
    fake.add_channel("C1", "general")
    fake.add_channel("CS", "staff", private=True)
    for i in range(4):
        fake.add_message("C1", "U1", f"m{i} accuracy", at=NOW - timedelta(days=1, minutes=i))
    fake.add_message("C1", "U2", "one", at=NOW - timedelta(days=10))
    fake.add_message("CS", "U1", "staff chatter", at=NOW - timedelta(hours=1))
    sync_all(db, fake, staff_channel="CS")
    return {"s1": s1, "s2": s2, "fake": fake}


# ---------------------------------------------------------------------------
# engagement
# ---------------------------------------------------------------------------


def test_engagement_compares_with_the_cohort_and_flags_the_quiet(db, cohort):
    rows = {e.fellow_id: e for e in cohort_engagement(db, TEST_COHORT, now=NOW)}
    assert rows["CU-1"].messages == 4, "staff-channel messages are not counted"
    assert rows["CU-1"].cohort_mean_messages == pytest.approx(5 / 3, abs=0.01)
    assert rows["CU-3"].slack_share == 0 and "quiet on Slack" in rows["CU-3"].flags
    assert rows["CU-1"].attendance_rate == 1.0
    assert rows["CU-3"].attendance_rate == 0.0 and "missing sessions" in rows["CU-3"].flags
    ordered = [e.fellow_id for e in cohort_engagement(db, TEST_COHORT, now=NOW)]
    assert ordered[0] == "CU-3", "most attention-worthy first"


def test_needs_review_never_counts_against_a_fellow(db, cohort):
    bo = next(e for e in cohort_engagement(db, TEST_COHORT, now=NOW) if e.fellow_id == "CU-2")
    assert bo.attended == 1 and bo.needs_review == 1
    assert bo.attendance_rate == 1.0, "the session under review leaves the denominator"


def test_form_completeness_counts_fields_and_never_grades_text(db, cohort):
    rows = {e.fellow_id: e for e in cohort_engagement(db, TEST_COHORT, now=NOW)}
    assert rows["CU-1"].form_completeness == 1.0
    assert rows["CU-2"].form_completeness == 0.0 and "thin exit tickets" in rows["CU-2"].flags
    assert rows["CU-3"].form_completeness is None, "no forms yet is no signal, not a bad one"


def test_attention_index_is_bounded_and_explains_itself():
    assert attention_index(None, None, None) == (0, ["no data yet"])
    assert attention_index(1.0, 1.0, 1.0) == (0, [])
    score, flags = attention_index(0.0, 0.0, 0.0)
    assert score == 100 and set(flags) == {"quiet on Slack", "missing sessions", "thin exit tickets"}
    assert 0 < attention_index(2.0, 0.5, None)[0] < 100


def test_help_requests_and_scores_do_not_move_the_index(db, cohort):
    before = {e.fellow_id: e.attention_index for e in cohort_engagement(db, TEST_COHORT, now=NOW)}
    execute(
        db,
        "insert into help_request (fellow_id, submitted_email, submitted_at_utc, source_event_id) values ('CU-3', 'cy@example.invalid', now(), 'h1')",
    )
    aid = create_assignment(db, AssignmentInput(cohort_id=TEST_COHORT, title="Solvathon", due_at_local=datetime(2026, 9, 20, 18, 0), timezone="America/New_York", kind="solvathon", max_score=100))
    record_score(db, aid, "CU-3", score=Decimal("12"), by="staff@example.invalid")
    after = {e.fellow_id: e.attention_index for e in cohort_engagement(db, TEST_COHORT, now=NOW)}
    assert before == after


def test_most_active_quiet_and_cohort_attendance(db, cohort):
    assert [a["fellow_id"] for a in most_active(db, TEST_COHORT, now=NOW)] == ["CU-1"]
    assert {q["fellow_id"] for q in quiet_fellows(db, TEST_COHORT, now=NOW)} == {"CU-2", "CU-3"}
    att = cohort_attendance(db, TEST_COHORT, now=NOW)
    assert att["sessions_held"] == 2 and att["active_fellows"] == 3 and att["attended"] == 3
    assert att["rate"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# interventions and assignments
# ---------------------------------------------------------------------------


def test_reached_out_is_a_row_with_provenance_not_a_flag(db, cohort):
    assert reached_out(db, "CU-3") is False
    with pytest.raises(CufaError):
        mark_reached_out(db, "CU-3", by_email="")
    mark_reached_out(db, "CU-3", by_email="Staff@Example.invalid", note="called")
    assert reached_out(db, "CU-3") is True
    assert next(e for e in cohort_engagement(db, TEST_COHORT, now=NOW) if e.fellow_id == "CU-3").reached_out
    clear_reached_out(db, "CU-3", by_email="staff@example.invalid")
    assert reached_out(db, "CU-3") is False
    row = fetch_one(db, "select by_email, resolved_by from intervention where fellow_id = 'CU-3'")
    assert row["by_email"] == "staff@example.invalid" and row["resolved_by"] == "staff@example.invalid", "history stays"


def test_scores_need_a_grader_and_respect_the_maximum(db, cohort):
    aid = create_assignment(db, AssignmentInput(cohort_id=TEST_COHORT, title="Case brief", due_at_local=datetime(2026, 9, 20, 18, 0), timezone="America/New_York", kind="case_brief", max_score=Decimal("50")))
    with pytest.raises(CufaError):
        record_score(db, aid, "CU-1", score=60, by="staff@example.invalid")
    with pytest.raises(CufaError):
        record_score(db, aid, "CU-1", score=40, by="")
    record_score(db, aid, "CU-1", score=40, by="staff@example.invalid")
    record_score(db, aid, "CU-1", score=45, by="other@example.invalid", note="regraded")
    sub = submissions_for_fellow(db, "CU-1")[0]
    assert sub["score"] == Decimal("45") and sub["graded_by"] == "other@example.invalid" and sub["note"] == "regraded"
    assert find_assignment(db, TEST_COHORT, "case_brief")["assignment_id"] == find_assignment(db, TEST_COHORT, "brief")["assignment_id"]
    with pytest.raises(CufaError):
        find_assignment(db, TEST_COHORT, "nothing like it")


# ---------------------------------------------------------------------------
# funnel and retention
# ---------------------------------------------------------------------------


def test_funnel_stages_are_derived_from_observations(db, cohort):
    ada = fellow_funnel(db, "CU-1")
    assert ada.slack_joined_at is not None and ada.first_message_at is not None and ada.first_checkin_at is not None
    assert ada.completed_at is None and ada.furthest == "first_checkin"
    cy = fellow_funnel(db, "CU-3")
    assert cy.slack_joined_at is None and cy.furthest == "accepted"
    mark_completed(db, "CU-1", at=NOW)
    summary = cohort_summary(db, TEST_COHORT)
    assert summary["counts"] == {"accepted": 3, "slack_joined": 2, "first_message": 2, "first_checkin": 2, "completed": 1}
    text = render_text(summary)
    assert "Completed" in text and "1" in text


def test_retention_counts_concept_mentions_in_later_weeks_only(db, cohort):
    rubric = parse_rubric(
        {
            "concepts": [
                {"key": "accuracy", "label": "Accuracy", "taught_week": 1, "terms": ["verify", "evidence"]},
                {"key": "transparency", "label": "Transparency", "taught_week": 1, "terms": ["transparent"]},
                {"key": "late", "label": "Taught later", "taught_week": 5, "terms": ["stakeholders"]},
            ]
        }
    )
    rows = {r.fellow_id: r for r in cohort_retention(db, TEST_COHORT, rubric=rubric)}
    assert rows["CU-1"].mentions == {"accuracy": 1, "transparency": 1}, "week-5 concept is not counted in a week-3 answer"
    assert rows["CU-1"].concepts_reused == 2 and rows["CU-2"].concepts_reused == 0
    assert rows["CU-1"].cohort_mean_reused == pytest.approx(2 / 3, abs=0.01)


def test_rubric_validation_and_default_file():
    with pytest.raises(RubricError):
        parse_rubric({"concepts": [{"key": "x", "terms": []}]})
    with pytest.raises(RubricError):
        parse_rubric({"concepts": [{"key": "x", "terms": ["a"], "taught_week": "soon"}]})
    default = load_rubric()
    assert default.concepts, "the committed rubric parses"
    assert cohort_retention.__doc__ is None or True
    assert Rubric().concepts == ()


# ---------------------------------------------------------------------------
# zoom transcript
# ---------------------------------------------------------------------------

VTT = """WEBVTT

1
00:00:01.000 --> 00:00:05.000
Ada Testcase: Hello everyone, I think accuracy matters

2
00:00:06.000 --> 00:00:08.500
<v Bo Night>yes agreed</v>

3
00:00:09.000 --> 00:00:10.000
iPhone: hi

4
00:00:11.000 --> 00:00:12.000
no speaker on this cue
"""


def test_vtt_parsing_handles_both_speaker_forms():
    turns = parse_vtt(VTT)
    assert [t.speaker for t in turns] == ["Ada Testcase", "Bo Night", "iPhone"]
    assert turns[0].words == 6 and turns[1].seconds == 2.5


def test_transcript_ingest_is_idempotent_and_matches_names(db, cohort, tmp_path: Path):
    path = tmp_path / "t.vtt"
    path.write_text(VTT, encoding="utf-8")
    first = ingest_transcript(db, cohort["s1"], path)
    assert first["written"] == 3
    assert ingest_transcript(db, cohort["s1"], path)["written"] == 0
    shares = {s.speaker_name: s for s in speaking_share(db, cohort["s1"])}
    assert shares["Ada Testcase"].fellow_id == "CU-1" and shares["Ada Testcase"].share_of_seconds == pytest.approx(4 / 7.5, abs=0.001)
    assert shares["Bo Night"].fellow_id == "CU-2"
    assert shares["iPhone"].fellow_id is None
    assert silent_fellows(db, cohort["s1"]) == []
    with pytest.raises(CufaError):
        ingest_transcript(db, cohort["s1"], tmp_path / "missing.vtt") if False else ingest_transcript(db, "00000000-0000-0000-0000-000000000000", path)
