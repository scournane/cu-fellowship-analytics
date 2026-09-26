"""Part A, the exit ticket: questions as data, provisioned per session, answers
stored raw and resolved at read time.

Organised the way the data flows — the rules a question set must satisfy, how
sets are stored and versioned, how a session's form is built from one, and how
the answers come back. Wherever a question id is involved the test runs under
BOTH copy behaviours (``preserve`` and ``regenerate``), because which one Google
does is not verified and code that is right under only one files answers
against the wrong question with no error.

The invariants these hold in place, by name: never drop a submission;
observation separate from decision; append-only with provenance; idempotent
ingest; cohort-keyed; no AI reads answers; free text counted, never graded.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import psycopg
import pytest

from cufa import part_a_questions, question_sets
from cufa.db import execute, fetch_all, fetch_one
from cufa.errors import (
    CufaError,
    InvalidQuestionSet,
    PublishVerificationFailed,
    QuestionSetMissing,
    QuestionsLocked,
    StaleQuestionSet,
    TemplateNotVerified,
)
from cufa.google.base import EMAIL_COLLECTION_RESPONDER_INPUT, GoogleApiError
from cufa.google.fake import (
    QUESTION_IDS_PRESERVED,
    QUESTION_IDS_REGENERATED,
    FakeGoogleClient,
)
from cufa.ingest.forms_api import pull_session
from cufa.provisioning import get_session_form, is_ready, provision_session
from cufa.question_map import QuestionMapIncomplete
from cufa.template import create_template, verify_template
from cufa.timeutil import UTC, session_window

from conftest import (
    TEST_COHORT,
    count,
    make_fellow,
    make_session,
    seed_part_a,
    verify_template_for,
)

ID_SCHEMES = [QUESTION_IDS_PRESERVED, QUESTION_IDS_REGENERATED]
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FILE = ROOT / "config" / "part_a_default_questions.json"

# 2026-09-15 19:00 America/New_York is 23:00Z; 90 minutes plus 15 of grace
# either side makes the window 22:45Z .. 00:45Z inclusive.
SESSION_LOCAL = datetime(2026, 9, 15, 19, 0)
IN_WINDOW = "2026-09-15T23:40:00Z"

#: Every question type, with a section break and a text block in the middle so
#: item indexes and question positions differ — the case that breaks any code
#: counting questions instead of items.
EXIT_TICKET = {
    "schema_version": 1,
    "title": 'Exit Ticket: Lesson {lesson} "{session_title}"',
    "description": "Week {lesson}. Two minutes.",
    "questions": [
        {"key": "q_rating", "type": "linear_scale", "title": "How was today?",
         "required": True,
         "scale": {"low": 1, "high": 5, "low_label": "Meh", "high_label": "Great"}},
        {"key": "q_topics", "type": "checkboxes", "title": "Which topics landed?",
         "options": ["Budgets", "Taxes", "Debt"], "allow_other": True},
        {"type": "section", "title": "A bit more"},
        {"key": "q_format", "type": "dropdown", "title": "Best format?",
         "options": ["Talk", "Workshop"]},
        {"key": "q_takeaway", "type": "paragraph",
         "title": "Biggest takeaway from Lesson {lesson}?", "required": True},
        {"type": "text", "title": "Thanks!", "description": "See you next week."},
    ],
}


def _fake(scheme: str = QUESTION_IDS_PRESERVED, **kwargs) -> FakeGoogleClient:
    from cufa.google.factory import set_fake_client

    client = FakeGoogleClient(question_id_scheme=scheme, page_size=3, **kwargs)
    set_fake_client(client)
    return client


def _provision(db, fake, *, content=EXIT_TICKET, week: int | None = 3,
               title: str = "Budgets", local: datetime = SESSION_LOCAL):
    """A cohort default, a verified Part A template, and a provisioned session."""
    question_sets.save_default(db, TEST_COHORT, content, created_by="tests")
    if not fetch_one(db, "select 1 as ok from form_template where part = 'a' and is_active"):
        verify_template_for(db, fake, "a")
    session_id = make_session(db, title=title, local=local, week_index=week)
    return session_id, provision_session(db, fake, session_id)


def _qid(db, form_id: str, key: str) -> str:
    row = fetch_one(
        db,
        "select question_id from part_a_form_question where form_id = %s and question_key = %s",
        (form_id, key),
    )
    assert row is not None, f"{key} is not mapped on {form_id}"
    return row["question_id"]


# ---------------------------------------------------------------------------
# the rules a question set must satisfy
# ---------------------------------------------------------------------------


def test_the_default_file_is_the_week_one_exit_ticket():
    """The real default loads, keeps its keys, and is allowed to be long."""
    raw = json.loads(DEFAULT_FILE.read_text(encoding="utf-8"))
    clean = part_a_questions.load_file(DEFAULT_FILE)

    assert [q["key"] for q in clean["questions"]] == [q["key"] for q in raw["questions"]]
    assert "status" not in clean and "_comment" not in clean
    result = part_a_questions.validate(raw)
    assert result.errors == []
    # Eight questions to answer is above the soft limit: a note, never a refusal.
    assert len(part_a_questions.answerable(clean)) == 8
    assert any("8 questions" in w for w in result.warnings)

    rendered = part_a_questions.render(clean, week_index=1, session_title="Nuanced Nation")
    assert rendered["title"] == 'Exit Ticket: Lesson 1 "Nuanced Nation"'
    assert rendered["questions"][3]["title"] == "What was your biggest takeaway from Lesson 1?"
    # The stored set keeps its placeholders; only the rendered copy is filled.
    assert "{lesson}" in clean["title"]


def test_keys_are_kept_or_minted_deterministically():
    content = {
        "title": "t",
        "questions": [
            {"type": "short_answer", "title": "Name"},
            {"key": "q_name_given", "type": "short_answer", "title": "Given name"},
            {"type": "short_answer", "title": "Name"},  # same wording, different question
        ],
    }
    first = part_a_questions.validate(content)
    second = part_a_questions.validate(content)
    keys = [q["key"] for q in first.clean["questions"]]

    assert first.errors == []
    assert keys[1] == "q_name_given", "an existing key is never replaced"
    assert all(part_a_questions.KEY_PATTERN.match(k) for k in keys)
    assert keys[0].startswith("q_") and len(keys[0]) == 12
    assert len(set(keys)) == 3, "identical wording still gets distinct keys"
    # Deterministic, so saving the same key-less file twice is the same content.
    assert keys == [q["key"] for q in second.clean["questions"]]
    assert part_a_questions.content_sha256(first.clean) == part_a_questions.content_sha256(
        second.clean
    )


def test_every_problem_is_reported_at_once():
    result = part_a_questions.validate(
        {
            "title": "",
            "questions": [
                {"key": "Bad Key", "type": "short_answer", "title": "x"},
                {"key": "q_dupe", "type": "multiple_choice", "title": "Pick", "options": []},
                {"key": "q_dupe", "type": "checkboxes", "title": "Pick", "options": ["a", " ", "a"]},
                {"type": "linear_scale", "title": "Rate", "scale": {"low": 2, "high": 11}},
                {"type": "star_rating", "title": "Stars"},
                {"type": "paragraph", "title": ""},
            ],
        }
    )
    text = "\n".join(result.errors)
    assert "needs a title" in text  # the form
    assert "Bad Key" in text
    assert "share the key 'q_dupe'" in text
    assert "at least one option" in text
    assert "option 2 is blank" in text
    assert "appears twice" in text
    assert "start at 0 or 1" in text and "end between 2 and 10" in text
    assert "star_rating" in text
    assert "a question needs a title" in text
    assert len(result.errors) >= 9


def test_leftovers_are_dropped_and_strings_trimmed():
    result = part_a_questions.validate(
        {
            "title": "  Exit ticket  ",
            "description": "line one\r\nline two  ",
            "questions": [
                # Switched from a scale to a paragraph in the editor: the scale
                # block is debris, not an error.
                {"type": "paragraph", "title": "  Why?  ", "scale": {"low": 1, "high": 5},
                 "options": ["x"], "required": True},
                # Switched from multiple choice to a dropdown with Other still on.
                {"type": "dropdown", "title": "Which", "options": [" a ", "b"],
                 "allow_other": True},
                {"type": "section", "title": "Next", "required": True},
            ],
        }
    )
    assert result.errors == []
    paragraph, dropdown, section = result.clean["questions"]
    assert result.clean["title"] == "Exit ticket"
    assert result.clean["description"] == "line one\nline two"
    assert paragraph["title"] == "Why?" and "scale" not in paragraph and "options" not in paragraph
    assert dropdown["options"] == ["a", "b"] and "allow_other" not in dropdown
    assert any("cannot offer “Other”" in w for w in result.warnings)
    assert "required" not in section, "a section break has nothing to require"
    assert any("ends with a section break" in w for w in result.warnings)


def test_a_set_with_nothing_to_answer_is_refused():
    result = part_a_questions.validate(
        {"title": "t", "questions": [{"type": "text", "title": "Hello"}]}
    )
    assert any("no question to answer" in e for e in result.errors)


def test_placeholders_fill_safely():
    content = part_a_questions.validate(
        {
            "title": "Lesson {lesson}: {session_title}",
            "questions": [
                {"type": "short_answer", "title": "What about Lesson {lesson}? {unknown} {"},
                {"type": "multiple_choice", "title": "Pick", "options": ["{session_title}", "No"]},
            ],
        }
    )
    assert any("{unknown}" in w for w in content.warnings)

    week = part_a_questions.render(content.clean, week_index=4, session_title="Taxes")
    assert week["title"] == "Lesson 4: Taxes"
    assert week["questions"][0]["title"] == "What about Lesson 4? {unknown} {"
    assert week["questions"][1]["options"] == ["Taxes", "No"]

    # A makeup session has no week: no "None", no double space.
    makeup = part_a_questions.render(content.clean, week_index=None, session_title="Makeup")
    assert makeup["title"] == "Lesson: Makeup"
    assert "None" not in json.dumps(makeup)
    assert makeup["questions"][0]["title"].startswith("What about Lesson?")


def test_every_type_maps_to_an_item_the_api_accepts_and_back():
    """to_item_body's output passes the fake's createItem validation, and
    from_forms_item reads it back as the same question."""
    clean = part_a_questions.require_valid(EXIT_TICKET).clean
    fake = FakeGoogleClient()
    ref = fake.create_template("t")
    fake.batch_update(
        ref.form_id,
        [
            {"createItem": {"item": part_a_questions.to_item_body(q), "location": {"index": i}}}
            for i, q in enumerate(clean["questions"])
        ],
    )
    raw_items = fake.get_form(ref.form_id).raw["items"]
    assert len(raw_items) == len(clean["questions"])

    for question, raw in zip(clean["questions"], raw_items):
        back = part_a_questions.from_forms_item(raw)
        assert back is not None
        expected = {k: v for k, v in question.items() if k != "key"}
        expected.setdefault("description", "")
        assert back == expected, question["key"]

    assert {t["type"] for t in part_a_questions.QUESTION_TYPES} == {
        "short_answer", "paragraph", "multiple_choice", "checkboxes", "dropdown",
        "linear_scale", "section", "text",
    }


def test_form_links_are_read_and_the_responder_link_is_refused():
    assert part_a_questions.form_id_from_url(
        "https://docs.google.com/forms/d/1nbd3lHZlXi7nWNlJX0IuMgWad6kelweL8kBbSIGJlhQ/edit"
    ) == "1nbd3lHZlXi7nWNlJX0IuMgWad6kelweL8kBbSIGJlhQ"
    assert part_a_questions.form_id_from_url("fake-form-0001") == "fake-form-0001"
    with pytest.raises(ValueError, match="link fellows answer on"):
        part_a_questions.form_id_from_url(
            "https://docs.google.com/forms/d/e/1FAIpQLSexampleexample/viewform"
        )


# ---------------------------------------------------------------------------
# storage: append-only, versioned, cohort-keyed
# ---------------------------------------------------------------------------


def test_saving_is_append_only_and_identical_content_is_a_no_op(db):
    v1 = question_sets.save_default(db, TEST_COHORT, EXIT_TICKET, created_by="ana")
    again = question_sets.save_default(db, TEST_COHORT, EXIT_TICKET, created_by="ana")
    assert again.question_set_id == v1.question_set_id
    assert count(db, "part_a_question_set") == 1

    edited = json.loads(json.dumps(EXIT_TICKET))
    edited["questions"][0]["title"] = "How was today, honestly?"
    v2 = question_sets.save_default(db, TEST_COHORT, edited, created_by="ben")

    assert (v1.version, v2.version) == (1, 2)
    assert v2.label == "default v2"
    old = question_sets.get(db, v1.question_set_id)
    assert old.superseded_at is not None
    assert old.superseded_by == v2.question_set_id
    assert old.retired_by == "ben"
    assert [q.version for q in question_sets.history(db, cohort_id=TEST_COHORT)] == [2, 1]
    assert question_sets.current_default(db, TEST_COHORT).question_set_id == v2.question_set_id
    # Cohort-keyed, overrides included.
    assert all(q.cohort_id == TEST_COHORT for q in question_sets.history(db, cohort_id=TEST_COHORT))


def test_the_database_refuses_to_edit_or_delete_a_version(db):
    v1 = question_sets.save_default(db, TEST_COHORT, EXIT_TICKET, created_by="ana")
    with pytest.raises(psycopg.errors.RestrictViolation):
        execute(db, "update part_a_question_set set created_by = 'someone else'")
    with pytest.raises(psycopg.errors.RestrictViolation):
        execute(db, "delete from part_a_question_set")

    edited = dict(EXIT_TICKET, title="Changed")
    question_sets.save_default(db, TEST_COHORT, edited, created_by="ana")
    with pytest.raises(psycopg.errors.RestrictViolation):
        execute(
            db,
            "update part_a_question_set set superseded_at = null where question_set_id = %s",
            (v1.question_set_id,),
        )
    # And there is only ever one current default.
    with pytest.raises(psycopg.errors.UniqueViolation):
        execute(
            db,
            "insert into part_a_question_set (cohort_id, version, content, content_sha256, source) "
            "values (%s, 99, '{\"questions\": []}'::jsonb, 'x', 'cli')",
            (TEST_COHORT,),
        )


def test_invalid_content_is_never_saved(db):
    with pytest.raises(InvalidQuestionSet) as excinfo:
        question_sets.save_default(
            db, TEST_COHORT, {"title": "", "questions": []}, created_by="ana"
        )
    assert excinfo.value.errors
    assert count(db, "part_a_question_set") == 0


def test_a_stale_edit_is_refused_rather_than_silently_winning(db):
    v1 = question_sets.save_default(db, TEST_COHORT, EXIT_TICKET, created_by="ana")
    question_sets.save_default(
        db, TEST_COHORT, dict(EXIT_TICKET, title="Ana's edit"), created_by="ana",
        base_id=v1.question_set_id,
    )
    with pytest.raises(StaleQuestionSet, match="now default v2"):
        question_sets.save_default(
            db, TEST_COHORT, dict(EXIT_TICKET, title="Ben's edit"), created_by="ben",
            base_id=v1.question_set_id,
        )


def test_an_override_is_a_full_snapshot_that_can_be_reverted(db):
    default = question_sets.save_default(db, TEST_COHORT, EXIT_TICKET, created_by="ana")
    session_id = make_session(db, week_index=3)
    assert question_sets.resolve_for_session(db, session_id)[0] == "default"

    custom = dict(EXIT_TICKET, title="Session 3's own")
    override = question_sets.save_override(
        db, session_id, custom, created_by="ana", base_id=default.question_set_id
    )
    assert override.label == "custom v1"
    assert override.based_on_id == default.question_set_id
    assert override.cohort_id == TEST_COHORT
    scope, resolved = question_sets.resolve_for_session(db, session_id)
    assert (scope, resolved.question_set_id) == ("session", override.question_set_id)

    # Starting again from the default once an override exists is stale.
    with pytest.raises(StaleQuestionSet):
        question_sets.save_override(
            db, session_id, dict(custom, title="Other"), created_by="ben",
            base_id=default.question_set_id,
        )

    # The default moving on does not touch the override, and a further edit of
    # the override still records the default it descends from.
    question_sets.save_default(db, TEST_COHORT, dict(EXIT_TICKET, title="Default v2"), created_by="x")
    v2 = question_sets.save_override(
        db, session_id, dict(custom, title="Session 3, edited"), created_by="ana",
        base_id=override.question_set_id,
    )
    assert v2.based_on_id == default.question_set_id

    question_sets.revert_override(db, session_id, by="ana")
    scope, resolved = question_sets.resolve_for_session(db, session_id)
    assert scope == "default" and resolved.label == "default v2"
    history = question_sets.history(db, session_id=session_id)
    assert [q.version for q in history] == [2, 1], "a revert keeps the history"
    assert history[0].retired_by == "ana" and history[0].superseded_by is None
    question_sets.revert_override(db, session_id, by="ana")  # nothing to revert: a no-op


def test_seeding_from_the_file_is_idempotent(db):
    first = question_sets.seed_default_from_file(db, TEST_COHORT, created_by="demo")
    second = question_sets.seed_default_from_file(db, TEST_COHORT, created_by="demo")
    assert first.question_set_id == second.question_set_id
    assert count(db, "part_a_question_set") == 1
    assert first.source == "seed_file"
    assert first.source_ref == "config/part_a_default_questions.json"
    assert first.question_count == 8


# ---------------------------------------------------------------------------
# importing an existing form
# ---------------------------------------------------------------------------


def _hand_made_form(fake: FakeGoogleClient) -> str:
    return fake.simulate_form_created_by_hand(
        [
            {"title": "First Name",
             "questionItem": {"question": {"required": True, "textQuestion": {}}}},
            {"title": "How would you rate today's session?",
             "questionItem": {"question": {"required": True,
                                           "scaleQuestion": {"low": 1, "high": 5}}}},
            {"title": "Grid", "questionGroupItem": {
                "questions": [{"rowQuestion": {"title": "Row 1"}}],
                "grid": {"columns": {"type": "RADIO", "options": [{"value": "A"}]}}}},
            {"title": "Upload your notes",
             "questionItem": {"question": {"fileUploadQuestion": {"folderId": "x"}}}},
            {"title": "Where next?", "questionItem": {"question": {"choiceQuestion": {
                "type": "RADIO",
                "options": [{"value": "Yes", "goToSectionId": "abc"}, {"value": "No"},
                            {"isOther": True}]}}}},
            {"title": "Part 2", "pageBreakItem": {}},
            {"title": "Any other feedback?",
             "questionItem": {"question": {"textQuestion": {"paragraph": True}}}},
        ],
        title='Exit Ticket: Lesson 1 "Nuanced Nation"',
        description="Welcome to the Exit Ticket.",
    )


def test_importing_a_form_keeps_what_it_can_and_names_what_it_cannot(db, fake):
    form_id = _hand_made_form(fake)

    content, warnings = question_sets.import_from_form(
        fake, db, TEST_COHORT, form_id, created_by="ana", dry_run=True
    )
    assert [q["type"] for q in content["questions"]] == [
        "short_answer", "linear_scale", "multiple_choice", "section", "paragraph",
    ]
    choice = content["questions"][2]
    assert choice["options"] == ["Yes", "No"] and choice["allow_other"] is True
    text = "\n".join(warnings)
    assert "grid" in text and "file-upload" in text
    assert "Branching is not supported" in text
    assert "{lesson}" in text, "literal wording is pointed out"
    assert count(db, "part_a_question_set") == 0, "a dry run saves nothing"

    question_sets.import_from_form(fake, db, TEST_COHORT, form_id, created_by="ana")
    saved = question_sets.current_default(db, TEST_COHORT)
    assert (saved.source, saved.source_ref) == ("import_form", form_id)
    assert saved.content == content

    # The editor URL works too, and the same form again is not a new version.
    question_sets.import_from_form(
        fake, db, TEST_COHORT,
        f"https://docs.google.com/forms/d/{form_id}/edit", created_by="ana",
    )
    assert count(db, "part_a_question_set") == 1


def test_a_form_the_app_cannot_see_is_explained(db, fake):
    with pytest.raises(CufaError, match="drive.file"):
        question_sets.import_from_form(
            fake, db, TEST_COHORT, "1nbd3lHZlXi7nWNlJX0IuMgWad6kelweL8kBbSIGJlhQ",
            created_by="ana",
        )


# ---------------------------------------------------------------------------
# provisioning
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_a_session_form_is_rebuilt_from_its_question_set(db, scheme):
    fake = _fake(scheme)
    session_id, result = _provision(db, fake)
    form = fake.forms[result.form_id]

    assert form.title == 'Exit Ticket: Lesson 3 "Budgets"'
    assert form.description == "Week 3. Two minutes."
    assert [item.title for item in form.items] == [
        "How was today?", "Which topics landed?", "A bit more", "Best format?",
        "Biggest takeaway from Lesson 3?", "Thanks!",
    ]

    # ONE batch: retitle, then create everything, on the copy.
    batches = [c for c in fake.calls("batch_update") if c["form_id"] == result.form_id]
    assert len(batches) == 1
    kinds = [next(iter(r)) for r in batches[0]["requests"]]
    assert kinds[0] == "updateFormInfo" and kinds.count("createItem") == 6

    # The map joins on item index — which counts the section break — and every
    # id is the one the form itself reports.
    rows = question_sets.form_map(db, result.form_id)
    assert [(r["question_key"], r["item_index"], r["kind"]) for r in rows] == [
        ("q_rating", 0, "linear_scale"),
        ("q_topics", 1, "checkboxes"),
        ("q_format", 3, "dropdown"),
        ("q_takeaway", 4, "paragraph"),
    ]
    on_form = {i.index: i.question_id for i in fake.get_form(result.form_id).items}
    assert all(on_form[r["item_index"]] == r["question_id"] for r in rows)
    assert rows[3]["question_text"] == "Biggest takeaway from Lesson 3?"
    assert rows[1]["spec"]["options"] == ["Budgets", "Taxes", "Debt"]

    default = question_sets.current_default(db, TEST_COHORT)
    row = get_session_form(db, session_id)
    assert str(row["question_set_id"]) == default.question_set_id
    assert row["email_collection_verified_at"] is not None
    assert all(str(r["question_set_id"]) == default.question_set_id for r in rows)
    assert is_ready(db, session_id)

    assert (result.question_scope, result.question_count) == ("default", 4)
    assert "questions: default v1 (4)" in result.summary


def test_a_passphrase_era_template_item_is_removed_from_the_copy(db, fake):
    """Existing installs have a template with the old passphrase question on it."""
    question_sets.save_default(db, TEST_COHORT, EXIT_TICKET, created_by="tests")
    record = create_template(db, fake, "a")
    fake.batch_update(record.form_id, [{"createItem": {"item": {
        "title": "Today's passphrase",
        "questionItem": {"question": {"required": True, "textQuestion": {"paragraph": False}}},
    }, "location": {"index": 0}}}])
    fake.simulate_human_sets_verified(record.form_id)
    verify_template(db, fake, "a")

    session_id = make_session(db, week_index=3, title="Budgets")
    result = provision_session(db, fake, session_id)

    titles = [item.title for item in fake.forms[result.form_id].items]
    assert "Today's passphrase" not in titles
    assert len(titles) == 6
    batch = [c for c in fake.calls("batch_update") if c["form_id"] == result.form_id][0]
    assert {"deleteItem": {"location": {"index": 0}}} in batch["requests"]


def test_no_question_set_blocks_provisioning_dry_run_included(db, fake):
    verify_template_for(db, fake, "a")
    session_id = make_session(db, part_a_questions=False)
    calls_before = len(fake.call_log)

    for dry_run in (True, False):
        with pytest.raises(QuestionSetMissing) as excinfo:
            provision_session(db, fake, session_id, dry_run=dry_run)
        assert f"cufa questions seed-default --cohort {TEST_COHORT}" in str(excinfo.value)

    assert fake.call_log[calls_before:] == [], "nothing was asked of Google"
    assert get_session_form(db, session_id) is None


def test_the_dry_run_plan_names_the_questions(db, fake):
    question_sets.save_default(db, TEST_COHORT, EXIT_TICKET, created_by="tests")
    verify_template_for(db, fake, "a")
    session_id = make_session(db, week_index=3, title="Budgets")

    result = provision_session(db, fake, session_id, dry_run=True)
    assert result.question_scope == "default" and result.question_count == 4
    plan = fetch_one(
        db, "select request_summary from provisioning_log where outcome = 'dry_run'"
    )["request_summary"]
    assert plan["form_title"] == 'Exit Ticket: Lesson 3 "Budgets"'
    assert plan["question_count"] == 4
    assert plan["would_rebuild_items"] is True
    assert fake.calls("copy_form") == []


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_a_published_form_is_never_written_to(db, scheme):
    fake = _fake(scheme)
    session_id, result = _provision(db, fake)
    batches_before = len(fake.calls("batch_update"))

    # The session's own questions are locked once its form is published…
    locked, reason = question_sets.is_locked(db, session_id)
    assert locked and "published" in reason
    # Printed the way the console prints every UTC instant, not as a repr.
    import re

    assert re.search(r"published at \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC,", reason), reason
    with pytest.raises(QuestionsLocked):
        question_sets.save_override(db, session_id, dict(EXIT_TICKET, title="x"), created_by="a")
    with pytest.raises(QuestionsLocked):
        question_sets.revert_override(db, session_id, by="a")

    # …while the cohort default stays editable, for sessions not yet published.
    question_sets.save_default(db, TEST_COHORT, dict(EXIT_TICKET, title="Edited"), created_by="a")
    again = provision_session(db, fake, session_id)

    assert again.already_ready
    assert len(fake.calls("batch_update")) == batches_before, "no write to a live form"
    assert again.question_set_label == "default v1", "it keeps what it was built with"
    assert again.questions_edited_after_provisioning
    assert "default v1" in again.questions_note and "default v2" in again.questions_note
    assert "edited after provisioning" in again.summary
    assert question_sets.provisioned_for_session(db, session_id).version == 1


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_provisioning_again_repairs_a_missing_map_without_writing(db, scheme):
    fake = _fake(scheme)
    session_id, result = _provision(db, fake)
    before = question_sets.form_map(db, result.form_id)
    execute(db, "delete from part_a_form_question where form_id = %s", (result.form_id,))
    batches_before = len(fake.calls("batch_update"))

    provision_session(db, fake, session_id)

    after = question_sets.form_map(db, result.form_id)
    assert [(r["question_key"], r["question_id"]) for r in after] == [
        (r["question_key"], r["question_id"]) for r in before
    ]
    assert len(fake.calls("batch_update")) == batches_before


def test_a_publish_that_did_not_read_back_freezes_the_form(db):
    """Publishing was attempted, so fellows may be answering: the retry
    publishes again but never rebuilds the questions — even though the
    default changed in between."""
    fake = _fake(publish_readback_fails=True)
    with pytest.raises(PublishVerificationFailed):
        _provision(db, fake)
    session_id = str(fetch_one(db, 'select session_id from "session"')["session_id"])
    form_id = get_session_form(db, session_id)["form_id"]
    assert question_sets.is_locked(db, session_id)[0], "published_at is enough to lock"

    question_sets.save_default(db, TEST_COHORT, dict(EXIT_TICKET, title="Edited"), created_by="a")
    batches_before = len(fake.calls("batch_update"))
    fake.publish_readback_fails = False
    result = provision_session(db, fake, session_id)

    assert result.resumed and result.form_id == form_id
    assert len(fake.calls("batch_update")) == batches_before
    assert result.question_set_label == "default v1"
    assert result.questions_edited_after_provisioning
    assert is_ready(db, session_id)


def test_an_unpublished_orphan_is_rebuilt_from_the_current_questions(db, fake):
    """The content step failed after the copy: nothing was ever published, so
    the retry rebuilds from whatever the session's questions are *now*."""
    question_sets.save_default(db, TEST_COHORT, EXIT_TICKET, created_by="tests")
    verify_template_for(db, fake, "a")
    session_id = make_session(db, week_index=3, title="Budgets")

    original = fake.batch_update

    def explode(form_id, requests):
        raise GoogleApiError("Backend error", status=400)

    fake.batch_update = explode
    with pytest.raises(GoogleApiError):
        provision_session(db, fake, session_id)
    fake.batch_update = original
    orphan = get_session_form(db, session_id)
    assert orphan is not None and orphan["published_at"] is None
    assert not question_sets.is_locked(db, session_id)[0]

    custom = dict(EXIT_TICKET, title="Session 3's own")
    override = question_sets.save_override(db, session_id, custom, created_by="ana")
    result = provision_session(db, fake, session_id)

    assert result.resumed and result.form_id == orphan["form_id"]
    assert len(fake.calls("copy_form")) == 1
    assert fake.forms[result.form_id].title == "Session 3's own"
    assert str(get_session_form(db, session_id)["question_set_id"]) == override.question_set_id
    assert result.question_scope == "session" and result.question_set_label == "custom v1"


def test_a_copy_that_does_not_collect_verified_email_is_never_published(db):
    class UnverifiedCopies(FakeGoogleClient):
        def copy_form(self, source_form_id, new_title):
            ref = super().copy_form(source_form_id, new_title)
            self.forms[ref.form_id].email_collection_type = EMAIL_COLLECTION_RESPONDER_INPUT
            return ref

    fake = UnverifiedCopies()
    with pytest.raises(TemplateNotVerified, match="has NOT been published"):
        _provision(db, fake)
    assert fake.calls("set_publish_settings") == []
    row = fetch_one(db, "select publish_verified_at, email_collection_verified_at from session_form")
    assert row["publish_verified_at"] is None and row["email_collection_verified_at"] is None


def test_a_form_that_reads_back_the_wrong_shape_is_refused(db):
    """Google accepted the batch but a choice came back one option short."""

    class LosesAnOption(FakeGoogleClient):
        def batch_update(self, form_id, requests):
            reply = super().batch_update(form_id, requests)
            for item in self.forms[form_id].items:
                choice = ((item.question or {}).get("choiceQuestion")) or {}
                if len(choice.get("options") or []) > 1:
                    choice["options"].pop()
            return reply

    fake = LosesAnOption()
    with pytest.raises(QuestionMapIncomplete, match="option"):
        _provision(db, fake)
    assert fake.calls("set_publish_settings") == []
    session_id = str(fetch_one(db, 'select session_id from "session"')["session_id"])
    assert not is_ready(db, session_id)
    # The copy is recorded so a retry resumes it instead of copying another.
    assert get_session_form(db, session_id) is not None


# ---------------------------------------------------------------------------
# ingest: stored raw, resolved at read time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_answers_are_stored_raw_and_resolved_at_read_time(db, scheme):
    fake = _fake(scheme)
    make_fellow(db)
    session_id, result = _provision(db, fake)
    seed_part_a(db, fake, result.form_id, [{
        "email": "ada@example.invalid",
        "submitted_at": IN_WINDOW,
        "answers": {
            "q_rating": "5",
            # Three values, one of which contains a comma: joining them would
            # make this indistinguishable from two answers.
            "q_topics": ["Budgets", "Taxes", "Budgets, taxes and debt"],
            "q_takeaway": "  Budgets are moral documents.  ",
        },
    }])

    pulled = pull_session(db, fake, session_id)
    assert pulled.rows_written == 1

    row = fetch_one(
        db,
        "select form_id, answers, extra_fields, passphrase_raw, passphrase_match, "
        "edit_distance from checkin",
    )
    topics = _qid(db, result.form_id, "q_topics")
    assert row["form_id"] == result.form_id
    assert row["answers"][topics] == {
        "values": ["Budgets", "Taxes", "Budgets, taxes and debt"],
        "title": "Which topics landed?",
    }
    assert set(row["extra_fields"]) == {"_response_id"}, "answers have their own column"
    assert (row["passphrase_raw"], row["passphrase_match"], row["edit_distance"]) == (None, None, None)

    by_key = {
        r["question_key"]: r
        for r in fetch_all(
            db,
            "select question_key, item_index, kind, question_text, answer_values, has_content "
            "from v_checkin_answer",
        )
    }
    assert by_key["q_topics"]["answer_values"] == ["Budgets", "Taxes", "Budgets, taxes and debt"]
    assert by_key["q_rating"]["answer_values"] == ["5"]
    assert by_key["q_takeaway"]["answer_values"] == ["  Budgets are moral documents.  "]
    # The unanswered mapped question is a row too, so "answered N of M" is a count.
    assert by_key["q_format"]["answer_values"] == [] and by_key["q_format"]["has_content"] is False
    assert by_key["q_format"]["item_index"] == 3

    resolved = fetch_one(
        db, "select questions_answered, in_session_window, fellow_id from v_checkin_resolved"
    )
    assert resolved["questions_answered"] == 3
    assert resolved["in_session_window"] is True
    assert resolved["fellow_id"] == "CU-0001"

    # Idempotent: the same responses again write nothing.
    fake._list_calls = 0
    execute(db, "update session_form set response_watermark = null where part = 'a'")
    again = pull_session(db, fake, session_id)
    assert again.rows_written == 0 and count(db, "checkin") == 1


def test_blank_and_whitespace_answers_are_kept_but_not_counted(db, fake):
    """Counted, never graded: whitespace is not content, and nothing scores
    what is written."""
    session_id, result = _provision(db, fake)
    seed_part_a(db, fake, result.form_id, [
        {"email": "a@example.invalid", "submitted_at": IN_WINDOW,
         "answers": {"q_rating": "3", "q_takeaway": "   "}},
        {"email": "b@example.invalid", "submitted_at": IN_WINDOW,
         "answers": {"q_rating": "4", "q_takeaway": "🙂"}},
    ])
    pull_session(db, fake, session_id)

    answered = {
        r["submitted_email"]: r["questions_answered"]
        for r in fetch_all(db, "select submitted_email, questions_answered from v_checkin_resolved")
    }
    assert answered == {"a@example.invalid": 1, "b@example.invalid": 2}
    stored = fetch_one(
        db,
        "select answer_values, has_content from v_checkin_answer a "
        "join checkin c using (checkin_id) "
        "where c.submitted_email = 'a@example.invalid' and a.question_key = 'q_takeaway'",
    )
    assert stored == {"answer_values": ["   "], "has_content": False}


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_a_missing_map_warns_and_never_refuses(db, scheme):
    fake = _fake(scheme)
    session_id, result = _provision(db, fake)
    seed_part_a(db, fake, result.form_id, [{
        "email": "a@example.invalid", "submitted_at": IN_WINDOW,
        "answers": {"q_takeaway": "Budgets."},
    }])
    execute(db, "delete from part_a_form_question where form_id = %s", (result.form_id,))

    pulled = pull_session(db, fake, session_id)
    assert pulled.rows_written == 1, "the attendance record is not held hostage"
    assert any("no recorded question map" in w for w in pulled.warnings)
    unresolved = fetch_one(db, "select question_key, question_text from v_checkin_answer")
    assert unresolved["question_key"] is None
    assert unresolved["question_text"] == "Biggest takeaway from Lesson 3?"

    # Provisioning again re-records the map; the stored answer resolves with no
    # change to the observation.
    provision_session(db, fake, session_id)
    resolved = fetch_one(db, "select question_key from v_checkin_answer where has_content")
    assert resolved["question_key"] == "q_takeaway"


@pytest.mark.parametrize("scheme", ID_SCHEMES)
def test_an_answer_to_a_question_added_by_hand_is_kept(db, scheme):
    fake = _fake(scheme)
    session_id, result = _provision(db, fake)
    fake.batch_update(result.form_id, [{"createItem": {"item": {
        "title": "Anything else?",
        "questionItem": {"question": {"textQuestion": {"paragraph": True}}},
    }, "location": {"index": 6}}}])
    extra = fake.get_form(result.form_id).by_index()[6].question_id

    seed_part_a(db, fake, result.form_id, [{
        "email": "a@example.invalid", "submitted_at": IN_WINDOW,
        "answers": {"q_rating": "4"}, "answers_by_id": {extra: "I liked the guest."},
    }])
    pulled = pull_session(db, fake, session_id)

    assert any("not in its map" in w for w in pulled.warnings)
    row = fetch_one(
        db, "select question_key, question_text, answer_values from v_checkin_answer "
            "where question_id = %s", (extra,),
    )
    assert row == {"question_key": None, "question_text": "Anything else?",
                   "answer_values": ["I liked the guest."]}


def test_the_window_is_an_observation_with_inclusive_edges(db, fake):
    session_id, result = _provision(db, fake)
    start, end = session_window(
        datetime(2026, 9, 15, 23, 0, tzinfo=UTC), 90, 15
    )
    stamps = {
        "early@example.invalid": "2026-09-15T22:44:59Z",
        "first@example.invalid": "2026-09-15T22:45:00Z",
        "last@example.invalid": "2026-09-16T00:45:00Z",
        "morning@example.invalid": "2026-09-16T13:00:00Z",
    }
    seed_part_a(db, fake, result.form_id, [
        {"email": email, "submitted_at": at, "answers": {"q_rating": "4"}}
        for email, at in stamps.items()
    ])
    pulled = pull_session(db, fake, session_id)
    assert pulled.rows_written == 4, "outside the window is still a row"
    assert any(w.startswith("note:") and "outside" in w for w in pulled.warnings)

    rows = {
        r["submitted_email"]: r
        for r in fetch_all(
            db,
            "select submitted_email, in_session_window, window_start_utc, window_end_utc "
            "from v_checkin_resolved",
        )
    }
    assert {e: r["in_session_window"] for e, r in rows.items()} == {
        "early@example.invalid": False,
        "first@example.invalid": True,
        "last@example.invalid": True,
        "morning@example.invalid": False,
    }
    assert rows["first@example.invalid"]["window_start_utc"] == start
    assert rows["first@example.invalid"]["window_end_utc"] == end


def test_the_new_observed_columns_are_immutable(db, fake):
    session_id, result = _provision(db, fake)
    seed_part_a(db, fake, result.form_id, [{
        "email": "a@example.invalid", "submitted_at": IN_WINDOW, "answers": {"q_rating": "4"},
    }])
    pull_session(db, fake, session_id)

    for statement in (
        "update checkin set answers = '{}'::jsonb",
        "update checkin set form_id = 'somewhere-else'",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation):
            execute(db, statement)
    # latency_seconds is still the one derived column that may be recomputed.
    execute(db, "update checkin set latency_seconds = 1")


# ---------------------------------------------------------------------------
# the Google fake behaves like Google
# ---------------------------------------------------------------------------


_GOOD_TEXT = {"title": "ok", "questionItem": {"question": {"textQuestion": {}}}}


@pytest.mark.parametrize(
    ("item", "fragment"),
    [
        ({"title": "x", "questionItem": {"question": {"choiceQuestion": {
            "type": "DROP_DOWN", "options": [{"value": "a"}, {"isOther": True}]}}}}, "Other"),
        ({"title": "x", "questionItem": {"question": {"choiceQuestion": {
            "type": "RADIO", "options": []}}}}, "at least one option"),
        ({"title": "x", "questionItem": {"question": {"choiceQuestion": {
            "type": "RADIO", "options": [{"value": " "}]}}}}, "cannot be empty"),
        ({"title": "x", "questionItem": {"question": {"choiceQuestion": {
            "type": "RADIO", "options": [{"value": "a"}, {"value": "a"}]}}}}, "duplicate"),
        ({"title": "x", "questionItem": {"question": {"scaleQuestion": {
            "low": 2, "high": 5}}}}, "low must be 0 or 1"),
        ({"title": "x", "questionItem": {"question": {"scaleQuestion": {
            "low": 1, "high": 11}}}}, "between 2 and 10"),
        ({"title": "x", "questionItem": {"question": {"fileUploadQuestion": {}}}}, "file upload"),
        ({"title": "x"}, "exactly one kind"),
    ],
)
def test_the_fake_rejects_what_create_item_rejects(item, fragment):
    fake = FakeGoogleClient()
    ref = fake.create_template("t")
    with pytest.raises(GoogleApiError) as excinfo:
        fake.batch_update(ref.form_id, [{"createItem": {"item": item, "location": {"index": 0}}}])
    assert excinfo.value.status == 400
    assert fragment in str(excinfo.value)
    assert fake.forms[ref.form_id].items == []


def test_the_fakes_batch_update_is_all_or_nothing():
    fake = FakeGoogleClient()
    ref = fake.create_template("Original")
    fake.batch_update(ref.form_id, [{"createItem": {"item": _GOOD_TEXT, "location": {"index": 0}}}])

    with pytest.raises(GoogleApiError, match="no item at index 5"):
        fake.batch_update(ref.form_id, [
            {"updateFormInfo": {"info": {"title": "Changed"}, "updateMask": "title"}},
            {"deleteItem": {"location": {"index": 0}}},
            {"deleteItem": {"location": {"index": 5}}},
        ])
    form = fake.forms[ref.form_id]
    assert form.title == "Original" and len(form.items) == 1
    with pytest.raises(GoogleApiError, match="out of range"):
        fake.batch_update(ref.form_id, [{"createItem": {"item": _GOOD_TEXT, "location": {"index": 3}}}])


def test_the_fake_skips_non_question_items_but_keeps_their_index():
    fake = FakeGoogleClient()
    form_id = fake.simulate_form_created_by_hand([
        {"title": "Intro", "textItem": {}},
        _GOOD_TEXT,
        {"title": "Page 2", "pageBreakItem": {}},
        {"title": "Rate", "questionItem": {"question": {"required": True,
                                                        "scaleQuestion": {"low": 0, "high": 3}}}},
    ])
    definition = fake.get_form(form_id)
    assert [(i.index, i.kind) for i in definition.items] == [(1, "text"), (3, "scale")]
    assert definition.items[1].required is True
    assert definition.items[1].question["scaleQuestion"] == {"low": 0, "high": 3}
    assert len(definition.raw["items"]) == 4


def test_seed_answers_never_invents_a_question(db, fake):
    session_id, result = _provision(db, fake)
    with pytest.raises(ValueError, match="not on form"):
        fake.seed_answers(result.form_id, [{
            "respondent_email": "a@example.invalid", "create_time": IN_WINDOW,
            "answers": {"q-made-up": ["x"]},
        }])


def test_the_fake_persists_bodies_and_every_value(tmp_path):
    state = tmp_path / "google.json"
    fake = FakeGoogleClient(state_path=state)
    form_id = fake.simulate_form_created_by_hand([{"title": "Pick", "questionItem": {"question": {
        "choiceQuestion": {"type": "CHECKBOX", "options": [{"value": "A"}, {"value": "B"}]}}}}])
    qid = fake.get_form(form_id).items[0].question_id
    fake.seed_answers(form_id, [{"respondent_email": "a@example.invalid",
                                 "create_time": IN_WINDOW, "answers": {qid: ["A", "B"]}}])

    again = FakeGoogleClient.restore(state)
    assert again.get_form(form_id).items[0].question == fake.get_form(form_id).items[0].question
    page = again.list_responses(form_id)
    assert page.responses[0].answer_values_by_id == {qid: ("A", "B")}
    assert page.titles_by_id == {qid: "Pick"}


def test_state_written_before_bodies_still_loads(tmp_path):
    state = tmp_path / "old.json"
    state.write_text(json.dumps({"next_id": 2, "next_question": 3, "forms": {"fake-form-0001": {
        "form_id": "fake-form-0001", "title": "Check-in", "items": [
            {"item_id": "q00001", "question_id": "q00002", "title": "Today's passphrase",
             "kind": "text"}],
        "responses": [{"response_id": "r1", "respondent_email": "a@example.invalid",
                       "submitted_at": IN_WINDOW, "answers_by_id": {"q00002": "justice"}}],
    }}}), encoding="utf-8")
    fake = FakeGoogleClient.restore(state)
    assert fake.get_form("fake-form-0001").items[0].question_id == "q00002"
    assert fake.list_responses("fake-form-0001").responses[0].answer_values_by_id == {
        "q00002": ("justice",)
    }


# ---------------------------------------------------------------------------
# the CLI
# ---------------------------------------------------------------------------


def test_cufa_questions_seed_show_set_export_revert(db, tmp_path, capsys):
    from cufa.cli import main

    assert main(["questions", "show", "--cohort", TEST_COHORT]) == 1
    assert "seed-default" in capsys.readouterr().err

    assert main(["questions", "seed-default", "--cohort", TEST_COHORT]) == 0
    assert "seeded as default v1" in capsys.readouterr().out
    assert main(["questions", "seed-default", "--cohort", TEST_COHORT]) == 0
    assert "unchanged" in capsys.readouterr().out
    assert count(db, "part_a_question_set") == 1

    exported = tmp_path / "exit.json"
    assert main(["questions", "export", "--cohort", TEST_COHORT, "--out", str(exported)]) == 0
    data = json.loads(exported.read_text(encoding="utf-8"))
    assert data["questions"][0]["key"] == "q_first_name"
    data["questions"].append({"type": "short_answer", "title": "Anything else?"})
    exported.write_text(json.dumps(data), encoding="utf-8")

    session_id = make_session(db, week_index=3, title="Budgets")
    capsys.readouterr()
    assert main(["questions", "set", "--session", session_id, "--file", str(exported)]) == 0
    assert "saved as custom v1 (9 questions)" in capsys.readouterr().out

    assert main(["questions", "show", "--session", session_id]) == 0
    shown = capsys.readouterr().out
    assert "this session's own override" in shown and "Anything else?" in shown
    assert "open until the form is published" in shown

    assert main(["questions", "revert", "--session", session_id]) == 0
    assert "uses the cohort default again" in capsys.readouterr().out
    assert question_sets.resolve_for_session(db, session_id)[0] == "default"


def test_cufa_questions_import_form_dry_run_writes_a_file(db, fake, tmp_path, capsys):
    from cufa.cli import main

    form_id = _hand_made_form(fake)
    out = tmp_path / "imported.json"
    assert main(["questions", "import-form", "--cohort", TEST_COHORT, "--form", form_id,
                 "--dry-run", "--write", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "dry run — nothing saved" in printed and "Branching" in printed
    assert json.loads(out.read_text(encoding="utf-8"))["title"].startswith("Exit Ticket")
    assert count(db, "part_a_question_set") == 0


# ---------------------------------------------------------------------------
# no AI reads answers
# ---------------------------------------------------------------------------


def test_no_module_that_calls_a_model_reads_exit_ticket_answers():
    """Static, because the promise is about code that exists, not a path a test
    happened to run: nothing that talks to a model touches Part A's answers."""
    source = ROOT / "src" / "cufa"
    model_callers = [
        path for path in source.rglob("*.py")
        if "genai" in path.read_text(encoding="utf-8")
        and path.name != "config.py"
    ]
    assert model_callers, "the scan must find the modules that call a model"
    for path in model_callers:
        text = path.read_text(encoding="utf-8").lower()
        for needle in ("v_checkin_answer", "part_a_form_question", "checkin.answers", "c.answers"):
            assert needle not in text, f"{path.name} references {needle}"
