"""Data subject access, export, erasure, and the access audit log.

This is a privacy feature, so the tests are the feature. Five properties are
worth more than any amount of rendering:

1. **An export holds one person and nobody else.** The whole point of a subject
   access request is that it is scoped, and the rows here are about teenagers, so
   a leak is not a cosmetic bug. Every export test seeds two fellows who write
   about each other and asserts on the seam between them.
2. **An export withholds what belongs to somebody else, visibly.** A shoutout
   another fellow wrote, and the note a staff member wrote — both are checked to
   be absent, and both are checked to be *declared* absent.
3. **Erasure is a dry run until told otherwise, and is idempotent.** Running it
   twice is the normal case, because the first run is always the dry one.
4. **The access log records the read, and records the shared-password door
   honestly** — as nobody, not as ``shared-password@console.local`` wearing a
   person's clothes.
5. **No email address reaches a log.** Neither the fellow's, in the audit table
   or in what the fellow is shown, nor anybody's in full in a log line.

Each test makes its own cohort with a random id and cleans up after itself, so
it does not collide with the truncating ``db`` fixture or with another module.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

os.environ.setdefault(
    "CUFA_DATABASE_URL", "postgresql://postgres:postgres@localhost:64322/cufa_test"
)
os.environ["CUFA_FAKE_GOOGLE"] = "1"
os.environ["CUFA_FAKE_SLACK"] = "1"
# setdefault, not assignment: the console test modules set these at import time
# too, and whichever one pytest imports last must not narrow the allowlist the
# earlier ones rely on.
os.environ.setdefault("CUFA_CONSOLE_ALLOWLIST", "staff@example.invalid,second@example.invalid")
os.environ.setdefault("CUFA_CONSOLE_SECRET", "test-secret-not-used-anywhere-real")

import logging  # noqa: E402

import psycopg  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from cufa.access_log import (  # noqa: E402
    Actor,
    actor_from_console_user,
    cli_actor,
    fellow_facing_label,
    reads_for_fellow,
    recent_reads,
    record_read,
    render_text as render_access_log,
    unattributable,
)
from cufa.config import get_settings, reset_settings_cache  # noqa: E402
from cufa.console.app import app  # noqa: E402
from cufa.console.auth import PASSWORD_IDENTITY, ConsoleUser  # noqa: E402
from cufa.data_rights import (  # noqa: E402
    ERASED_NAME,
    erase_fellow,
    erasure_email,
    export_fellow,
    render_plan,
    render_text,
)
from cufa.db import connection, execute, fetch_all, fetch_one  # noqa: E402
from cufa.decisions import record_decision  # noqa: E402
from cufa.errors import CufaError  # noqa: E402
from cufa.slack.dashboard_links import issue_token  # noqa: E402

reset_settings_cache()

STAFF = "staff@example.invalid"
SITE_PASSWORD = "correct-horse-battery-staple"


# ---------------------------------------------------------------------------
# a cohort with a row in every table the export reads
# ---------------------------------------------------------------------------


class Seeded:
    """Ids for the fixture below, so a test can name what it is asserting on."""

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


@pytest.fixture
def seeded() -> Iterator[Seeded]:
    """Two fellows who appear in each other's records, plus one of everything.

    Ada is the subject of every export test. Bo exists to be the person whose
    data must never appear in it — and, because that is the harder case, Bo
    writes a shoutout naming Ada and Ada writes one naming Bo.
    """
    tag = uuid.uuid4().hex[:8]
    cohort_id = f"rights-{tag}"
    ada, bo = f"CU-A{tag}", f"CU-B{tag}"
    ada_email, bo_email = f"ada-{tag}@example.invalid", f"bo-{tag}@example.invalid"
    ada_alias = f"ada-school-{tag}@example.invalid"
    team_id = f"T{tag.upper()}"
    ada_slack, bo_slack = f"UADA{tag.upper()}", f"UBO{tag.upper()}"
    channel = f"C{tag.upper()}"
    now = datetime(2026, 9, 15, 19, 0, tzinfo=timezone.utc)

    with connection() as conn:
        execute(conn, "insert into cohort (cohort_id, label) values (%s, %s)", (cohort_id, "rights"))
        execute(
            conn,
            """
            insert into fellow (fellow_id, cohort_id, full_name, primary_email, timezone, accepted_on)
            values (%s, %s, %s, %s, 'America/New_York', date '2026-08-01'),
                   (%s, %s, %s, %s, 'America/New_York', date '2026-08-01')
            """,
            (ada, cohort_id, "Ada Rights", ada_email, bo, cohort_id, "Bo Rights", bo_email),
        )
        execute(
            conn,
            "insert into fellow_alias (fellow_id, email, kind, added_by) values (%s, %s, 'school', 'staff@example.invalid')",
            (ada, ada_alias),
        )
        session_id = str(
            fetch_one(
                conn,
                """
                insert into "session" (cohort_id, title, scheduled_at_local, timezone,
                                       scheduled_at_utc, duration_minutes, passphrase,
                                       announced_at_utc, week_index)
                values (%s, 'Week 3 — Deliberation', timestamp '2026-09-15 15:00',
                        'America/New_York', %s, 90, 'justice', %s, 3)
                returning session_id
                """,
                (cohort_id, now, now - timedelta(minutes=30)),
            )["session_id"]
        )

        # Part A, one each, with a decision on Ada's.
        checkins = {}
        for fellow_id, email, raw in ((ada, ada_email, "justice"), (bo, bo_email, "justise")):
            checkins[fellow_id] = str(
                fetch_one(
                    conn,
                    """
                    insert into checkin (source_event_id, source, submitted_email,
                                         submitted_at_utc, submitted_at_raw, session_id,
                                         session_match, passphrase_raw, passphrase_match,
                                         edit_distance, extra_fields)
                    values (%s, 'csv', %s, %s, %s, %s, 'matched', %s, 'exact', 0,
                            %s::jsonb)
                    returning checkin_id
                    """,
                    (
                        f"evt-a-{fellow_id}",
                        email,
                        now,
                        now.isoformat(),
                        session_id,
                        raw,
                        json.dumps({"Anything else?": f"a note from {fellow_id}"}),
                    ),
                )["checkin_id"]
            )
        record_decision(
            conn,
            checkins[ada],
            status="attended",
            decided_by="human",
            confidence=1.0,
            human_email=STAFF,
            note="looked right to me",
        )

        # Part B, one each, each giving the other a shoutout.
        tickets = {}
        for fellow_id, email, other_name in (
            (ada, ada_email, "Bo Rights"),
            (bo, bo_email, "Ada Rights"),
        ):
            tickets[fellow_id] = str(
                fetch_one(
                    conn,
                    """
                    insert into checkin_b (source_event_id, source, submitted_email,
                                           submitted_at_utc, session_id, session_match,
                                           confidence_raw, takeaway_text, rotating_kind,
                                           rotating_text, shoutout_text)
                    values (%s, 'csv', %s, %s, %s, 'matched', 6, %s, 'muddiest_point', %s, %s)
                    returning checkin_b_id
                    """,
                    (
                        f"evt-b-{fellow_id}",
                        email,
                        now,
                        session_id,
                        f"takeaway written by {fellow_id}",
                        f"muddiest point written by {fellow_id}",
                        other_name,
                    ),
                )["checkin_b_id"]
            )
        execute(
            conn,
            """
            insert into peer_shoutout (checkin_b_id, raw_text, named_fellow_id, match_method, confidence)
            values (%s, 'Bo Rights', %s, 'exact_name', 1.0),
                   (%s, 'Ada Rights', %s, 'exact_name', 1.0)
            """,
            (tickets[ada], bo, tickets[bo], ada),
        )
        theme_id = str(
            fetch_one(
                conn,
                """
                insert into muddiest_theme (session_id, label, summary, model, prompt_version)
                values (%s, 'Standing', 'Who has standing to sue', 'stub', 'test')
                returning theme_id
                """,
                (session_id,),
            )["theme_id"]
        )
        execute(
            conn,
            "insert into muddiest_theme_member (theme_id, checkin_b_id) values (%s, %s), (%s, %s)",
            (theme_id, tickets[ada], theme_id, tickets[bo]),
        )

        # Staff records about Ada.
        execute(
            conn,
            """
            insert into intervention (fellow_id, kind, by_email, note, source)
            values (%s, 'outreach', %s, 'Ada seemed flat; called her mother.', 'console')
            """,
            (ada, STAFF),
        )
        execute(
            conn,
            """
            insert into help_request (fellow_id, submitted_email, session_id,
                                      submitted_at_utc, source_event_id, status,
                                      acknowledged_by, acknowledged_at, note)
            values (%s, %s, %s, %s, %s, 'acknowledged', %s, now(), 'rang on Tuesday')
            """,
            (ada, ada_email, session_id, now, f"evt-help-{tag}", STAFF),
        )
        execute(
            conn,
            "insert into identity_unresolved (cohort_id, email, best_guess_fellow_id, best_guess_score) values (%s, %s, %s, 0.9)",
            (cohort_id, f"ada.rights-{tag}@typo.invalid", ada),
        )

        # Slack.
        execute(
            conn,
            "insert into slack_workspace (team_id, team_name, cohort_id) values (%s, 'Rights WS', %s)",
            (team_id, cohort_id),
        )
        execute(
            conn,
            "insert into slack_channel (team_id, channel_id, name) values (%s, %s, 'general')",
            (team_id, channel),
        )
        execute(
            conn,
            """
            insert into slack_user (team_id, slack_user_id, email, display_name, real_name, tz)
            values (%s, %s, %s, 'ada', 'Ada Rights', 'America/New_York'),
                   (%s, %s, %s, 'bo', 'Bo Rights', 'America/New_York')
            """,
            (team_id, ada_slack, ada_email, team_id, bo_slack, bo_email),
        )
        execute(
            conn,
            """
            insert into slack_event (source_event_id, team_id, event_type, channel_id,
                                     slack_user_id, user_email, message_ts, text_length,
                                     word_count, event_time_utc, mentions)
            values (%s, %s, 'message', %s, %s, %s, '1.0', 20, 4, %s, %s::text[]),
                   (%s, %s, 'message', %s, %s, %s, '2.0', 12, 3, %s, %s::text[])
            """,
            (
                f"evt-msg-ada-{tag}", team_id, channel, ada_slack, ada_email, now, [bo_slack],
                f"evt-msg-bo-{tag}", team_id, channel, bo_slack, bo_email, now, [ada_slack],
            ),
        )
        execute(
            conn,
            """
            insert into slack_event (source_event_id, team_id, event_type, channel_id,
                                     slack_user_id, user_email, message_ts, reaction,
                                     item_user_id, event_time_utc)
            values (%s, %s, 'reaction_added', %s, %s, %s, '1.0', 'tada', %s, %s)
            """,
            (f"evt-react-{tag}", team_id, channel, bo_slack, bo_email, ada_slack, now),
        )
        question_id = str(
            fetch_one(
                conn,
                """
                insert into slack_qa_question (team_id, channel_id, message_ts, slack_user_id,
                                               text, normalized_text, asked_at_utc)
                values (%s, %s, 'q1', %s, 'What counts as standing?', 'what counts as standing', %s)
                returning question_id
                """,
                (team_id, channel, ada_slack, now),
            )["question_id"]
        )
        execute(
            conn,
            """
            insert into slack_qa_answer (question_id, team_id, channel_id, message_ts,
                                         slack_user_id, text, answered_at_utc)
            values (%s, %s, %s, 'a1', %s, 'Injury in fact, mostly.', %s)
            """,
            (question_id, team_id, channel, ada_slack, now),
        )
        execute(
            conn,
            "insert into slack_preference (slack_user_id) values (%s), (%s)",
            (ada_slack, bo_slack),
        )
        execute(
            conn,
            "insert into reminder_sent (target_kind, target_id, slack_user_id, offset_minutes) values ('session', %s, %s, 60)",
            (session_id, ada_slack),
        )
        execute(
            conn,
            "insert into roster_alert (slack_user_id, email) values (%s, %s)",
            (ada_slack, ada_email),
        )
        execute(conn, "insert into fellow_reminder_preference (fellow_id) values (%s)", (ada,))

        # Assignments, badges, deliveries, transcript.
        assignment_id = str(
            fetch_one(
                conn,
                """
                insert into assignment (cohort_id, title, kind, due_at_utc, max_score)
                values (%s, 'Case brief', 'case_brief', %s, 100)
                returning assignment_id
                """,
                (cohort_id, now + timedelta(days=5)),
            )["assignment_id"]
        )
        execute(
            conn,
            """
            insert into assignment_submission (assignment_id, fellow_id, submitted_at_utc,
                                               score, graded_by, graded_at)
            values (%s, %s, %s, 88.5, %s, now())
            """,
            (assignment_id, ada, now, STAFF),
        )
        execute(
            conn,
            "insert into badge_award (fellow_id, badge_key, level, evidence) values (%s, 'first_checkin', 1, '{\"checkins\": 1}'::jsonb)",
            (ada,),
        )
        execute(
            conn,
            """
            insert into bot_delivery (dedupe_key, kind, team_id, fellow_id, scheduled_for_utc, status)
            values (%s, 'weekly_digest', %s, %s, %s, 'sent')
            """,
            (f"del-{tag}", team_id, ada, now),
        )
        execute(
            conn,
            """
            insert into zoom_transcript_turn (session_id, speaker_name, started_at_s,
                                              ended_at_s, word_count, source_sha256)
            values (%s, 'Ada Rights', 10, 25.5, 40, %s),
                   (%s, 'Bo Rights', 30, 41, 22, %s)
            """,
            (session_id, f"sha-{tag}", session_id, f"sha-{tag}"),
        )

    yield Seeded(
        tag=tag,
        cohort_id=cohort_id,
        ada=ada,
        bo=bo,
        ada_email=ada_email,
        bo_email=bo_email,
        ada_alias=ada_alias,
        team_id=team_id,
        ada_slack=ada_slack,
        bo_slack=bo_slack,
        channel=channel,
        session_id=session_id,
        assignment_id=assignment_id,
        ada_checkin=checkins[ada],
        ada_ticket=tickets[ada],
        bo_ticket=tickets[bo],
        now=now,
    )

    # No hand-rolled teardown. Every table this fixture writes is in
    # `conftest._TABLES`, which the `db` fixture truncates before each test, so
    # nothing here can reach another module. The block that used to be here
    # deleted from all 27 of them by hand, and three — `slack_event`,
    # `checkin` and `checkin_b` — carry immutability triggers that refuse a
    # DELETE outright. TRUNCATE does not fire row triggers and DELETE does,
    # which is the whole difference: the teardown took the module down with
    # 32 errors before a single assertion ran.


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def signed_in(client: TestClient) -> TestClient:
    assert client.post("/signin/dev", data={"email": STAFF, "next": "/"}).status_code == 303
    return client


@pytest.fixture
def password_configured(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("CUFA_CONSOLE_PASSWORD", SITE_PASSWORD)
    reset_settings_cache()
    yield
    monkeypatch.undo()
    reset_settings_cache()


def boot_state(response) -> dict:
    match = re.search(
        r'<script type="application/json" id="__CUFA_STATE__">(.*?)</script>',
        response.text,
        re.DOTALL,
    )
    assert match, "no boot state in the response"
    return json.loads(match.group(1))


# ---------------------------------------------------------------------------
# export: scope
# ---------------------------------------------------------------------------


def test_an_export_holds_this_fellow_and_not_the_other_one(seeded: Seeded) -> None:
    """The scoping property, asserted on the rendered archive rather than on a
    query, because the rendering is what actually leaves the building."""
    with connection() as conn:
        text = render_text(export_fellow(conn, seeded.ada))

    assert seeded.ada in text
    assert "Ada Rights" in text
    assert seeded.ada_email in text
    assert seeded.ada_alias in text
    # Ada's own answers, in her own words.
    assert f"takeaway written by {seeded.ada}" in text
    assert f"muddiest point written by {seeded.ada}" in text

    # Bo is in the same cohort, wrote about Ada, reacted to Ada's message and
    # spoke on the same transcript. None of that is Ada's record.
    assert seeded.bo not in text
    assert seeded.bo_email not in text
    assert f"takeaway written by {seeded.bo}" not in text
    assert f"muddiest point written by {seeded.bo}" not in text


def test_an_export_gives_back_the_shoutout_this_fellow_wrote(seeded: Seeded) -> None:
    """Their own words, including the name they typed. They already know who
    they praised; withholding it would be pretending otherwise."""
    with connection() as conn:
        archive = export_fellow(conn, seeded.ada)
    given = archive.section("shoutouts_given")
    assert given is not None and len(given.rows) == 1
    assert given.rows[0]["raw_text"] == "Bo Rights"
    assert given.rows[0]["matched_to_someone_on_the_roster"] is True
    # The other fellow's id and address are not carried along with the name.
    assert seeded.bo not in json.dumps(given.to_dict(), default=str)


def test_an_export_withholds_a_shoutout_somebody_else_wrote_and_says_so(
    seeded: Seeded,
) -> None:
    """Bo wrote "Ada Rights" about Ada. Ada gets the count, not Bo's sentence,
    and the archive states that it is holding something back."""
    with connection() as conn:
        archive = export_fellow(conn, seeded.ada)
        text = render_text(archive)
    received = archive.section("shoutouts_received")
    assert received is not None
    assert sum(int(r["shoutouts"]) for r in received.rows) == 1
    assert received.note and "withheld" in received.note
    columns = {k for row in received.rows for k in row}
    assert "raw_text" not in columns and "checkin_b_id" not in columns
    # And the reader is told, in the archive itself, what is missing and why.
    assert "WHAT THIS ARCHIVE LEAVES OUT" in text


def test_an_export_withholds_the_staff_note_but_not_the_fact_of_the_record(
    seeded: Seeded,
) -> None:
    """An intervention note can carry a third party's account and can be
    safeguarding material. That a record exists is what lets a person ask."""
    with connection() as conn:
        archive = export_fellow(conn, seeded.ada)
        text = render_text(archive)

    interventions = archive.section("interventions")
    assert interventions is not None and len(interventions.rows) == 1
    assert interventions.rows[0]["kind"] == "outreach"
    assert interventions.rows[0]["a_note_exists"] is True
    assert "called her mother" not in text
    # The staff member who wrote it is not named either.
    assert STAFF not in text
    assert interventions.note and "withheld" in interventions.note

    # Same rule on the help-request row: the fact is Ada's, the note is not.
    help_requests = archive.section("help_requests")
    assert help_requests is not None and len(help_requests.rows) == 1
    assert "rang on Tuesday" not in text


def test_an_export_covers_every_table_that_holds_something(seeded: Seeded) -> None:
    """A section with nothing in it is fine; a section that is missing is not.

    The fixture seeds one row in each of these on purpose, so an empty section
    here means a query that stopped finding what it used to."""
    with connection() as conn:
        archive = export_fellow(conn, seeded.ada)
    filled = {s.key for s in archive.sections if s.rows}
    assert filled >= {
        "roster",
        "other_addresses",
        "unplaced_addresses",
        "attendance",
        "exit_tickets",
        "shoutouts_given",
        "shoutouts_received",
        "assignments",
        "badges",
        "slack_accounts",
        "slack_activity",
        "slack_qa",
        "reminders",
        "airtime",
        "interventions",
        "help_requests",
        "journey",
    }


def test_an_export_names_no_staff_address_on_a_decision(seeded: Seeded) -> None:
    """A person decided Ada's borderline check-in. Ada is entitled to know a
    person decided it; the person's address is theirs."""
    with connection() as conn:
        archive = export_fellow(conn, seeded.ada)
    attendance = archive.section("attendance")
    assert attendance is not None and len(attendance.rows) == 1
    assert attendance.rows[0]["decided_by"] == "a member of staff"
    assert "human_email" not in attendance.rows[0]
    assert STAFF not in json.dumps(attendance.to_dict(), default=str)


def test_an_export_of_a_fellow_who_does_not_exist_is_an_error(seeded: Seeded) -> None:
    with connection() as conn:
        with pytest.raises(CufaError):
            export_fellow(conn, "CU-nobody")


# ---------------------------------------------------------------------------
# erasure
# ---------------------------------------------------------------------------


def _fellow_row(conn: Any, fellow_id: str) -> dict[str, Any]:
    row = fetch_one(
        conn,
        "select full_name, primary_email, status, erased_at from fellow where fellow_id = %s",
        (fellow_id,),
    )
    assert row is not None
    return row


def test_erase_is_a_dry_run_by_default_and_changes_nothing(seeded: Seeded) -> None:
    """The default has to be the safe one, and the report has to be real: a dry
    run that under-counts is worse than no dry run at all."""
    with connection() as conn:
        plan = erase_fellow(conn, seeded.ada, requested_by=STAFF)
        assert plan.applied is False
        assert plan.rows_touched > 0
        assert _fellow_row(conn, seeded.ada)["full_name"] == "Ada Rights"
        assert fetch_one(conn, "select count(*) as n from badge_award where fellow_id = %s", (seeded.ada,))["n"] == 1
        assert fetch_one(conn, "select count(*) as n from data_erasure where fellow_id = %s", (seeded.ada,))["n"] == 0
    assert "DRY RUN" in render_plan(plan)


def test_erase_with_apply_takes_the_name_and_address_out(seeded: Seeded) -> None:
    with connection() as conn:
        plan = erase_fellow(conn, seeded.ada, requested_by=STAFF, apply=True)
        assert plan.applied is True

        row = _fellow_row(conn, seeded.ada)
        assert row["full_name"] == ERASED_NAME
        assert row["primary_email"] == erasure_email(seeded.ada)
        assert row["status"] == "withdrawn"
        assert row["erased_at"] is not None

        # Deleted outright.
        for table in (
            "badge_award",
            "assignment_submission",
            "fellow_alias",
            "fellow_reminder_preference",
            "bot_delivery",
        ):
            assert fetch_one(
                conn, f"select count(*) as n from {table} where fellow_id = %s", (seeded.ada,)
            )["n"] == 0, table
        assert fetch_one(
            conn, "select count(*) as n from slack_preference where slack_user_id = %s", (seeded.ada_slack,)
        )["n"] == 0
        assert fetch_one(
            conn, "select count(*) as n from slack_qa_question where slack_user_id = %s", (seeded.ada_slack,)
        )["n"] == 0

        # Anonymised in place.
        ticket = fetch_one(
            conn,
            "select submitted_email, takeaway_text, rotating_text, shoutout_text from checkin_b where checkin_b_id = %s",
            (seeded.ada_ticket,),
        )
        assert ticket["submitted_email"] == erasure_email(seeded.ada)
        assert ticket["takeaway_text"] is None
        assert ticket["rotating_text"] is None
        assert ticket["shoutout_text"] is None

        slack_user = fetch_one(
            conn,
            "select email, real_name, linked_fellow_id from slack_user where slack_user_id = %s",
            (seeded.ada_slack,),
        )
        assert slack_user["email"] is None and slack_user["real_name"] == ERASED_NAME

        # Bo is untouched throughout.
        assert _fellow_row(conn, seeded.bo)["full_name"] == "Bo Rights"
        assert fetch_one(
            conn, "select count(*) as n from slack_preference where slack_user_id = %s", (seeded.bo_slack,)
        )["n"] == 1

        # And the erasure is on the record.
        ledger = fetch_one(
            conn, "select requested_by, touched from data_erasure where fellow_id = %s", (seeded.ada,)
        )
        assert ledger is not None and ledger["requested_by"] == STAFF
        assert ledger["touched"]


def test_erase_is_idempotent(seeded: Seeded) -> None:
    """Every predicate is written so a second run matches nothing, which is what
    makes it safe to re-run after an interrupted first attempt."""
    with connection() as conn:
        erase_fellow(conn, seeded.ada, requested_by=STAFF, apply=True)
        before = fetch_all(
            conn, "select * from fellow where fellow_id = %s", (seeded.ada,)
        )

        second = erase_fellow(conn, seeded.ada, requested_by=STAFF, apply=True)
        assert second.rows_touched == 0
        assert second.already_erased_at is not None
        assert fetch_all(conn, "select * from fellow where fellow_id = %s", (seeded.ada,)) == before

        # A dry run after the fact also reports nothing left to do.
        assert erase_fellow(conn, seeded.ada, requested_by=STAFF).rows_touched == 0


def test_erase_cannot_delete_a_checkin_but_does_take_the_address_off_it(
    seeded: Seeded,
) -> None:
    """`checkin` is immutable and undeletable by trigger. The erasure exemption
    is exactly wide enough to remove the person and no wider."""
    with connection() as conn:
        erase_fellow(conn, seeded.ada, requested_by=STAFF, apply=True)
        row = fetch_one(
            conn,
            "select submitted_email, passphrase_raw, extra_fields, session_id from checkin where checkin_id = %s",
            (seeded.ada_checkin,),
        )
        assert row["submitted_email"] == erasure_email(seeded.ada)
        assert row["extra_fields"] == {}
        # The observation itself is intact.
        assert row["passphrase_raw"] == "justice"
        assert str(row["session_id"]) == seeded.session_id

    # And the trigger still refuses everything else.
    with connection() as conn:
        with pytest.raises(psycopg.errors.RestrictViolation):
            execute(conn, "delete from checkin where checkin_id = %s", (seeded.ada_checkin,))
    with connection() as conn:
        with pytest.raises(psycopg.errors.RestrictViolation):
            execute(
                conn,
                "update checkin set passphrase_match = 'mismatch' where checkin_id = %s",
                (seeded.ada_checkin,),
            )


def test_erase_leaves_the_decision_ledger_alone(seeded: Seeded) -> None:
    """Invariant 2 and 4: a decision is appended, never edited or dropped. What
    erasure does is empty the observation it points at."""
    with connection() as conn:
        before = fetch_one(
            conn,
            "select count(*) as n from attendance_decision where checkin_id = %s",
            (seeded.ada_checkin,),
        )["n"]
        erase_fellow(conn, seeded.ada, requested_by=STAFF, apply=True)
        after = fetch_one(
            conn,
            "select count(*) as n from attendance_decision where checkin_id = %s",
            (seeded.ada_checkin,),
        )["n"]
    assert before == after == 1


def test_erase_anonymises_a_shoutout_that_named_the_erased_fellow(seeded: Seeded) -> None:
    """Bo's act survives; the name Bo typed does not. The row stops pointing at
    Ada and stops claiming to have been matched to anybody."""
    with connection() as conn:
        erase_fellow(conn, seeded.ada, requested_by=STAFF, apply=True)
        row = fetch_one(
            conn,
            "select raw_text, named_fellow_id, match_method from peer_shoutout where checkin_b_id = %s",
            (seeded.bo_ticket,),
        )
    assert row is not None
    assert "Ada" not in row["raw_text"]
    assert row["named_fellow_id"] is None
    assert row["match_method"] == "unresolved"


def test_erase_removes_the_staff_note_but_keeps_the_intervention(seeded: Seeded) -> None:
    with connection() as conn:
        erase_fellow(conn, seeded.ada, requested_by=STAFF, apply=True)
        row = fetch_one(
            conn,
            "select kind, note, by_email from intervention where fellow_id = %s",
            (seeded.ada,),
        )
    assert row is not None and row["kind"] == "outreach" and row["note"] is None


def test_an_erasure_survives_a_roster_reload(seeded: Seeded) -> None:
    """`cufa load-roster` upserts the name and the address on conflict. Without
    the guard trigger, re-loading yesterday's CSV would undo the erasure."""
    with connection() as conn:
        erase_fellow(conn, seeded.ada, requested_by=STAFF, apply=True)

    from cufa.roster import load_roster  # imported here: only this test needs it

    import csv
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "roster.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["fellow_id", "full_name", "email"])
            writer.writeheader()
            writer.writerow(
                {"fellow_id": seeded.ada, "full_name": "Ada Rights", "email": seeded.ada_email}
            )
            writer.writerow(
                {"fellow_id": seeded.bo, "full_name": "Bo Rights", "email": seeded.bo_email}
            )
        with connection() as conn:
            load_roster(conn, path, seeded.cohort_id)

    with connection() as conn:
        ada = _fellow_row(conn, seeded.ada)
        bo = _fellow_row(conn, seeded.bo)
    # Ada stays erased…
    assert ada["full_name"] == ERASED_NAME
    assert ada["primary_email"] == erasure_email(seeded.ada)
    assert ada["erased_at"] is not None
    # …and the rest of the CSV still loaded, which is why the guard discards the
    # identity rather than raising.
    assert bo["full_name"] == "Bo Rights"


def test_an_erasure_survives_a_slack_member_refresh(seeded: Seeded) -> None:
    """`cufa slack users` upserts email and names straight from users.list. The
    suppression list is what stops that writing the person back."""
    with connection() as conn:
        erase_fellow(conn, seeded.ada, requested_by=STAFF, apply=True)
        # Exactly the upsert sync_users performs.
        execute(
            conn,
            """
            insert into slack_user (team_id, slack_user_id, email, display_name, real_name, tz)
            values (%s, %s, %s, 'ada', 'Ada Rights', 'America/New_York')
            on conflict (team_id, slack_user_id) do update
               set email = excluded.email,
                   display_name = excluded.display_name,
                   real_name = excluded.real_name,
                   tz = excluded.tz
            """,
            (seeded.team_id, seeded.ada_slack, seeded.ada_email),
        )
        row = fetch_one(
            conn,
            "select email, display_name, real_name, tz from slack_user where slack_user_id = %s",
            (seeded.ada_slack,),
        )
    assert row["email"] is None
    assert row["real_name"] == ERASED_NAME
    assert row["display_name"] == ERASED_NAME
    assert row["tz"] is None


def test_erase_names_what_it_cannot_reach(seeded: Seeded) -> None:
    """A report that silently omits the holes is the failure mode worth testing
    for: somebody finds one later and is right to call it a lie."""
    with connection() as conn:
        plan = erase_fellow(conn, seeded.ada, requested_by=STAFF)
    report = render_plan(plan)
    assert "What erasure cannot reach" in report
    assert "pseudonymisation" in report
    assert "Slack still holds the messages" in report
    # Every kept table says why it is kept.
    kept = {s.table for s in plan.steps if s.action == "keep"}
    assert kept == {"fellow_access_log", "attendance_decision"}
    assert all(s.why for s in plan.steps)


def test_erase_needs_to_know_who_asked(seeded: Seeded) -> None:
    with connection() as conn:
        with pytest.raises(CufaError):
            erase_fellow(conn, seeded.ada, requested_by="  ")
        with pytest.raises(CufaError):
            erase_fellow(conn, "CU-nobody", requested_by=STAFF)


def test_an_export_after_an_erasure_is_empty_of_the_person(seeded: Seeded) -> None:
    """The end-to-end check that the two halves agree: ask the export what is
    held, after erasure, and the answer must not be the person."""
    with connection() as conn:
        erase_fellow(conn, seeded.ada, requested_by=STAFF, apply=True)
        text = render_text(export_fellow(conn, seeded.ada))
    assert "Ada Rights" not in text
    assert seeded.ada_email not in text
    assert seeded.ada_alias not in text
    assert f"takeaway written by {seeded.ada}" not in text


# ---------------------------------------------------------------------------
# the access audit log
# ---------------------------------------------------------------------------


def test_the_access_log_records_a_staff_read(signed_in: TestClient, seeded: Seeded) -> None:
    page = signed_in.get(f"/dashboard/fellow/{seeded.ada}")
    assert page.status_code == 200, page.text[:400]

    with connection() as conn:
        rows = reads_for_fellow(conn, seeded.ada)
    assert len(rows) == 1
    assert rows[0]["fellow_id"] == seeded.ada
    assert rows[0]["route"] == "GET /dashboard/fellow/{id}"
    assert rows[0]["actor_kind"] == "dev_bypass"
    assert rows[0]["actor_email"] == STAFF
    assert rows[0]["at"] is not None

    # Nothing was recorded against the fellow who was not opened.
    with connection() as conn:
        assert reads_for_fellow(conn, seeded.bo) == []


def test_a_second_read_appends_rather_than_replacing(
    signed_in: TestClient, seeded: Seeded
) -> None:
    signed_in.get(f"/dashboard/fellow/{seeded.ada}")
    signed_in.get(f"/dashboard/fellow/{seeded.ada}")
    with connection() as conn:
        assert len(reads_for_fellow(conn, seeded.ada)) == 2


def test_the_staff_archive_route_is_logged_and_scoped(
    signed_in: TestClient, seeded: Seeded
) -> None:
    response = signed_in.get(f"/dashboard/fellow/{seeded.ada}/export.txt")
    assert response.status_code == 200
    assert "Everything held about Ada Rights" in response.text
    assert seeded.bo_email not in response.text

    with connection() as conn:
        routes = [r["route"] for r in reads_for_fellow(conn, seeded.ada)]
    assert routes == ["GET /dashboard/fellow/{id}/export.txt"]


def test_the_access_log_is_shown_on_the_record_it_is_about(
    signed_in: TestClient, seeded: Seeded
) -> None:
    signed_in.get(f"/dashboard/fellow/{seeded.ada}")
    state = boot_state(signed_in.get(f"/dashboard/fellow/{seeded.ada}"))
    assert state["staff_view"] is True
    assert len(state["access_log"]) >= 1
    assert state["access_log"][0]["by"] == f"{STAFF} (developer bypass)"
    assert state["unattributable_reads"] == 0


def test_a_fellow_reading_their_own_page_is_not_in_the_access_log(seeded: Seeded) -> None:
    """The log answers "who ELSE looked". Filling it with the subject's own
    visits would bury that, and the signed link is already scoped to them."""
    token = issue_token(get_settings(), seeded.ada)
    client = TestClient(app, follow_redirects=False)
    assert client.get(f"/me/{token}").status_code == 200
    assert client.get(f"/me/{token}/export.txt").status_code == 200
    with connection() as conn:
        assert reads_for_fellow(conn, seeded.ada) == []


def test_a_shared_password_session_is_recorded_as_shared_not_as_a_person(
    client: TestClient, seeded: Seeded, password_configured
) -> None:
    """RUNBOOK §8 door 2. Every session it issues carries
    ``shared-password@console.local``, which is not a mailbox and not a person.
    The log must not launder it into one."""
    assert client.post(
        "/signin/password", data={"password": SITE_PASSWORD, "next": "/"}
    ).status_code == 303
    assert client.get(f"/dashboard/fellow/{seeded.ada}").status_code == 200

    with connection() as conn:
        rows = reads_for_fellow(conn, seeded.ada)
        rendered = render_access_log(recent_reads(conn, fellow_id=seeded.ada))
    assert len(rows) == 1
    assert rows[0]["actor_kind"] == "shared_password"
    assert rows[0]["actor_email"] is None
    # The placeholder identity the cookie carries is nowhere in the row.
    assert PASSWORD_IDENTITY not in json.dumps(rows[0], default=str)

    assert unattributable(rows) == 1
    assert "no named person" in rendered
    # And the cost of the door is stated rather than left to be inferred.
    assert "1 name nobody" in rendered
    assert "CUFA_CONSOLE_PASSWORD" in rendered


def test_the_shared_password_combination_is_the_only_one_the_table_accepts(
    seeded: Seeded,
) -> None:
    """The honesty is a CHECK constraint, not a convention: a future caller
    cannot write a named shared-password read even by mistake."""
    with connection() as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            execute(
                conn,
                "insert into fellow_access_log (fellow_id, actor_kind, actor_email, route) "
                "values (%s, 'shared_password', %s, 'GET /x')",
                (seeded.ada, STAFF),
            )
    with connection() as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            execute(
                conn,
                "insert into fellow_access_log (fellow_id, actor_kind, actor_email, route) "
                "values (%s, 'person', null, 'GET /x')",
                (seeded.ada,),
            )


def test_the_access_log_contains_no_email_address(seeded: Seeded) -> None:
    """The fellow's address is not in the table, and nobody's address is in what
    the fellow is shown of it.

    This is the rule the rest of the codebase already follows for `cufa slack
    report`, every DM and every digest. An audit trail is the one thing kept
    forever, so it is the last place a roster dump belongs.
    """
    with connection() as conn:
        record_read(
            conn,
            seeded.ada,
            actor=Actor(kind="person", email=STAFF),
            route="GET /dashboard/fellow/{id}",
        )
        record_read(
            conn,
            seeded.ada,
            actor=Actor(kind="shared_password", email=None),
            route="GET /dashboard/fellow/{id}",
        )
        rows = reads_for_fellow(conn, seeded.ada)
        archive = export_fellow(conn, seeded.ada)

    # Nothing in the table carries the FELLOW's address, in any column.
    dumped = json.dumps(rows, default=str)
    assert seeded.ada_email not in dumped
    assert seeded.ada_alias not in dumped

    # And the log as the fellow reads it back carries no address at all.
    section = archive.section("access_log")
    assert section is not None and len(section.rows) == 2
    rendered = json.dumps(section.to_dict(), default=str)
    assert "@" not in rendered, rendered
    assert {r["opened_by"] for r in section.rows} == {
        "a member of staff, signed in with their CU Google account",
        "the shared site password — no named person",
    }
    assert section.note and "not shown" in section.note


def test_a_log_line_about_a_read_names_nobody_in_full(
    seeded: Seeded, caplog: pytest.LogCaptureFixture
) -> None:
    """Same rule one level down: the line the handler emits at INFO carries the
    fellow id and a masked staff address, never a whole one."""
    with caplog.at_level(logging.INFO, logger="cufa.access_log"):
        with connection() as conn:
            record_read(
                conn,
                seeded.ada,
                actor=Actor(kind="person", email=STAFF),
                route="GET /dashboard/fellow/{id}",
            )
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert seeded.ada in text
    assert seeded.ada_email not in text
    assert STAFF not in text
    assert "s***@example.invalid" in text


def test_the_command_line_export_is_logged_as_a_command_line_read(
    seeded: Seeded,
) -> None:
    """The largest single read of one person's record there is."""
    with connection() as conn:
        record_read(
            conn, seeded.ada, actor=cli_actor(STAFF), route="cufa fellow export"
        )
        rows = reads_for_fellow(conn, seeded.ada)
    assert rows[0]["actor_kind"] == "cli"
    assert rows[0]["route"] == "cufa fellow export"
    assert fellow_facing_label(rows[0]) == "a member of staff, from the command line"


def test_recording_a_read_of_a_fellow_who_does_not_exist_is_a_no_op() -> None:
    """A mistyped URL must not become a 500 on the way to a 404."""
    with connection() as conn:
        assert record_read(
            conn, "CU-nobody", actor=Actor(kind="cli", email=STAFF), route="GET /x"
        ) is None


def test_a_console_user_maps_to_the_right_door() -> None:
    assert actor_from_console_user(ConsoleUser(email=STAFF, via="google")) == Actor(
        kind="person", email=STAFF
    )
    assert actor_from_console_user(ConsoleUser(email=STAFF, via="dev")) == Actor(
        kind="dev_bypass", email=STAFF
    )
    # The address on a shared-password cookie is dropped rather than recorded.
    shared = actor_from_console_user(ConsoleUser(email=PASSWORD_IDENTITY, via="password"))
    assert shared == Actor(kind="shared_password", email=None)
    assert shared.is_named is False


# ---------------------------------------------------------------------------
# the privacy notice on the fellow's own page
# ---------------------------------------------------------------------------


def test_the_fellow_page_carries_the_privacy_notice_and_the_export_link(
    seeded: Seeded,
) -> None:
    """§12's "privacy notice inside the app, not only on a form", and §6's
    "export my data" — both as data the server ships, since the screen renders
    client-side."""
    token = issue_token(get_settings(), seeded.ada)
    client = TestClient(app, follow_redirects=False)
    state = boot_state(client.get(f"/me/{token}"))

    assert state["frame"] == "fellow" and state["staff_view"] is False
    labels = [item["label"] for item in state["collected"]]
    assert "Who you are" in labels
    assert "Who opened your record" in labels
    assert all(item["detail"] for item in state["collected"])
    assert any("not collected" in line or "not stored" in line or "not read" in line.lower()
               for line in state["not_collected"])
    assert "deleted" in state["how_to_ask"]

    # The staff apparatus is still nowhere near this page.
    assert "access_log" not in state
    assert "allowlist" not in state and state.get("user") is None


def test_the_full_archive_needs_a_valid_token(seeded: Seeded) -> None:
    client = TestClient(app, follow_redirects=False)
    assert client.get("/me/not-a-token/export.txt").status_code == 403

    token = issue_token(get_settings(), seeded.ada)
    response = client.get(f"/me/{token}/export.txt")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "Everything held about Ada Rights" in response.text
    # One fellow's link shows one fellow. Not by scanning for the other's name,
    # though: "Bo Rights" is both Bo's name and the text Ada typed into her own
    # shoutout, and `test_an_export_gives_back_the_shoutout_this_fellow_wrote`
    # settles that she gets her own words back — she already knows who she
    # praised. What must not be here is anything Ada did not write and could not
    # otherwise see: Bo's address, Bo's fellowship id, and Bo's own sentences.
    assert seeded.bo_email not in response.text
    assert seeded.bo not in response.text
    assert f"takeaway written by {seeded.bo}" not in response.text
    assert f"muddiest point written by {seeded.bo}" not in response.text


def test_the_fellow_archive_route_is_not_a_staff_door(seeded: Seeded) -> None:
    """The staff archive is behind the console gate; the token route is not a
    way around it."""
    client = TestClient(app, follow_redirects=False)
    assert client.get(f"/dashboard/fellow/{seeded.ada}/export.txt").status_code in (302, 303, 401)
