"""Outbound Slack automations, including their anti-pressure boundaries."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg
import pytest

from conftest import TEST_COHORT, TEST_TZ, count, make_fellow

from cufa.assignments import AssignmentInput, create_assignment
from cufa.db import execute, fetch_all, fetch_one
from cufa.sessions import SessionInput, create_session
from cufa.slack.fake import TEAM_ID, FakeSlackWebClient, FakeWorkspace
from cufa.slack.reminders import (
    ASSIGNMENT_OFFSETS,
    NUDGE_OFFSETS,
    SESSION_OFFSETS,
    ReminderEngine,
    is_quiet_time,
    preference_command,
)
from cufa.slack.store import ensure_workspace
from cufa.slack.users import sync_users

UTC = timezone.utc
NY = ZoneInfo(TEST_TZ)


@pytest.fixture
def reminder_stack(db, settings):
    workspace = FakeWorkspace()
    workspace.add_user(
        email="ada@example.invalid", real_name="Ada Testcase", user_id="U0ADA"
    )
    client = FakeSlackWebClient(workspace)
    ensure_workspace(db, client, TEST_COHORT)
    sync_users(db, client, TEAM_ID)
    make_fellow(
        db,
        "CU-0001",
        "ada@example.invalid",
        "Ada Testcase",
        timezone=TEST_TZ,
    )
    configured = replace(
        settings,
        slack_default_fellow_timezone=TEST_TZ,
        slack_announcement_channel="announcements",
        slack_digest_weekday=6,
        slack_sync_part_b=False,
    )
    engine = ReminderEngine(
        configured, client, team_id=TEAM_ID, cohort_id=TEST_COHORT
    )
    return workspace, client, configured, engine


def _session_at(
    db,
    starts_at_utc: datetime,
    *,
    title: str = "Public Narrative",
    duration: int = 60,
    grace: int = 0,
    zoom_url: str | None = "https://zoom.example.invalid/j/123",
    agenda: str | None = None,
    slack_channel_id: str | None = None,
) -> str:
    local = starts_at_utc.astimezone(NY).replace(tzinfo=None)
    return create_session(
        db,
        SessionInput(
            cohort_id=TEST_COHORT,
            title=title,
            scheduled_at_local=local,
            timezone=TEST_TZ,
            duration_minutes=duration,
            grace_minutes=grace,
            zoom_url=zoom_url,
            agenda=agenda,
            slack_channel_id=slack_channel_id,
        ),
    )


def _part_b_form(db, session_id: str, *, polled_at: datetime) -> None:
    execute(
        db,
        """
        insert into session_form (
            session_id, form_id, form_url, part, published_at,
            publish_verified_at, last_polled_at, last_successful_poll_at
        )
        values (%s, %s, %s, 'b', %s, %s, %s, %s)
        """,
        (
            session_id,
            f"form-{session_id}",
            f"https://forms.example.invalid/{session_id}",
            polled_at,
            polled_at,
            polled_at,
            polled_at,
        ),
    )


def test_preference_modes_have_deliberately_distinct_cadence():
    assert SESSION_OFFSETS == {
        "all": (1440, 60, 10),
        "fewer": (60,),
        "later": (10,),
        "none": (),
    }
    assert ASSIGNMENT_OFFSETS["all"] == (1440, 60)
    assert ASSIGNMENT_OFFSETS["fewer"] == (1440,)
    assert ASSIGNMENT_OFFSETS["later"] == (60,)
    assert len(NUDGE_OFFSETS["all"]) == 2
    assert NUDGE_OFFSETS["fewer"] == ((1, 1440),)
    assert not NUDGE_OFFSETS["none"]


def test_observed_rhythm_replaces_default_quiet_hours_but_never_a_preference(db, reminder_stack):
    """A night owl with no stated preference gets the 22:00 message and is
    spared the 09:00 one. State a preference and the inference steps aside."""
    from dataclasses import replace as dc_replace

    from cufa.slack.events import parse_event
    from cufa.slack.store import record

    workspace, client, configured, engine = reminder_stack
    general = workspace.channel_id("general")
    # Ada is on Slack 21:00–01:00 New York, every night for a fortnight.
    base = datetime(2026, 8, 20, 1, 0, tzinfo=UTC)  # 21:00 EDT the evening before
    for day in range(14):
        for hour in range(5):
            obs = parse_event(workspace.message_event("U0ADA", general, "night"), TEAM_ID)
            record(db, dc_replace(obs, event_time_utc=base + timedelta(days=day, hours=hour)), email="ada@example.invalid", load_id=None)

    engine = ReminderEngine(dc_replace(configured, slack_rhythm_days=3650), client, team_id=TEAM_ID, cohort_id=TEST_COHORT)
    fellows = engine._fellows(db)
    engine._learn_quiet_hours(db, fellows, __import__("collections").Counter())
    ada = next(f for f in fellows if f["fellow_id"] == "CU-0001")
    quiet_start, quiet_end = engine._quiet_window(ada)
    assert (quiet_start, quiet_end) == (time(2), time(21)), "quiet is 02:00–21:00 local, not the 21:00–08:00 default"

    ten_pm = datetime(2026, 9, 15, 2, 0, tzinfo=UTC)   # 22:00 EDT — default would suppress
    nine_am = datetime(2026, 9, 15, 13, 0, tzinfo=UTC)  # 09:00 EDT — default would allow
    from collections import Counter

    assert engine._dm_allowed(ada, ten_pm, Counter()) is True
    assert engine._dm_allowed(ada, nine_am, Counter()) is False

    # A stated preference wins over any inference.
    execute(
        db,
        "insert into fellow_reminder_preference (fellow_id, quiet_start_local, quiet_end_local) values (%s, %s, %s)",
        ("CU-0001", time(23), time(7)),
    )
    fellows = engine._fellows(db)
    engine._learn_quiet_hours(db, fellows, Counter())
    ada = next(f for f in fellows if f["fellow_id"] == "CU-0001")
    assert engine._quiet_window(ada) == (time(23), time(7))
    assert engine._dm_allowed(ada, nine_am, Counter()) is True


def test_quiet_hours_are_evaluated_in_the_fellows_timezone():
    assert is_quiet_time(
        datetime(2026, 9, 3, 2, 0, tzinfo=UTC),
        TEST_TZ,
        time(21),
        time(8),
    )
    assert not is_quiet_time(
        datetime(2026, 9, 3, 13, 0, tzinfo=UTC),
        TEST_TZ,
        time(21),
        time(8),
    )


def test_session_reminders_send_all_three_zoom_links_once(db, reminder_stack):
    workspace, _, _, engine = reminder_stack
    starts = datetime(2026, 9, 17, 23, 0, tzinfo=UTC)
    _session_at(db, starts)

    for offset in (timedelta(hours=24), timedelta(hours=1), timedelta(minutes=10)):
        engine.run_once(db, now=starts - offset)
    engine.run_once(db, now=starts - timedelta(minutes=10))

    dms = [row for row in workspace.posted if row["to_user"] == "U0ADA"]
    assert len(dms) == 3
    assert all("https://zoom.example.invalid/j/123" in row["text"] for row in dms)
    assert {row["text"].split("starts in ", 1)[1].split(" ", 2)[0] for row in dms} == {
        "24",
        "1",
        "10",
    }
    assert count(db, "bot_delivery", "kind = 'session_reminder'") == 3


def test_quiet_session_reminder_waits_until_morning(db, reminder_stack):
    workspace, _, _, engine = reminder_stack
    starts = datetime(2026, 9, 18, 3, 0, tzinfo=UTC)  # 11pm in New York
    _session_at(db, starts)

    engine.run_once(db, now=starts - timedelta(hours=24))
    assert workspace.posted == []
    assert count(db, "bot_delivery") == 0

    engine.run_once(db, now=datetime(2026, 9, 17, 12, 0, tzinfo=UTC))  # 8am
    assert len(workspace.posted) == 1
    assert workspace.posted[0]["to_user"] == "U0ADA"


def test_assignment_due_reminder_is_personal_and_idempotent(db, reminder_stack):
    workspace, _, _, engine = reminder_stack
    due = datetime(2026, 9, 18, 21, 0, tzinfo=UTC)
    assignment_id = create_assignment(
        db,
        AssignmentInput(
            cohort_id=TEST_COHORT,
            title="Community interview notes",
            due_at_local=due.astimezone(NY).replace(tzinfo=None),
            timezone=TEST_TZ,
            url="https://classroom.example.invalid/interview",
        ),
    )

    engine.run_once(db, now=due - timedelta(hours=24))
    engine.run_once(db, now=due - timedelta(hours=24))

    assert len(workspace.posted) == 1
    assert workspace.posted[0]["to_user"] == "U0ADA"
    assert "Hi Ada" in workspace.posted[0]["text"]
    assert "Community interview notes" in workspace.posted[0]["text"]
    delivery = fetch_one(db, "select * from bot_delivery")
    assert str(delivery["assignment_id"]) == assignment_id


def test_none_preference_suppresses_every_personal_message(db, reminder_stack):
    workspace, _, _, engine = reminder_stack
    starts = datetime(2026, 9, 17, 23, 0, tzinfo=UTC)
    _session_at(db, starts)
    execute(
        db,
        "insert into fellow_reminder_preference (fellow_id, mode) values ('CU-0001', 'none')",
    )

    result = engine.run_once(db, now=starts - timedelta(hours=1))
    assert workspace.posted == []
    assert result.get("sent", 0) == 0
    assert count(db, "bot_delivery") == 0


def test_agenda_posts_to_channel_at_start_not_to_a_dm(db, reminder_stack):
    workspace, _, _, engine = reminder_stack
    starts = datetime(2026, 9, 15, 16, 0, tzinfo=UTC)
    _session_at(db, starts, agenda="1. Welcome\n2. Small groups")

    engine.run_once(db, now=starts + timedelta(minutes=1))
    engine.run_once(db, now=starts + timedelta(minutes=2))

    assert len(workspace.posted) == 1
    assert workspace.posted[0]["to_user"] is None
    assert workspace.posted[0]["channel"] == workspace.channel_id("announcements")
    assert "Small groups" in workspace.posted[0]["text"]


def test_nudges_require_fresh_part_b_data_and_never_send_a_third(
    db, reminder_stack
):
    workspace, _, _, engine = reminder_stack
    starts = datetime(2026, 9, 15, 16, 0, tzinfo=UTC)
    closes = starts + timedelta(hours=1)
    session_id = _session_at(db, starts)
    _part_b_form(db, session_id, polled_at=closes)

    first = closes + timedelta(minutes=30)
    # An attempted pull is not enough: failed pulls advance last_polled_at.
    execute(
        db,
        "update session_form set last_polled_at = %s "
        "where session_id = %s and part = 'b'",
        (first, session_id),
    )
    stale = engine.run_once(db, now=first)
    assert stale["skipped_stale_part_b"] == 1
    assert workspace.posted == []

    execute(
        db,
        "update session_form set last_polled_at = %s, last_successful_poll_at = %s "
        "where session_id = %s and part = 'b'",
        (first, first, session_id),
    )
    engine.run_once(db, now=first)

    second = closes + timedelta(hours=24)
    execute(
        db,
        "update session_form set last_polled_at = %s, last_successful_poll_at = %s "
        "where session_id = %s and part = 'b'",
        (second, second, session_id),
    )
    engine.run_once(db, now=second)
    engine.run_once(db, now=closes + timedelta(hours=47))

    nudges = fetch_all(
        db,
        "select nudge_number from bot_delivery where kind = 'part_b_nudge' order by nudge_number",
    )
    assert [row["nudge_number"] for row in nudges] == [1, 2]
    assert len([row for row in workspace.posted if row["to_user"] == "U0ADA"]) == 2
    assert "won't send another reminder" in workspace.posted[-1]["text"]

    with pytest.raises(psycopg.errors.CheckViolation):
        execute(
            db,
            """
            insert into bot_delivery (
                dedupe_key, kind, team_id, fellow_id, session_id, nudge_number,
                target_channel, scheduled_for_utc
            ) values ('attempted-third', 'part_b_nudge', %s, 'CU-0001', %s, 3,
                      'U0ADA', %s)
            """,
            (TEAM_ID, session_id, closes + timedelta(hours=48)),
        )


def test_part_b_submitter_is_never_nudged(db, reminder_stack):
    workspace, _, _, engine = reminder_stack
    starts = datetime(2026, 9, 15, 16, 0, tzinfo=UTC)
    closes = starts + timedelta(hours=1)
    first = closes + timedelta(minutes=30)
    session_id = _session_at(db, starts)
    _part_b_form(db, session_id, polled_at=first)
    execute(
        db,
        """
        insert into checkin_b (
            source_event_id, source, submitted_email, submitted_at_utc,
            session_id, session_match
        ) values ('ada-part-b', 'forms_api', 'ADA@example.invalid', %s, %s, 'matched')
        """,
        (closes, session_id),
    )

    engine.run_once(db, now=first)
    assert workspace.posted == []
    assert count(db, "bot_delivery", "kind = 'part_b_nudge'") == 0


def test_weekly_digest_has_session_due_work_and_changes(db, reminder_stack):
    workspace, client, configured, _ = reminder_stack
    now = datetime(2026, 9, 7, 14, 0, tzinfo=UTC)  # Monday, 10am New York
    session_id = _session_at(
        db,
        datetime(2026, 9, 9, 23, 0, tzinfo=UTC),
        title="Coalition Building",
    )
    create_assignment(
        db,
        AssignmentInput(
            cohort_id=TEST_COHORT,
            title="Stakeholder map",
            due_at_local=datetime(2026, 9, 11, 17, 0),
            timezone=TEST_TZ,
            url="https://classroom.example.invalid/map",
        ),
    )
    execute(
        db,
        'update "session" set created_at = %s, updated_at = %s where session_id = %s',
        (now - timedelta(days=2), now - timedelta(hours=1), session_id),
    )
    engine = ReminderEngine(
        replace(configured, slack_digest_weekday=0),
        client,
        team_id=TEAM_ID,
        cohort_id=TEST_COHORT,
    )

    engine.run_once(db, now=now)
    text = workspace.posted[0]["text"]
    assert "*Sessions*" in text and "Coalition Building" in text
    assert "*Due*" in text and "Stakeholder map" in text
    assert "*Changed*" in text and "Updated session" in text
    assert "https://zoom.example.invalid/j/123" in text


def test_fellow_can_control_preferences_from_slash_command(db, reminder_stack):
    _, client, configured, _ = reminder_stack
    response = preference_command(
        db,
        client,
        team_id=TEAM_ID,
        cohort_id=TEST_COHORT,
        user_id="U0ADA",
        text="fewer",
        default_timezone=configured.slack_default_fellow_timezone,
    )
    assert "set to *fewer*" in response
    assert fetch_one(
        db,
        "select mode from fellow_reminder_preference where fellow_id = 'CU-0001'",
    )["mode"] == "fewer"

    response = preference_command(
        db,
        client,
        team_id=TEAM_ID,
        cohort_id=TEST_COHORT,
        user_id="U0ADA",
        text="timezone America/Chicago",
        default_timezone=configured.slack_default_fellow_timezone,
    )
    assert "America/Chicago" in response

    response = preference_command(
        db,
        client,
        team_id=TEAM_ID,
        cohort_id=TEST_COHORT,
        user_id="U0ADA",
        text="none",
        default_timezone=configured.slack_default_fellow_timezone,
    )
    assert "no personal reminders" in response


def test_preference_status_uses_deployment_quiet_hour_defaults(db, reminder_stack):
    _, client, configured, _ = reminder_stack
    response = preference_command(
        db,
        client,
        team_id=TEAM_ID,
        cohort_id=TEST_COHORT,
        user_id="U0ADA",
        text="status",
        default_timezone=configured.slack_default_fellow_timezone,
        default_quiet_start="22:30",
        default_quiet_end="07:15",
    )

    assert "22:30-07:15" in response
