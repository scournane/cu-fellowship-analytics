"""The Slack bot: identity, reminders, badges, digests, commands.

No test here touches Slack. ``FakeSlackClient`` holds the workspace in memory
and records every message in an outbox, so what is asserted is what would
have been sent, to whom, and — just as often — what would *not*.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from conftest import TEST_COHORT, make_fellow, make_session

from cufa.assignments import AssignmentInput, create_assignment, record_score
from cufa.config import load_settings
from cufa.db import execute, fetch_all, fetch_one
from cufa.errors import CufaError
from cufa.interventions import reached_out, open_requests
from cufa.slack.badges import award_badges, badges_for, collect_evidence, leaderboard, notify_new_awards
from cufa.slack.client import FakeSlackClient, SlackApiError
from cufa.slack.commands import dispatch
from cufa.slack.digest import (
    post_roster_alerts,
    post_session_summaries,
    post_weekly_digest,
    session_summary_text,
    tick,
)
from cufa.slack.identity import add_alias, find_fellow, link_slack_user, merge, open_alerts, AmbiguousFellow, UnknownFellow
from cufa.slack.preferences import get_preferences, parse_offset, set_gamification, set_reminder
from cufa.slack.reminders import in_quiet_hours, run_reminders, set_zoom_link
from cufa.slack.sync import record_message, sync_all, word_count
from cufa.slack.client import SlackMessage

STAFF = "staff@example.invalid"
NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)  # a Monday, 08:00 New York


@pytest.fixture
def settings():
    return load_settings(
        {
            "CUFA_DATABASE_URL": os.environ["CUFA_DATABASE_URL"],
            "CUFA_FAKE_SLACK": "1",
            "CUFA_SLACK_COHORT": TEST_COHORT,
            "CUFA_SLACK_STAFF_CHANNEL": "CSTAFF",
            "CUFA_SLACK_ADMINS": STAFF,
            "CUFA_CONSOLE_SECRET": "test-secret",
        }
    )


@pytest.fixture
def workspace(db):
    """Two fellows, a staffer, a stranger, a public channel and a staff channel."""
    make_fellow(db, "CU-1", "ada@example.invalid", "Ada Testcase")
    make_fellow(db, "CU-2", "bo@example.invalid", "Bo Night")
    fake = FakeSlackClient()
    fake.add_user("U1", email="ada@example.invalid", name="Ada Testcase", tz="America/New_York")
    fake.add_user("U2", email="bo@example.invalid", name="Bo Night", tz="Asia/Tokyo")
    fake.add_user("US", email=STAFF, name="Staff Member", is_admin=True)
    fake.add_user("U9", email="stranger@example.invalid", name="Stranger")
    fake.add_user("UB", email=None, name="bot", is_bot=True)
    fake.add_channel("C1", "general")
    fake.add_channel("CSTAFF", "staff-only", private=True)
    return fake


def _attended(db, fellow_email: str, session_id: str, at: str) -> None:
    execute(
        db,
        """
        insert into checkin (source_event_id, source, submitted_email, submitted_at_utc, submitted_at_raw,
                             session_id, session_match, passphrase_match)
        values (%s, 'forms_api', %s, %s, %s, %s, 'matched', 'exact')
        """,
        (f"{fellow_email}:{session_id}:{at}", fellow_email, at, at, session_id),
    )
    checkin_id = fetch_one(db, "select checkin_id from checkin where source_event_id = %s", (f"{fellow_email}:{session_id}:{at}",))["checkin_id"]
    execute(
        db,
        "insert into attendance_decision (checkin_id, status, attended, confidence, decided_by, rule_name) values (%s, 'attended', true, 1.0, 'rule', 'exact_match')",
        (checkin_id,),
    )


# ---------------------------------------------------------------------------
# sync and identity
# ---------------------------------------------------------------------------


def test_sync_resolves_members_by_email_and_alerts_on_strangers(db, workspace, settings):
    summary = sync_all(db, workspace, staff_channel="CSTAFF", staff_emails=settings.slack_admins)
    assert summary.users_seen == 5
    resolved = {r["slack_user_id"]: r["fellow_id"] for r in fetch_all(db, "select slack_user_id, fellow_id from v_slack_user_resolved")}
    assert resolved["U1"] == "CU-1" and resolved["U2"] == "CU-2"
    assert resolved["U9"] is None
    alerts = open_alerts(db)
    assert [a["slack_user_id"] for a in alerts] == ["U9"], "the stranger alerts; staff, bots and fellows do not"


def test_sync_is_idempotent_and_incremental(db, workspace):
    workspace.add_message("C1", "U1", "hello")
    first = sync_all(db, workspace)
    assert first.messages_written == 1
    again = sync_all(db, workspace)
    assert again.messages_written == 0 and again.messages_read == 0, "nothing older than the watermark is re-read"
    workspace.add_message("C1", "U2", "hi back")
    third = sync_all(db, workspace)
    assert third.messages_read == 1 and third.messages_written == 1


def test_word_count_ignores_mentions_and_links():
    assert word_count("hey <@U1> look at <https://example.invalid|this> and https://x.y") == 4


def test_bot_and_system_messages_are_not_recorded(db, workspace):
    sync_all(db, workspace)
    assert record_message(db, SlackMessage(channel_id="C1", ts="1.0", user=None, text="x")) is False
    assert record_message(db, SlackMessage(channel_id="C1", ts="2.0", user="U1", text="joined", subtype="channel_join")) is False


def test_an_alias_reattributes_history_at_read_time(db, workspace):
    """The whole point of aliases: a message sent before the link counts after it."""
    workspace.add_user("U3", email="ada.school@example.invalid", name="Ada (school)")
    workspace.add_message("C1", "U3", "posted from my school account")
    sync_all(db, workspace)
    assert fetch_one(db, "select fellow_id from v_slack_message_resolved where slack_user_id = 'U3'")["fellow_id"] is None
    add_alias(db, "CU-1", "ada.school@example.invalid", by=STAFF, kind="school")
    assert fetch_one(db, "select fellow_id from v_slack_message_resolved where slack_user_id = 'U3'")["fellow_id"] == "CU-1"
    # And Part A check-ins from that address now resolve too.
    session_id = make_session(db)
    _attended(db, "ada.school@example.invalid", session_id, "2026-09-15T23:30:00Z")
    row = fetch_one(db, "select fellow_id from v_checkin_resolved where submitted_email = 'ada.school@example.invalid'")
    assert row["fellow_id"] == "CU-1"


def test_an_alias_cannot_belong_to_two_fellows(db, workspace):
    add_alias(db, "CU-1", "shared@example.invalid", by=STAFF)
    with pytest.raises(CufaError):
        add_alias(db, "CU-2", "shared@example.invalid", by=STAFF)
    with pytest.raises(CufaError):
        add_alias(db, "CU-2", "ada@example.invalid", by=STAFF)  # another fellow's primary


def test_manual_link_resolves_an_alert_and_records_the_address(db, workspace):
    sync_all(db, workspace)
    assert [a["slack_user_id"] for a in open_alerts(db)] == ["U9"]
    link_slack_user(db, "U9", "CU-2", by=STAFF)
    assert open_alerts(db) == []
    row = fetch_one(db, "select fellow_id, match_method from v_slack_user_resolved where slack_user_id = 'U9'")
    assert row["fellow_id"] == "CU-2" and row["match_method"] == "manual"
    assert fetch_one(db, "select 1 from fellow_alias where email = 'stranger@example.invalid' and fellow_id = 'CU-2'")


def test_merge_is_an_alias_not_a_roster_rewrite(db, workspace):
    merge(db, "CU-1", "ada.personal@example.invalid", by=STAFF, kind="personal")
    fellow = fetch_one(db, "select primary_email from fellow where fellow_id = 'CU-1'")
    assert fellow["primary_email"] == "ada@example.invalid"
    assert fetch_one(db, "select kind from fellow_alias where email = 'ada.personal@example.invalid'")["kind"] == "personal"


def test_find_fellow_by_id_email_name_and_refuses_ambiguity(db, workspace):
    assert find_fellow(db, "CU-1")["fellow_id"] == "CU-1"
    assert find_fellow(db, "bo@example.invalid")["fellow_id"] == "CU-2"
    assert find_fellow(db, "ada")["fellow_id"] == "CU-1"
    make_fellow(db, "CU-3", "ada2@example.invalid", "Ada Second")
    with pytest.raises(AmbiguousFellow):
        find_fellow(db, "ada")
    with pytest.raises(UnknownFellow):
        find_fellow(db, "nobody")


# ---------------------------------------------------------------------------
# reminders
# ---------------------------------------------------------------------------


def test_reminders_go_out_at_the_three_intervals_with_the_zoom_link(db, workspace):
    sync_all(db, workspace)
    session_id = make_session(db, local=datetime(2026, 9, 15, 19, 0))  # 23:00 UTC
    set_zoom_link(db, session_id, "https://zoom.us/j/1")
    start = datetime(2026, 9, 15, 23, 0, tzinfo=timezone.utc)
    sent = []
    for offset in (1440, 60, 10):
        run = run_reminders(db, workspace, cohort_id=TEST_COHORT, now=start - timedelta(minutes=offset))
        sent.append(run.sent)
    assert sent == [2, 2, 2], "Ada and Bo, each interval; staff and strangers are not on the roster"
    texts = [m.text for m in workspace.dms_to("U1")]
    assert any("24 hours" in t for t in texts) and any("1 hour" in t for t in texts) and any("10 minutes" in t for t in texts)
    assert all("https://zoom.us/j/1" in t for t in texts)
    assert "7:00 PM EDT" in texts[0]
    assert "8:00 AM JST" in workspace.dms_to("U2")[0].text, "rendered in the recipient's own zone"


def test_reminders_are_sent_once_even_if_the_tick_runs_twice(db, workspace):
    sync_all(db, workspace)
    make_session(db, local=datetime(2026, 9, 15, 19, 0))
    at = datetime(2026, 9, 15, 22, 0, tzinfo=timezone.utc)
    assert run_reminders(db, workspace, cohort_id=TEST_COHORT, now=at).sent == 2
    again = run_reminders(db, workspace, cohort_id=TEST_COHORT, now=at + timedelta(minutes=3))
    assert again.sent == 0 and again.skipped_dup == 2


def test_a_fellow_can_switch_one_interval_off(db, workspace):
    sync_all(db, workspace)
    set_reminder(db, "U1", kind="session", offset=10, enabled=False)
    make_session(db, local=datetime(2026, 9, 15, 19, 0))
    run = run_reminders(db, workspace, cohort_id=TEST_COHORT, now=datetime(2026, 9, 15, 22, 50, tzinfo=timezone.utc))
    assert run.sent == 1 and run.skipped_pref == 1
    assert workspace.dms_to("U1") == [] and len(workspace.dms_to("U2")) == 1
    prefs = get_preferences(db, "U1")
    assert prefs.session_reminders == (1440, 60) and prefs.assignment_reminders == (1440, 60, 10)


def test_no_reminder_lands_in_the_middle_of_the_night(db, workspace):
    sync_all(db, workspace)
    # Session at 03:00 New York; the 24h reminder would fire at 03:00 NY / 16:00 Tokyo.
    make_session(db, local=datetime(2026, 9, 16, 3, 0))
    run = run_reminders(db, workspace, cohort_id=TEST_COHORT, now=datetime(2026, 9, 15, 7, 0, tzinfo=timezone.utc))
    assert run.skipped_quiet == 1 and run.sent == 1
    assert workspace.dms_to("U1") == [] and len(workspace.dms_to("U2")) == 1
    assert in_quiet_hours(datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc), "America/New_York")
    assert not in_quiet_hours(datetime(2026, 9, 15, 13, 0, tzinfo=timezone.utc), "America/New_York")


def test_assignment_reminders_carry_the_link_and_a_failed_dm_is_retried(db, workspace):
    sync_all(db, workspace)
    create_assignment(
        db,
        AssignmentInput(cohort_id=TEST_COHORT, title="Solvathon deck", due_at_local=datetime(2026, 9, 20, 18, 0), timezone="America/New_York", kind="solvathon", link="https://forms.invalid/s"),
    )
    at = datetime(2026, 9, 19, 22, 0, tzinfo=timezone.utc)
    workspace.fail_post_for.add("D-U2")
    run = run_reminders(db, workspace, cohort_id=TEST_COHORT, now=at)
    assert run.sent == 1 and run.failed == 1
    assert "https://forms.invalid/s" in workspace.dms_to("U1")[0].text
    workspace.fail_post_for.clear()
    assert run_reminders(db, workspace, cohort_id=TEST_COHORT, now=at + timedelta(minutes=2)).sent == 1, "the failed one is not marked sent"


@pytest.mark.parametrize("raw,minutes", [("24h", 1440), ("1h", 60), ("10m", 10), ("2h", 120), ("45", 45)])
def test_parse_offset(raw, minutes):
    assert parse_offset(raw) == minutes


# ---------------------------------------------------------------------------
# badges and digests
# ---------------------------------------------------------------------------


def test_badges_are_awarded_once_and_dmd_unless_opted_out(db, workspace):
    sync_all(db, workspace)
    s1 = make_session(db, title="L1", local=datetime(2026, 9, 1, 19, 0))
    s2 = make_session(db, title="L2", local=datetime(2026, 9, 8, 19, 0))
    s3 = make_session(db, title="L3", local=datetime(2026, 9, 10, 19, 0))
    for sid, at in ((s1, "2026-09-01T23:30:00Z"), (s2, "2026-09-08T23:30:00Z"), (s3, "2026-09-10T23:30:00Z")):
        _attended(db, "ada@example.invalid", sid, at)
    _attended(db, "bo@example.invalid", s3, "2026-09-10T23:31:00Z")
    set_gamification(db, "U2", False)

    run = award_badges(db, TEST_COHORT, now=NOW)
    keys = {(f, k, l) for f, k, l in run.new_awards}
    assert ("CU-1", "first_checkin", 1) in keys and ("CU-1", "regular", 1) in keys and ("CU-1", "streak", 1) in keys
    assert ("CU-2", "first_checkin", 1) in keys
    notify_new_awards(db, workspace, run, cohort_id=TEST_COHORT)
    assert len(workspace.dms_to("U1")) == 1 and "First check-in" in workspace.dms_to("U1")[0].text
    assert workspace.dms_to("U2") == [], "Bo opted out and hears nothing"
    assert award_badges(db, TEST_COHORT, now=NOW).new_awards == [], "never awarded twice"
    assert [b["badge_key"] for b in badges_for(db, "CU-2")] == ["first_checkin"]
    evidence = {e.fellow_id: e for e in collect_evidence(db, TEST_COHORT, now=NOW)}
    assert evidence["CU-1"].streak == 3 and evidence["CU-2"].streak == 1


def test_leaderboard_is_staff_only_data_and_never_posted(db, workspace):
    sync_all(db, workspace)
    for i in range(3):
        workspace.add_message("C1", "U2", f"m{i}")
    sync_all(db, workspace)
    top = leaderboard(db, TEST_COHORT, by="messages", now=NOW)
    assert top[0]["fellow_id"] == "CU-2" and top[0]["value"] == 3
    assert workspace.sent_to("C1") == []


def test_session_summary_posts_once_after_the_session_ends(db, workspace):
    sync_all(db, workspace)
    sid = make_session(db, local=datetime(2026, 9, 8, 19, 0))
    _attended(db, "ada@example.invalid", sid, "2026-09-08T23:30:00Z")
    before_end = datetime(2026, 9, 9, 0, 30, tzinfo=timezone.utc)
    assert post_session_summaries(db, workspace, cohort_id=TEST_COHORT, channel_id="CSTAFF", now=before_end) == 0
    after_end = datetime(2026, 9, 9, 1, 0, tzinfo=timezone.utc)
    assert post_session_summaries(db, workspace, cohort_id=TEST_COHORT, channel_id="CSTAFF", now=after_end) == 1
    assert post_session_summaries(db, workspace, cohort_id=TEST_COHORT, channel_id="CSTAFF", now=after_end) == 0
    text = workspace.sent_to("CSTAFF")[0].text
    assert "Checked in: *1/2*" in text and "No check-in: Bo Night" in text
    assert text == session_summary_text(db, sid) or "Session summary" in text


def test_weekly_digest_posts_on_monday_once_and_names_the_quiet(db, workspace):
    sync_all(db, workspace)
    workspace.add_message("C1", "U1", "hi", at=NOW - timedelta(days=1))
    sync_all(db, workspace)
    sunday = NOW - timedelta(days=1)
    assert post_weekly_digest(db, workspace, cohort_id=TEST_COHORT, channel_id="CSTAFF", now=sunday) is False
    assert post_weekly_digest(db, workspace, cohort_id=TEST_COHORT, channel_id="CSTAFF", now=NOW) is True
    assert post_weekly_digest(db, workspace, cohort_id=TEST_COHORT, channel_id="CSTAFF", now=NOW + timedelta(hours=2)) is False
    text = workspace.sent_to("CSTAFF")[0].text
    assert "Bo Night" in text.split("Quiet for 7+ days")[1].splitlines()[0]
    assert "Ada Testcase" in text.split("Most active")[1].splitlines()[0]
    assert "Stranger" in text, "unrostered accounts are listed for a human"


def test_roster_alert_is_posted_once_and_a_failed_post_is_retried(db, workspace):
    sync_all(db, workspace)
    workspace.fail_post_for.add("CSTAFF")
    assert post_roster_alerts(db, workspace, channel_id="CSTAFF") == 0
    workspace.fail_post_for.clear()
    assert post_roster_alerts(db, workspace, channel_id="CSTAFF") == 1
    assert post_roster_alerts(db, workspace, channel_id="CSTAFF") == 0
    assert "<@U9>" in workspace.sent_to("CSTAFF")[0].text


def test_tick_does_everything_due_and_is_safe_to_repeat(db, workspace, settings):
    sid = make_session(db, local=datetime(2026, 9, 8, 19, 0))
    _attended(db, "ada@example.invalid", sid, "2026-09-08T23:30:00Z")
    make_session(db, title="Tonight", local=datetime(2026, 9, 14, 9, 0))  # 13:00 UTC, 1h after NOW
    first = tick(db, workspace, settings=settings, now=NOW)
    assert first.synced and first.reminders_sent == 2 and first.summaries_posted == 1 and first.weekly_posted
    assert first.alerts_posted == 1 and first.badges_awarded >= 1 and first.errors == []
    second = tick(db, workspace, settings=settings, now=NOW + timedelta(minutes=5))
    assert (second.reminders_sent, second.summaries_posted, second.weekly_posted, second.alerts_posted, second.badges_awarded) == (0, 0, False, 0, 0)


def test_tick_without_a_cohort_is_a_clear_error(db, workspace):
    bare = load_settings({"CUFA_DATABASE_URL": os.environ["CUFA_DATABASE_URL"], "CUFA_FAKE_SLACK": "1"})
    with pytest.raises(CufaError):
        tick(db, workspace, settings=bare, now=NOW)


# ---------------------------------------------------------------------------
# slash commands
# ---------------------------------------------------------------------------


def _run(db, fake, settings, command, text="", user="US"):
    return dispatch(db, fake, command=command, text=text, slack_user_id=user, settings=settings, now=NOW).text


def test_staff_commands_are_refused_to_fellows(db, workspace, settings):
    sync_all(db, workspace)
    for command in ("/report", "/fellow", "/attendance", "/leaderboard", "/assignment", "/zoom", "/alias", "/link", "/alerts", "/outreach", "/score", "/digest", "/sync"):
        assert "staff command" in _run(db, workspace, settings, command, "x", user="U1"), command
    assert "staff command" not in _run(db, workspace, settings, "/report")
    assert "Staff" in _run(db, workspace, settings, "/help") and "Staff" not in _run(db, workspace, settings, "/help", user="U1")


def test_a_fellow_never_sees_another_fellows_data(db, workspace, settings):
    sync_all(db, workspace)
    me = _run(db, workspace, settings, "/me", user="U1")
    assert "Ada Testcase" in me and "Bo" not in me
    assert "Bo" not in _run(db, workspace, settings, "/badges", user="U1")


def test_checkin_button_pings_staff_and_outreach_closes_it(db, workspace, settings):
    sync_all(db, workspace)
    reply = _run(db, workspace, settings, "/checkin", "not sure about the brief", user="U1")
    assert "pinged" in reply
    ping = workspace.sent_to("CSTAFF")[-1].text
    assert "Ada Testcase" in ping and "not sure about the brief" in ping
    assert len(open_requests(db)) == 1
    assert reached_out(db, "CU-1") is False
    _run(db, workspace, settings, "/outreach", "ada called her")
    assert reached_out(db, "CU-1") is True and open_requests(db) == []
    _run(db, workspace, settings, "/outreach", "clear ada")
    assert reached_out(db, "CU-1") is False


def test_zoom_link_and_assignment_flow_through_commands(db, workspace, settings):
    sync_all(db, workspace)
    make_session(db, title="Week 2", local=datetime(2026, 9, 15, 19, 0))
    assert "Zoom link set" in _run(db, workspace, settings, "/zoom", "week 2 <https://zoom.us/j/9>")
    assert fetch_one(db, 'select zoom_link from "session"')["zoom_link"] == "https://zoom.us/j/9"
    created = _run(db, workspace, settings, "/assignment", 'create "Case brief" 2026-09-25 18:00 case_brief <https://forms.invalid/cb>')
    assert "Created *Case brief*" in created
    assert "Case brief" in _run(db, workspace, settings, "/assignment", "list")
    assert "Recorded 91" in _run(db, workspace, settings, "/score", "case_brief bo 91 strong")
    card = _run(db, workspace, settings, "/fellow", "bo")
    assert "Case brief: 91" in card and "<@U2>" in card
    assert "not a number" in _run(db, workspace, settings, "/score", "case_brief bo lots")


def test_alias_link_and_alerts_commands(db, workspace, settings):
    sync_all(db, workspace)
    assert "<@U9>" in _run(db, workspace, settings, "/alerts")
    assert "now *Bo Night*" in _run(db, workspace, settings, "/link", "<@U9> CU-2")
    assert "No unrostered" in _run(db, workspace, settings, "/alerts")
    assert "also resolves" in _run(db, workspace, settings, "/alias", "ada <mailto:ada.school@example.invalid|ada.school@example.invalid> school")
    assert fetch_one(db, "select kind from fellow_alias where email = 'ada.school@example.invalid'")["kind"] == "school"
    assert "matches more than one" not in _run(db, workspace, settings, "/fellow", "ada")


def test_attendance_report_and_dashboard_commands(db, workspace, settings):
    sync_all(db, workspace)
    sid = make_session(db, title="Week 1", local=datetime(2026, 9, 8, 19, 0))
    _attended(db, "ada@example.invalid", sid, "2026-09-08T23:30:00Z")
    att = _run(db, workspace, settings, "/attendance", "last")
    assert "Checked in: *1/2*" in att
    report = _run(db, workspace, settings, "/report")
    assert "Sessions held: 1" in report and "overall attendance: 50%" in report
    link = _run(db, workspace, settings, "/dashboard", user="U1")
    assert "/me/" in link
    unknown = _run(db, workspace, settings, "/whatever")
    assert "don't know" in unknown


def test_unlinked_fellow_gets_a_plain_answer_not_a_traceback(db, workspace, settings):
    sync_all(db, workspace)
    assert "not linked" in _run(db, workspace, settings, "/me", user="U9")
    assert "not linked" in _run(db, workspace, settings, "/checkin", user="U9")
