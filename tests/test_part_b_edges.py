"""Edge cases that would be invisible if they were wrong.

Everything here is a case where a bug produces plausible data rather than an
error: a name split down the middle, a confidence value that parses to something
nobody selected, a rotating answer filed under the wrong week's question type.

The shared-question-id block at the top exists because `files.copy` was
confirmed in August 2026 to **preserve** question ids — so every Part B form
copied from one template carries the *same* ids, and the rotating slot's id is
identical in week 2 and week 5. Anything that keyed on the id alone would have
looked correct in every test written before that was known.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import psycopg
import pytest

from cufa.db import execute, fetch_all, fetch_one
from cufa.form_content_b import (
    HELP_OPTION,
    SLOT_CONFIDENCE,
    SLOT_ROTATING,
    SLOT_SHOUTOUT,
    SLOT_TAKEAWAY,
)
from cufa.google.fake import (
    QUESTION_IDS_PRESERVED,
    QUESTION_IDS_REGENERATED,
    FakeGoogleClient,
)
from cufa.help_routing import HelpRouting, Recipient, RecordingNotifier
from cufa.ingest.common import parse_confidence
from cufa.ingest.forms_b import _ticked, pull_cohort_b, pull_session_b
from cufa.provisioning import provision_session
from cufa.question_map import load_map
from cufa.rotation import RotationConfigError, load_rotation
from cufa.shoutouts import normalize_name, split_names
from cufa.template import create_template, verify_template

from conftest import TEST_COHORT, make_fellow, make_session, seed_part_b

ROUTED = HelpRouting(recipients=(Recipient("DoP", "dop@example.invalid"),))
SESSION_LOCAL = datetime(2026, 9, 15, 19, 0)


def _fake(scheme: str = QUESTION_IDS_PRESERVED) -> FakeGoogleClient:
    from cufa.google.factory import set_fake_client

    client = FakeGoogleClient(question_id_scheme=scheme, page_size=5)
    set_fake_client(client)
    return client


def _template(db, fake) -> None:
    record = create_template(db, fake, "b")
    fake.simulate_human_sets_verified(record.form_id)
    verify_template(db, fake, "b")


def _session(db, fake, *, week: int, title: str, offset_weeks: int = 0):
    import cufa.provisioning as provisioning

    session_id = make_session(
        db,
        title=title,
        local=SESSION_LOCAL + timedelta(weeks=offset_weeks),
        week_index=week,
        teacher_question="What surprised you?",
    )
    original = provisioning.get_help_routing
    provisioning.get_help_routing = lambda *a, **k: ROUTED
    try:
        result = provision_session(db, fake, session_id, part="b")
    finally:
        provisioning.get_help_routing = original
    return session_id, result


def _pull(db, fake, session_id):
    return pull_session_b(
        db, fake, session_id, routing=ROUTED, notifier=RecordingNotifier()
    )


# ---------------------------------------------------------------------------
# shared question ids across forms
# ---------------------------------------------------------------------------


def test_two_forms_from_one_template_share_question_ids(db):
    """Confirmed against the live API in August 2026: copies preserve ids.

    The fake's default reproduces that. This test pins the premise the next
    three depend on.
    """
    fake = _fake(QUESTION_IDS_PRESERVED)
    _template(db, fake)
    _s1, week2 = _session(db, fake, week=2, title="Week 2")
    _s2, week5 = _session(db, fake, week=5, title="Week 5", offset_weeks=3)

    ids_2 = {m.slot: m.question_id for m in load_map(db, week2.form_id).values()}
    ids_5 = {m.slot: m.question_id for m in load_map(db, week5.form_id).values()}

    # The four copied from the template are identical, including the rotating
    # slot — whose *text* differs between the two forms.
    for slot in (SLOT_CONFIDENCE, SLOT_TAKEAWAY, SLOT_ROTATING, SLOT_SHOUTOUT):
        assert ids_2[slot] == ids_5[slot], slot


def test_the_rotating_slots_text_differs_per_form_though_the_id_does_not(db):
    """This is why the text is snapshotted per form.

    The question id says nothing about which week's question it was — it is the
    same id in week 2 and week 5 — so "what was asked in week 3" is answerable
    only from ``form_question_map.question_text``.
    """
    fake = _fake(QUESTION_IDS_PRESERVED)
    _template(db, fake)
    _s1, week2 = _session(db, fake, week=2, title="Week 2")
    _s2, week3 = _session(db, fake, week=3, title="Week 3", offset_weeks=1)

    m2 = next(m for m in load_map(db, week2.form_id).values() if m.slot == SLOT_ROTATING)
    m3 = next(m for m in load_map(db, week3.form_id).values() if m.slot == SLOT_ROTATING)

    assert m2.question_id == m3.question_id, "same id"
    assert m2.question_text != m3.question_text, "different question"
    assert m2.rotating_kind == "muddiest_point"
    assert m3.rotating_kind == "application"


def test_answers_are_never_cross_attributed_between_forms_sharing_ids(db):
    """The failure this would cause is silent: a week-3 application answer filed
    as a week-2 muddiest point, feeding the wrong themes."""
    fake = _fake(QUESTION_IDS_PRESERVED)
    _template(db, fake)
    make_fellow(db, "CU-1", "ada@example.invalid", "Ada Testcase")
    s2, week2 = _session(db, fake, week=2, title="Week 2")
    s3, week3 = _session(db, fake, week=3, title="Week 3", offset_weeks=1)

    seed_part_b(db, fake, week2.form_id, [{
        "email": "ada@example.invalid",
        "submitted_at": "2026-09-16T00:20:00Z",
        "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "t2",
                  SLOT_ROTATING: "I do not get the surplus."},
    }])
    seed_part_b(db, fake, week3.form_id, [{
        "email": "ada@example.invalid",
        "submitted_at": "2026-09-23T00:20:00Z",
        "slots": {SLOT_CONFIDENCE: "6", SLOT_TAKEAWAY: "t3",
                  SLOT_ROTATING: "I would map the bus route."},
    }])
    _pull(db, fake, s2)
    _pull(db, fake, s3)

    rows = {
        str(r["session_id"]): r
        for r in fetch_all(
            db, "select session_id, rotating_kind, rotating_text from checkin_b"
        )
    }
    assert rows[s2]["rotating_kind"] == "muddiest_point"
    assert rows[s2]["rotating_text"] == "I do not get the surplus."
    assert rows[s3]["rotating_kind"] == "application"
    assert rows[s3]["rotating_text"] == "I would map the bus route."

    # And clustering only ever sees the muddiest-point one.
    from cufa.themes import muddiest_answers

    assert len(muddiest_answers(db, s2)) == 1
    assert muddiest_answers(db, s3) == []


def test_the_help_field_gets_a_fresh_id_per_form(db):
    """It is created on the copy, not carried by it — the template has four
    items, not five."""
    fake = _fake(QUESTION_IDS_PRESERVED)
    _template(db, fake)
    _s1, week2 = _session(db, fake, week=2, title="Week 2")
    _s2, week5 = _session(db, fake, week=5, title="Week 5", offset_weeks=3)

    help_2 = next(m for m in load_map(db, week2.form_id).values() if m.slot == "help")
    help_5 = next(m for m in load_map(db, week5.form_id).values() if m.slot == "help")
    assert help_2.question_id != help_5.question_id


# ---------------------------------------------------------------------------
# names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        # Apostrophes and hyphens are parts of names, not separators.
        ("Siobhán O'Brien", ["Siobhán O'Brien"]),
        ("Anne-Marie Dubois", ["Anne-Marie Dubois"]),
        ("Anne-Marie and Jean-Luc", ["Anne-Marie", "Jean-Luc"]),
        # "and" only splits as a whole word.
        ("Alexander Andrade", ["Alexander Andrade"]),
        ("Amanda", ["Amanda"]),
        ("Sandy", ["Sandy"]),
        ("Andrea and Sandra", ["Andrea", "Sandra"]),
        # Trailing separators leave no empty fragment.
        ("Kestrel and", ["Kestrel"]),
        ("Kestrel, ", ["Kestrel"]),
        (", , ,", []),
        ("and", []),
        # Decoration is stripped for the fragment, not for matching only.
        ("@kestrel", ["kestrel"]),
        ("- Lorne", ["Lorne"]),
        ("“Marisol”", ["Marisol"]),
        ("Marisol!!!", ["Marisol"]),
        # Emoji is not a name but is also not a crash.
        ("🙂", ["🙂"]),
        # Several separators at once.
        ("A, B & C and D", ["A", "B", "C", "D"]),
    ],
)
def test_names_split_the_way_a_person_would_read_them(text, expected):
    assert split_names(text) == expected


@pytest.mark.parametrize(
    "a,b",
    [
        ("Siobhán", "siobhán"),
        ("  Marisol   Mossgate ", "marisol mossgate"),
        ("MARISOL", "marisol"),
        # NFKC: a full-width character typed on a phone keyboard.
        ("Ｍarisol", "marisol"),
    ],
)
def test_names_normalize_for_comparison(a, b):
    assert normalize_name(a) == b


def test_an_accented_roster_name_still_matches(db):
    """A fellow whose name has an accent must not be permanently unresolvable."""
    fake = _fake()
    _template(db, fake)
    make_fellow(db, "CU-1", "siobhan@example.invalid", "Siobhán O'Brien")
    session_id, result = _session(db, fake, week=2, title="Week 2")

    seed_part_b(db, fake, result.form_id, [{
        "email": "other@example.invalid",
        "submitted_at": "2026-09-16T00:20:00Z",
        "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "x",
                  SLOT_SHOUTOUT: "siobhán o'brien"},
    }])
    _pull(db, fake, session_id)

    row = fetch_one(db, "select match_method, named_fellow_id from peer_shoutout")
    assert row["match_method"] == "exact_name"
    assert row["named_fellow_id"] == "CU-1"


# ---------------------------------------------------------------------------
# confidence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,value,rejected",
    [
        ("5", 5, None),
        ("  5  ", 5, None),
        ("1", 1, None),
        ("7", 7, None),
        ("", None, None),
        ("   ", None, None),
        (None, None, None),
        ("0", None, "0"),
        ("8", None, "8"),
        ("-1", None, "-1"),
        ("four", None, "four"),
        ("5.0", None, "5.0"),
        ("5 out of 7", None, "5 out of 7"),
        # "+5" parses as 5 in Python. It is inside the scale and unambiguous.
        ("+5", 5, None),
    ],
)
def test_confidence_parses_or_rejects_but_never_invents(raw, value, rejected):
    assert parse_confidence(raw) == (value, rejected)


def test_a_rejected_confidence_never_lands_inside_the_scale(db):
    """The failure mode is a clamped 8 reading as a genuine 7."""
    fake = _fake()
    _template(db, fake)
    session_id, result = _session(db, fake, week=2, title="Week 2")
    seed_part_b(db, fake, result.form_id, [
        {"email": f"f{i}@example.invalid", "submitted_at": f"2026-09-16T00:2{i}:00Z",
         "slots": {SLOT_CONFIDENCE: raw, SLOT_TAKEAWAY: "x"}}
        for i, raw in enumerate(["0", "8", "four", "99"])
    ])
    _pull(db, fake, session_id)

    rows = fetch_all(db, "select confidence_raw, extra_fields from checkin_b")
    assert len(rows) == 4
    assert all(r["confidence_raw"] is None for r in rows)
    assert {r["extra_fields"]["_confidence_rejected_raw"] for r in rows} == {
        "0", "8", "four", "99"
    }


# ---------------------------------------------------------------------------
# the help checkbox as it actually arrives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,ticked",
    [
        (HELP_OPTION, True),
        (HELP_OPTION.upper(), True),
        ("", False),
        ("   ", False),
        (None, False),
        # An unexpected shape is treated as ticked. Not answering someone who
        # raised their hand is the worse error.
        ("Yes", True),
    ],
)
def test_the_help_checkbox_errs_towards_answering(value, ticked):
    assert _ticked(value) is ticked


# ---------------------------------------------------------------------------
# straight-lining, which is easy to get subtly wrong in SQL
# ---------------------------------------------------------------------------


def _five_weeks(db, values: dict[str, list[int | None]]):
    """Provision five weekly sessions and submit the given values per fellow."""
    fake = _fake()
    _template(db, fake)
    for email in values:
        make_fellow(db, f"CU-{email[:4]}", email, f"Fellow {email[:4]}")

    sessions = []
    for week in range(1, 6):
        session_id, result = _session(
            db, fake, week=week, title=f"Week {week}", offset_weeks=week - 1
        )
        stamp = (SESSION_LOCAL + timedelta(weeks=week - 1, hours=4, minutes=30)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        rows = []
        for email, series in values.items():
            value = series[week - 1]
            if value is None:
                continue  # did not submit that week
            rows.append({
                "email": email,
                "submitted_at": stamp,
                "slots": {SLOT_CONFIDENCE: str(value), SLOT_TAKEAWAY: "x"},
            })
        if rows:
            seed_part_b(db, fake, result.form_id, rows)
        _pull(db, fake, session_id)
        sessions.append(session_id)
    return sessions


def test_five_identical_values_are_one_run_of_five(db):
    _five_weeks(db, {"steady@example.invalid": [4, 4, 4, 4, 4]})
    runs = fetch_all(db, "select fellow_id, confidence_raw, run_length from v_confidence_straightline")
    assert len(runs) == 1
    assert runs[0]["run_length"] == 5


def test_a_run_broken_in_the_middle_is_not_flagged(db):
    """4,4,5,4,4 is two runs of two — neither reaches the threshold."""
    _five_weeks(db, {"varied@example.invalid": [4, 4, 5, 4, 4]})
    assert fetch_all(db, "select * from v_confidence_straightline") == []


def test_a_missed_week_does_not_break_a_run(db):
    """"Consecutive" means consecutive among the sessions they answered.

    A fellow who misses week 3 and answers 4 either side has still given the
    same answer four times running, which is the thing being flagged.
    """
    _five_weeks(db, {"gappy@example.invalid": [4, 4, None, 4, 4]})
    runs = fetch_all(db, "select run_length from v_confidence_straightline")
    assert len(runs) == 1
    assert runs[0]["run_length"] == 4


def test_two_fellows_are_counted_separately(db):
    _five_weeks(
        db,
        {
            "steady@example.invalid": [4, 4, 4, 4, 4],
            "varied@example.invalid": [1, 2, 3, 4, 5],
        },
    )
    runs = {r["fellow_id"]: r["run_length"] for r in fetch_all(
        db, "select fellow_id, run_length from v_confidence_straightline")}
    assert len(runs) == 1
    assert list(runs.values()) == [5]


def test_a_rejected_confidence_does_not_join_two_runs(db):
    """4,4,<invalid>,4,4 — the invalid week stores NULL and is excluded, so the
    two runs of two are adjacent among *answered* weeks and become a run of
    four. That is correct: the fellow did answer 4 four times running."""
    fake = _fake()
    _template(db, fake)
    make_fellow(db, "CU-1", "steady@example.invalid", "Steady")
    for week in range(1, 6):
        session_id, result = _session(
            db, fake, week=week, title=f"Week {week}", offset_weeks=week - 1
        )
        raw = "99" if week == 3 else "4"
        stamp = (SESSION_LOCAL + timedelta(weeks=week - 1, hours=4, minutes=30)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        seed_part_b(db, fake, result.form_id, [{
            "email": "steady@example.invalid", "submitted_at": stamp,
            "slots": {SLOT_CONFIDENCE: raw, SLOT_TAKEAWAY: "x"},
        }])
        _pull(db, fake, session_id)

    runs = fetch_all(db, "select run_length from v_confidence_straightline")
    assert len(runs) == 1
    assert runs[0]["run_length"] == 4


# ---------------------------------------------------------------------------
# reads that have to survive awkward data
# ---------------------------------------------------------------------------


def test_a_session_with_no_week_still_appears_in_the_confidence_trend(db):
    """An unnumbered session is legal. Dropping it would make the trend look
    complete when it is not."""
    fake = _fake()
    _template(db, fake)

    import cufa.provisioning as provisioning

    numbered, result = _session(db, fake, week=2, title="Week 2")
    seed_part_b(db, fake, result.form_id, [{
        "email": "a@example.invalid", "submitted_at": "2026-09-16T00:20:00Z",
        "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "x"}}])
    _pull(db, fake, numbered)

    # A session with no week gets a Part B row written directly: it can have no
    # form, so this is the only way that state arises.
    makeup = make_session(db, title="Makeup", local=SESSION_LOCAL, week_index=None)
    execute(
        db,
        "insert into checkin_b (source_event_id, source, submitted_email, "
        "submitted_at_utc, session_id, session_match, confidence_raw) "
        "values ('manual-1', 'csv', 'b@example.invalid', now(), %s, 'matched', 3)",
        (makeup,),
    )

    from cufa.confidence import trend

    rows = trend(db, TEST_COHORT)
    weeks = [r["week_index"] for r in rows]
    assert 2 in weeks
    assert None in weeks, "the unnumbered session must still be visible"
    # nulls last, so the numbered weeks read in order.
    assert weeks[-1] is None


def test_the_json_report_serialises_every_value_it_carries(db):
    """`cufa report --json` has to survive Decimal, UUID and datetime."""
    import json

    fake = _fake()
    _template(db, fake)
    make_fellow(db, "CU-1", "ada@example.invalid", "Ada")
    session_id, result = _session(db, fake, week=2, title="Week 2")
    seed_part_b(db, fake, result.form_id, [{
        "email": "ada@example.invalid", "submitted_at": "2026-09-16T00:20:00Z",
        "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "x", SLOT_SHOUTOUT: "Ada"}}])
    _pull(db, fake, session_id)

    from cufa.report import cohort_report, render_report_text

    report = cohort_report(db, TEST_COHORT)
    encoded = json.dumps(report.to_dict(), default=str)
    assert "part_b" in encoded
    # And the text renderer copes with the same data.
    text = render_report_text(report)
    assert "Part B — end-of-session check-in" in text
    assert "responses" in text


def test_a_shoutout_confidence_is_a_number_the_report_can_add_up(db):
    fake = _fake()
    _template(db, fake)
    make_fellow(db, "CU-1", "ada@example.invalid", "Ada")
    session_id, result = _session(db, fake, week=2, title="Week 2")
    seed_part_b(db, fake, result.form_id, [{
        "email": "b@example.invalid", "submitted_at": "2026-09-16T00:20:00Z",
        "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "x", SLOT_SHOUTOUT: "Ada"}}])
    _pull(db, fake, session_id)
    row = fetch_one(db, "select confidence from peer_shoutout")
    assert isinstance(row["confidence"], Decimal)
    assert row["confidence"] == Decimal("1.000")


# ---------------------------------------------------------------------------
# constraints that exist to stop bad rows, not to be decoration
# ---------------------------------------------------------------------------


def test_an_unresolved_shoutout_cannot_carry_a_fellow(db):
    fake = _fake()
    _template(db, fake)
    make_fellow(db, "CU-1", "ada@example.invalid", "Ada")
    session_id, result = _session(db, fake, week=2, title="Week 2")
    seed_part_b(db, fake, result.form_id, [{
        "email": "b@example.invalid", "submitted_at": "2026-09-16T00:20:00Z",
        "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "x"}}])
    _pull(db, fake, session_id)
    checkin_b_id = fetch_one(db, "select checkin_b_id from checkin_b")["checkin_b_id"]

    with pytest.raises(psycopg.errors.CheckViolation):
        execute(
            db,
            "insert into peer_shoutout (checkin_b_id, raw_text, named_fellow_id, match_method) "
            "values (%s, 'x', 'CU-1', 'unresolved')",
            (checkin_b_id,),
        )
    db.rollback()


def test_a_manual_link_must_name_who_made_it(db):
    fake = _fake()
    _template(db, fake)
    make_fellow(db, "CU-1", "ada@example.invalid", "Ada")
    session_id, result = _session(db, fake, week=2, title="Week 2")
    seed_part_b(db, fake, result.form_id, [{
        "email": "b@example.invalid", "submitted_at": "2026-09-16T00:20:00Z",
        "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "x"}}])
    _pull(db, fake, session_id)
    checkin_b_id = fetch_one(db, "select checkin_b_id from checkin_b")["checkin_b_id"]

    with pytest.raises(psycopg.errors.CheckViolation):
        execute(
            db,
            "insert into peer_shoutout (checkin_b_id, raw_text, named_fellow_id, match_method) "
            "values (%s, 'x', 'CU-1', 'manual')",
            (checkin_b_id,),
        )
    db.rollback()


def test_an_open_help_request_cannot_claim_to_be_acknowledged(db):
    with pytest.raises(psycopg.errors.CheckViolation):
        execute(
            db,
            "insert into help_request (source_event_id, submitted_email, "
            "submitted_at_utc, status, acknowledged_by) "
            "values ('e1', 'a@example.invalid', now(), 'open', 'someone@x.invalid')",
        )
    db.rollback()


def test_checkin_b_latency_may_be_recomputed_but_nothing_else(db):
    fake = _fake()
    _template(db, fake)
    session_id, result = _session(db, fake, week=2, title="Week 2")
    seed_part_b(db, fake, result.form_id, [{
        "email": "a@example.invalid", "submitted_at": "2026-09-16T00:20:00Z",
        "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "original"}}])
    _pull(db, fake, session_id)

    # Allowed: it is derived from session state that legitimately moves.
    execute(db, "update checkin_b set latency_seconds = 42")
    assert fetch_one(db, "select latency_seconds from checkin_b")["latency_seconds"] == 42

    for column, value in [
        ("takeaway_text", "'edited'"),
        ("confidence_raw", "1"),
        ("submitted_email", "'someone.else@example.invalid'"),
        ("rotating_text", "'edited'"),
    ]:
        with pytest.raises(psycopg.errors.RestrictViolation):
            execute(db, f"update checkin_b set {column} = {value}")
        db.rollback()


# ---------------------------------------------------------------------------
# rotation arithmetic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("week", [11, 12, 20, 21, 101, 1000])
def test_wrapping_is_exact_modular_arithmetic(week):
    rotation = load_rotation()
    expected = ((week - 1) % rotation.weeks) + 1
    assert rotation.schedule_week_for(week) == expected
    assert rotation.kind_for(week) == rotation.kind_for(expected)


@pytest.mark.parametrize("week", [0, -1, -100])
def test_week_numbering_starts_at_one(week):
    with pytest.raises(RotationConfigError):
        load_rotation().schedule_week_for(week)


def test_a_wrapped_week_still_needs_its_own_teacher_question(db):
    """Week 11 repeats week 1's *kind*, not week 1's question."""
    from cufa.rotation import TeacherQuestionMissing

    rotation = load_rotation()
    assert rotation.kind_for(11) == "teacher_question"
    with pytest.raises(TeacherQuestionMissing):
        rotation.resolve(11, teacher_question=None)
    slot = rotation.resolve(11, teacher_question="Week eleven's own question")
    assert slot.text == "Week eleven's own question"
    assert slot.wrapped


# ---------------------------------------------------------------------------
# cohort-level pull
# ---------------------------------------------------------------------------


def test_pulling_a_cohort_skips_sessions_with_no_part_b_form(db):
    fake = _fake()
    _template(db, fake)
    session_id, result = _session(db, fake, week=2, title="Week 2")
    make_session(db, title="No Part B", local=SESSION_LOCAL, week_index=None)

    seed_part_b(db, fake, result.form_id, [{
        "email": "a@example.invalid", "submitted_at": "2026-09-16T00:20:00Z",
        "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "x"}}])

    outcome = pull_cohort_b(
        db, fake, TEST_COHORT, routing=ROUTED, notifier=RecordingNotifier()
    )
    assert outcome.rows_written == 1


def test_an_unverified_form_warns_rather_than_refusing_to_pull(db):
    """Trap 1: it may be accepting nothing. Any responses that did arrive are
    still worth having."""
    fake = _fake()
    _template(db, fake)
    session_id, result = _session(db, fake, week=2, title="Week 2")
    execute(
        db,
        "update session_form set publish_verified_at = null "
        "where session_id = %s and part = 'b'",
        (session_id,),
    )
    seed_part_b(db, fake, result.form_id, [{
        "email": "a@example.invalid", "submitted_at": "2026-09-16T00:20:00Z",
        "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "x"}}])

    outcome = _pull(db, fake, session_id)
    assert outcome.rows_written == 1
    assert any("trap 1" in w for w in outcome.warnings)


@pytest.mark.parametrize("scheme", [QUESTION_IDS_PRESERVED, QUESTION_IDS_REGENERATED])
def test_a_teacher_added_question_lands_in_extra_fields(db, scheme):
    """Not dropped. Somebody asked it and somebody answered it."""
    fake = _fake(scheme)
    _template(db, fake)
    session_id, result = _session(db, fake, week=2, title="Week 2")

    fake.batch_update(result.form_id, [{
        "createItem": {
            "item": {"title": "Anything else?",
                     "questionItem": {"question": {"required": False,
                                                   "textQuestion": {"paragraph": True}}}},
            "location": {"index": 5},
        }
    }])
    extra_qid = fake.get_form(result.form_id).items[5].question_id

    seed_part_b(db, fake, result.form_id, [{
        "email": "a@example.invalid", "submitted_at": "2026-09-16T00:20:00Z",
        "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "x"},
        "answers_by_id": {extra_qid: "I liked the guest speaker."},
    }])
    _pull(db, fake, session_id)

    extra = fetch_one(db, "select extra_fields from checkin_b")["extra_fields"]
    assert extra.get(extra_qid) == "I liked the guest speaker."


# ---------------------------------------------------------------------------
# warning bookkeeping
#
# Found while testing Part B, but the bug was in Part A's IngestResult and hit
# both. Three separate corruptions, all of which quietly removed the message an
# operator most needs.
# ---------------------------------------------------------------------------


def test_a_directly_appended_warning_is_not_dropped_by_finalising():
    """Trap 1's warning is appended directly, before any counted one.

    The old implementation zipped the counted-warning dict against the list and
    rebuilt it, so the shorter of the two truncated the other — and the trap-1
    message, which is the whole explanation for why nothing is arriving, went
    with it.
    """
    from cufa.ingest.common import IngestResult

    result = IngestResult()
    result.warnings.append("trap 1: this form may be accepting nothing")
    result.warn("overlapping session windows")
    result.finalize_warnings()

    assert len(result.warnings) == 2
    assert any("trap 1" in w for w in result.warnings)
    assert any("overlapping" in w for w in result.warnings)


def test_the_repeat_count_lands_on_the_warning_that_repeated():
    from cufa.ingest.common import IngestResult

    result = IngestResult()
    result.warnings.append("trap 1: this form may be accepting nothing")
    result.warn("config error: outside the window")
    result.warn("config error: outside the window")
    result.finalize_warnings()

    trap, repeated = result.warnings
    assert "×" not in trap, "the multiplier must not be attached to trap 1"
    assert "config error" in repeated and "2×" in repeated


def test_a_cohort_rollup_keeps_the_warnings_it_collected():
    """`pull_cohort` extends `warnings` and never calls `warn`, so the counted
    dict is empty. Rebuilding from it emptied the list."""
    from cufa.ingest.common import IngestResult

    combined = IngestResult()
    combined.warnings.extend(["from session 1", "from session 2"])
    combined.finalize_warnings()
    assert combined.warnings == ["from session 1", "from session 2"]


def test_finalising_twice_does_not_double_the_suffix():
    from cufa.ingest.common import IngestResult

    result = IngestResult()
    result.warn("something")
    result.warn("something")
    result.finalize_warnings()
    result.finalize_warnings()
    assert result.warnings == ["something [2× in this run]"]


# ---------------------------------------------------------------------------
# batches
#
# A cohort run touches ten sessions and ten real Google forms. Treating it as
# one all-or-nothing unit is wrong in both directions: the database rolls back
# and Drive does not.
# ---------------------------------------------------------------------------


def test_one_blocked_session_does_not_stop_the_rest_of_the_cohort(db, capsys):
    """Week 1 needs a teacher question and has none; weeks 2 and 3 are fine.

    Before this was fixed, the batch aborted on week 1 and provisioned nothing —
    ordered by date, the blocked session came first and took the other two with
    it.
    """
    from cufa.cli import main

    fake = _fake()
    _template(db, fake)
    for week in (1, 2, 3):
        make_session(
            db,
            title=f"W{week}",
            local=SESSION_LOCAL + timedelta(weeks=week),
            week_index=week,
            teacher_question=None,  # only week 1 actually needs one
            passphrase=f"p{week}",
        )
    db.commit()

    exit_code = main(["provision", "--cohort", TEST_COHORT, "--part", "b"])
    out = capsys.readouterr()

    assert exit_code == 1, "a blocked session still makes the run fail"
    provisioned = {
        row["title"]: row["form_id"]
        for row in fetch_all(
            db,
            'select s.title, f.form_id from "session" s '
            "left join session_form f on f.session_id = s.session_id and f.part = 'b' "
            "where s.cohort_id = %s",
            (TEST_COHORT,),
        )
    }
    assert provisioned["W2"] is not None
    assert provisioned["W3"] is not None
    assert provisioned["W1"] is None

    # And the operator is told both halves: what failed, and how many did not.
    assert "W1" in out.err
    assert "teacher" in out.err.lower()
    assert "2 session(s) provisioned, 1 failed" in out.err


def test_a_failure_partway_through_a_batch_does_not_orphan_earlier_forms(db, capsys):
    """The database rolls back; Google does not.

    Running the whole batch in one transaction meant a late failure discarded
    the `session_form` rows of everything already provisioned — while those
    forms stayed in Drive with nothing pointing at them. A retry then copied a
    second form for each.
    """
    from cufa.cli import main

    fake = _fake()
    _template(db, fake)
    # Week 3 is fine and comes first by date; week 4 needs a question and does not.
    make_session(db, title="First", local=SESSION_LOCAL, week_index=3,
                 passphrase="p1")
    make_session(db, title="Second", local=SESSION_LOCAL + timedelta(weeks=1),
                 week_index=4, teacher_question=None, passphrase="p2")
    db.commit()

    assert main(["provision", "--cohort", TEST_COHORT, "--part", "b"]) == 1
    capsys.readouterr()

    row = fetch_one(
        db,
        'select f.form_id from "session" s '
        "join session_form f on f.session_id = s.session_id and f.part = 'b' "
        "where s.title = 'First'",
    )
    assert row is not None, "the form provisioned before the failure kept its row"
    assert row["form_id"] in fake.forms

    # Every form the fake created is accounted for in the database — no orphans.
    tracked = {
        r["form_id"] for r in fetch_all(db, "select form_id from session_form")
    } | {r["form_id"] for r in fetch_all(db, "select form_id from form_template")}
    assert set(fake.forms) <= tracked, f"orphaned in Drive: {set(fake.forms) - tracked}"


def test_a_form_that_refuses_to_ingest_does_not_block_the_cohort(db):
    """An incomplete question map must refuse *that form*, not the other nine."""
    fake = _fake()
    _template(db, fake)
    good, good_form = _session(db, fake, week=2, title="Good")
    bad, bad_form = _session(db, fake, week=3, title="Bad", offset_weeks=1)

    for form in (good_form, bad_form):
        seed_part_b(db, fake, form.form_id, [{
            "email": "a@example.invalid", "submitted_at": "2026-09-16T00:20:00Z",
            "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "x"}}])

    execute(
        db,
        "delete from form_question_map where form_id = %s and slot = 'takeaway'",
        (bad_form.form_id,),
    )

    outcome = pull_cohort_b(
        db, fake, TEST_COHORT, routing=ROUTED, notifier=RecordingNotifier()
    )

    assert outcome.rows_written == 1, "the healthy session was still collected"
    assert any(bad in w and "could not be pulled" in w for w in outcome.warnings)
    assert fetch_one(
        db, "select count(*) as n from checkin_b where session_id = %s", (good,)
    )["n"] == 1
    assert fetch_one(
        db, "select count(*) as n from checkin_b where session_id = %s", (bad,)
    )["n"] == 0


# ---------------------------------------------------------------------------
# every session_form query has to name a part
# ---------------------------------------------------------------------------


def test_every_session_form_query_is_scoped_to_a_part():
    """`session_form` gained a second row per session when Part B arrived.

    Any query that joined it without naming a part silently began returning
    whichever row Postgres felt like. That is how the demo started seeding Part A
    responses into Part B forms: the lookup was correct in Part A, was never
    revisited, and produced plausible-looking output — two forms with no
    responses and two with too many — rather than an error.

    A static scan rather than a runtime one, because the bug is in queries that
    are only reached on particular paths. The rule is simple enough to check
    mechanically: a reference to `session_form` must mention `part` nearby.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    # The one deliberate exception: this check counts unverified forms across
    # BOTH parts, which is the point of it.
    allowed = {(root / "scripts" / "verify_demo.py").resolve()}

    unscoped: list[str] = []
    for directory in ("src/cufa", "scripts"):
        for path in (root / directory).rglob("*.py"):
            if path.resolve() in allowed:
                continue
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"(join|from)\s+session_form(\s+\w+)?", text):
                window = text[match.start(): match.start() + 260]
                if "part" not in window:
                    line = text[: match.start()].count("\n") + 1
                    unscoped.append(f"{path.name}:{line}")

    assert unscoped == [], (
        "session_form queries that do not name a part: " + ", ".join(unscoped)
    )


def test_a_partial_cohort_pull_exits_non_zero(db, capsys):
    """Continuing past a broken session is right; doing it silently is not.

    A scheduled pull that collected nine sessions and missed one must be
    distinguishable from one that collected all ten, or nobody finds out until
    the numbers are needed.
    """
    from cufa.cli import main

    fake = _fake()
    _template(db, fake)
    good, good_form = _session(db, fake, week=2, title="Good")
    bad, bad_form = _session(db, fake, week=3, title="Bad", offset_weeks=1)
    for form in (good_form, bad_form):
        seed_part_b(db, fake, form.form_id, [{
            "email": "a@example.invalid", "submitted_at": "2026-09-16T00:20:00Z",
            "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "x"}}])
    execute(
        db,
        "delete from form_question_map where form_id = %s and slot = 'takeaway'",
        (bad_form.form_id,),
    )
    db.commit()

    exit_code = main(["pull", "--cohort", TEST_COHORT, "--part", "b"])
    out = capsys.readouterr()

    assert exit_code == 1
    assert "could not be pulled" in out.out
    assert "uncollected" in out.err
    # The healthy session was still collected.
    assert fetch_one(
        db, "select count(*) as n from checkin_b where session_id = %s", (good,)
    )["n"] == 1


def test_a_clean_cohort_pull_still_exits_zero_despite_warnings(db, capsys):
    """Advisory warnings must not fail the command — the demo depends on it."""
    from cufa.cli import main

    fake = _fake()
    _template(db, fake)
    session_id, result = _session(db, fake, week=2, title="Week 2")
    seed_part_b(db, fake, result.form_id, [{
        "email": "a@example.invalid", "submitted_at": "2026-09-16T00:20:00Z",
        "slots": {SLOT_CONFIDENCE: "99", SLOT_TAKEAWAY: "x"}}])
    db.commit()

    exit_code = main(["pull", "--cohort", TEST_COHORT, "--part", "b"])
    out = capsys.readouterr()

    assert exit_code == 0
    assert "NOT clamped" in out.out, "the advisory warning is still shown"
