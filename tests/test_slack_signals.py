"""The newer Slack signals, and the rules that shape them.

Three things are proved here beyond "it parses":

1. The absences are honest. A huddle has no channel and a canvas edit has no
   editor, and both are recorded that way rather than guessed.
2. Received recognition is never ranked. The reply graph and the mentions
   report are read for who nobody talks to; neither returns a received count,
   and the emoji signal has no per-person shape at all. These tests read the
   functions' SQL and outputs and fail if that changes.
3. The rhythm signal serves the fellow. Observed quiet hours replace only the
   deployment default, never a preference the fellow set.
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime, time, timedelta, timezone

import pytest
from conftest import TEST_COHORT, count, make_fellow

from cufa.db import execute, fetch_all, fetch_one
from cufa.slack import insights
from cufa.slack.events import (
    POLL_ACTION_ID,
    Skipped,
    SlackObservation,
    mentions_in,
    parse_event,
    parse_interaction,
)
from cufa.slack.fake import FakeSlackWebClient, FakeWorkspace
from cufa.slack.insights import (
    activity_rhythm,
    canvases,
    channel_liveness,
    emoji_mood,
    huddles,
    mentions_given,
    observed_quiet_hours,
    poll_results,
    quiet_hours_from_histogram,
    reply_graph,
)
from cufa.slack.polls import create_poll
from cufa.slack.store import ensure_workspace, resolve_and_record, stats
from cufa.slack.users import sync_users

TEAM = "T0DEMO000"
UTC = timezone.utc


@pytest.fixture
def ws() -> FakeWorkspace:
    w = FakeWorkspace()
    w.add_user(email="ada@example.invalid", real_name="Ada Testcase", user_id="U0ADA")
    w.add_user(email="bob@example.invalid", real_name="Bob Fixture", user_id="U0BOB")
    w.add_user(email="cy@example.invalid", real_name="Cy Quietly", user_id="U0CY")
    return w


@pytest.fixture
def client(ws) -> FakeSlackWebClient:
    return FakeSlackWebClient(ws)


@pytest.fixture
def stack(db, ws, client):
    """Workspace row, user cache, and three roster fellows."""
    ensure_workspace(db, client, TEST_COHORT)
    sync_users(db, client, TEAM)
    make_fellow(db, "CU-0001", "ada@example.invalid", "Ada Testcase")
    make_fellow(db, "CU-0002", "bob@example.invalid", "Bob Fixture")
    make_fellow(db, "CU-0003", "cy@example.invalid", "Cy Quietly")
    return db


def general(ws: FakeWorkspace) -> str:
    return ws.channel_id("general")


def rec(db, client, event, *, team=TEAM):
    return resolve_and_record(db, client, parse_event(event, team), cohort_id=TEST_COHORT, load_id=None)


# ===========================================================================
# parsing
# ===========================================================================

def test_mentions_are_extracted_and_text_is_still_not_stored(ws):
    ev = ws.message_event("U0ADA", general(ws), "thanks <@U0BOB> and <@U0CY|cy>, not <!here> or <#C123|general>")
    obs = parse_event(ev, TEAM)
    assert isinstance(obs, SlackObservation)
    assert obs.mentions == ("U0BOB", "U0CY")
    assert obs.text is None and "text" not in obs.raw
    assert mentions_in(None) == () and mentions_in("no one") == ()


def test_huddle_join_and_leave_have_no_channel(ws):
    join = parse_event(ws.huddle_event("U0ADA", joined=True, call_id="R1"), TEAM)
    leave = parse_event(ws.huddle_event("U0ADA", joined=False, call_id="R1"), TEAM)
    assert join.event_type == "huddle_joined" and leave.event_type == "huddle_left"
    assert join.channel_id is None and leave.channel_id is None
    assert join.call_id == "R1" and join.slack_user_id == "U0ADA"
    assert join.source_event_id != leave.source_event_id


def test_huddle_thread_message_is_skipped_so_joins_are_not_double_counted(ws):
    ev = ws.message_event("U0ADA", general(ws), "started a huddle")
    ev["subtype"] = "huddle_thread"
    assert isinstance(parse_event(ev, TEAM), Skipped)


def test_canvas_events_parse_with_honest_absences(ws):
    fid = ws.add_file("U0ADA", canvas=True)
    created = parse_event(ws.file_created_event(fid, "U0ADA"), TEAM)
    edited = parse_event(ws.file_change_event(fid), TEAM)
    shared = parse_event(ws.file_shared_event(fid, "U0BOB", general(ws)), TEAM)
    comment = parse_event(ws.file_comment_event(fid, "U0CY"), TEAM)
    assert created.event_type == "canvas_created" and created.slack_user_id == "U0ADA"
    assert edited.event_type == "canvas_edited" and edited.slack_user_id is None, "file_change names no editor"
    assert shared.event_type == "canvas_shared" and shared.channel_id == general(ws)
    assert comment.event_type == "canvas_comment" and comment.slack_user_id == "U0CY"
    assert {o.file_id for o in (created, edited, shared, comment)} == {fid}


def test_poll_vote_interaction_parses_and_changing_a_vote_is_a_new_key(ws):
    a = parse_interaction(ws.poll_vote_payload("U0ADA", general(ws), "p1", "Tuesday", message_ts="1.0"), TEAM)
    b = parse_interaction(ws.poll_vote_payload("U0ADA", general(ws), "p1", "Thursday", message_ts="1.0"), TEAM)
    assert a.event_type == "poll_vote" and a.poll_id == "p1" and a.poll_choice == "Tuesday"
    assert a.source_event_id != b.source_event_id
    other = ws.poll_vote_payload("U0ADA", general(ws), "p1", "x", message_ts="1.0")
    other["actions"][0]["action_id"] = "something_else"
    assert isinstance(parse_interaction(other, TEAM), Skipped)
    assert isinstance(parse_interaction({"type": "view_submission"}, TEAM), Skipped)


# ===========================================================================
# recording
# ===========================================================================

def test_canvas_rows_are_recorded_and_a_pdf_is_looked_up_then_skipped(stack, ws, client):
    db = stack
    canvas = ws.add_file("U0ADA", canvas=True)
    pdf = ws.add_file("U0ADA", canvas=False)
    assert rec(db, client, ws.file_created_event(canvas, "U0ADA")).status == "written"
    assert rec(db, client, ws.file_change_event(canvas)).status == "written"
    assert rec(db, client, ws.file_change_event(canvas)).status == "written", "each edit is its own act"
    assert rec(db, client, ws.file_created_event(pdf, "U0ADA")).status == "skipped"
    assert client.call_count("files.info") == 2, "one files.info per file, then cached"
    rows = fetch_all(db, "select event_type, slack_user_id, user_email from slack_event order by event_time_utc")
    assert [r["event_type"] for r in rows] == ["canvas_created", "canvas_edited", "canvas_edited"]
    assert rows[1]["slack_user_id"] is None and rows[1]["user_email"] is None
    assert count(db, "slack_file", "not is_canvas") == 1


def test_huddles_and_mentions_and_votes_land_in_the_same_stream(stack, ws, client):
    db = stack
    join = ws.huddle_event("U0ADA", joined=True, call_id="R9")
    assert rec(db, client, join).status == "written"
    assert rec(db, client, join).status == "duplicate", "Slack redelivers the same event; same key, no second row"
    assert rec(db, client, ws.huddle_event("U0ADA", joined=True, call_id="R9")).status == "written", "a later join of the same huddle is a new act"
    assert rec(db, client, ws.message_event("U0ADA", general(ws), "hey <@U0BOB>")).status == "written"
    body = ws.poll_vote_payload("U0BOB", general(ws), "00000000-0000-0000-0000-000000000001", "A", message_ts="1.0")
    out = resolve_and_record(db, client, parse_interaction(body, TEAM), cohort_id=TEST_COHORT, load_id=None)
    assert out.status == "written" and out.fellow_id == "CU-0002"
    s = stats(db, TEAM)
    assert s["huddle_joins"] == 2 and s["poll_votes"] == 1
    assert None not in s["by_channel"], "a channel-less act must not surface as a None key"
    assert s["by_channel"]["(no channel)"] == 2, "the two huddle joins are counted, labelled honestly"
    row = fetch_one(db, "select mentions from slack_event where event_type = 'message'")
    assert row["mentions"] == ["U0BOB"]


# ===========================================================================
# insights — and what they must not say
# ===========================================================================

def _seed_conversation(db, ws, client):
    g = general(ws)
    root = ws.message_event("U0ADA", g, "root <@U0BOB>")
    rec(db, client, root)
    rec(db, client, ws.message_event("U0BOB", g, "reply to ada", thread_ts=root["ts"]))
    rec(db, client, ws.message_event("U0BOB", g, "another reply", thread_ts=root["ts"]))
    rec(db, client, ws.message_event("U0ADA", g, "self reply", thread_ts=root["ts"]))  # not an edge
    rec(db, client, ws.message_event("U0CY", g, "cy posts and nobody answers"))
    rec(db, client, ws.reaction_event("U0ADA", g, root["ts"], "fire"))
    rec(db, client, ws.reaction_event("U0BOB", g, root["ts"], "fire"))
    rec(db, client, ws.reaction_event("U0CY", g, root["ts"], "eyes"))


def test_reply_graph_finds_the_person_nobody_talks_to_without_ranking_anyone(stack, ws, client):
    db = stack
    _seed_conversation(db, ws, client)
    g = reply_graph(db, TEST_COHORT, days=None)
    assert g["edges"] == [
        {"replier_id": "CU-0002", "replier": "Bob Fixture", "parent_id": "CU-0001", "replied_to": "Ada Testcase", "replies": 2}
    ], "self-replies are not edges; edges are ordered by the replier"
    # Ada was replied to; Bob was mentioned; Cy got neither — and DID post.
    assert [(r["fellow_id"], r["posted"]) for r in g["not_replied_to"]] == [("CU-0003", True)]
    assert "received" not in json.dumps(g, default=str)


def test_mentions_report_gives_only_and_never_receives(stack, ws, client):
    db = stack
    _seed_conversation(db, ws, client)
    rows = mentions_given(db, TEST_COHORT, days=None)
    assert [(r["fellow_id"], r["mentions_given"], r["people_mentioned"]) for r in rows] == [("CU-0001", 1, 1)]
    assert not any("received" in k for r in rows for k in r), "no received column, ever"
    src = inspect.getsource(mentions_given)
    assert "unnest(e.mentions) as m(uid)" in src and "group by m.uid" not in src, "never grouped by the mentioned person"


def test_emoji_mood_is_cohort_level_with_no_user_dimension(stack, ws, client):
    db = stack
    _seed_conversation(db, ws, client)
    mood = emoji_mood(db, TEST_COHORT, days=None)
    assert [(r["reaction"], r["n"]) for r in mood["top"]] == [("fire", 2), ("eyes", 1)]
    assert mood["total_reactions"] == 3 and mood["top"][0]["share"] == round(2 / 3, 3)
    assert not any(k in json.dumps(mood, default=str) for k in ("user", "email", "fellow"))
    src = inspect.getsource(emoji_mood)
    for forbidden in ("slack_user_id", "user_email", "fellow"):
        assert forbidden not in src, f"emoji_mood must not read {forbidden}: per-person emoji is surveillance"


def test_huddles_and_canvases_summaries(stack, ws, client):
    db = stack
    rec(db, client, ws.huddle_event("U0ADA", joined=True, call_id="R1"))
    rec(db, client, ws.huddle_event("U0BOB", joined=True, call_id="R1"))
    rec(db, client, ws.huddle_event("U0ADA", joined=False, call_id="R1"))
    rec(db, client, ws.huddle_event("U0ADA", joined=True, call_id="R2"))
    h = huddles(db, TEST_COHORT, days=None)
    assert (h["joins"], h["leaves"], h["huddles"], h["people"]) == (3, 1, 2, 2)
    assert [(r["fellow_id"], r["joins"], r["huddles"]) for r in h["per_fellow"]] == [("CU-0001", 2, 2), ("CU-0002", 1, 1)]

    fid = ws.add_file("U0ADA")
    rec(db, client, ws.file_created_event(fid, "U0ADA"))
    rec(db, client, ws.file_change_event(fid))
    rec(db, client, ws.file_comment_event(fid, "U0BOB"))
    c = canvases(db, TEST_COHORT, days=None)
    assert (c["canvases"], c["created"], c["edits"], c["comments"]) == (1, 1, 1, 1)


def test_channel_liveness_classifies_by_recency(stack, ws, client):
    db = stack
    from cufa.slack.backfill import sync_channels

    sync_channels(db, client, TEAM)
    g = general(ws)
    rec(db, client, ws.message_event("U0ADA", g, "now"))
    old = ws.message_event("U0BOB", ws.channel_id("help-desk"), "a while ago")
    rec(db, client, old)
    execute(db, "update slack_event set event_time_utc = event_time_utc - interval '20 days' where 1 = 0")  # immutable; set at insert instead
    # Age the help-desk row by inserting a second observation dated 20 days back.
    stale = ws.message_event("U0BOB", ws.channel_id("help-desk"), "twenty days ago")
    obs = parse_event(stale, TEAM)
    from dataclasses import replace as dc_replace
    from cufa.slack.store import record
    record(db, dc_replace(obs, event_time_utc=datetime.now(UTC) - timedelta(days=20)), email="bob@example.invalid", load_id=None)

    rows = {r["name"]: r for r in channel_liveness(db, TEAM, quiet_days=7)}
    assert rows["general"]["status"] == "alive" and rows["general"]["posters_recent"] == 1
    assert rows["help-desk"]["status"] == "alive", "the fresh message keeps it alive"
    assert rows["announcements"]["status"] == "silent"
    assert not any("email" in k or "user_id" in k for k in rows["general"]), "posters is a count, not a list"


def test_rhythm_is_read_in_each_fellows_own_zone(stack, ws, client):
    db = stack
    from dataclasses import replace as dc_replace
    from cufa.slack.store import record

    execute(db, "update fellow set timezone = 'America/Los_Angeles' where fellow_id = 'CU-0001'")
    execute(db, "update fellow set timezone = 'Europe/London' where fellow_id = 'CU-0002'")
    at = datetime(2026, 9, 16, 18, 0, tzinfo=UTC)  # Wed 11:00 LA, 19:00 London
    for uid, email in (("U0ADA", "ada@example.invalid"), ("U0BOB", "bob@example.invalid")):
        obs = parse_event(ws.message_event(uid, general(ws), "x"), TEAM)
        record(db, dc_replace(obs, event_time_utc=at), email=email, load_id=None)
    r = activity_rhythm(db, TEST_COHORT, days=None)
    assert r["acts"] == 2
    assert r["by_hour"][11] == 1 and r["by_hour"][19] == 1, "same instant, two local hours"
    assert r["by_weekday"][2] == 2, "Wednesday is index 2 (Monday = 0)"


def test_quiet_hours_from_histogram_is_conservative():
    assert quiet_hours_from_histogram([0] * 24, min_acts=20) is None, "no acts, no opinion"
    night_owl = [0] * 24
    for h in range(14, 24):
        night_owl[h] = 3
    night_owl[0] = 2
    got = quiet_hours_from_histogram(night_owl, min_acts=20)
    assert got is not None and (got.start, got.end) == (time(1), time(14))
    assert quiet_hours_from_histogram(night_owl, min_acts=100) is None, "too few acts: default stands"
    always_on = [1] * 24
    assert quiet_hours_from_histogram(always_on, min_acts=20) is None
    scattered = [5 if h % 2 else 0 for h in range(24)]
    assert quiet_hours_from_histogram(scattered, min_acts=20) is None, "no gap of four hours: no inference"


def test_observed_quiet_hours_reads_the_fellows_zone(stack, ws, client):
    db = stack
    from dataclasses import replace as dc_replace
    from cufa.slack.store import record

    # Ada, in New York, is active 20:00–23:00 local every evening for a week.
    base = datetime(2026, 9, 10, 0, 0, tzinfo=UTC)  # 20:00 EDT the day before
    for day in range(7):
        for hour in (0, 1, 2, 3):  # 20:00, 21:00, 22:00, 23:00 EDT
            obs = parse_event(ws.message_event("U0ADA", general(ws), "x"), TEAM)
            record(db, dc_replace(obs, event_time_utc=base + timedelta(days=day, hours=hour)), email="ada@example.invalid", load_id=None)
    got = observed_quiet_hours(db, TEAM, "ada@example.invalid", "America/New_York", days=3650, min_acts=20)
    assert got is not None and got.start == time(0) and got.end == time(20)
    assert observed_quiet_hours(db, TEAM, "cy@example.invalid", "America/New_York", days=3650, min_acts=20) is None


def test_poll_lifecycle_counts_the_latest_vote_and_names_nobody(stack, ws, client):
    db = stack
    poll = create_poll(db, client, team_id=TEAM, channel_id=general(ws), question="Which night?", options=["Tue", "Thu"], created_by="test")
    assert poll["message_ts"] and client.call_count("chat.postMessage") == 1
    posted = ws.posted[-1]
    assert posted["blocks"][1]["block_id"] == f"cufa_poll:{poll['poll_id']}"
    assert all(e["action_id"] == POLL_ACTION_ID for e in posted["blocks"][1]["elements"])

    def vote(user, choice):
        body = ws.poll_vote_payload(user, general(ws), poll["poll_id"], choice, message_ts=poll["message_ts"])
        return resolve_and_record(db, client, parse_interaction(body, TEAM), cohort_id=TEST_COHORT, load_id=None)

    assert vote("U0ADA", "Tue").status == "written"
    assert vote("U0BOB", "Thu").status == "written"
    assert vote("U0ADA", "Thu").status == "written", "a change of mind is a new observation"
    res = poll_results(db, poll["poll_id"])
    assert res["results"] == [{"option": "Tue", "votes": 0}, {"option": "Thu", "votes": 2}]
    assert res["voters"] == 2
    assert not any(k in json.dumps(res, default=str) for k in ("U0ADA", "U0BOB", "email", "fellow"))
    with pytest.raises(ValueError):
        create_poll(db, client, team_id=TEAM, channel_id=general(ws), question="one option", options=["only"])


def test_fake_server_describes_every_new_event_shape_without_crashing(ws):
    """The demo log line is built from the envelope. A huddle carries a user
    OBJECT, a file event a user_id, a comment a nested user — none may crash."""
    from cufa.slack.fake_server import FakeSlackHTTPServer

    server = FakeSlackHTTPServer(ws, signing_secret="s", bot_events_url="http://bot.invalid/slack/events", port=0)
    fid = ws.add_file("U0ADA")
    for ev in (
        ws.huddle_event("U0ADA", joined=True),
        ws.file_created_event(fid, "U0ADA"),
        ws.file_change_event(fid),
        ws.file_shared_event(fid, "U0BOB", general(ws)),
        ws.file_comment_event(fid, "U0CY"),
        ws.message_event("U0ADA", general(ws), "<@U0BOB> hi"),
    ):
        entry = server._describe(ws.envelope(ev), 200, "ok", retry=None, tamper=False, ms=1)
        assert entry["status"] == 200 and isinstance(entry["who"], str)
    huddle = server._describe(ws.envelope(ws.huddle_event("U0ADA", joined=True)), 200, "ok", retry=None, tamper=False, ms=1)
    assert huddle["who"] == "Ada Testcase" and "in_a_huddle" in huddle["kind"]
    pdf = ws.add_file("U0ADA", canvas=False)
    plain = server._describe(ws.envelope(ws.file_created_event(pdf, "U0ADA")), 200, "ok", retry=None, tamper=False, ms=1)
    canvas = server._describe(ws.envelope(ws.file_created_event(fid, "U0ADA")), 200, "ok", retry=None, tamper=False, ms=1)
    assert "(not a canvas)" in plain["kind"] and "(not a canvas)" not in canvas["kind"], \
        "the log says which accepted file events will leave no row"


# ===========================================================================
# over HTTP — a vote through Bolt's real signature check
# ===========================================================================

@pytest.fixture
def http_stack(db, ws):
    import os

    from fastapi.testclient import TestClient
    from slack_sdk import WebClient

    from cufa.config import load_settings
    from cufa.slack.bot import EventProcessor, build_http_app
    from cufa.slack.fake_server import FakeSlackHTTPServer

    fake = FakeSlackHTTPServer(ws, signing_secret="test-signing-secret", bot_events_url="http://bot.invalid/slack/events", port=0).start_in_thread()
    settings = load_settings({**os.environ, "SLACK_BOT_TOKEN": "xoxb-test-token", "SLACK_SIGNING_SECRET": "test-signing-secret",
                              "SLACK_API_BASE_URL": fake.api_base_url, "CUFA_SLACK_COHORT": TEST_COHORT, "CUFA_SLACK_AUTOMATIONS": "0"})
    web = WebClient(token="xoxb-test-token", base_url=fake.api_base_url)
    processor = EventProcessor(settings, web)
    app = build_http_app(settings, client=web, processor=processor)
    with TestClient(app) as tc:
        yield tc, fake, processor, web
    fake.stop()


def test_http_poll_vote_is_recorded_through_bolt(db, ws, http_stack):
    import urllib.parse

    from cufa.slack.signing import sign

    tc, fake, processor, web = http_stack
    make_fellow(db, "CU-0001", "ada@example.invalid", "Ada Testcase")
    poll = create_poll(db, web, team_id=TEAM, channel_id=general(ws), question="Q?", options=["A", "B"])
    body = urllib.parse.urlencode({"payload": json.dumps(ws.poll_vote_payload("U0ADA", general(ws), poll["poll_id"], "B", message_ts=poll["message_ts"]))})
    headers = {**sign("test-signing-secret", body), "Content-Type": "application/x-www-form-urlencoded"}
    r = tc.post("/slack/events", content=body, headers=headers)
    assert r.status_code == 200
    assert poll_results(db, poll["poll_id"])["results"][1] == {"option": "B", "votes": 1}
    assert processor.counts["poll_votes"] == 1

    bad = tc.post("/slack/events", content=body, headers={**sign("wrong", body), "Content-Type": "application/x-www-form-urlencoded"})
    assert bad.status_code != 200
    assert count(db, "slack_event", "event_type = 'poll_vote'") == 1


def test_http_huddle_and_canvas_route_and_insights_has_no_addresses(db, ws, http_stack):
    from cufa.slack.signing import sign

    tc, fake, processor, web = http_stack
    make_fellow(db, "CU-0001", "ada@example.invalid", "Ada Testcase")
    fid = ws.add_file("U0ADA")
    for ev in (ws.huddle_event("U0ADA", joined=True), ws.file_created_event(fid, "U0ADA"), ws.message_event("U0ADA", general(ws), "hi <@U0BOB>")):
        body = json.dumps(ws.envelope(ev))
        assert tc.post("/slack/events", content=body, headers=sign("test-signing-secret", body)).status_code == 200
    types = {r["event_type"] for r in fetch_all(db, "select event_type from slack_event")}
    assert {"huddle_joined", "canvas_created", "message"} <= types
    r = tc.get("/insights")
    assert r.status_code == 200 and "@" not in r.text
    data = r.json()
    assert data["huddles"]["joins"] == 1 and data["canvases"]["created"] == 1
    assert data["mentions_given"][0]["mentions_given"] == 1
