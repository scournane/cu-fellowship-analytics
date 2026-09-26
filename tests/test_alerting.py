"""The bot noticing its own failures.

Three behaviours, and the whole value of each is in the negative assertion:
the dead-man switch fires ONCE and not every minute, a repeating error is
reported as a count and not repeated, and nothing any of it posts names a
fellow's address. A watchdog that is merely loud is a watchdog that gets muted,
which leaves the install exactly where it started.

No test here touches Slack. ``FakeSlackClient`` records the outbox;
``FakeSlackWebClient`` stands in for the wider Web API the backfill needs.
"""

from __future__ import annotations

import itertools
import os
from datetime import datetime, timedelta, timezone

import pytest
from conftest import TEST_COHORT, make_fellow

from cufa.config import load_settings
from cufa.db import execute, fetch_all, fetch_one
from cufa.slack.alerting import (
    ALERT_LIVENESS,
    BEAT_BACKFILL,
    BEAT_TICK,
    ERROR_PREFIX,
    REDACTED,
    check_liveness,
    check_scheduler_gap,
    fingerprint,
    last_beat,
    last_live_signal,
    liveness,
    maybe_backfill,
    report_errors,
    run_alerting,
    scrub,
)
from cufa.slack.client import FakeSlackClient, WebClientAdapter
from cufa.slack.backfill import sync_channels
from cufa.slack.fake import FakeSlackWebClient, FakeWorkspace
from cufa.slack.store import ensure_workspace
from cufa.slack.sync import record_message, sync_all

STAFF = "CSTAFF"
FELLOW_EMAIL = "ada@example.invalid"
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def _settings(**overrides: str):
    """Settings built from explicit values, never from the developer's shell.

    F-13: the suite is not hermetic where fixtures inherit the process
    environment. Nothing here does.
    """
    env = {
        "CUFA_DATABASE_URL": os.environ["CUFA_DATABASE_URL"],
        "CUFA_FAKE_SLACK": "1",
        "CUFA_SLACK_COHORT": TEST_COHORT,
        "CUFA_SLACK_STAFF_CHANNEL": STAFF,
        "CUFA_CONSOLE_SECRET": "test-secret",
        "CUFA_ALERT_SILENCE_HOURS": "6",
        "CUFA_ALERT_TICK_GAP_MINUTES": "30",
        "CUFA_ALERT_ERROR_COOLDOWN_MINUTES": "60",
        "CUFA_AUTO_BACKFILL_MINUTES": "60",
        "CUFA_SLACK_QA_CHANNELS": "",
        **overrides,
    }
    return load_settings(env)


@pytest.fixture
def settings():
    return _settings()


@pytest.fixture
def client(db):
    """A workspace with a staff channel, and a `slack_workspace` row for it."""
    make_fellow(db, "CU-1", FELLOW_EMAIL, "Ada Testcase")
    fake = FakeSlackClient()
    fake.add_user("U1", email=FELLOW_EMAIL, name="Ada Testcase")
    fake.add_channel("C1", "general")
    fake.add_channel(STAFF, "staff-only", private=True)
    sync_all(db, fake, staff_channel=STAFF, cohort_id=TEST_COHORT)
    return fake


_events = itertools.count(1)


def _received(db, client, *, at: datetime, load_id: str | None = None) -> None:
    """One ingested event, with a ``received_at`` the test chooses.

    Written with an INSERT rather than through ``record_message`` because
    ``received_at`` defaults to ``now()`` and ``slack_event`` is immutable —
    invariant 2's trigger refuses an UPDATE, so the only moment the value can be
    chosen is when the row is written. Every column here is what the live path
    writes; only the clock is the test's.
    """
    n = next(_events)
    execute(
        db,
        """
        insert into slack_event (source_event_id, team_id, event_type, channel_id,
                                 slack_user_id, user_email, message_ts,
                                 event_time_utc, load_id, received_at)
        values (%s, %s, 'message', 'C1', 'U1', %s, %s, %s, %s, %s)
        """,
        (f"test-event-{n}", client.team_id, FELLOW_EMAIL, f"{at.timestamp():.6f}", at, load_id, at),
    )


def _posts(client) -> list[str]:
    return [item.text for item in client.sent_to(STAFF)]


def _connected_at(db, at: datetime) -> None:
    execute(db, "update slack_workspace set connected_at = %s", (at,))


# ---------------------------------------------------------------------------
# the signal
# ---------------------------------------------------------------------------


def test_liveness_reads_ingested_data_and_not_load_run(db, client, settings):
    """The signal is real data, not a `running` load_run row.

    F-14: on serverless every cold start opens a `load_run` row and a reclaimed
    instance leaves it open, so a stale `running` row is the ordinary case.
    Three of them here, and the switch is unmoved by all of them.
    """
    _connected_at(db, NOW - timedelta(days=30))
    _received(db, client, at=NOW - timedelta(hours=1))
    for _ in range(3):
        execute(
            db,
            "insert into load_run (source, origin, cohort_id, status) "
            "values ('slack_bot', 'T', %s, 'running')",
            (TEST_COHORT,),
        )
    assert liveness(db, settings=settings, now=NOW).stale is False

    assert liveness(db, settings=settings, now=NOW + timedelta(hours=8)).stale is True, (
        "nine hours of silence is stale at a six-hour threshold, whatever load_run says"
    )


def test_a_backfilled_row_is_not_evidence_that_the_bot_is_receiving(db, client, settings):
    """Otherwise the automatic recovery walk would hold the switch shut."""
    _connected_at(db, NOW - timedelta(days=30))
    _received(db, client, at=NOW - timedelta(hours=9))
    load_id = fetch_one(
        db,
        "insert into load_run (source, origin, cohort_id, status) "
        "values ('slack_backfill', 'T', %s, 'succeeded') returning load_id",
        (TEST_COHORT,),
    )["load_id"]
    # A recovery walk has just written a fresh row for an old message.
    _received(db, client, at=NOW, load_id=str(load_id))

    at, evidence = last_live_signal(db)
    assert at is not None and at < NOW - timedelta(hours=8), evidence
    assert liveness(db, settings=settings, now=NOW).stale is True


def test_a_fresh_install_is_anchored_on_connection_not_on_nothing(db, client, settings):
    """No event has ever arrived, so the honest statement is "nothing since install"."""
    _connected_at(db, NOW - timedelta(minutes=5))
    assert liveness(db, settings=settings, now=NOW).stale is False, "a five-minute-old install is not an outage"

    _connected_at(db, NOW - timedelta(days=3))
    state = liveness(db, settings=settings, now=NOW)
    assert state.stale is True and "connected" in state.evidence


# ---------------------------------------------------------------------------
# the dead-man switch
# ---------------------------------------------------------------------------


def test_the_dead_man_switch_fires_once_and_not_every_minute(db, client, settings):
    """The whole requirement: one alert per outage, not one per tick.

    A minute-by-minute tick over two hours of a continuing outage. Without
    durable suppression this is 120 messages, and on serverless in-memory
    suppression is no suppression at all — the next tick is very likely a
    different instance.
    """
    _connected_at(db, NOW - timedelta(days=30))
    _received(db, client, at=NOW - timedelta(hours=9))

    assert check_liveness(db, client, channel_id=STAFF, settings=settings, now=NOW) == "fired"
    for minute in range(1, 121):
        assert check_liveness(
            db, client, channel_id=STAFF, settings=settings, now=NOW + timedelta(minutes=minute)
        ) == ""

    posts = _posts(client)
    assert len(posts) == 1, f"one alert per outage, got {len(posts)}"
    assert "Nothing has reached the bot" in posts[0]
    assert "9.0h" in posts[0] and "6h" in posts[0], "it says how long, and against what threshold"

    row = fetch_one(db, "select state, occurrences, posted_count from ops_alert where alert_key = %s", (ALERT_LIVENESS,))
    assert row["state"] == "firing"
    assert row["posted_count"] == 1
    assert row["occurrences"] == 121, "every tick is counted; only the first is spoken"


def test_the_switch_posts_a_recovery_notice_when_data_resumes(db, client, settings):
    _connected_at(db, NOW - timedelta(days=30))
    _received(db, client, at=NOW - timedelta(hours=9))
    assert check_liveness(db, client, channel_id=STAFF, settings=settings, now=NOW) == "fired"

    later = NOW + timedelta(hours=2)
    _received(db, client, at=later)  # a fellow posts; data is flowing again
    assert check_liveness(db, client, channel_id=STAFF, settings=settings, now=later) == "recovered"
    # And the recovery is said once, too.
    assert check_liveness(db, client, channel_id=STAFF, settings=settings, now=later + timedelta(minutes=1)) == ""

    posts = _posts(client)
    assert len(posts) == 2
    assert "Receiving again" in posts[1]
    assert fetch_one(db, "select state from ops_alert where alert_key = %s", (ALERT_LIVENESS,))["state"] == "ok"


def test_a_second_outage_can_speak_again(db, client, settings):
    """`digest_log`'s unique (kind, target_key) would have wedged this shut."""
    _connected_at(db, NOW - timedelta(days=30))
    _received(db, client, at=NOW - timedelta(hours=9))
    check_liveness(db, client, channel_id=STAFF, settings=settings, now=NOW)

    recovered = NOW + timedelta(hours=1)
    _received(db, client, at=recovered)
    check_liveness(db, client, channel_id=STAFF, settings=settings, now=recovered)

    again = recovered + timedelta(hours=7)
    assert check_liveness(db, client, channel_id=STAFF, settings=settings, now=again) == "fired"
    assert len(_posts(client)) == 3, "fire, recover, fire again"


def test_the_switch_says_nothing_when_there_is_no_workspace(db, settings):
    """Nothing to be alive about, so nothing to say."""
    fake = FakeSlackClient()
    fake.add_channel(STAFF, "staff-only", private=True)
    assert check_liveness(db, fake, channel_id=STAFF, settings=settings, now=NOW) == ""
    assert _posts(fake) == []


# ---------------------------------------------------------------------------
# the scheduler gap
# ---------------------------------------------------------------------------


def test_a_scheduler_gap_is_reported_by_the_tick_that_comes_back(db, client, settings):
    """The laptop-sleep failure, said out loud instead of never."""
    assert check_scheduler_gap(db, client, channel_id=STAFF, settings=settings, now=NOW) is None, (
        "the first tick ever has no previous beat, so no gap"
    )
    assert last_beat(db, BEAT_TICK) == NOW

    ordinary = NOW + timedelta(minutes=1)
    assert check_scheduler_gap(db, client, channel_id=STAFF, settings=settings, now=ordinary) is None
    assert _posts(client) == []

    after_sleep = ordinary + timedelta(hours=9)
    key = check_scheduler_gap(db, client, channel_id=STAFF, settings=settings, now=after_sleep)
    assert key is not None
    posts = _posts(client)
    assert len(posts) == 1 and "scheduler stopped calling" in posts[0] and "9.0h" in posts[0]

    # The same gap cannot be reported twice, whichever instance notices it.
    assert check_scheduler_gap(db, client, channel_id=STAFF, settings=settings, now=after_sleep) is None
    assert len(_posts(client)) == 1


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


def test_errors_are_deduplicated_rather_than_repeated(db, client, settings):
    """A persistent error must not become a per-minute firehose.

    `tick` runs every minute and the cron route answers 200 with its errors so
    the scheduler keeps calling, which means one broken step is 1,440 identical
    errors a day. Two hours of them here.
    """
    error = "sync: Slack users.list failed: ratelimited"
    assert report_errors(db, client, channel_id=STAFF, errors=[error], settings=settings, now=NOW) != []
    for minute in range(1, 60):
        report_errors(
            db, client, channel_id=STAFF, errors=[error], settings=settings, now=NOW + timedelta(minutes=minute)
        )
    assert len(_posts(client)) == 1, "announced once, then held"

    # Past the cooldown it speaks again — as a count, not as another copy.
    report_errors(db, client, channel_id=STAFF, errors=[error], settings=settings, now=NOW + timedelta(minutes=61))
    posts = _posts(client)
    assert len(posts) == 2
    assert "still failing" in posts[1] and "61 times" in posts[1]
    assert posts[1].count(error) == 1, "the repeat notice states it once, as context for the count"


def test_an_error_whose_details_vary_is_still_one_error(db, client, settings):
    """Ids and counts are normalised away, or the rate limit never engages."""
    first = "session_summaries: No session with id 4f2c0b1e-1111-4222-8333-444455556666"
    second = "session_summaries: No session with id 91ab0b1e-9999-4222-8333-444455556666"
    assert fingerprint(first) == fingerprint(second)
    report_errors(db, client, channel_id=STAFF, errors=[first], settings=settings, now=NOW)
    report_errors(db, client, channel_id=STAFF, errors=[second], settings=settings, now=NOW + timedelta(minutes=1))
    assert len(_posts(client)) == 1


def test_distinct_errors_are_each_reported_and_capped(db, client, settings):
    errors = [f"step{n}: distinct failure number {n} of a different kind" for n in range(8)]
    # Each one is a different WORD, not a different number, or they fingerprint alike.
    errors = [
        "sync: users.list failed",
        "welcome: DM refused",
        "reminders: quiet hours unparseable",
        "badges: award table locked",
        "weekly: digest text failed",
        "roster_alerts: post refused",
    ]
    capped = _settings(CUFA_ALERT_MAX_ERRORS_PER_POST="3")
    report_errors(db, client, channel_id=STAFF, errors=errors, settings=capped, now=NOW)
    posts = _posts(client)
    assert len(posts) == 1
    assert posts[0].count("•") == 3, "three lines, the rest counted"
    assert "suppressed" in posts[0]
    assert len(fetch_all(db, "select 1 from ops_alert where alert_key like %s", (f"{ERROR_PREFIX}%",))) == 6


def test_an_error_that_stops_happening_is_reported_as_cleared(db, client, settings):
    """So staff can tell "fixed" from "still broken but quiet"."""
    error = "badges: award table locked"
    report_errors(db, client, channel_id=STAFF, errors=[error], settings=settings, now=NOW)
    # Absent for less than a cooldown: no flapping.
    report_errors(db, client, channel_id=STAFF, errors=[], settings=settings, now=NOW + timedelta(minutes=30))
    assert len(_posts(client)) == 1

    report_errors(db, client, channel_id=STAFF, errors=[], settings=settings, now=NOW + timedelta(minutes=90))
    posts = _posts(client)
    assert len(posts) == 2 and "cleared" in posts[1]
    assert fetch_one(
        db, "select state from ops_alert where alert_key = %s", (f"{ERROR_PREFIX}{fingerprint(error)}",)
    )["state"] == "ok"


def test_nothing_is_posted_when_there_is_nothing_to_say(db, client, settings):
    assert report_errors(db, client, channel_id=STAFF, errors=[], settings=settings, now=NOW) == []
    assert _posts(client) == []


# ---------------------------------------------------------------------------
# addresses
# ---------------------------------------------------------------------------


def test_no_alert_names_a_fellows_email_address(db, client, settings):
    """The rule the whole codebase keeps: names in staff posts, never addresses.

    An error string is the one place an address could arrive by accident — it is
    built from an exception message, not from a template somebody reviewed — so
    the scrub is applied to every message this module sends, not to the ones
    that looked risky.
    """
    _connected_at(db, NOW - timedelta(days=30))
    _received(db, client, at=NOW - timedelta(hours=9))
    errors = [
        f"welcome: could not DM {FELLOW_EMAIL}: users_not_found",
        "sync: unknown address bo@example.invalid on the roster",
    ]
    result = run_alerting(
        db, client, settings=settings, staff_channel=STAFF, errors=errors, now=NOW
    )
    assert result.liveness == "fired" and result.errors_reported

    posts = _posts(client)
    assert posts, "something was posted, or this test proves nothing"
    for text in posts:
        assert "@example.invalid" not in text, text
        assert FELLOW_EMAIL not in text
    assert REDACTED in "\n".join(posts), "and the reader is told something was withheld"

    # Not in the state table either: it is readable in Studio and exportable.
    for row in fetch_all(db, "select detail from ops_alert where detail is not null"):
        assert "@example.invalid" not in (row["detail"] or "")


def test_scrub_leaves_ordinary_text_alone():
    assert scrub("sync: users.list failed: ratelimited") == "sync: users.list failed: ratelimited"
    assert scrub("Ada Testcase has not checked in") == "Ada Testcase has not checked in"
    assert scrub("mail to ada@example.invalid failed") == f"mail to {REDACTED} failed"


# ---------------------------------------------------------------------------
# automatic backfill
# ---------------------------------------------------------------------------


@pytest.fixture
def web(db):
    """A workspace behind the wider Web API, which the backfill needs.

    ``FakeWorkspace`` stamps history with the real clock, so these tests work in
    real time rather than from ``NOW``: the lookback window has to contain the
    messages being recovered, and inventing a ts would only test the invention.
    """
    workspace = FakeWorkspace()
    workspace.add_user(email=FELLOW_EMAIL, real_name="Ada Testcase", user_id="U0ADA")
    make_fellow(db, "CU-1", FELLOW_EMAIL, "Ada Testcase")
    fake = FakeSlackWebClient(workspace)
    ensure_workspace(db, fake, TEST_COHORT)
    return workspace, WebClientAdapter(fake)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_backfill_runs_on_the_tick_and_then_holds_its_cadence(db, web, settings):
    """There is no "restart" here, so the cadence lives in the database.

    Held in process memory it would mean every cold start backfills again, and
    an idle weekend — when an unnoticed gap is most likely — backfills never.
    """
    workspace, client = web
    channel = workspace.channel_id("general")
    workspace.message_event("U0ADA", channel, "said something the live bot missed")

    now = _now()
    assert maybe_backfill(db, client, settings=settings, now=now) is not None
    assert fetch_one(db, "select count(*) as n from slack_event")["n"] == 1
    assert last_beat(db, BEAT_BACKFILL) == now

    workspace.message_event("U0ADA", channel, "and another")
    assert maybe_backfill(db, client, settings=settings, now=now + timedelta(minutes=30)) is None, (
        "not due: the cadence is 60 minutes and it survives the instance that set it"
    )
    assert maybe_backfill(db, client, settings=settings, now=now + timedelta(minutes=61)) is not None
    assert fetch_one(db, "select count(*) as n from slack_event")["n"] == 2


def test_backfill_heals_a_gap_that_the_minute_sync_walked_past(db, web, settings):
    """The watermark is a high-water mark, not a record of what was read.

    ``sync_all`` advances ``backfilled_through_ts`` every minute, so a walk that
    respected it would be a no-op and would never recover what only history
    carries. This is the 2026-09-14 socket drop, healing itself.
    """
    workspace, client = web
    channel = workspace.channel_id("general")
    sync_channels(db, client.web, workspace.team_id)
    dropped = workspace.message_event("U0ADA", channel, "the message the socket dropped")
    # The minute sync ran, missed that message (the drop), and moved the mark
    # past it anyway. Nothing that respects the mark can ever see it again.
    execute(
        db,
        "update slack_channel set backfilled_through_ts = %s, tracked = true where channel_id = %s",
        (f"{float(dropped['ts']) + 60:.6f}", channel),
    )
    assert fetch_one(db, "select count(*) as n from slack_event")["n"] == 0

    now = _now()
    assert maybe_backfill(db, client, settings=settings, now=now) is not None
    assert fetch_one(db, "select count(*) as n from slack_event")["n"] == 1, "recovered anyway"

    # And it is idempotent, which is the property that makes re-reading free.
    maybe_backfill(db, client, settings=settings, now=now + timedelta(hours=2), force=True)
    assert fetch_one(db, "select count(*) as n from slack_event")["n"] == 1

    # The mark still only ever moves forward, whatever the walk read.
    mark = fetch_one(
        db, "select backfilled_through_ts as t from slack_channel where channel_id = %s", (channel,)
    )["t"]
    assert float(mark) >= float(dropped["ts"]) + 60


def test_backfill_is_skipped_when_turned_off_or_unavailable(db, web, client, settings):
    _workspace, adapter = web
    off = _settings(CUFA_AUTO_BACKFILL_MINUTES="0")
    assert maybe_backfill(db, adapter, settings=off, now=NOW) is None
    assert last_beat(db, BEAT_BACKFILL) is None
    # The in-memory FakeSlackClient has no Web API underneath it, so there is
    # nothing to walk — and that is a skip, not a failure.
    assert maybe_backfill(db, client, settings=settings, now=NOW) is None


# ---------------------------------------------------------------------------
# the whole watchdog, from the tick
# ---------------------------------------------------------------------------


def test_run_alerting_never_raises_and_reports_its_own_failures(db, client, settings):
    """A reminder must not go unsent because the report about reminders broke."""
    _connected_at(db, NOW - timedelta(days=30))
    _received(db, client, at=NOW - timedelta(hours=9))
    client.fail_post_for.add(STAFF)

    result = run_alerting(db, client, settings=settings, staff_channel=STAFF, errors=["sync: broke"], now=NOW)
    assert _posts(client) == [], "nothing was delivered"
    assert result.failures == [], "a refused post is logged, not raised, and not a watchdog failure"


def test_the_master_switch_silences_everything(db, client, settings):
    _connected_at(db, NOW - timedelta(days=30))
    _received(db, client, at=NOW - timedelta(hours=9))
    off = _settings(CUFA_ALERTS_ENABLED="0")
    result = run_alerting(db, client, settings=off, staff_channel=STAFF, errors=["sync: broke"], now=NOW)
    assert result.posted == 0 and _posts(client) == []
    assert fetch_all(db, "select 1 from ops_alert") == []


def test_the_tick_runs_the_watchdog(db, client, settings):
    """Every transport gets it: the cron route, the CLI, the scheduler thread."""
    from cufa.slack.digest import tick

    _connected_at(db, NOW - timedelta(days=30))
    _received(db, client, at=NOW - timedelta(hours=9))
    result = tick(db, client, settings=settings, cohort_id=TEST_COHORT, now=NOW, sync=False, reminders=False)
    assert result.ops_liveness == "fired"
    assert result.ops_alerts_posted >= 1
    assert last_beat(db, BEAT_TICK) == NOW
    assert any("Nothing has reached the bot" in text for text in _posts(client))


def test_a_tick_with_no_staff_channel_still_keeps_its_heartbeat(db, client):
    """The gap notice needs the beat recorded even when there is nowhere to post."""
    nowhere = _settings(CUFA_SLACK_STAFF_CHANNEL="")
    run_alerting(db, client, settings=nowhere, staff_channel=None, errors=["sync: broke"], now=NOW)
    assert last_beat(db, BEAT_TICK) == NOW
    assert _posts(client) == []
