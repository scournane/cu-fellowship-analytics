"""Slack participation capture.

Three layers, tested at three levels:

* ``events``  — pure parsing, no database. The idempotency key is the thing
                under test: live delivery and backfill must hash identically.
* ``store`` / ``users`` / ``backfill`` — against the real Postgres, with the
                in-memory fake client. Nothing is dropped; nothing duplicates.
* ``bot``     — once, over HTTP, with Bolt's real signature verifier and the
                real ``slack_sdk.WebClient`` pointed at the fake server.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone

import pytest
from conftest import TEST_COHORT, count, make_fellow

from cufa.config import load_settings
from cufa.db import execute, fetch_all, fetch_one
from cufa.slack.backfill import backfill_channel, backfill_workspace, sync_channels
from cufa.slack.events import (
    Skipped,
    SlackObservation,
    history_message_to_event,
    history_reactions_to_events,
    parse_event,
    source_event_id,
)
from cufa.slack.fake import FakeSlackWebClient, FakeWorkspace, demo_workspace
from cufa.slack.signing import sign
from cufa.slack.store import ensure_workspace, record, resolve_and_record, stats
from cufa.slack.users import resolve_user

TEAM = "T0DEMO000"


@pytest.fixture
def ws() -> FakeWorkspace:
    w = FakeWorkspace()
    w.add_user(email="ada@example.invalid", real_name="Ada Testcase", user_id="U0ADA")
    w.add_user(email="bob@example.invalid", real_name="Bob Fixture", user_id="U0BOB")
    w.add_user(email="stranger@example.invalid", real_name="Not On Roster", user_id="U0STRANGER")
    w.add_user(email=None, real_name="No Email", user_id="U0NOEMAIL")
    return w


@pytest.fixture
def client(ws) -> FakeSlackWebClient:
    return FakeSlackWebClient(ws)


@pytest.fixture
def workspace_row(db, client):
    """A slack_workspace row attached to the test cohort."""
    return ensure_workspace(db, client, TEST_COHORT)


def general(ws: FakeWorkspace) -> str:
    return ws.channel_id("general")


# ===========================================================================
# events — pure
# ===========================================================================

def test_message_event_parses_to_an_observation(ws):
    ev = ws.message_event("U0ADA", general(ws), "hello <https://example.invalid> world", thread_ts=None)
    obs = parse_event(ev, TEAM)
    assert isinstance(obs, SlackObservation)
    assert obs.event_type == "message"
    assert obs.slack_user_id == "U0ADA"
    assert obs.channel_id == general(ws)
    assert obs.word_count == 3 and obs.text_length == len(ev["text"])
    assert obs.has_link is True
    assert obs.is_thread_reply is False
    assert obs.text is None, "text is not stored unless asked for"
    assert obs.event_time_utc.tzinfo is timezone.utc


def test_text_is_stored_only_when_asked(ws):
    ev = ws.message_event("U0ADA", general(ws), "keep me")
    assert parse_event(ev, TEAM, store_text=False).text is None
    assert parse_event(ev, TEAM, store_text=True).text == "keep me"


def test_raw_never_contains_text(ws):
    ev = ws.message_event("U0ADA", general(ws), "secret words")
    obs = parse_event(ev, TEAM, store_text=True)
    assert "secret words" not in json.dumps(obs.raw)


def test_thread_parent_is_not_a_reply_but_a_reply_is(ws):
    parent = ws.message_event("U0ADA", general(ws), "parent")
    parent["thread_ts"] = parent["ts"]  # Slack marks a parent with thread_ts == ts
    reply = ws.message_event("U0BOB", general(ws), "reply", thread_ts=parent["ts"])
    assert parse_event(parent, TEAM).is_thread_reply is False
    assert parse_event(reply, TEAM).is_thread_reply is True
    assert parse_event(reply, TEAM).thread_ts == parent["ts"]


def test_bot_and_system_messages_are_skipped(ws):
    assert isinstance(parse_event(ws.bot_message_event(general(ws), "reminder"), TEAM), Skipped)
    join_line = ws.message_event("U0ADA", general(ws), "has joined the channel")
    join_line["subtype"] = "channel_join"
    assert isinstance(parse_event(join_line, TEAM), Skipped)
    assert isinstance(parse_event({"type": "app_mention", "user": "U0ADA"}, TEAM), Skipped)


def test_idempotency_key_is_the_same_for_live_and_backfill(ws):
    """The whole reason the key is built from the act and not from event_id."""
    live = ws.message_event("U0ADA", general(ws), "same message")
    history = ws.history[general(ws)][-1]
    assert "channel" not in history, "history messages omit the channel"
    replayed = history_message_to_event(history, general(ws))
    assert parse_event(live, TEAM).source_event_id == parse_event(replayed, TEAM).source_event_id


def test_reaction_key_distinguishes_user_and_reaction(ws):
    msg = ws.message_event("U0ADA", general(ws), "react to me")
    a = parse_event(ws.reaction_event("U0BOB", general(ws), msg["ts"], "thumbsup"), TEAM)
    b = parse_event(ws.reaction_event("U0BOB", general(ws), msg["ts"], "heart"), TEAM)
    c = parse_event(ws.reaction_event("U0ADA", general(ws), msg["ts"], "thumbsup"), TEAM)
    again = parse_event(ws.reaction_event("U0BOB", general(ws), msg["ts"], "thumbsup"), TEAM)
    assert len({a.source_event_id, b.source_event_id, c.source_event_id}) == 3
    assert a.source_event_id == again.source_event_id, "same user, same reaction, same message → same key"
    assert a.item_user_id == "U0ADA"


def test_edit_and_delete_are_new_observations_not_mutations(ws):
    original = ws.message_event("U0ADA", general(ws), "first draft")
    edit = parse_event(ws.edit_event(original, "second draft"), TEAM)
    delete = parse_event(ws.delete_event(original), TEAM)
    base = parse_event(original, TEAM)
    assert edit.event_type == "message_changed" and edit.message_ts == original["ts"]
    assert delete.event_type == "message_deleted" and delete.message_ts == original["ts"]
    assert len({base.source_event_id, edit.source_event_id, delete.source_event_id}) == 3


def test_history_reactions_expand_to_one_event_per_user(ws):
    msg = ws.message_event("U0ADA", general(ws), "popular")
    ws.reaction_event("U0BOB", general(ws), msg["ts"], "fire")
    ws.reaction_event("U0STRANGER", general(ws), msg["ts"], "fire")
    ws.reaction_event("U0BOB", general(ws), msg["ts"], "heart")
    history = ws.history[general(ws)][-1]
    expanded = history_reactions_to_events(history, general(ws))
    assert len(expanded) == 3
    keys = {parse_event(e, TEAM).source_event_id for e in expanded}
    live_key = parse_event(ws.reaction_event("U0BOB", general(ws), msg["ts"], "fire", record=False), TEAM).source_event_id
    assert live_key in keys, "a backfilled reaction collides with the live one"


def test_signing_matches_slacks_own_verifier():
    from slack_sdk.signature import SignatureVerifier

    body = '{"type":"event_callback"}'
    import time

    headers = sign("secret-1", body, timestamp=int(time.time()))
    verifier = SignatureVerifier("secret-1")
    assert verifier.is_valid(body, headers["X-Slack-Request-Timestamp"], headers["X-Slack-Signature"]) is True
    assert SignatureVerifier("secret-2").is_valid(body, headers["X-Slack-Request-Timestamp"], headers["X-Slack-Signature"]) is False


# ===========================================================================
# store / users / backfill — database
# ===========================================================================

def test_record_is_idempotent(db, ws, workspace_row):
    obs = parse_event(ws.message_event("U0ADA", general(ws), "once"), TEAM)
    assert record(db, obs, email="ada@example.invalid", load_id=None) is True
    assert record(db, obs, email="ada@example.invalid", load_id=None) is False
    assert count(db, "slack_event") == 1


def test_slack_event_rows_are_immutable(db, ws, workspace_row):
    import psycopg

    obs = parse_event(ws.message_event("U0ADA", general(ws), "fixed"), TEAM)
    record(db, obs, email="ada@example.invalid", load_id=None)
    with pytest.raises(psycopg.errors.RestrictViolation):
        execute(db, "update slack_event set word_count = 99 where source_event_id = %s", (obs.source_event_id,))
    db.rollback()
    with pytest.raises(psycopg.errors.RestrictViolation):
        execute(db, "delete from slack_event where source_event_id = %s", (obs.source_event_id,))
    db.rollback()


def test_email_resolves_and_matches_roster(db, ws, client, workspace_row):
    make_fellow(db, "CU-0001", "ada@example.invalid", "Ada Testcase")
    obs = parse_event(ws.message_event("U0ADA", general(ws), "hi"), TEAM)
    out = resolve_and_record(db, client, obs, cohort_id=TEST_COHORT, load_id=None)
    assert out.status == "written" and out.email == "ada@example.invalid" and out.fellow_id == "CU-0001"
    row = fetch_one(db, "select user_email from slack_event")
    assert row["user_email"] == "ada@example.invalid"
    assert count(db, "identity_unresolved") == 0


def test_unknown_email_is_recorded_and_queued(db, ws, client, workspace_row):
    obs = parse_event(ws.message_event("U0STRANGER", general(ws), "who am i"), TEAM)
    out = resolve_and_record(db, client, obs, cohort_id=TEST_COHORT, load_id=None)
    assert out.status == "written" and out.fellow_id is None
    assert count(db, "slack_event") == 1, "identity never blocks ingest"
    q = fetch_one(db, "select email, occurrence_count from identity_unresolved")
    assert q["email"] == "stranger@example.invalid" and q["occurrence_count"] == 1


def test_user_with_no_email_is_recorded_and_not_queued(db, ws, client, workspace_row):
    obs = parse_event(ws.message_event("U0NOEMAIL", general(ws), "anonymous-ish"), TEAM)
    out = resolve_and_record(db, client, obs, cohort_id=TEST_COHORT, load_id=None)
    assert out.status == "written" and out.email is None
    assert fetch_one(db, "select user_email from slack_event")["user_email"] is None
    assert count(db, "identity_unresolved") == 0, "nothing to resolve"


def test_users_info_is_cached_per_user(db, ws, client, workspace_row):
    for text in ("one", "two", "three"):
        resolve_and_record(db, client, parse_event(ws.message_event("U0ADA", general(ws), text), TEAM), cohort_id=TEST_COHORT, load_id=None)
    assert client.call_count("users.info") == 1
    # A stale cache entry refreshes.
    execute(db, "update slack_user set fetched_at = now() - interval '2 days' where slack_user_id = 'U0ADA'")
    resolve_user(db, client, TEAM, "U0ADA")
    assert client.call_count("users.info") == 2


def test_unknown_user_id_still_records_the_event(db, ws, client, workspace_row):
    obs = parse_event(ws.message_event("U0ADA", general(ws), "x"), TEAM)
    obs = SlackObservation(**{**obs.__dict__, "slack_user_id": "U0GHOST", "source_event_id": source_event_id("ghost")})
    out = resolve_and_record(db, client, obs, cohort_id=TEST_COHORT, load_id=None)
    assert out.status == "written" and out.email is None


def test_backfill_collides_with_what_the_bot_already_saw(db, ws, client, workspace_row):
    make_fellow(db, "CU-0001", "ada@example.invalid")
    live = [ws.message_event("U0ADA", general(ws), f"live {i}") for i in range(3)]
    for ev in live:
        resolve_and_record(db, client, parse_event(ev, TEAM), cohort_id=TEST_COHORT, load_id=None)
    ws.message_event("U0BOB", general(ws), "missed while the bot was down")
    ws.reaction_event("U0ADA", general(ws), live[0]["ts"], "fire")

    result = backfill_channel(db, client, TEAM, general(ws))
    assert result.messages_read == 4
    assert result.events_written == 2, "one missed message + one reaction; the three live ones collide"
    assert result.events_duplicate == 3
    assert count(db, "slack_event") == 5
    mark = fetch_one(db, "select backfilled_through_ts from slack_channel where channel_id = %s", (general(ws),))
    assert mark["backfilled_through_ts"] is not None

    again = backfill_channel(db, client, TEAM, general(ws))
    assert again.events_written == 0 and count(db, "slack_event") == 5


def test_backfill_pages_and_advances_watermark(db, ws, workspace_row):
    small = FakeSlackWebClient(ws, page_size=4)
    for i in range(11):
        ws.message_event("U0ADA", general(ws), f"m{i}")
    result = backfill_channel(db, small, TEAM, general(ws))
    assert result.messages_read == 11 and result.events_written == 11
    assert small.call_count("conversations.history") == 3
    newest = max(float(m["ts"]) for m in ws.history[general(ws)])
    mark = fetch_one(db, "select backfilled_through_ts from slack_channel where channel_id = %s", (general(ws),))
    assert float(mark["backfilled_through_ts"]) == pytest.approx(newest)


def test_backfill_workspace_writes_a_load_run(db, ws, client, workspace_row):
    ws.message_event("U0ADA", general(ws), "a")
    ws.message_event("U0BOB", ws.channel_id("help-desk"), "b")
    result = backfill_workspace(db, client, TEAM, channels=["general", "help-desk"])
    assert result.channels == 2 and result.events_written == 2
    run = fetch_one(db, "select * from load_run where source = 'slack_backfill'")
    assert run["status"] == "succeeded" and run["rows_written"] == 2
    assert count(db, "slack_event", "load_id = %s", (run["load_id"],)) == 2


def test_stats_and_per_fellow_join_the_roster(db, ws, client, workspace_row):
    from cufa.slack.store import per_fellow

    make_fellow(db, "CU-0001", "ada@example.invalid", "Ada Testcase")
    m = ws.message_event("U0ADA", general(ws), "hi")
    for ev in (m, ws.message_event("U0STRANGER", general(ws), "hey"), ws.reaction_event("U0ADA", general(ws), m["ts"], "tada")):
        resolve_and_record(db, client, parse_event(ev, TEAM), cohort_id=TEST_COHORT, load_id=None)
    s = stats(db, TEAM)
    assert s["events"] == 3 and s["messages"] == 2 and s["reactions"] == 1
    assert s["distinct_users"] == 2 and s["users_on_roster"] == 1
    rows = per_fellow(db, TEST_COHORT)
    by = {r["user_email"]: r for r in rows}
    assert by["ada@example.invalid"]["fellow_id"] == "CU-0001" and by["ada@example.invalid"]["reactions_given"] == 1
    assert by["stranger@example.invalid"]["fellow_id"] is None, "unattributed rows are kept, not dropped"


# ===========================================================================
# bot — the processor, and once over HTTP with Bolt's real verifier
# ===========================================================================

def _settings(api_base_url: str, **extra: str):
    env = {
        **os.environ,
        "SLACK_BOT_TOKEN": "xoxb-test-token",
        "SLACK_SIGNING_SECRET": "test-signing-secret",
        "SLACK_API_BASE_URL": api_base_url,
        "CUFA_SLACK_COHORT": TEST_COHORT,
        **extra,
    }
    return load_settings(env)


def test_processor_lifecycle_and_counts(db, ws, client):
    from cufa.slack.bot import EventProcessor

    proc = EventProcessor(_settings("http://unused.invalid/api/"), client)
    info = proc.start()
    assert info.team_id == TEAM and info.cohort_id == TEST_COHORT
    ev = ws.message_event("U0ADA", general(ws), "x")
    assert proc.process(ev, TEAM).status == "written"
    assert proc.process(ev, TEAM, retry_num=1).status == "duplicate"
    assert proc.process(ws.bot_message_event(general(ws), "bot"), TEAM).status == "skipped"
    own = ws.message_event(ws.bot_user_id, general(ws), "the bot itself")
    assert proc.process(own, TEAM).status == "skipped"
    proc.stop()
    run = fetch_one(db, "select * from load_run where source = 'slack_bot'")
    assert run["status"] == "succeeded" and run["rows_written"] == 1 and run["rows_read"] == 4
    assert proc.counts["retries_seen"] == 1


@pytest.fixture
def http_stack(db, ws):
    """The fake Slack server + the real bot app, wired together over HTTP."""
    from fastapi.testclient import TestClient
    from slack_sdk import WebClient

    from cufa.slack.bot import EventProcessor, build_http_app
    from cufa.slack.fake_server import FakeSlackHTTPServer

    fake = FakeSlackHTTPServer(ws, signing_secret="test-signing-secret", bot_events_url="http://bot.invalid/slack/events", port=0).start_in_thread()
    settings = _settings(fake.api_base_url)
    web = WebClient(token="xoxb-test-token", base_url=fake.api_base_url)
    processor = EventProcessor(settings, web)
    app = build_http_app(settings, client=web, processor=processor)
    with TestClient(app) as tc:
        yield tc, fake, processor
    fake.stop()


def _post(tc, envelope: dict, *, secret: str = "test-signing-secret", retry: int | None = None):
    body = json.dumps(envelope)
    headers = sign(secret, body)
    if retry:
        headers["X-Slack-Retry-Num"] = str(retry)
    return tc.post("/slack/events", content=body, headers=headers)


def test_http_url_verification_challenge_is_echoed(http_stack):
    tc, _, _ = http_stack
    r = _post(tc, {"type": "url_verification", "challenge": "abc123", "token": "t"})
    assert r.status_code == 200 and "abc123" in r.text


def test_http_signed_delivery_is_recorded(db, ws, http_stack):
    tc, fake, processor = http_stack
    make_fellow(db, "CU-0001", "ada@example.invalid")
    env = ws.envelope(ws.message_event("U0ADA", general(ws), "over http"))
    r = _post(tc, env)
    assert r.status_code == 200
    assert count(db, "slack_event") == 1
    assert fetch_one(db, "select user_email from slack_event")["user_email"] == "ada@example.invalid"
    assert processor.counts["written"] == 1


def test_http_retry_delivery_is_deduplicated(db, ws, http_stack):
    tc, _, processor = http_stack
    env = ws.envelope(ws.message_event("U0BOB", general(ws), "delivered twice"))
    assert _post(tc, env).status_code == 200
    assert _post(tc, env, retry=1).status_code == 200, "a retry must still be acked, or Slack keeps retrying"
    assert count(db, "slack_event") == 1
    assert processor.counts["duplicate"] == 1 and processor.counts["retries_seen"] == 1


def test_http_bad_signature_is_rejected(db, ws, http_stack):
    tc, _, _ = http_stack
    env = ws.envelope(ws.message_event("U0BOB", general(ws), "forged"))
    r = _post(tc, env, secret="not-the-secret")
    assert r.status_code in (401, 403)
    assert count(db, "slack_event") == 0


def test_http_all_event_types_route_through_one_listener(db, ws, http_stack):
    tc, _, _ = http_stack
    msg = ws.message_event("U0ADA", general(ws), "root")
    for ev in (
        msg,
        ws.message_event("U0BOB", general(ws), "reply", thread_ts=msg["ts"]),
        ws.reaction_event("U0BOB", general(ws), msg["ts"], "eyes"),
        ws.reaction_event("U0BOB", general(ws), msg["ts"], "eyes", removed=True),
        ws.join_event("U0STRANGER", general(ws)),
        ws.edit_event(msg, "root, edited"),
        ws.delete_event(msg),
    ):
        assert _post(tc, ws.envelope(ev)).status_code == 200
    types = {r["event_type"] for r in fetch_all(db, "select event_type from slack_event")}
    assert types == {"message", "reaction_added", "reaction_removed", "member_joined_channel", "message_changed", "message_deleted"}
    assert count(db, "slack_event") == 7


def test_http_stats_endpoint_shows_no_addresses(db, ws, http_stack):
    tc, _, _ = http_stack
    _post(tc, ws.envelope(ws.message_event("U0ADA", general(ws), "x")))
    r = tc.get("/stats")
    assert r.status_code == 200
    assert "@" not in r.text, "the status endpoint never exposes an address"
    assert r.json()["database"]["events"] == 1


def test_demo_workspace_has_the_edge_cases(tmp_path):
    roster = tmp_path / "roster.csv"
    roster.write_text("fellow_id,full_name,primary_email,status\nCU-1,One Person,one@example.invalid,active\n")
    ws = demo_workspace(roster)
    emails = {(u.get("profile") or {}).get("email") for u in ws.users.values()}
    assert "one@example.invalid" in emails
    assert None in emails, "a profile with no email"
    assert any(u.get("deleted") for u in ws.users.values()), "a deactivated account"
