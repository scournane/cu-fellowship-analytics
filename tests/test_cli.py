"""The CLI surface.

Everything the console does has to be doable from a terminal — that is what
keeps the system scriptable, testable without a browser, and usable on the day
the web app breaks. These tests cover the parts of that surface with real
behaviour behind them, not the argparse wiring.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from conftest import TEST_COHORT, count, make_session

from cufa.cli import _checkin_id, _session_id, build_parser, main
from cufa.db import fetch_one
from cufa.errors import CufaError
from cufa.sessions import get_session


# --- id validation ----------------------------------------------------------

@pytest.mark.parametrize("bad", ["typo", "", "   ", "1234", "not-a-uuid-at-all"])
def test_a_malformed_session_id_is_a_clear_error_not_a_traceback(bad):
    with pytest.raises(CufaError) as excinfo:
        _session_id(bad)
    assert "session id" in str(excinfo.value)
    assert "cufa session list" in str(excinfo.value)


def test_a_malformed_checkin_id_points_at_the_review_queue():
    with pytest.raises(CufaError) as excinfo:
        _checkin_id("oops")
    assert "cufa review" in str(excinfo.value)


def test_a_valid_uuid_passes_through_normalized():
    assert _session_id("  3929BEE7-ED99-40AE-9136-A533EEC9E183 ") == (
        "3929bee7-ed99-40ae-9136-a533eec9e183"
    )


# --- session edit -----------------------------------------------------------

def test_editing_one_field_leaves_the_others_alone(db, capsys):
    """Editing the title must not require re-typing the schedule.

    Re-typing a schedule is how a session time gets changed by accident, and
    the schedule is what attendance is judged against.
    """
    session_id = make_session(
        db,
        title="Original",
        local=datetime(2026, 9, 15, 19, 0),
        zoom_url="https://zoom.example.invalid/j/123",
        agenda="Opening\nWorkshop",
        slack_channel_id="announcements",
    )
    before = get_session(db, session_id)

    assert main(["session", "edit", "--session", session_id, "--title", "Renamed"]) == 0

    after = get_session(db, session_id)
    assert after["title"] == "Renamed"
    assert after["scheduled_at_local"] == before["scheduled_at_local"]
    assert after["timezone"] == before["timezone"]
    assert after["duration_minutes"] == before["duration_minutes"]
    assert after["grace_minutes"] == before["grace_minutes"]
    assert after["zoom_url"] == before["zoom_url"]
    assert after["agenda"] == before["agenda"]
    assert after["slack_channel_id"] == before["slack_channel_id"]


def test_editing_the_schedule_recomputes_utc(db):
    session_id = make_session(db, local=datetime(2026, 9, 15, 19, 0))

    assert main(
        ["session", "edit", "--session", session_id, "--scheduled-at", "2026-09-15T20:30"]
    ) == 0

    after = get_session(db, session_id)
    assert after["scheduled_at_local"].strftime("%H:%M") == "20:30"
    # 20:30 America/New_York in September (EDT, UTC-4) is 00:30Z the next day.
    assert after["scheduled_at_utc"].strftime("%Y-%m-%dT%H:%MZ") == "2026-09-16T00:30Z"


@pytest.mark.parametrize(
    "argv",
    [
        ["session", "suggest-passphrase"],
        ["session", "edit", "--session", "3929bee7-ed99-40ae-9136-a533eec9e183",
         "--passphrase", "harbor"],
        ["session", "create", "--cohort", "c", "--title", "t", "--scheduled-at",
         "2026-09-15T19:00", "--timezone", "UTC", "--duration", "60", "--allow-reuse"],
    ],
)
def test_the_passphrase_commands_are_gone(argv, capsys):
    """Part A has no passphrase. A script still passing one should fail loudly
    rather than believe it set something."""
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(argv)
    assert excinfo.value.code == 2


def test_creating_a_session_never_writes_the_legacy_passphrase_column(db, capsys):
    assert main(
        [
            "session", "create", "--cohort", TEST_COHORT, "--title", "Week 1",
            "--scheduled-at", "2026-09-15T19:00", "--timezone", "America/New_York",
            "--duration", "90",
        ]
    ) == 0
    session_id = capsys.readouterr().out.strip()
    assert get_session(db, session_id)["passphrase"] is None


def test_load_sessions_says_a_passphrase_column_is_ignored(db, tmp_path, capsys):
    from conftest import write_csv

    path = write_csv(
        tmp_path / "sessions.csv",
        [
            {
                "cohort_id": TEST_COHORT, "title": "Week 1",
                "scheduled_at_local": "2026-09-15 19:00", "timezone": "America/New_York",
                "duration_minutes": "90", "passphrase": "harbor",
            },
            {
                "cohort_id": TEST_COHORT, "title": "Week 2",
                "scheduled_at_local": "2026-09-22 19:00", "timezone": "America/New_York",
                "duration_minutes": "90", "passphrase": "lantern",
            },
        ],
        ["cohort_id", "title", "scheduled_at_local", "timezone", "duration_minutes", "passphrase"],
    )

    from cufa.roster import inspect_sessions_csv

    # The console's read-before-write check says it too, before anything is saved.
    check = inspect_sessions_csv(path)
    assert not check.problems
    assert sum("'passphrase' column is ignored" in w for w in check.warnings) == 1

    assert main(["load-sessions", "--csv", str(path)]) == 0

    err = capsys.readouterr().err
    assert err.count("'passphrase' column is ignored") == 1, "once per file, not per row"
    assert count(db, '"session"') == 2
    assert count(db, '"session"', "passphrase is not null") == 0


def test_editing_an_unknown_session_says_so(db):
    import uuid

    with pytest.raises(SystemExit) as excinfo:
        raise SystemExit(
            main(["session", "edit", "--session", str(uuid.uuid4()), "--title", "x"])
        )
    assert excinfo.value.code == 1


# --- assignments ------------------------------------------------------------

def test_assignment_commands_create_and_cancel_a_due_item(db, capsys):
    assert main(
        [
            "assignment",
            "create",
            "--cohort",
            TEST_COHORT,
            "--title",
            "Community interview notes",
            "--due-at",
            "2026-09-18T17:00",
            "--timezone",
            "America/New_York",
            "--url",
            "https://classroom.example.invalid/interview",
        ]
    ) == 0
    assignment_id = capsys.readouterr().out.strip()
    row = fetch_one(
        db, "select * from assignment where assignment_id = %s", (assignment_id,)
    )
    assert row["title"] == "Community interview notes"
    assert row["due_at_utc"].strftime("%Y-%m-%dT%H:%MZ") == "2026-09-18T21:00Z"

    assert main(
        [
            "assignment",
            "edit",
            "--assignment",
            assignment_id,
            "--status",
            "cancelled",
        ]
    ) == 0
    assert fetch_one(
        db, "select status from assignment where assignment_id = %s", (assignment_id,)
    )["status"] == "cancelled"


# --- decide -----------------------------------------------------------------

def test_decide_records_a_human_decision_and_reports_what_it_superseded(db, tmp_path, capsys):
    from conftest import write_csv
    from cufa.adjudicate.engine import adjudicate_cohort
    from cufa.ingest.csv_path import ingest_csv

    make_session(db, local=datetime(2026, 9, 15, 19, 0))
    ingest_csv(
        db,
        write_csv(
            tmp_path / "r.csv",
            [
                {
                    "Timestamp": "2026-09-15 19:20:00",
                    "Email Address": "a@example.invalid",
                    "What stood out today?": "budgets",
                }
            ],
            ["Timestamp", "Email Address", "What stood out today?"],
        ),
        TEST_COHORT,
        "America/New_York",
    )
    adjudicate_cohort(db, TEST_COHORT)
    checkin_id = str(fetch_one(db, "select checkin_id from checkin")["checkin_id"])

    assert main(
        [
            "decide", "--checkin", checkin_id, "--status", "attended",
            "--by", "staff@cu.invalid", "--note", "Confirmed present",
        ]
    ) == 0

    out = capsys.readouterr().out
    assert "superseding" in out, "the person deciding should see what they replaced"
    assert "unverified_email_in_window" in out
    assert "ai=" not in out, "no model decided it, so none is named"

    current = fetch_one(
        db,
        "select status, decided_by, human_email, note from attendance_decision "
        "where checkin_id = %s and superseded_at is null",
        (checkin_id,),
    )
    assert current["status"] == "attended"
    assert current["decided_by"] == "human"
    assert current["human_email"] == "staff@cu.invalid"
    assert current["note"] == "Confirmed present"


# --- adjudicate and review -------------------------------------------------

def _checkin(db, session_id, at, *, email="ada@example.invalid", source="forms_api",
             passphrase_match=None, form_id=None, answers=None) -> str:
    """One observation, inserted the way ingest writes it."""
    from psycopg.types.json import Jsonb

    load = fetch_one(
        db,
        "insert into load_run (source, origin, cohort_id) values (%s, 'test', %s) "
        "returning load_id",
        (source, TEST_COHORT),
    )
    return str(fetch_one(
        db,
        """
        insert into checkin (source_event_id, source, submitted_email, submitted_at_utc,
                             submitted_at_raw, session_id, session_match,
                             passphrase_match, form_id, answers, load_id)
        values (%s, %s, %s, %s, %s, %s, 'matched', %s, %s, %s, %s)
        returning checkin_id
        """,
        (f"{source}:{email}:{at}", source, email, at, at, session_id, passphrase_match,
         form_id, Jsonb(answers or {}), load["load_id"]),
    )["checkin_id"])


def test_adjudicate_accepts_no_ai_as_a_no_op_and_says_so(db, capsys):
    session_id = make_session(db, local=datetime(2026, 9, 15, 19, 0))
    _checkin(db, session_id, "2026-09-15T23:20:00Z")

    assert main(["adjudicate", "--cohort", TEST_COHORT, "--no-ai"]) == 0

    captured = capsys.readouterr()
    assert "--no-ai is no longer needed and is ignored" in captured.err
    assert "adjudicate:" in captured.out
    assert count(db, "v_current_decision", "rule_name = 'verified_email_in_window'") == 1


def test_redecide_legacy_says_how_many_before_it_touches_any(db, capsys):
    from cufa.decisions import record_decision

    session_id = make_session(db, local=datetime(2026, 9, 15, 19, 0))
    legacy = _checkin(db, session_id, "2026-09-15T23:20:00Z", passphrase_match="exact")
    record_decision(db, legacy, status="attended", decided_by="rule",
                    rule_name="exact_match", confidence=1.0)

    assert main(["adjudicate", "--cohort", TEST_COHORT]) == 0
    out = capsys.readouterr().out
    assert "1 passphrase-era check-in(s) kept the decision they already had" in out
    assert "--redecide-legacy" in out

    assert main(["adjudicate", "--cohort", TEST_COHORT, "--redecide-legacy"]) == 0
    out = capsys.readouterr().out
    announced = out.index("re-judging 1 passphrase-era check-in(s)")
    assert announced < out.index("adjudicate:"), "the count comes first"
    assert "1 by rule, 0 by the AI tier" in out
    assert count(db, "v_current_decision", "rule_name = 'verified_email_in_window'") == 1


def test_review_shows_timing_and_answer_counts_never_the_answers(db, capsys):
    from cufa.adjudicate.engine import adjudicate_cohort
    from cufa.db import execute

    session_id = make_session(db, title="Week 1", local=datetime(2026, 9, 15, 19, 0))
    for index, (question_id, key) in enumerate(
        [("1a", "q_takeaway"), ("2b", "q_muddy"), ("3c", "q_clarity")]
    ):
        execute(
            db,
            "insert into part_a_form_question (form_id, question_id, question_key, "
            "item_index, kind, question_text) values ('form-1', %s, %s, %s, 'paragraph', %s)",
            (question_id, key, index, key),
        )
    # The window closes at 00:45Z; this arrives twelve minutes later.
    _checkin(
        db, session_id, "2026-09-16T00:57:00Z", form_id="form-1",
        answers={
            "1a": {"values": ["A private thought about budgets"], "title": "Takeaway"},
            "2b": {"values": ["   "], "title": "Muddy"},
        },
    )
    _checkin(db, session_id, "2026-09-15T23:20:00Z", email="bo@example.invalid", source="csv")
    adjudicate_cohort(db, TEST_COHORT)

    assert main(["review", "--status", "outside-window", "--cohort", TEST_COHORT]) == 0
    out = capsys.readouterr().out
    assert "12 min after the window" in out
    assert "answered 1 of 3" in out
    assert "outside_session_window (window 2026-09-15T22:45:00Z..2026-09-16T00:45:00Z)" in out
    assert "Google Form (verified address)" in out
    assert "1 check-in(s) marked not attended because of when they arrived" in out

    assert main(["review", "--cohort", TEST_COHORT]) == 0
    out += capsys.readouterr().out
    assert "inside the window" in out
    assert "sheet export (address not verified)" in out
    assert "unverified_email_in_window" in out
    assert "1 check-in(s) need review" in out

    assert "private thought" not in out, "a count, never the answers"
    assert "passphrase" not in out.lower()


def test_report_text_shows_timing_as_an_observation(db, capsys):
    session_id = make_session(db, local=datetime(2026, 9, 15, 19, 0))
    _checkin(db, session_id, "2026-09-15T23:20:00Z")
    _checkin(db, session_id, "2026-09-16T09:00:00Z", email="late@example.invalid")

    assert main(["adjudicate", "--cohort", TEST_COHORT]) == 0
    capsys.readouterr()
    assert main(["report", "--cohort", TEST_COHORT]) == 0
    out = capsys.readouterr().out

    assert "Timing (observation, not judgment)" in out
    lines = {line.split("  ")[1].strip(): line.split()[-1] for line in out.splitlines()
             if line.startswith("  inside the window") or line.startswith("  outside the window ")}
    assert lines == {"inside the window": "1", "outside the window": "1"}
    assert "passphrase" not in out.lower()
    assert "ai cache" not in out.lower()


# --- parser completeness ----------------------------------------------------

def test_every_documented_command_is_reachable():
    """The README and docs promise this surface; keep them honest."""
    parser = build_parser()
    subparsers = next(
        action for action in parser._actions if hasattr(action, "choices") and action.choices
    )
    expected = {
        "db", "serve", "google", "template", "load-roster", "load-sessions",
        "session", "assignment", "provision", "pull", "ingest", "adjudicate", "decide",
        "review", "report",
        # Part A's exit-ticket questions
        "questions",
        # Part B
        "themes", "shoutouts", "help-requests", "rotation",
    }
    assert expected <= set(subparsers.choices)


def test_the_part_aware_commands_all_take_a_part_flag():
    """Everything Part B needs is a flag on an existing command, not a parallel
    command tree. Two command trees is two places for a fix to be applied to
    one of."""
    parser = build_parser()
    subparsers = next(
        action for action in parser._actions if hasattr(action, "choices") and action.choices
    )
    for command in ("template", "provision", "pull"):
        options = {
            option
            for action in subparsers.choices[command]._actions
            for option in action.option_strings
        }
        assert "--part" in options, command


def test_ingest_rejects_a_missing_timezone_at_the_cli(db, tmp_path, capsys):
    from conftest import write_csv

    path = write_csv(
        tmp_path / "r.csv",
        [
            {
                "Timestamp": "2026-09-15 19:20:00",
                "Email Address": "a@example.invalid",
                "What stood out today?": "budgets",
            }
        ],
        ["Timestamp", "Email Address", "What stood out today?"],
    )
    make_session(db)

    assert main(["ingest", "part-a", "--csv", str(path), "--cohort", TEST_COHORT]) == 1
    assert "--sheet-timezone" in capsys.readouterr().err
