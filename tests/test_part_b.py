"""Part B: mapping, rotation, and the six fields.

Numbered to match Deliverable 12 of the implementation prompt, so a reader can
go from the requirement to the test that proves it without searching.

The mapping tests are the load-bearing ones. Whether Drive's ``files.copy``
preserves question ids across copies could not be verified, so every mapping
test runs under BOTH possibilities — not as an edge case, but because either
could be what Google actually does, and code that is correct under only one
produces answers filed against the wrong field with no error at all.

No test here touches a network. The Google client is the in-memory fake and the
theme clusterer is an injected stub.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from cufa.db import execute, fetch_all, fetch_one
from cufa.form_content_b import (
    HELP_OPTION,
    SLOT_CONFIDENCE,
    SLOT_HELP,
    SLOT_ROTATING,
    SLOT_SHOUTOUT,
    SLOT_TAKEAWAY,
)
from cufa.google.fake import (
    QUESTION_IDS_PRESERVED,
    QUESTION_IDS_REGENERATED,
    FakeGoogleClient,
)
from cufa.help_routing import HelpRouting, RecordingNotifier, Recipient, parse_help_routing
from cufa.ingest.forms_b import pull_session_b
from cufa.provisioning import provision_session
from cufa.question_map import QuestionMapIncomplete, load_map
from cufa.rotation import (
    APPLICATION,
    MUDDIEST_POINT,
    TEACHER_QUESTION,
    RotationConfigError,
    TeacherQuestionMissing,
    load_rotation,
    parse_rotation,
)
from cufa.shoutouts import split_names
from cufa.template import create_template, verify_template

from conftest import make_fellow, make_session, seed_part_b

# Both possibilities, every time. Parameterising rather than picking one is the
# point: the mapping logic must be correct under either.
ID_SCHEMES = [QUESTION_IDS_PRESERVED, QUESTION_IDS_REGENERATED]

ROUTED = HelpRouting(recipients=(Recipient("Director of Programs", "dop@example.invalid"),))
UNROUTED = HelpRouting()

# 2026-09-15 19:00 America/New_York is 23:00Z; the session runs 90 minutes.
SESSION_LOCAL = datetime(2026, 9, 15, 19, 0)
END_OF_SESSION = "2026-09-16T00:20:00Z"


def _fake(scheme: str) -> FakeGoogleClient:
    from cufa.google.factory import set_fake_client

    client = FakeGoogleClient(question_id_scheme=scheme, page_size=3)
    set_fake_client(client)
    return client


def provision_b(db, fake, *, week: int = 2, teacher_question: str | None = None,
                routing: HelpRouting = ROUTED, title: str = "Week 2"):
    """A session with a verified Part B template and a provisioned form."""
    import cufa.provisioning as provisioning

    record = create_template(db, fake, "b")
    fake.simulate_human_sets_verified(record.form_id)
    verify_template(db, fake, "b")

    session_id = make_session(
        db, title=title, local=SESSION_LOCAL, week_index=week,
        teacher_question=teacher_question,
    )
    original = provisioning.get_help_routing
    provisioning.get_help_routing = lambda *a, **k: routing
    try:
        result = provision_session(db, fake, session_id, part="b")
    finally:
        provisioning.get_help_routing = original
    return session_id, result


def pull_b(db, fake, session_id, *, routing: HelpRouting = ROUTED, notifier=None):
    return pull_session_b(
        db, fake, session_id, routing=routing, notifier=notifier or RecordingNotifier()
    )


# ---------------------------------------------------------------------------
# Mapping — tests 1-4
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_1_2_question_ids_resolve_under_either_copy_behaviour(db, scheme):
    """1 and 2 in one test, because they are the same assertion twice.

    Under `preserve` the copy answers under the template's ids; under
    `regenerate` it answers under fresh ones. Both must land the confidence
    rating in confidence_raw and the takeaway in takeaway_text — and it is the
    read-back after provisioning, not any assumption about ids, that makes that
    true.
    """
    fake = _fake(scheme)
    make_fellow(db, email="ada@example.invalid")
    session_id, result = provision_b(db, fake)

    seed_part_b(db, fake, result.form_id, [{
        "email": "ada@example.invalid",
        "submitted_at": END_OF_SESSION,
        "slots": {
            SLOT_CONFIDENCE: "6",
            SLOT_TAKEAWAY: "Budgets are documents about priorities.",
            SLOT_ROTATING: "Where the surplus goes.",
            SLOT_SHOUTOUT: "",
        },
    }])
    pull_b(db, fake, session_id)

    row = fetch_one(db, "select * from checkin_b")
    assert row["confidence_raw"] == 6
    assert row["takeaway_text"] == "Budgets are documents about priorities."
    assert row["rotating_text"] == "Where the surplus goes."
    assert row["rotating_kind"] == MUDDIEST_POINT


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_2b_a_retitled_question_does_not_move_its_answers(db, scheme):
    """Slots follow the item INDEX, not the title.

    A teacher fixing a typo in the Forms UI changes the title and not the
    question id. Matching on text would silently re-file every answer from that
    point on, which is exactly the failure that looks like ordinary data.
    """
    fake = _fake(scheme)
    make_fellow(db, email="ada@example.invalid")
    session_id, result = provision_b(db, fake)

    fake.simulate_teacher_retitles(result.form_id, 1, "What did you take away? (edited)")

    seed_part_b(db, fake, result.form_id, [{
        "email": "ada@example.invalid",
        "submitted_at": END_OF_SESSION,
        "slots": {SLOT_CONFIDENCE: "3", SLOT_TAKEAWAY: "Still the takeaway."},
    }])
    pull_b(db, fake, session_id)

    row = fetch_one(db, "select * from checkin_b")
    assert row["takeaway_text"] == "Still the takeaway."
    assert row["confidence_raw"] == 3


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_3_an_incomplete_map_refuses_to_ingest(db, scheme):
    """A missing slot is not "skip that field"; it is "this form is not the
    shape we think it is". Guessing files a confidence score as a takeaway."""
    fake = _fake(scheme)
    session_id, result = provision_b(db, fake)
    seed_part_b(db, fake, result.form_id, [{
        "email": "ada@example.invalid",
        "submitted_at": END_OF_SESSION,
        "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "Something."},
    }])

    execute(
        db,
        "delete from form_question_map where form_id = %s and slot = %s",
        (result.form_id, SLOT_TAKEAWAY),
    )

    with pytest.raises(QuestionMapIncomplete) as excinfo:
        pull_b(db, fake, session_id)
    assert SLOT_TAKEAWAY in str(excinfo.value)

    # And nothing was written on the way to refusing.
    assert fetch_one(db, "select count(*) as n from checkin_b")["n"] == 0


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_3b_a_missing_map_entirely_refuses_to_ingest(db, scheme):
    fake = _fake(scheme)
    session_id, result = provision_b(db, fake)
    execute(db, "delete from form_question_map where form_id = %s", (result.form_id,))
    with pytest.raises(QuestionMapIncomplete):
        pull_b(db, fake, session_id)


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_4_question_text_is_snapshot_and_survives_a_config_change(db, scheme, tmp_path):
    """"What was actually asked in week 3" must be answerable from the database
    alone. The config may well have changed since."""
    fake = _fake(scheme)
    session_id, result = provision_b(db, fake, week=2)

    mapping = load_map(db, result.form_id)
    rotating = next(m for m in mapping.values() if m.slot == SLOT_ROTATING)
    assert rotating.question_text == "What's still unclear?"
    assert rotating.rotating_kind == MUDDIEST_POINT

    # The config is rewritten. The snapshot must not move.
    config = tmp_path / "rotation.json"
    config.write_text(
        json.dumps({
            "version": "2.0.0",
            "schedule": {"muddiest_point": [1, 2]},
            "fixed_text": {"muddiest_point": "COMPLETELY DIFFERENT WORDING"},
            "wrap": True,
        }),
        encoding="utf-8",
    )
    assert load_rotation(config).fixed_text[MUDDIEST_POINT] != rotating.question_text

    stored = fetch_one(
        db,
        "select question_text from form_question_map where form_id = %s and slot = 'rotating'",
        (result.form_id,),
    )
    assert stored["question_text"] == "What's still unclear?"


# ---------------------------------------------------------------------------
# Rotation — tests 5-8
# ---------------------------------------------------------------------------


def test_5_weeks_1_to_10_resolve_to_the_documented_kinds():
    rotation = load_rotation()
    expected = {
        1: TEACHER_QUESTION, 2: MUDDIEST_POINT, 3: APPLICATION,
        4: TEACHER_QUESTION, 5: MUDDIEST_POINT, 6: APPLICATION,
        7: TEACHER_QUESTION, 8: MUDDIEST_POINT, 9: APPLICATION,
        10: TEACHER_QUESTION,
    }
    for week, kind in expected.items():
        assert rotation.kind_for(week) == kind, week

    # The teacher's question comes up most often, because it is the only
    # unfakeable one.
    counts = {kind: sum(1 for k in expected.values() if k == kind) for kind in set(expected.values())}
    assert counts[TEACHER_QUESTION] > counts[MUDDIEST_POINT]


def test_5b_weeks_past_the_schedule_wrap_and_say_so():
    rotation = load_rotation()
    assert rotation.kind_for(11) == rotation.kind_for(1)
    assert rotation.kind_for(23) == rotation.kind_for(3)
    slot = rotation.resolve(11, teacher_question="anything")
    assert slot.wrapped and slot.schedule_week == 1


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_6_week_index_drives_rotation_not_the_calendar(db, scheme):
    """Rescheduling a session must not change the question it asks."""
    from cufa.sessions import SessionInput, get_session, update_session

    fake = _fake(scheme)
    session_id, result = provision_b(db, fake, week=3)
    assert result.rotating_kind == APPLICATION

    before = fetch_one(
        db,
        "select question_text, rotating_kind from form_question_map "
        "where form_id = %s and slot = 'rotating'",
        (result.form_id,),
    )

    # Move it four months later. Under a calendar-derived week this would become
    # a different week, and the whole rotation after it would slide.
    row = get_session(db, session_id)
    update_session(
        db,
        session_id,
        SessionInput(
            cohort_id=row["cohort_id"],
            title=row["title"],
            scheduled_at_local=datetime(2027, 1, 19, 19, 0),
            timezone=row["timezone"],
            duration_minutes=row["duration_minutes"],
            grace_minutes=row["grace_minutes"],
            passphrase=row["passphrase"],
            week_index=row["week_index"],
            teacher_question=row["teacher_question"],
        ),
    )

    again = provision_session(db, fake, session_id, part="b")
    assert again.rotating_kind == APPLICATION
    after = fetch_one(
        db,
        "select question_text, rotating_kind from form_question_map "
        "where form_id = %s and slot = 'rotating'",
        (result.form_id,),
    )
    assert after == before


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_7_a_teacher_question_week_with_no_question_blocks_provisioning(db, scheme):
    """No generic substitute. The teacher's question is the only genuinely
    unfakeable item on the form, and a stand-in would produce data that looks
    identical and is worth nothing."""
    fake = _fake(scheme)
    with pytest.raises(TeacherQuestionMissing) as excinfo:
        provision_b(db, fake, week=1, teacher_question=None, title="Week 1")

    message = str(excinfo.value)
    assert "Week 1" in message
    assert "Week 1" in message and "session" in message
    # And nothing half-provisioned was left behind claiming to be ready.
    ready = fetch_one(
        db,
        "select count(*) as n from session_form where part = 'b' "
        "and publish_verified_at is not null",
    )
    assert ready["n"] == 0


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_7b_the_same_week_provisions_once_a_question_is_set(db, scheme):
    fake = _fake(scheme)
    _session_id, result = provision_b(
        db, fake, week=1, teacher_question="What surprised you?", title="Week 1"
    )
    assert result.rotating_kind == TEACHER_QUESTION
    assert result.rotating_text == "What surprised you?"


def test_7c_a_session_with_no_week_number_cannot_resolve_a_question(db, fake):
    from cufa.provisioning import resolve_rotating_slot

    make_session(db, title="Makeup", local=SESSION_LOCAL, week_index=None)
    row = fetch_one(db, 'select * from "session" where title = %s', ("Makeup",))
    with pytest.raises(RotationConfigError) as excinfo:
        resolve_rotating_slot(row)
    assert "week" in str(excinfo.value).lower()


@pytest.mark.parametrize(
    "payload,because",
    [
        ({"schedule": {"muddiest_point": [1, 3]}, "fixed_text": {"muddiest_point": "x"}},
         "week 2 has no question at all"),
        ({"schedule": {"muddiest_point": [1], "application": [1]},
          "fixed_text": {"muddiest_point": "x", "application": "y"}},
         "two kinds claim week 1"),
        ({"schedule": {"unknown_kind": [1]}}, "the kind is not one of the three"),
        ({"schedule": {"muddiest_point": [0, 1]}, "fixed_text": {"muddiest_point": "x"}},
         "week numbering starts at 1"),
        ({"schedule": {"muddiest_point": [1]}, "fixed_text": {}},
         "the wording fellows see cannot be defaulted"),
        ({}, "there is no schedule"),
    ],
)
def test_8_a_malformed_rotation_config_is_rejected_at_load(payload, because):
    """At load, not mid-provisioning with half the forms already made."""
    with pytest.raises(RotationConfigError):
        parse_rotation(payload, source="test.json"), because


def test_8b_the_shipped_config_is_valid_and_matches_the_fixtures():
    """The generator duplicates the rotation rather than importing it, so that
    fixtures are generatable before the package is installed. This is what
    catches the two drifting apart."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import generate_fixtures  # noqa: E402

    rotation = load_rotation()
    assert generate_fixtures.ROTATION_BY_WEEK == rotation.by_week


# ---------------------------------------------------------------------------
# Fields — tests 9-14
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scheme", ID_SCHEMES)
@pytest.mark.parametrize("raw", ["0", "8", "four", "-1", "3.5"])
def test_9_confidence_out_of_range_is_null_plus_raw_never_clamped(db, scheme, raw):
    fake = _fake(scheme)
    make_fellow(db, email="ada@example.invalid")
    session_id, result = provision_b(db, fake)

    seed_part_b(db, fake, result.form_id, [{
        "email": "ada@example.invalid",
        "submitted_at": END_OF_SESSION,
        "slots": {SLOT_CONFIDENCE: raw, SLOT_TAKEAWAY: "x"},
    }])
    outcome = pull_b(db, fake, session_id)

    row = fetch_one(db, "select confidence_raw, extra_fields from checkin_b")
    assert row["confidence_raw"] is None, "an out-of-range value must not be clamped"
    assert row["extra_fields"]["_confidence_rejected_raw"] == raw
    assert any("NOT clamped" in w for w in outcome.warnings)


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_9b_valid_confidence_is_stored_exactly_as_submitted(db, scheme):
    fake = _fake(scheme)
    session_id, result = provision_b(db, fake)
    seed_part_b(db, fake, result.form_id, [
        {
            "email": f"f{value}@example.invalid",
            "submitted_at": f"2026-09-16T00:2{value}:00Z",
            "slots": {SLOT_CONFIDENCE: str(value), SLOT_TAKEAWAY: "x"},
        }
        for value in range(1, 8)
    ])
    pull_b(db, fake, session_id)

    stored = sorted(
        row["confidence_raw"]
        for row in fetch_all(db, "select confidence_raw from checkin_b")
    )
    assert stored == [1, 2, 3, 4, 5, 6, 7]


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_10_blank_optional_fields_are_legal_and_write_no_shoutout_row(db, scheme):
    fake = _fake(scheme)
    session_id, result = provision_b(db, fake)
    seed_part_b(db, fake, result.form_id, [{
        "email": "ada@example.invalid",
        "submitted_at": END_OF_SESSION,
        "slots": {SLOT_CONFIDENCE: "4", SLOT_TAKEAWAY: "x", SLOT_SHOUTOUT: ""},
    }])
    pull_b(db, fake, session_id)

    assert fetch_one(db, "select count(*) as n from checkin_b")["n"] == 1
    assert fetch_one(db, "select count(*) as n from peer_shoutout")["n"] == 0
    assert fetch_one(db, "select count(*) as n from help_request")["n"] == 0


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_10b_free_text_is_stored_verbatim_including_whitespace(db, scheme):
    """Counted, never graded — and whitespace is what distinguishes "answered
    with a space" from "did not answer". Both are real."""
    fake = _fake(scheme)
    session_id, result = provision_b(db, fake)
    seed_part_b(db, fake, result.form_id, [
        {"email": "a@example.invalid", "submitted_at": "2026-09-16T00:21:00Z",
         "slots": {SLOT_CONFIDENCE: "4", SLOT_TAKEAWAY: "   "}},
        {"email": "b@example.invalid", "submitted_at": "2026-09-16T00:22:00Z",
         "slots": {SLOT_CONFIDENCE: "4", SLOT_TAKEAWAY: "🙂"}},
        {"email": "c@example.invalid", "submitted_at": "2026-09-16T00:23:00Z",
         "slots": {SLOT_CONFIDENCE: "4", SLOT_TAKEAWAY: "ok"}},
    ])
    pull_b(db, fake, session_id)

    stored = {
        row["submitted_email"]: row["takeaway_text"]
        for row in fetch_all(db, "select submitted_email, takeaway_text from checkin_b")
    }
    assert stored["a@example.invalid"] == "   "
    assert stored["b@example.invalid"] == "🙂"
    assert stored["c@example.invalid"] == "ok"

    # And the view's "substantive" test is presence of content, nothing more.
    flags = {
        row["submitted_email"]: row["has_takeaway"]
        for row in fetch_all(
            db, "select submitted_email, has_takeaway from v_checkin_b_resolved"
        )
    }
    assert flags["a@example.invalid"] is False
    assert flags["b@example.invalid"] is True
    assert flags["c@example.invalid"] is True


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Kestrel", ["Kestrel"]),
        ("Kestrel, Lorne, Nevin", ["Kestrel", "Lorne", "Nevin"]),
        ("Ingram and Jessamy", ["Ingram", "Jessamy"]),
        ("Halcyon & Glenna", ["Halcyon", "Glenna"]),
        ("Kestrel\nLorne", ["Kestrel", "Lorne"]),
        ("Marisol!!", ["Marisol"]),
        # `\band\b`, so a name containing "and" survives intact.
        ("Alexander", ["Alexander"]),
        ("Sandy and Amanda", ["Sandy", "Amanda"]),
        ("", []),
        ("   ", []),
    ],
)
def test_11_multi_name_shoutouts_split_correctly(text, expected):
    assert split_names(text) == expected


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_11b_multi_name_shoutouts_become_multiple_rows(db, scheme):
    fake = _fake(scheme)
    make_fellow(db, "CU-1", "kestrel.larkspur@example.invalid", "Kestrel Larkspur")
    make_fellow(db, "CU-2", "lorne.mossgate@example.invalid", "Lorne Mossgate")
    session_id, result = provision_b(db, fake)

    seed_part_b(db, fake, result.form_id, [{
        "email": "ada@example.invalid",
        "submitted_at": END_OF_SESSION,
        "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "x",
                  SLOT_SHOUTOUT: "Kestrel and Lorne"},
    }])
    pull_b(db, fake, session_id)

    rows = fetch_all(
        db, "select raw_text, named_fellow_id, match_method from peer_shoutout "
            "order by raw_text"
    )
    assert [r["raw_text"] for r in rows] == ["Kestrel", "Lorne"]
    assert all(r["match_method"] == "exact_name" for r in rows)
    assert {r["named_fellow_id"] for r in rows} == {"CU-1", "CU-2"}


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_12_an_ambiguous_name_is_unresolved_never_auto_linked(db, scheme):
    """Two fellows called Jordan. Guessing is a coin flip whose loser never
    finds out, so it is not taken."""
    fake = _fake(scheme)
    make_fellow(db, "CU-1", "jordan.ironwood@example.invalid", "Jordan Ironwood")
    make_fellow(db, "CU-2", "jordan.oakhaven@example.invalid", "Jordan Oakhaven")
    session_id, result = provision_b(db, fake)

    seed_part_b(db, fake, result.form_id, [{
        "email": "ada@example.invalid",
        "submitted_at": END_OF_SESSION,
        "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "x", SLOT_SHOUTOUT: "Jordan"},
    }])
    pull_b(db, fake, session_id)

    row = fetch_one(db, "select * from peer_shoutout")
    assert row["match_method"] == "unresolved"
    assert row["named_fellow_id"] is None
    # And it is in the queue for a human rather than nowhere.
    assert fetch_one(db, "select count(*) as n from v_shoutout_review")["n"] == 1


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_12b_an_unambiguous_full_name_beats_an_ambiguous_first_name(db, scheme):
    fake = _fake(scheme)
    make_fellow(db, "CU-1", "jordan.ironwood@example.invalid", "Jordan Ironwood")
    make_fellow(db, "CU-2", "jordan.oakhaven@example.invalid", "Jordan Oakhaven")
    session_id, result = provision_b(db, fake)

    seed_part_b(db, fake, result.form_id, [{
        "email": "ada@example.invalid",
        "submitted_at": END_OF_SESSION,
        "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "x",
                  SLOT_SHOUTOUT: "Jordan Oakhaven"},
    }])
    pull_b(db, fake, session_id)

    row = fetch_one(db, "select * from peer_shoutout")
    assert row["match_method"] == "exact_name"
    assert row["named_fellow_id"] == "CU-2"


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_13_a_name_matching_nobody_is_legal_not_an_error(db, scheme):
    """Guest speakers and staff get thanked too."""
    fake = _fake(scheme)
    session_id, result = provision_b(db, fake)
    seed_part_b(db, fake, result.form_id, [{
        "email": "ada@example.invalid",
        "submitted_at": END_OF_SESSION,
        "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "x",
                  SLOT_SHOUTOUT: "Ms Aldergrove from the district office"},
    }])
    outcome = pull_b(db, fake, session_id)

    assert outcome.rows_written == 1
    row = fetch_one(db, "select * from peer_shoutout")
    assert row["match_method"] == "unresolved"
    assert row["raw_text"] == "Ms Aldergrove from the district office"


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_13b_a_human_link_records_who_decided(db, scheme):
    from cufa.shoutouts import link

    fake = _fake(scheme)
    make_fellow(db, "CU-1", "jordan.ironwood@example.invalid", "Jordan Ironwood")
    make_fellow(db, "CU-2", "jordan.oakhaven@example.invalid", "Jordan Oakhaven")
    session_id, result = provision_b(db, fake)
    seed_part_b(db, fake, result.form_id, [{
        "email": "ada@example.invalid",
        "submitted_at": END_OF_SESSION,
        "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "x", SLOT_SHOUTOUT: "Jordan"},
    }])
    pull_b(db, fake, session_id)

    shoutout = fetch_one(db, "select shoutout_id from peer_shoutout")
    link(db, str(shoutout["shoutout_id"]), "CU-2", by_email="Staff@Example.Invalid")

    row = fetch_one(db, "select * from peer_shoutout")
    assert row["match_method"] == "manual"
    assert row["named_fellow_id"] == "CU-2"
    assert row["resolved_by"] == "staff@example.invalid"
    assert row["resolved_at"] is not None
    assert fetch_one(db, "select count(*) as n from v_shoutout_review")["n"] == 0


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_14_straightlining_is_flagged_after_four_consecutive_identical_values(db, scheme):
    fake = _fake(scheme)
    make_fellow(db, "CU-1", "steady@example.invalid", "Steady Fellow")
    make_fellow(db, "CU-2", "varied@example.invalid", "Varied Fellow")

    from cufa.template import create_template as ct, verify_template as vt

    record = ct(db, fake, "b")
    fake.simulate_human_sets_verified(record.form_id)
    vt(db, fake, "b")

    for week in range(1, 6):
        local = SESSION_LOCAL + timedelta(weeks=week - 1)
        session_id = make_session(
            db,
            title=f"Week {week}",
            local=local,
            week_index=week,
            teacher_question="What surprised you?",
        )
        result = provision_session(db, fake, session_id, part="b")
        # 19:00 America/New_York is 23:00Z while EDT is in force, so this is
        # half an hour into each session's own window.
        stamp = (local + timedelta(hours=4, minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        seed_part_b(db, fake, result.form_id, [
            {"email": "steady@example.invalid", "submitted_at": stamp,
             "slots": {SLOT_CONFIDENCE: "4", SLOT_TAKEAWAY: "same"}},
            {"email": "varied@example.invalid", "submitted_at": stamp,
             "slots": {SLOT_CONFIDENCE: str((week % 7) + 1), SLOT_TAKEAWAY: "different"}},
        ])
        pull_b(db, fake, session_id)

    runs = fetch_all(db, "select * from v_confidence_straightline")
    flagged = {row["fellow_id"]: row["run_length"] for row in runs}
    assert flagged.get("CU-1") == 5
    assert "CU-2" not in flagged


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_14b_three_in_a_row_is_not_flagged(db, scheme):
    fake = _fake(scheme)
    make_fellow(db, "CU-1", "steady@example.invalid", "Steady Fellow")

    from cufa.template import create_template as ct, verify_template as vt

    record = ct(db, fake, "b")
    fake.simulate_human_sets_verified(record.form_id)
    vt(db, fake, "b")

    for week in range(1, 4):
        local = SESSION_LOCAL + timedelta(weeks=week - 1)
        session_id = make_session(
            db, title=f"Week {week}", local=local,
            week_index=week, teacher_question="What surprised you?",
        )
        result = provision_session(db, fake, session_id, part="b")
        seed_part_b(db, fake, result.form_id, [{
            "email": "steady@example.invalid",
            "submitted_at": (local + timedelta(hours=4, minutes=30)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "slots": {SLOT_CONFIDENCE: "4", SLOT_TAKEAWAY: "same"},
        }])
        pull_b(db, fake, session_id)

    assert fetch_all(db, "select * from v_confidence_straightline") == []


# ---------------------------------------------------------------------------
# Independence — test 24
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_24_part_a_and_part_b_ingest_independently(db, scheme):
    """Neither is evidence for the other, and neither backfills the other."""
    from cufa.ingest.forms_api import pull_session

    fake = _fake(scheme)
    make_fellow(db, "CU-A", "a-only@example.invalid", "A Only")
    make_fellow(db, "CU-B", "b-only@example.invalid", "B Only")

    from cufa.template import create_template as ct, verify_template as vt

    for part in ("a", "b"):
        record = ct(db, fake, part)
        fake.simulate_human_sets_verified(record.form_id)
        vt(db, fake, part)

    session_id = make_session(
        db, title="Week 2", local=SESSION_LOCAL, week_index=2, passphrase="justice"
    )
    form_a = provision_session(db, fake, session_id, part="a")
    form_b = provision_session(db, fake, session_id, part="b")
    assert form_a.form_id != form_b.form_id

    fake.seed_responses(form_a.form_id, [("a-only@example.invalid", "2026-09-15T23:20:00Z", "justice")])
    seed_part_b(db, fake, form_b.form_id, [{
        "email": "b-only@example.invalid",
        "submitted_at": END_OF_SESSION,
        "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "Only came to the end."},
    }])

    pull_session(db, fake, session_id)
    pull_b(db, fake, session_id)

    assert fetch_one(db, "select count(*) as n from checkin")["n"] == 1
    assert fetch_one(db, "select count(*) as n from checkin_b")["n"] == 1
    assert fetch_one(db, "select submitted_email from checkin")["submitted_email"] == "a-only@example.invalid"
    assert fetch_one(db, "select submitted_email from checkin_b")["submitted_email"] == "b-only@example.invalid"

    # Neither part invented a row in the other's table for the missing fellow.
    assert fetch_one(
        db, "select count(*) as n from checkin where submitted_email = %s",
        ("b-only@example.invalid",),
    )["n"] == 0
    assert fetch_one(
        db, "select count(*) as n from checkin_b where submitted_email = %s",
        ("a-only@example.invalid",),
    )["n"] == 0


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_24b_a_second_pull_writes_nothing_new(db, scheme):
    fake = _fake(scheme)
    session_id, result = provision_b(db, fake)
    seed_part_b(db, fake, result.form_id, [{
        "email": "ada@example.invalid",
        "submitted_at": END_OF_SESSION,
        "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "x", SLOT_SHOUTOUT: "Kestrel"},
        "help": True,
    }])
    first = pull_b(db, fake, session_id)
    second = pull_b(db, fake, session_id)

    assert first.rows_written == 1
    assert second.rows_written == 0
    assert fetch_one(db, "select count(*) as n from checkin_b")["n"] == 1
    assert fetch_one(db, "select count(*) as n from peer_shoutout")["n"] == 1
    assert fetch_one(db, "select count(*) as n from help_request")["n"] == 1


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_provisioning_twice_does_not_create_a_second_part_b_form(db, scheme):
    fake = _fake(scheme)
    session_id, first = provision_b(db, fake)
    second = provision_session(db, fake, session_id, part="b")
    assert second.form_id == first.form_id
    assert second.already_ready
    assert fetch_one(
        db, "select count(*) as n from session_form where part = 'b'"
    )["n"] == 1


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_checkin_b_is_immutable(db, scheme):
    import psycopg

    fake = _fake(scheme)
    session_id, result = provision_b(db, fake)
    seed_part_b(db, fake, result.form_id, [{
        "email": "ada@example.invalid",
        "submitted_at": END_OF_SESSION,
        "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "original"},
    }])
    pull_b(db, fake, session_id)

    with pytest.raises(psycopg.errors.RestrictViolation):
        execute(db, "update checkin_b set takeaway_text = 'edited'")
    db.rollback()
    with pytest.raises(psycopg.errors.RestrictViolation):
        execute(db, "delete from checkin_b")
    db.rollback()


def test_the_shipped_help_routing_config_names_someone():
    """Invariant 2 shipped in the state CU can actually use.

    An empty recipients list is a legal state and is handled — the field is
    omitted — but shipping in it would mean the demo never exercises the help
    path at all.
    """
    payload = json.loads(
        (Path(__file__).resolve().parents[1] / "config" / "help_routing.json")
        .read_text(encoding="utf-8")
    )
    routing = parse_help_routing(payload)
    assert routing.has_recipient
    assert routing.reason_omitted is None


def test_an_empty_recipients_list_parses_as_no_recipient():
    for payload in ({"recipients": []}, {}, {"recipients": [{"name": "x", "email": "  "}]}):
        routing = parse_help_routing(payload)
        assert not routing.has_recipient
        assert routing.reason_omitted


def test_the_six_fields_are_in_the_documented_order_with_the_documented_requirements():
    """The order is load-bearing, not cosmetic. See form_content_b's docstring."""
    from cufa.form_content_b import item_specs

    specs = item_specs("This week's question", include_help=True)
    assert [s.slot for s in specs] == [
        SLOT_CONFIDENCE, SLOT_TAKEAWAY, SLOT_ROTATING, SLOT_SHOUTOUT, SLOT_HELP,
    ]
    assert [s.index for s in specs] == [0, 1, 2, 3, 4]

    required = {
        spec.slot: spec.request["createItem"]["item"]["questionItem"]["question"]["required"]
        for spec in specs
    }
    assert required == {
        SLOT_CONFIDENCE: True,
        SLOT_TAKEAWAY: True,
        SLOT_ROTATING: True,
        # Forcing either of these would be actively wrong: an optional field
        # that must be answered is not optional, and a compulsory "do you need
        # help?" is a different question from a voluntary one.
        SLOT_SHOUTOUT: False,
        SLOT_HELP: False,
    }

    scale = specs[0].request["createItem"]["item"]["questionItem"]["question"]["scaleQuestion"]
    assert scale["low"] == 1 and scale["high"] == 7, "seven points, not five"
    assert set(scale) == {"low", "high", "lowLabel", "highLabel"}, "endpoints only"

    checkbox = specs[4].request["createItem"]["item"]["questionItem"]["question"]["choiceQuestion"]
    assert checkbox["type"] == "CHECKBOX"
    assert checkbox["options"] == [{"value": HELP_OPTION}]
