"""The CLI surface.

Everything the console does has to be doable from a terminal — that is what
keeps the system scriptable, testable without a browser, and usable on the day
the web app breaks. These tests cover the parts of that surface with real
behaviour behind them, not the argparse wiring.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from conftest import TEST_COHORT, make_session

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
    """Editing the passphrase must not require re-typing the schedule.

    Re-typing a schedule is how a session time gets changed by accident.
    """
    session_id = make_session(
        db, title="Original", local=datetime(2026, 9, 15, 19, 0), passphrase="justice"
    )
    before = get_session(db, session_id)

    assert main(["session", "edit", "--session", session_id, "--title", "Renamed"]) == 0

    after = get_session(db, session_id)
    assert after["title"] == "Renamed"
    assert after["scheduled_at_local"] == before["scheduled_at_local"]
    assert after["timezone"] == before["timezone"]
    assert after["duration_minutes"] == before["duration_minutes"]
    assert after["grace_minutes"] == before["grace_minutes"]
    assert after["passphrase"] == "justice"


def test_editing_the_schedule_recomputes_utc(db):
    session_id = make_session(db, local=datetime(2026, 9, 15, 19, 0))

    assert main(
        ["session", "edit", "--session", session_id, "--scheduled-at", "2026-09-15T20:30"]
    ) == 0

    after = get_session(db, session_id)
    assert after["scheduled_at_local"].strftime("%H:%M") == "20:30"
    # 20:30 America/New_York in September (EDT, UTC-4) is 00:30Z the next day.
    assert after["scheduled_at_utc"].strftime("%Y-%m-%dT%H:%MZ") == "2026-09-16T00:30Z"


def test_edit_refuses_a_reused_passphrase_without_allow_reuse(db, capsys):
    make_session(db, title="Week 1", local=datetime(2026, 9, 15, 19, 0), passphrase="harbor")
    target = make_session(
        db, title="Week 2", local=datetime(2026, 9, 22, 19, 0), passphrase="lantern"
    )

    assert main(["session", "edit", "--session", target, "--passphrase", "harbor"]) == 1
    assert get_session(db, target)["passphrase"] == "lantern", "nothing was saved"

    captured = capsys.readouterr()
    assert "already used" in captured.err
    assert "--allow-reuse" in captured.err

    assert main(
        ["session", "edit", "--session", target, "--passphrase", "harbor", "--allow-reuse"]
    ) == 0
    assert get_session(db, target)["passphrase"] == "harbor"


def test_edit_does_not_warn_about_a_session_reusing_its_own_passphrase(db, capsys):
    """A session keeps its own word when only the title changes."""
    target = make_session(db, title="Week 1", passphrase="harbor")

    assert main(["session", "edit", "--session", target, "--title", "Week One"]) == 0
    assert "already used" not in capsys.readouterr().err


def test_editing_an_unknown_session_says_so(db):
    import uuid

    with pytest.raises(SystemExit) as excinfo:
        raise SystemExit(
            main(["session", "edit", "--session", str(uuid.uuid4()), "--title", "x"])
        )
    assert excinfo.value.code == 1


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
                    "Today's passphrase": "justice",
                }
            ],
            ["Timestamp", "Email Address", "Today's passphrase"],
        ),
        TEST_COHORT,
        "America/New_York",
    )
    adjudicate_cohort(db, TEST_COHORT, use_ai=False)
    checkin_id = str(fetch_one(db, "select checkin_id from checkin")["checkin_id"])

    assert main(
        [
            "decide", "--checkin", checkin_id, "--status", "not_attended",
            "--by", "staff@cu.invalid", "--note", "Confirmed absent",
        ]
    ) == 0

    out = capsys.readouterr().out
    assert "superseding" in out, "the person deciding should see what they replaced"
    assert "exact_match" in out

    current = fetch_one(
        db,
        "select status, decided_by, human_email, note from attendance_decision "
        "where checkin_id = %s and superseded_at is null",
        (checkin_id,),
    )
    assert current["status"] == "not_attended"
    assert current["decided_by"] == "human"
    assert current["human_email"] == "staff@cu.invalid"
    assert current["note"] == "Confirmed absent"


# --- parser completeness ----------------------------------------------------

def test_every_documented_command_is_reachable():
    """The README and docs promise this surface; keep them honest."""
    parser = build_parser()
    subparsers = next(
        action for action in parser._actions if hasattr(action, "choices") and action.choices
    )
    expected = {
        "db", "serve", "google", "template", "load-roster", "load-sessions",
        "session", "provision", "pull", "ingest", "adjudicate", "decide",
        "review", "report",
    }
    assert expected <= set(subparsers.choices)


def test_ingest_rejects_a_missing_timezone_at_the_cli(db, tmp_path, capsys):
    from conftest import write_csv

    path = write_csv(
        tmp_path / "r.csv",
        [
            {
                "Timestamp": "2026-09-15 19:20:00",
                "Email Address": "a@example.invalid",
                "Today's passphrase": "justice",
            }
        ],
        ["Timestamp", "Email Address", "Today's passphrase"],
    )
    make_session(db)

    assert main(["ingest", "part-a", "--csv", str(path), "--cohort", TEST_COHORT]) == 1
    assert "--sheet-timezone" in capsys.readouterr().err
