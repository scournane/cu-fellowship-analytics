"""The help-request path, and the AI boundary.

These are the tests the rest of Part B is allowed to depend on. Every one of
them proves a property that is invisible when it breaks:

* A help checkbox routed nowhere looks identical to one that works.
* A help request leaking into a participation count looks like a lower number,
  not like a bug — and the fellow who ticked the box is the only person who
  could tell, and cannot.
* A model given a name instead of a sentence returns something that reads
  exactly as plausible.

So none of them are asserted by convention. Test 19 in particular inspects the
SQL each export and report path *actually executes*, rather than trusting a
comment or a naming rule.

Numbered to match Deliverable 12 of the implementation prompt.
"""

from __future__ import annotations

import importlib
import inspect
from datetime import datetime
from pathlib import Path

import pytest

from cufa.db import fetch_all, fetch_one
from cufa.form_content_b import (
    HELP_OPTION,
    SLOT_CONFIDENCE,
    SLOT_HELP,
    SLOT_ROTATING,
    SLOT_SHOUTOUT,
    SLOT_TAKEAWAY,
)
from cufa.google.fake import QUESTION_IDS_PRESERVED, FakeGoogleClient
from cufa.help_requests import acknowledge, list_requests, open_count
from cufa.help_routing import (
    NOTIFIABLE_FIELDS,
    HelpRouting,
    RecordingNotifier,
    Recipient,
    build_notification,
)
from cufa.ingest.forms_b import pull_session_b
from cufa.provisioning import provision_session
from cufa.question_map import load_map
from cufa.report import EXPORT_PATHS, cohort_report, render_report_text
from cufa.template import create_template, verify_template
from cufa.themes import (
    ThemeDraft,
    build_prompt,
    current_themes,
    generate_themes,
    muddiest_answers,
)

from conftest import (
    TEST_COHORT,
    ExplodingClusterer,
    StubClusterer,
    make_fellow,
    make_session,
    seed_part_b,
)

ROUTED = HelpRouting(recipients=(Recipient("Director of Programs", "dop@example.invalid"),))
UNROUTED = HelpRouting()

SESSION_LOCAL = datetime(2026, 9, 15, 19, 0)
END_OF_SESSION = "2026-09-16T00:20:00Z"

TAKEAWAY = "The budget is a document about priorities, not about money."
ROTATING = "I still do not understand where the surplus goes."
SHOUTOUT = "Kestrel"


def _fake() -> FakeGoogleClient:
    from cufa.google.factory import set_fake_client

    client = FakeGoogleClient(question_id_scheme=QUESTION_IDS_PRESERVED, page_size=5)
    set_fake_client(client)
    return client


def _provision(db, fake, routing: HelpRouting, *, week: int = 2, title: str = "Week 2",
               local: datetime | None = None):
    import cufa.provisioning as provisioning

    if fetch_one(db, "select count(*) as n from form_template where part = 'b'")["n"] == 0:
        record = create_template(db, fake, "b")
        fake.simulate_human_sets_verified(record.form_id)
        verify_template(db, fake, "b")

    session_id = make_session(
        db, title=title, local=local or SESSION_LOCAL, week_index=week,
        teacher_question="What surprised you?",
    )
    original = provisioning.get_help_routing
    provisioning.get_help_routing = lambda *a, **k: routing
    try:
        result = provision_session(db, fake, session_id, part="b")
    finally:
        provisioning.get_help_routing = original
    return session_id, result


def _submit(db, fake, form_id, *, email="ada@example.invalid", help=False,
            submitted_at=END_OF_SESSION, confidence="6", rotating=ROTATING):
    seed_part_b(db, fake, form_id, [{
        "email": email,
        "submitted_at": submitted_at,
        "slots": {
            SLOT_CONFIDENCE: confidence,
            SLOT_TAKEAWAY: TAKEAWAY,
            SLOT_ROTATING: rotating,
            SLOT_SHOUTOUT: SHOUTOUT,
        },
        "help": help,
    }])


# ---------------------------------------------------------------------------
# 15 — no recipient, no field
# ---------------------------------------------------------------------------


def test_15_no_configured_recipient_means_no_checkbox_on_the_form(db):
    """Design invariant 2. A system that invites someone to ask for help and
    routes it nowhere is worse than one that never asks."""
    fake = _fake()
    _session_id, result = _provision(db, fake, UNROUTED)

    definition = fake.get_form(result.form_id)
    titles = [item.title for item in definition.items]
    assert len(definition.items) == 4, titles
    assert HELP_OPTION not in " ".join(titles)

    slots = {entry.slot for entry in load_map(db, result.form_id).values()}
    assert SLOT_HELP not in slots
    assert result.help_field_omitted_reason, "the omission must be reported, not silent"


def test_15b_the_reason_is_recorded_in_the_provisioning_log(db):
    fake = _fake()
    session_id, _result = _provision(db, fake, UNROUTED)
    rows = fetch_all(
        db,
        "select action, outcome, error from provisioning_log "
        "where session_id = %s and action = 'help_field_omitted'",
        (session_id,),
    )
    assert len(rows) == 1
    assert "no recipient" in rows[0]["error"].lower()


def test_15c_with_a_recipient_the_checkbox_is_there(db):
    fake = _fake()
    _session_id, result = _provision(db, fake, ROUTED)
    definition = fake.get_form(result.form_id)
    assert len(definition.items) == 5
    slots = {entry.slot for entry in load_map(db, result.form_id).values()}
    assert SLOT_HELP in slots


def test_15d_removing_the_recipient_removes_the_field_on_re_provision(db):
    """A recipient can be un-named. The next form provisioned must reflect
    today's routing, not the routing that happened to hold when the template was
    made."""
    import cufa.provisioning as provisioning

    fake = _fake()
    session_id, result = _provision(db, fake, ROUTED)
    assert len(fake.get_form(result.form_id).items) == 5

    original = provisioning.get_help_routing
    provisioning.get_help_routing = lambda *a, **k: UNROUTED
    try:
        provision_session(db, fake, session_id, part="b")
    finally:
        provisioning.get_help_routing = original

    # The already-ready path refreshes the map; a full re-provision would also
    # delete the item. Either way the map must no longer claim a help slot.
    slots = {entry.slot for entry in load_map(db, result.form_id).values()}
    assert SLOT_HELP not in slots


# ---------------------------------------------------------------------------
# 16 and 17 — routed immediately, and told the minimum
# ---------------------------------------------------------------------------


def test_16_a_help_request_notifies_during_the_ingest_pass(db):
    """Not on a batch schedule. Someone asking for contact should not wait for
    a weekly pipeline run."""
    fake = _fake()
    make_fellow(db, "CU-1", "ada@example.invalid", "Ada Testcase")
    session_id, result = _provision(db, fake, ROUTED)
    _submit(db, fake, result.form_id, help=True)

    notifier = RecordingNotifier()
    assert notifier.sent == []
    pull_session_b(db, fake, session_id, routing=ROUTED, notifier=notifier)

    assert len(notifier.sent) == 1, "the notification is sent inside the pull"
    assert fetch_one(db, "select count(*) as n from help_request")["n"] == 1
    assert open_count(db) == 1


def test_16b_a_re_pull_does_not_raise_the_same_hand_twice(db):
    fake = _fake()
    make_fellow(db, "CU-1", "ada@example.invalid", "Ada Testcase")
    session_id, result = _provision(db, fake, ROUTED)
    _submit(db, fake, result.form_id, help=True)

    notifier = RecordingNotifier()
    pull_session_b(db, fake, session_id, routing=ROUTED, notifier=notifier)
    pull_session_b(db, fake, session_id, routing=ROUTED, notifier=notifier)

    assert len(notifier.sent) == 1
    assert fetch_one(db, "select count(*) as n from help_request")["n"] == 1


def test_16c_an_unresolved_address_still_produces_a_request(db):
    """Losing a request because the roster is out of date is the one failure
    this table cannot have."""
    fake = _fake()
    session_id, result = _provision(db, fake, ROUTED)
    _submit(db, fake, result.form_id, email="not-on-roster@example.invalid", help=True)

    notifier = RecordingNotifier()
    pull_session_b(db, fake, session_id, routing=ROUTED, notifier=notifier)

    row = fetch_one(db, "select * from help_request")
    assert row is not None
    assert row["fellow_id"] is None
    assert row["submitted_email"] == "not-on-roster@example.invalid"
    assert len(notifier.sent) == 1


def test_16d_a_mail_failure_does_not_lose_the_request(db):
    class Failing:
        def send(self, notification):
            raise RuntimeError("smtp is down")

    fake = _fake()
    make_fellow(db, "CU-1", "ada@example.invalid", "Ada Testcase")
    session_id, result = _provision(db, fake, ROUTED)
    _submit(db, fake, result.form_id, help=True)

    pull_session_b(db, fake, session_id, routing=ROUTED, notifier=Failing())

    # The row is what the console reads. It must survive a broken mail path.
    assert fetch_one(db, "select count(*) as n from help_request")["n"] == 1
    assert fetch_one(db, "select count(*) as n from checkin_b")["n"] == 1


def test_17_the_notification_carries_the_name_and_session_and_nothing_else(db):
    fake = _fake()
    make_fellow(db, "CU-1", "ada@example.invalid", "Ada Testcase")
    session_id, result = _provision(db, fake, ROUTED, title="Reading a budget")
    _submit(db, fake, result.form_id, help=True, confidence="2")

    notifier = RecordingNotifier()
    pull_session_b(db, fake, session_id, routing=ROUTED, notifier=notifier)
    notification = notifier.sent[0]

    assert "Ada Testcase" in notification.body
    assert "Reading a budget" in notification.body

    # Minimum necessary. Everything else the fellow wrote stays theirs.
    for secret in (TAKEAWAY, ROTATING, SHOUTOUT):
        assert secret not in notification.body, secret
        assert secret not in notification.subject

    # The confidence score is harder to assert by string search — every value it
    # can take is a single digit that also occurs in a timestamp — so it is
    # asserted structurally instead, which is stronger. The notification is a
    # dataclass with exactly three content fields, and there is no parameter on
    # the builder through which a score, a takeaway or a shoutout could be
    # passed even by a future edit reaching for "a bit more context".
    import dataclasses

    content_fields = {
        field.name for field in dataclasses.fields(notification)
    } - {"to", "subject", "body"}
    assert content_fields == set(NOTIFIABLE_FIELDS)

    parameters = set(inspect.signature(build_notification).parameters) - {"routing"}
    assert parameters == set(NOTIFIABLE_FIELDS)


def test_17b_a_notification_says_out_loud_that_it_withholds_the_rest(db):
    """A responder who does not know the rest exists cannot ask for it, and a
    responder who thinks they were told everything will not go and talk to the
    person."""
    notification = build_notification(
        ROUTED,
        fellow_name="Ada Testcase",
        session_title="Reading a budget",
        submitted_at_utc="2026-09-16 00:20:00",
    )
    assert "on purpose" in notification.body
    assert "theirs to say" in notification.body


# ---------------------------------------------------------------------------
# 18 and 19 — reachable from nothing
# ---------------------------------------------------------------------------


class _RecordingCursor:
    """Wraps a real cursor and records every statement it runs."""

    def __init__(self, cursor, log: list[str]) -> None:
        self._cursor = cursor
        self._log = log

    def execute(self, query, params=None, *args, **kwargs):
        self._log.append(str(query))
        return self._cursor.execute(query, params, *args, **kwargs)

    def __enter__(self):
        self._cursor.__enter__()
        return self

    def __exit__(self, *exc):
        return self._cursor.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _RecordingConnection:
    """A connection that records the SQL every caller actually executes.

    This is how test 19 inspects the query paths rather than trusting a naming
    convention. A future report that joins ``help_request`` fails here whatever
    it is called and wherever it lives.
    """

    def __init__(self, conn) -> None:
        self._conn = conn
        self.statements: list[str] = []

    def cursor(self, *args, **kwargs):
        return _RecordingCursor(self._conn.cursor(*args, **kwargs), self.statements)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _populated(db):
    """A cohort with a help request in it, plus ordinary Part B data."""
    fake = _fake()
    make_fellow(db, "CU-1", "ada@example.invalid", "Ada Testcase")
    make_fellow(db, "CU-2", "kestrel.larkspur@example.invalid", "Kestrel Larkspur")
    session_id, result = _provision(db, fake, ROUTED)
    _submit(db, fake, result.form_id, help=True)
    _submit(
        db, fake, result.form_id, email="kestrel.larkspur@example.invalid",
        submitted_at="2026-09-16T00:21:00Z", confidence="3",
    )
    pull_session_b(db, fake, session_id, routing=ROUTED, notifier=RecordingNotifier())
    return session_id


def test_18_help_requests_appear_in_no_export_or_report(db):
    session_id = _populated(db)
    assert fetch_one(db, "select count(*) as n from help_request")["n"] == 1

    report = cohort_report(db, TEST_COHORT)
    rendered = render_report_text(report)
    payload = repr(report.to_dict())

    request = fetch_one(db, "select * from help_request")
    for needle in (
        str(request["help_request_id"]),
        "help_request",
        "check in with me",
    ):
        assert needle not in rendered, needle
        assert needle not in payload, needle

    # The report says the field exists and is excluded, which is different from
    # not mentioning it: a staffer has to be able to tell the omission is
    # deliberate.
    assert "help checkbox appears nowhere" in rendered


def test_18b_no_export_path_returns_anything_from_the_help_table(db):
    session_id = _populated(db)
    request_id = str(fetch_one(db, "select help_request_id from help_request")["help_request_id"])

    for path in EXPORT_PATHS:
        module_name, _, attribute = path.rpartition(".")
        function = getattr(importlib.import_module(module_name), attribute)
        signature = inspect.signature(function)

        if "cohort_id" in signature.parameters and "conn" in signature.parameters:
            result = function(db, TEST_COHORT)
        elif "session_id" in signature.parameters:
            result = function(db, session_id)
        elif "conn" in signature.parameters:
            result = function(db)
        elif attribute == "render_report_text":
            result = function(cohort_report(db, TEST_COHORT))
        elif attribute == "render_trend_text":
            from cufa.confidence import trend

            result = function(TEST_COHORT, trend(db, TEST_COHORT))
        else:  # pragma: no cover - a new export shape needs a decision here
            pytest.fail(f"{path} has an unhandled signature: {signature}")

        assert request_id not in repr(result), path


def test_19_no_report_or_participation_query_reads_help_request(db):
    """Asserted by inspecting the SQL each path actually executes.

    Not by convention, not by a naming rule, and not by reading the source: a
    query built at runtime out of an f-string would pass a source scan and fail
    here, which is the right way round.
    """
    session_id = _populated(db)
    recording = _RecordingConnection(db)

    from cufa.adjudicate.engine import adjudicate_cohort
    from cufa.confidence import by_fellow, for_session, straightliners, trend
    from cufa.report import ai_decisions, needs_review_queue, unresolved_identities
    from cufa.shoutouts import review_queue
    from cufa.themes import current_themes as themes_for

    report = cohort_report(recording, TEST_COHORT)
    render_report_text(report)
    needs_review_queue(recording, TEST_COHORT)
    ai_decisions(recording, TEST_COHORT)
    unresolved_identities(recording, TEST_COHORT)
    trend(recording, TEST_COHORT)
    by_fellow(recording, TEST_COHORT)
    for_session(recording, session_id)
    straightliners(recording, TEST_COHORT)
    review_queue(recording, TEST_COHORT)
    themes_for(recording, session_id)
    adjudicate_cohort(recording, TEST_COHORT, use_ai=False)

    # The HTML report, and each query only it makes.
    from cufa.report_html import fellow_grid, provenance, render_report_html, slack_summary

    fellow_grid(recording, TEST_COHORT)
    slack_summary(recording, TEST_COHORT)
    provenance(recording, TEST_COHORT)
    render_report_html(recording, TEST_COHORT)

    assert recording.statements, "nothing was recorded — the wrapper is not working"
    offenders = [sql for sql in recording.statements if "help_request" in sql.lower()]
    assert offenders == [], (
        "a participation, report or export query reads help_request:\n"
        + "\n".join(offenders)
    )


def test_19b_the_help_table_is_read_from_exactly_one_module(db):
    """A source-level companion to the runtime check above.

    The two catch different things — a runtime f-string escapes a source scan,
    and a query on a path this suite does not exercise escapes the runtime
    check — so both are kept.
    """
    source_root = Path(__file__).resolve().parents[1] / "src" / "cufa"
    allowed = {
        # Where the table is legitimately read and written.
        source_root / "help_requests.py",
        # Where the console's access-gated screen calls into it.
        source_root / "console" / "app.py",
        # The provisioning-time decision about the field, which names the
        # config rather than the table.
        source_root / "provisioning.py",
    }

    offenders = []
    for path in source_root.rglob("*.py"):
        if path in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        # `from help_request` / `join help_request` / `into help_request` —
        # a SQL reference rather than the module name or a comment.
        for keyword in ("from help_request", "join help_request", "into help_request",
                        "update help_request", "delete from help_request"):
            if keyword in text.lower():
                offenders.append(f"{path.name}: {keyword}")

    assert offenders == [], offenders


def test_19c_the_console_help_screen_is_gated_separately(db):
    from cufa.config import load_settings
    from cufa.console.app import help_access_list, may_read_help
    from cufa.console.auth import ConsoleUser

    settings = load_settings(
        {
            "CUFA_CONSOLE_ALLOWLIST": "everyone@example.invalid,dop@example.invalid",
            "CUFA_HELP_ALLOWLIST": "dop@example.invalid",
        }
    )
    assert help_access_list(settings) == ("dop@example.invalid",)
    assert may_read_help(ConsoleUser("dop@example.invalid", "dev"), settings)
    # On the general console allowlist, and still refused.
    assert not may_read_help(ConsoleUser("everyone@example.invalid", "dev"), settings)


def test_help_requests_can_be_acknowledged_and_closed(db):
    _populated(db)
    request_id = str(fetch_one(db, "select help_request_id from help_request")["help_request_id"])

    acknowledge(db, request_id, by_email="DOP@Example.Invalid", note="Emailed them.")
    row = fetch_one(db, "select * from help_request")
    assert row["status"] == "acknowledged"
    assert row["acknowledged_by"] == "dop@example.invalid"
    assert row["acknowledged_at"] is not None
    assert row["note"] == "Emailed them."
    assert open_count(db) == 0

    acknowledge(db, request_id, by_email="dop@example.invalid", status="closed")
    assert fetch_one(db, "select status from help_request")["status"] == "closed"
    assert len(list_requests(db, status="closed")) == 1


def test_nothing_about_a_help_request_is_logged(db, caplog):
    """Never logged at any level, DEBUG included."""
    import logging

    fake = _fake()
    make_fellow(db, "CU-1", "ada@example.invalid", "Ada Testcase")
    session_id, result = _provision(db, fake, ROUTED)
    _submit(db, fake, result.form_id, help=True)

    with caplog.at_level(logging.DEBUG):
        pull_session_b(db, fake, session_id, routing=ROUTED, notifier=RecordingNotifier())

    emitted = "\n".join(record.getMessage() for record in caplog.records)
    for secret in ("ada@example.invalid", "Ada Testcase", TAKEAWAY, SHOUTOUT, HELP_OPTION):
        assert secret not in emitted, secret


# ---------------------------------------------------------------------------
# 20-23 — the AI boundary
# ---------------------------------------------------------------------------


def test_20_and_21_the_clustering_payload_is_text_only(db):
    """Asserted on the exact string that would be sent.

    ``build_prompt`` is the whole payload, so this is not a proxy for the claim
    — it is the claim.
    """
    fake = _fake()
    make_fellow(db, "CU-1", "ada@example.invalid", "Ada Testcase")
    make_fellow(db, "CU-2", "kestrel.larkspur@example.invalid", "Kestrel Larkspur")
    session_id, result = _provision(db, fake, ROUTED, week=2)

    for index, (email, answer) in enumerate([
        ("ada@example.invalid", "Where the surplus goes."),
        ("kestrel.larkspur@example.invalid", "Who signs off on the budget."),
        ("ada@example.invalid", "The difference between operating and capital."),
        ("kestrel.larkspur@example.invalid", "How long any of this takes."),
    ]):
        seed_part_b(db, fake, result.form_id, [{
            "email": email,
            "submitted_at": f"2026-09-16T00:2{index}:00Z",
            "slots": {
                SLOT_CONFIDENCE: "5",
                SLOT_TAKEAWAY: TAKEAWAY,
                SLOT_ROTATING: answer,
                SLOT_SHOUTOUT: SHOUTOUT,
            },
            "help": index == 0,
        }])
    pull_session_b(db, fake, session_id, routing=ROUTED, notifier=RecordingNotifier())

    # The query itself does not even fetch an identifying column.
    answers = muddiest_answers(db, session_id)
    assert answers, "the fixture must produce muddiest-point answers"
    assert set(answers[0]) == {"checkin_b_id", "rotating_text"}

    clusterer = StubClusterer()
    generate_themes(db, session_id, clusterer, regenerate=True)

    assert len(clusterer.prompts) == 1
    payload = clusterer.prompts[0]

    for forbidden in (
        "ada@example.invalid",
        "kestrel.larkspur@example.invalid",
        "Ada Testcase",
        "Kestrel Larkspur",
        "CU-1",
        "CU-2",
        # 20: no help-request data reaches the model, ever.
        HELP_OPTION,
        "help_request",
        # And no confidence score or takeaway either — the model's job is the
        # muddiest point and nothing else.
        TAKEAWAY,
        str(session_id),
    ):
        assert forbidden not in payload, forbidden

    for answer in ("Where the surplus goes.", "How long any of this takes."):
        assert answer in payload

    # The prompt also tells the model not to characterise anyone, since a model
    # asked to group sentences will otherwise describe the people who wrote them.
    assert "do not describe or characterise any student" in payload.lower()


def test_21b_no_email_or_id_appears_in_a_prompt_built_from_anything(db):
    """``build_prompt`` numbers the strings it is given and adds nothing."""
    prompt = build_prompt(["first answer", "second answer"])
    assert "1. first answer" in prompt
    assert "2. second answer" in prompt
    assert "@" not in prompt.replace("@", "", 0) or "@example" not in prompt


def test_22_no_api_key_means_no_themes_and_a_clear_message(db):
    """Degrade, never crash. The answers are the data; themes are a view."""
    fake = _fake()
    session_id, result = _provision(db, fake, ROUTED, week=2)
    for index in range(4):
        seed_part_b(db, fake, result.form_id, [{
            "email": f"f{index}@example.invalid",
            "submitted_at": f"2026-09-16T00:2{index}:00Z",
            "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "x",
                      SLOT_ROTATING: f"Unclear thing {index}"},
        }])
    pull_session_b(db, fake, session_id, routing=ROUTED, notifier=RecordingNotifier())

    # conftest removes GEMINI_API_KEY from the environment, so this is the real
    # no-key path rather than a simulated one.
    result_no_key = generate_themes(db, session_id, regenerate=True)
    assert result_no_key.generated is False
    assert result_no_key.themes == []
    assert "GEMINI_API_KEY" in result_no_key.message
    assert "stored and readable" in result_no_key.message

    # The answers are untouched.
    assert fetch_one(db, "select count(*) as n from checkin_b")["n"] == 4


def test_22b_a_failing_model_degrades_rather_than_raising(db):
    fake = _fake()
    session_id, result = _provision(db, fake, ROUTED, week=2)
    for index in range(4):
        seed_part_b(db, fake, result.form_id, [{
            "email": f"f{index}@example.invalid",
            "submitted_at": f"2026-09-16T00:2{index}:00Z",
            "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "x",
                      SLOT_ROTATING: f"Unclear thing {index}"},
        }])
    pull_session_b(db, fake, session_id, routing=ROUTED, notifier=RecordingNotifier())

    outcome = generate_themes(db, session_id, ExplodingClusterer(), regenerate=True)
    assert outcome.generated is False
    assert "quota exhausted" in outcome.message


def test_22c_too_few_answers_is_a_message_not_a_guess(db):
    """Three sentences do not have a theme. A model asked to find one will."""
    fake = _fake()
    session_id, result = _provision(db, fake, ROUTED, week=2)
    seed_part_b(db, fake, result.form_id, [{
        "email": "one@example.invalid",
        "submitted_at": END_OF_SESSION,
        "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "x", SLOT_ROTATING: "Only one."},
    }])
    pull_session_b(db, fake, session_id, routing=ROUTED, notifier=RecordingNotifier())

    clusterer = StubClusterer()
    outcome = generate_themes(db, session_id, clusterer, regenerate=True)
    assert outcome.generated is False
    assert clusterer.calls == [], "the model must not be called at all"


def test_23_regenerating_supersedes_rather_than_overwriting(db):
    """A teacher who planned a lesson around last week's themes must still be
    able to see what they read."""
    fake = _fake()
    session_id, result = _provision(db, fake, ROUTED, week=2)
    for index in range(4):
        seed_part_b(db, fake, result.form_id, [{
            "email": f"f{index}@example.invalid",
            "submitted_at": f"2026-09-16T00:2{index}:00Z",
            "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "x",
                      SLOT_ROTATING: f"Unclear thing {index}"},
        }])
    pull_session_b(db, fake, session_id, routing=ROUTED, notifier=RecordingNotifier())

    first = generate_themes(db, session_id, StubClusterer(), regenerate=True)
    assert first.generated
    first_ids = {str(theme["theme_id"]) for theme in current_themes(db, session_id)}
    assert first_ids

    second = generate_themes(
        db,
        session_id,
        StubClusterer(themes=[
            ThemeDraft("Money", "Where the money goes.", (1, 2)),
            ThemeDraft("Process", "Who decides.", (3, 4)),
        ]),
        regenerate=True,
    )
    assert second.generated
    assert second.superseded == len(first_ids)

    live = {str(theme["theme_id"]) for theme in current_themes(db, session_id)}
    assert live.isdisjoint(first_ids), "the new batch replaced the old as current"

    total = fetch_one(db, "select count(*) as n from muddiest_theme")["n"]
    assert total == len(first_ids) + 2, "the old rows are still there, superseded"

    superseded = fetch_all(
        db, "select label from muddiest_theme where superseded_at is not null"
    )
    assert len(superseded) == len(first_ids)


def test_23b_without_regenerate_no_model_call_is_made(db):
    fake = _fake()
    session_id, result = _provision(db, fake, ROUTED, week=2)
    for index in range(4):
        seed_part_b(db, fake, result.form_id, [{
            "email": f"f{index}@example.invalid",
            "submitted_at": f"2026-09-16T00:2{index}:00Z",
            "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "x",
                      SLOT_ROTATING: f"Unclear thing {index}"},
        }])
    pull_session_b(db, fake, session_id, routing=ROUTED, notifier=RecordingNotifier())

    generate_themes(db, session_id, StubClusterer(), regenerate=True)

    second = StubClusterer()
    outcome = generate_themes(db, session_id, second)
    assert second.calls == [], "showing existing themes must not spend an API call"
    assert outcome.generated is False
    assert outcome.themes


def test_23c_themes_are_only_clustered_from_muddiest_point_weeks(db):
    """An application-week answer is not a muddiest point, and clustering the
    two together would produce a theme about nothing."""
    fake = _fake()
    session_id, result = _provision(db, fake, ROUTED, week=3, title="Week 3")
    for index in range(4):
        seed_part_b(db, fake, result.form_id, [{
            "email": f"f{index}@example.invalid",
            "submitted_at": f"2026-09-16T00:2{index}:00Z",
            "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "x",
                      SLOT_ROTATING: f"I would use it for {index}"},
        }])
    pull_session_b(db, fake, session_id, routing=ROUTED, notifier=RecordingNotifier())

    assert fetch_one(db, "select rotating_kind from checkin_b")["rotating_kind"] == "application"
    assert muddiest_answers(db, session_id) == []

    clusterer = StubClusterer()
    outcome = generate_themes(db, session_id, clusterer, regenerate=True)
    assert clusterer.calls == []
    assert outcome.generated is False


# ---------------------------------------------------------------------------
# RLS, asked of the database rather than read off the migrations
# ---------------------------------------------------------------------------

#: Tables holding a fellow's identity, their words, or something derived from
#: them. Every one must have RLS enabled — the service role bypasses it, so this
#: costs the pipeline nothing and is the only thing standing between a Studio
#: session on an anon key and the data.
#:
#: Adding a table that holds fellow content means adding it here. That is the
#: point: the check below is what catches the one somebody forgets.
FELLOW_DATA_TABLES = (
    "fellow",
    "identity_unresolved",
    "checkin",
    "checkin_b",
    "attendance_decision",
    "peer_shoutout",
    "muddiest_theme",
    "muddiest_theme_member",
    "help_request",
    "google_credential",
)


def test_every_table_holding_fellow_data_has_rls_enabled(db):
    """Asked of pg_class, not of the migration files.

    `muddiest_theme` was found exposed this way: the migration that should have
    covered it simply did not, and reading the SQL would not have shown that —
    it shows what was intended, and the intent was the thing missing.
    """
    exposed = [
        row["relname"]
        for row in fetch_all(
            db,
            "select relname from pg_class where relname = any(%s) "
            "and relkind = 'r' and not relrowsecurity",
            (list(FELLOW_DATA_TABLES),),
        )
    ]
    assert exposed == [], f"tables holding fellow data with RLS off: {exposed}"


def test_no_fellow_data_table_has_a_permissive_policy(db):
    """Until CU writes the real rule, every policy predicate is `false`.

    A policy that grants something is a decision about who may read a young
    person's data, and it is not one this codebase is entitled to make. See
    ADR-020.
    """
    permissive = [
        f"{row['tablename']}.{row['policyname']}: {row['qual']}"
        for row in fetch_all(
            db,
            "select tablename, policyname, qual from pg_policies "
            "where schemaname = 'public' and tablename = any(%s)",
            (list(FELLOW_DATA_TABLES),),
        )
        if (row["qual"] or "").strip().lower() not in ("false", "(false)")
    ]
    assert permissive == [], f"permissive policies found: {permissive}"


def test_rls_actually_filters_rows_rather_than_merely_being_on(db):
    """`relrowsecurity = true` with a permissive policy would pass the check
    above and expose everything. This reads as the role that matters."""
    import psycopg

    from cufa.config import get_settings

    fake = _fake()
    make_fellow(db, "CU-1", "ada@example.invalid", "Ada Testcase")
    session_id, result = _provision(db, fake, ROUTED)
    _submit(db, fake, result.form_id, help=True)
    pull_session_b(db, fake, session_id, routing=ROUTED, notifier=RecordingNotifier())
    db.commit()

    counts = {}
    with psycopg.connect(get_settings().database_url) as probe:
        with probe.cursor() as cur:
            cur.execute("grant usage on schema public to authenticated")
            cur.execute("grant select on all tables in schema public to authenticated")
        probe.commit()
        for table in ("checkin_b", "peer_shoutout", "help_request", "muddiest_theme"):
            with probe.cursor() as cur:
                cur.execute(f"select count(*) from {table}")
                as_service = cur.fetchone()[0]
            with probe.cursor() as cur:
                cur.execute("set local role authenticated")
                cur.execute(f"select count(*) from {table}")
                as_authenticated = cur.fetchone()[0]
            probe.rollback()
            counts[table] = (as_service, as_authenticated)

    assert counts["checkin_b"][0] > 0, "the fixture must actually write rows"
    assert counts["help_request"][0] > 0
    for table, (as_service, as_authenticated) in counts.items():
        assert as_authenticated == 0, (
            f"{table}: authenticated saw {as_authenticated} of {as_service} rows"
        )
