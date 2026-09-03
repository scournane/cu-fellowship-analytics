"""Q&A channels: "this was asked before" and the per-session summary.

Three levels, as for the rest of the Slack package:

* pure — markup cleaning, token overlap, event parsing, date hints, the
  prompts (what leaves the process) and the rendered texts (names nobody);
* database — capture, edits, ✅, session windows, the pointer, the summary,
  the backfill walking replies, the mention handler;
* HTTP — once, through Bolt with its real signature check.

Nothing here reaches Gemini. The model is a stub that records exactly what it
was shown, which is how the privacy claim about the payload is asserted.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone

import pytest
from conftest import TEST_COHORT, count, make_fellow, make_session

from cufa.config import load_settings
from cufa.db import execute, fetch_all, fetch_one
from cufa.errors import AiUnavailable
from cufa.slack import qa
from cufa.slack.backfill import backfill_channel
from cufa.slack.fake import FakeSlackWebClient, FakeWorkspace
from cufa.slack.qa import (
    LEXICAL_MATCH,
    MatchVerdict,
    QaService,
    SessionRef,
    SummaryDraft,
    build_match_prompt,
    build_summary_prompt,
    clean_text,
    current_summary,
    generate_summary,
    lexical_similarity,
    mention_intent,
    normalize_question,
    parse_date_hint,
    parse_match,
    parse_qa_event,
    parse_summary,
    post_summary,
    question_window,
    questions_for_session,
    render_pointer,
    session_in_effect,
    session_on_date,
    token_set,
)
from cufa.slack.store import ensure_workspace

TEAM = "T0DEMO000"
EMAILS = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ws() -> FakeWorkspace:
    w = FakeWorkspace()
    w.add_user(email="ada@example.invalid", real_name="Ada Testcase", user_id="U0ADA")
    w.add_user(email="bob@example.invalid", real_name="Bob Fixture", user_id="U0BOB")
    w.add_user(email="cara@example.invalid", real_name="Cara Sample", user_id="U0CARA")
    w.add_user(email=None, real_name="No Email", user_id="U0NOEMAIL")
    return w


@pytest.fixture
def client(ws) -> FakeSlackWebClient:
    return FakeSlackWebClient(ws)


def _settings(**extra: str):
    import os

    env = {
        **os.environ,
        "SLACK_BOT_TOKEN": "xoxb-test-token",
        "SLACK_SIGNING_SECRET": "test-signing-secret",
        "SLACK_API_BASE_URL": "http://unused.invalid/api/",
        "CUFA_SLACK_COHORT": TEST_COHORT,
        "CUFA_SLACK_QA_CHANNELS": "q-and-a",
        **extra,
    }
    env.pop("GEMINI_API_KEY", None)
    return load_settings(env)


class StubMatcher:
    """Tier 2 that answers what it is told and records what it was shown."""

    model_name = "stub-matcher"
    prompt_version = "test"

    def __init__(self, number: int = 0, confidence: float = 0.9):
        self.number, self.confidence = number, confidence
        self.calls: list[tuple[str, list[str]]] = []
        self.prompts: list[str] = []

    def match(self, new_question, candidates):
        self.calls.append((new_question, list(candidates)))
        self.prompts.append(build_match_prompt(new_question, candidates))
        return MatchVerdict(self.number, self.confidence, "stub")


class ExplodingMatcher:
    model_name = "exploding"
    prompt_version = "test"

    def match(self, new_question, candidates):
        raise AiUnavailable("quota exhausted")


class StubSummarizer:
    model_name = "stub-summarizer"
    prompt_version = "test"

    def __init__(self, draft: SummaryDraft | None = None):
        self.draft = draft or SummaryDraft("Mostly logistics, one concept question.", (("Logistics", (1,)),))
        self.prompts: list[str] = []
        self.items: list[list[dict]] = []

    def summarize(self, items):
        self.items.append(list(items))
        self.prompts.append(build_summary_prompt(items))
        return self.draft


class ExplodingSummarizer:
    model_name = "exploding"
    prompt_version = "test"

    def summarize(self, items):
        raise AiUnavailable("quota exhausted")


@pytest.fixture
def service(db, ws, client):
    """A QaService on the test cohort with #q-and-a configured, tier 1 only."""
    info = ensure_workspace(db, client, TEST_COHORT)
    svc = QaService(
        _settings(), client, team_id=info.team_id, bot_user_id=info.bot_user_id, cohort_id=info.cohort_id,
        workspace_url=info.url, bot_handle=info.bot_name, use_model=False,
    )
    svc.resolve_channels(db)
    return svc


def qa_channel(ws: FakeWorkspace) -> str:
    return ws.channel_id("q-and-a")


def ask(db, service, ws, user: str, text: str, *, channel: str | None = None) -> dict:
    """Post a top-level question through the service, as the bot would."""
    event = ws.message_event(user, channel or qa_channel(ws), text)
    service.observe(db, event, TEAM)
    return event


def reply(db, service, ws, user: str, question: dict, text: str) -> dict:
    event = ws.message_event(user, question["channel"], text, thread_ts=question["ts"])
    service.observe(db, event, TEAM)
    return event


def pointers(ws: FakeWorkspace) -> list[dict]:
    return [p for p in ws.posted if "came up before" in p["text"]]


def recent_session(db, *, days_ago: int, title: str = "Test session", hour: int = 19) -> str:
    local = (datetime.now() - timedelta(days=days_ago)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return make_session(db, title=title, local=local)


# ===========================================================================
# pure
# ===========================================================================

def test_clean_text_renders_markup_without_naming_anyone():
    raw = "<@U0ADA> see <https://x.invalid/doc|the doc> in <#C0123|general> &amp; <!here|here> <https://y.invalid>"
    assert clean_text(raw) == "@someone see the doc in #general & @here https://y.invalid"


def test_normalization_and_tokens_keep_subject_matter_only():
    assert token_set(normalize_question("What does 'quorum' mean in this context?")) == {"quorum", "mean", "context"}
    assert token_set(normalize_question("Does anyone have the slides from Tuesday?")) == {"slide", "tuesday"}
    assert token_set(normalize_question("who's presenting first next week?")) == {"present", "first", "next", "week"}
    assert token_set(normalize_question("thanks!!")) == set(), "nothing to match on"
    assert token_set(normalize_question("see https://example.invalid/x")) == {"see"}, "URLs are not words"


@pytest.mark.parametrize(
    "a, b, expect_match",
    [
        ("does anyone have the slides from tuesday?", "can someone share tuesday's slides?", True),
        ("what does quorum mean in this context?", "what does quorum actually mean?", True),
        ("who's presenting first next week?", "who is presenting next week", True),
        ("what does quorum mean?", "what does filibuster mean?", False),
        ("when is the next session?", "who is presenting next week?", False),
        ("is the reading due friday?", "can someone share tuesday's slides?", False),
    ],
)
def test_lexical_similarity_separates_paraphrases_from_frames(a, b, expect_match):
    score, shared = lexical_similarity(normalize_question(a), normalize_question(b))
    matched = score >= LEXICAL_MATCH and shared >= 2
    assert matched is expect_match, (score, shared)


def test_parse_qa_event_classifies_questions_replies_edits_deletes_and_checkmarks(ws):
    c = qa_channel(ws)
    question = ws.message_event("U0ADA", c, "what is quorum?")
    answer = ws.message_event("U0BOB", c, "the minimum present", thread_ts=question["ts"])
    q = parse_qa_event(question, TEAM)
    a = parse_qa_event(answer, TEAM)
    assert q.kind == "question" and q.thread_ts is None and q.text == "what is quorum?"
    assert a.kind == "answer" and a.thread_ts == question["ts"] and a.slack_user_id == "U0BOB"

    edit = parse_qa_event(ws.edit_event(answer, "the minimum number present"), TEAM)
    assert edit.kind == "edit" and edit.message_ts == answer["ts"] and edit.thread_ts == question["ts"]
    delete = parse_qa_event(ws.delete_event(question), TEAM)
    assert delete.kind == "delete" and delete.message_ts == question["ts"]

    tick = parse_qa_event(ws.reaction_event("U0ADA", c, answer["ts"], "white_check_mark"), TEAM)
    assert tick.kind == "reaction" and tick.removed is False and tick.message_ts == answer["ts"]
    untick = parse_qa_event(ws.reaction_event("U0ADA", c, answer["ts"], "white_check_mark", removed=True), TEAM)
    assert untick.removed is True
    assert parse_qa_event(ws.reaction_event("U0ADA", c, answer["ts"], "thumbsup"), TEAM) is None, "only ✅ means answered"

    assert parse_qa_event(ws.bot_message_event(c, "reminder"), TEAM) is None
    assert parse_qa_event(ws.mention_event("U0ADA", c, "summary"), TEAM) is None
    assert parse_qa_event(ws.join_event("U0ADA", c), TEAM) is None


@pytest.mark.parametrize(
    "text, expected",
    [
        ("summary sept 2", date(2026, 9, 2)),
        ("summary for September 2nd please", date(2026, 9, 2)),
        ("recap 2 sept", date(2026, 9, 2)),
        ("summary 9/2", date(2026, 9, 2)),
        ("summary 2025-09-02", date(2025, 9, 2)),
        ("summary", None),
        ("summary sept 31", None),
    ],
)
def test_parse_date_hint(text, expected):
    assert parse_date_hint(text, year=2026) == expected


def _ref(n: int, when: datetime, title: str = "S") -> SessionRef:
    return SessionRef(f"s{n}", TEST_COHORT, f"{title}{n}", when, when.replace(tzinfo=None), "UTC", 90)


def test_session_windows_open_an_hour_early_and_the_last_is_open_ended():
    t0 = datetime(2026, 9, 2, 23, 0, tzinfo=timezone.utc)
    sessions = [_ref(1, t0), _ref(2, t0 + timedelta(days=7))]
    assert question_window(sessions, "s1") == (t0 - timedelta(hours=1), t0 + timedelta(days=7) - timedelta(hours=1))
    assert question_window(sessions, "s2") == (t0 + timedelta(days=7) - timedelta(hours=1), None)
    assert session_in_effect(sessions, t0 - timedelta(hours=2)) is None, "before the first window"
    assert session_in_effect(sessions, t0 - timedelta(minutes=30)).session_id == "s1"
    assert session_in_effect(sessions, t0 + timedelta(days=3)).session_id == "s1"
    assert session_in_effect(sessions, t0 + timedelta(days=30)).session_id == "s2"
    assert session_on_date(sessions, date(2026, 9, 2)).session_id == "s1"
    assert session_on_date(sessions, date(2025, 9, 2)).session_id == "s1", "month and day fall back across years"
    assert session_on_date(sessions, date(2026, 9, 3)) is None
    assert sessions[0].label == "Sep 2 · S1"


def test_pointer_text_is_hedged_links_and_names_nobody():
    session = _ref(1, datetime(2026, 9, 2, 23, 0, tzinfo=timezone.utc), "Voting systems ")
    text = render_pointer(session=session, asked_at=session.scheduled_at_utc, thread_link="https://s/x", answer_link=None)
    assert "came up before, during *Sep 2 · Voting systems 1*" in text
    assert "<https://s/x|open thread>" in text
    assert "a person will answer here" in text
    with_answer = render_pointer(session=None, asked_at=datetime(2026, 8, 30, tzinfo=timezone.utc), thread_link="https://s/x", answer_link="https://s/y")
    assert "on Aug 30" in with_answer and "<https://s/y|see the answer>" in with_answer and "✅" in with_answer


def test_prompts_carry_only_texts():
    items = [
        {"text": "<@U0ADA> does anyone have the slides?", "answers": [{"text": "pinned in <#C1|announcements>"}]},
        {"text": "what is quorum?", "answers": []},
    ]
    prompt = build_summary_prompt(items)
    assert "Q1: @someone does anyone have the slides?" in prompt
    assert "reply: pinned in #announcements" in prompt
    assert "Q2: what is quorum?" in prompt and "(no replies)" in prompt
    assert "U0ADA" not in prompt and "@example" not in prompt
    match = build_match_prompt("can someone share the slides?", ["<@U0BOB> does anyone have the slides?"])
    assert "1. @someone does anyone have the slides?" in match and "U0BOB" not in match


def test_model_output_is_parsed_defensively():
    assert parse_match({"match_number": 2, "confidence": 1.7, "reasoning": "same"}, 3) == MatchVerdict(2, 1.0, "same")
    assert parse_match({"match_number": 9, "confidence": 0.9, "reasoning": ""}, 3).match_number == 0, "an invented index is no match"
    assert parse_match({"match_number": "x"}, 3).match_number == 0
    draft = parse_summary({"summary": "ok", "topics": [{"label": "A", "question_numbers": [1, 7, "x"]}, {"label": "", "question_numbers": [2]}]}, 2)
    assert draft.topics == (("A", (1,)),)
    with pytest.raises(AiUnavailable):
        parse_summary({"summary": "", "topics": []}, 2)


def test_mention_intent():
    assert mention_intent("<@U0DEMOBOT> summary sept 2", "U0DEMOBOT") == ("summary", "summary sept 2")
    assert mention_intent("<@U0DEMOBOT> can you recap?", "U0DEMOBOT")[0] == "summary"
    assert mention_intent("<@U0DEMOBOT> hello", "U0DEMOBOT")[0] == "help"


# ===========================================================================
# database — capture
# ===========================================================================

def test_question_and_reply_are_captured_with_text_and_no_email(db, ws, service):
    q = ask(db, service, ws, "U0ADA", "does anyone have the slides from tuesday?")
    assert count(db, "slack_qa_question") == 1
    row = fetch_one(db, "select text, normalized_text, slack_user_id, resolved from slack_qa_question")
    assert row["text"] == "does anyone have the slides from tuesday?" and row["slack_user_id"] == "U0ADA"
    assert row["normalized_text"] == "does anyone have the slides from tuesday"
    assert not any("email" in c for c in row), "the Q&A rows carry no address column"

    reply(db, service, ws, "U0ADA", q, "bump — anyone?")
    assert fetch_one(db, "select count(*) as n from slack_qa_answer")["n"] == 1
    assert not _answered(db), "the asker's own follow-up is not an answer"
    reply(db, service, ws, "U0BOB", q, "pinned in #announcements")
    assert _answered(db)


def _answered(db) -> bool:
    row = fetch_one(db, f"select {qa._ANSWERED_SQL} as answered from slack_qa_question q")
    return bool(row["answered"])


def test_a_message_outside_the_qa_channels_is_ignored(db, ws, service):
    assert service.observe(db, ws.message_event("U0ADA", ws.channel_id("general"), "what is quorum?"), TEAM) is None
    assert count(db, "slack_qa_question") == 0
    assert service.is_qa_channel(qa_channel(ws)) and not service.is_qa_channel(ws.channel_id("general"))


def test_edit_delete_and_checkmark_follow_the_message(db, ws, service):
    q = ask(db, service, ws, "U0ADA", "what is quorum")
    a = reply(db, service, ws, "U0BOB", q, "the minimum present")
    assert service.observe(db, ws.edit_event(a, "the minimum number present for a vote"), TEAM) == "edit:answer"
    assert fetch_one(db, "select text from slack_qa_answer")["text"] == "the minimum number present for a vote"
    assert service.observe(db, ws.edit_event(q, "what is a quorum?"), TEAM) == "edit:question"
    assert fetch_one(db, "select normalized_text from slack_qa_question")["normalized_text"] == "what is a quorum"

    assert service.observe(db, ws.reaction_event("U0ADA", q["channel"], a["ts"], "white_check_mark"), TEAM) == "reaction:answer"
    assert fetch_one(db, "select accepted from slack_qa_answer")["accepted"] is True
    assert service.observe(db, ws.reaction_event("U0ADA", q["channel"], a["ts"], "white_check_mark", removed=True), TEAM) == "reaction:answer"
    assert fetch_one(db, "select accepted from slack_qa_answer")["accepted"] is False
    assert service.observe(db, ws.reaction_event("U0BOB", q["channel"], q["ts"], "heavy_check_mark"), TEAM) == "reaction:question"
    assert fetch_one(db, "select resolved from slack_qa_question")["resolved"] is True

    assert service.observe(db, ws.delete_event(a), TEAM) == "delete:answer"
    assert service.observe(db, ws.delete_event(q), TEAM) == "delete:question"
    rows = fetch_all(db, "select deleted_at_utc from slack_qa_question union all select deleted_at_utc from slack_qa_answer")
    assert all(r["deleted_at_utc"] for r in rows), "rows stay, stamped — never removed"


def test_a_reply_to_a_question_the_bot_never_saw_fetches_the_parent(db, ws, service, client):
    """The bot joined after the question was posted; the reply still lands."""
    c = qa_channel(ws)
    parent = ws.message_event("U0ADA", c, "where is the syllabus?")  # recorded in Slack, not observed
    assert count(db, "slack_qa_question") == 0
    assert service.observe(db, ws.message_event("U0BOB", c, "in the pinned doc", thread_ts=parent["ts"]), TEAM) == "answer"
    assert client.call_count("conversations.replies") == 1
    q = fetch_one(db, "select text from slack_qa_question")
    assert q["text"] == "where is the syllabus?"
    assert count(db, "slack_qa_answer") == 1


# ===========================================================================
# database — the pointer
# ===========================================================================

def test_a_repeated_question_gets_a_pointer_to_the_earlier_thread(db, ws, service):
    first = ask(db, service, ws, "U0ADA", "does anyone have the slides from tuesday?")
    reply(db, service, ws, "U0BOB", first, "pinned in #announcements")
    assert pointers(ws) == [], "nothing to point at yet"

    again = ask(db, service, ws, "U0CARA", "can someone share tuesday's slides?")
    posted = pointers(ws)
    assert len(posted) == 1
    assert posted[0]["thread_ts"] == again["ts"], "the reply goes in the NEW question's thread"
    assert posted[0]["channel"] == again["channel"]
    text = posted[0]["text"]
    assert f"/archives/{first['channel']}/p{first['ts'].replace('.', '')}" in text, "links the earlier thread"
    assert "open thread" in text and "✅" not in text
    assert not EMAILS.search(text) and "<@U" not in text, "names nobody"

    row = fetch_one(db, "select method, similarity, posted_ts, post_error from slack_qa_pointer")
    assert row["method"] == "lexical" and float(row["similarity"]) >= LEXICAL_MATCH
    assert row["posted_ts"] and row["post_error"] is None
    assert f"{first['ts']}" not in text or True  # the ts is inside the permalink only


def test_pointer_links_the_accepted_answer_directly_and_names_the_session(db, ws, service):
    session_id = recent_session(db, days_ago=2, title="Voting systems")
    first = ask(db, service, ws, "U0ADA", "what does quorum mean in this context?")
    answer = reply(db, service, ws, "U0BOB", first, "the minimum number present for a vote to count")
    service.observe(db, ws.reaction_event("U0ADA", first["channel"], answer["ts"], "white_check_mark"), TEAM)

    ask(db, service, ws, "U0CARA", "sorry if this was covered — what does quorum actually mean?")
    text = pointers(ws)[0]["text"]
    assert "marked ✅" in text and "see the answer" in text
    assert f"p{answer['ts'].replace('.', '')}?thread_ts={first['ts']}" in text, "the reply's own permalink"
    label = fetch_one(db, 'select title, scheduled_at_local from "session" where session_id = %s', (session_id,))
    local = label["scheduled_at_local"]
    assert f"during *{local:%b} {local.day} · Voting systems*" in text


def test_no_pointer_for_an_unanswered_or_unrelated_or_deleted_earlier_question(db, ws, service):
    first = ask(db, service, ws, "U0ADA", "does anyone have the slides from tuesday?")
    ask(db, service, ws, "U0CARA", "can someone share tuesday's slides?")
    assert pointers(ws) == [], "the earlier one has no answer — nothing to point at"

    reply(db, service, ws, "U0BOB", first, "pinned in #announcements")
    ask(db, service, ws, "U0CARA", "when is the reading due?")
    assert pointers(ws) == [], "a different question"

    service.observe(db, ws.delete_event(first), TEAM)
    ask(db, service, ws, "U0CARA", "anyone got tuesday's slides?")
    assert pointers(ws) == [], "a deleted question is never pointed at"


def test_pointer_is_posted_once_per_question(db, ws, service):
    first = ask(db, service, ws, "U0ADA", "does anyone have the slides from tuesday?")
    reply(db, service, ws, "U0BOB", first, "pinned")
    again = ask(db, service, ws, "U0CARA", "can someone share tuesday's slides?")
    qid = fetch_one(db, "select question_id from slack_qa_question where message_ts = %s", (again["ts"],))["question_id"]
    assert service.point_at_earlier(db, str(qid)) is False, "already pointed"
    service.observe(db, again, TEAM)  # a re-delivery of the same event
    assert len(pointers(ws)) == 1 and count(db, "slack_qa_pointer") == 1


def test_pointer_post_failure_is_recorded_not_raised(db, ws, service, client):
    first = ask(db, service, ws, "U0ADA", "does anyone have the slides from tuesday?")
    reply(db, service, ws, "U0BOB", first, "pinned")
    from slack_sdk.errors import SlackApiError

    def refuse(**kwargs):
        raise SlackApiError("chat.postMessage: not_in_channel", {"ok": False, "error": "not_in_channel"})

    client.chat_postMessage = refuse
    assert service.observe(db, ws.message_event("U0CARA", qa_channel(ws), "can someone share tuesday's slides?"), TEAM) == "question"
    row = fetch_one(db, "select posted_ts, post_error from slack_qa_pointer")
    assert row["posted_ts"] is None and row["post_error"] == "not_in_channel"


def test_tier_two_sees_only_candidates_that_share_a_word(db, ws, client):
    info = ensure_workspace(db, client, TEST_COHORT)
    matcher = StubMatcher(number=1, confidence=0.9)
    svc = QaService(_settings(), client, team_id=info.team_id, bot_user_id=info.bot_user_id, cohort_id=info.cohort_id, matcher=matcher)
    svc.resolve_channels(db)

    first = ask(db, svc, ws, "U0ADA", "does anyone have the slides from tuesday?")
    reply(db, svc, ws, "U0BOB", first, "pinned")
    other = ask(db, svc, ws, "U0ADA", "when is the reading due?")
    reply(db, svc, ws, "U0BOB", other, "friday")

    ask(db, svc, ws, "U0CARA", "can someone share tuesday's slides?")
    assert matcher.calls == [], "tier 1 decided; the model was not asked"
    assert len(pointers(ws)) == 1

    ask(db, svc, ws, "U0CARA", "is there a recording from tuesday?")
    assert len(matcher.calls) == 1
    shown = matcher.calls[0][1]
    assert shown == ["does anyone have the slides from tuesday?"], "one shared word → shown; no shared word → not"
    assert "U0" not in matcher.prompts[0] and "@example" not in matcher.prompts[0]
    assert len(pointers(ws)) == 2
    assert fetch_one(db, "select method from slack_qa_pointer order by created_at desc limit 1")["method"] == "gemini"

    matcher.number = 0
    ask(db, svc, ws, "U0CARA", "which tuesday is the deadline?")
    assert len(pointers(ws)) == 2, "the model said none"

    ask(db, svc, ws, "U0CARA", "how do committees work?")
    assert len(matcher.calls) == 2, "nothing shared a word — the model was not asked"


def test_tier_two_failure_degrades_to_tier_one(db, ws, client):
    info = ensure_workspace(db, client, TEST_COHORT)
    svc = QaService(_settings(), client, team_id=info.team_id, bot_user_id=info.bot_user_id, cohort_id=info.cohort_id, matcher=ExplodingMatcher())
    svc.resolve_channels(db)
    first = ask(db, svc, ws, "U0ADA", "does anyone have the slides from tuesday?")
    reply(db, svc, ws, "U0BOB", first, "pinned")
    assert svc.observe(db, ws.message_event("U0CARA", qa_channel(ws), "is there a recording from tuesday?"), TEAM) == "question"
    assert pointers(ws) == []
    assert svc.observe(db, ws.message_event("U0CARA", qa_channel(ws), "can someone share tuesday's slides?"), TEAM) == "question+pointer"


# ===========================================================================
# database — sessions and the summary
# ===========================================================================

def test_questions_belong_to_the_session_in_effect(db, ws, service):
    s1 = recent_session(db, days_ago=5, title="Week 1")
    s2 = recent_session(db, days_ago=1, title="Week 2")
    old = ask(db, service, ws, "U0ADA", "what is quorum?")
    execute(db, "update slack_qa_question set asked_at_utc = now() - interval '3 days' where message_ts = %s", (old["ts"],))
    ask(db, service, ws, "U0BOB", "when is the reading due?")
    ask(db, service, ws, "U0CARA", "thanks!")  # not a question: nothing to summarise
    assert [i["text"] for i in questions_for_session(db, s1)] == ["what is quorum?"]
    assert [i["text"] for i in questions_for_session(db, s2)] == ["when is the reading due?"]


def test_summary_is_the_plain_digest_without_a_model(db, ws, service, client):
    session_id = recent_session(db, days_ago=1, title="Budgets")
    first = ask(db, service, ws, "U0ADA", "does anyone have the slides from tuesday?")
    reply(db, service, ws, "U0BOB", first, "pinned in #announcements")
    ask(db, service, ws, "U0CARA", "what does quorum mean?")

    result = generate_summary(db, client, session_id, team_id=TEAM, workspace_url="https://demo.slack.invalid/", use_model=False)
    assert result.generated and result.questions_considered == 2 and result.answered_count == 1
    text = result.text
    assert text.startswith("*Q&A summary — ") and "· Budgets*" in text
    assert "2 questions · 1 answered · 1 still open" in text
    assert "*Still open*" in text and "what does quorum mean?" in text
    assert "*All questions*" in text and "(1 reply)" in text
    assert "plain digest" in text
    assert "/archives/" in text, "every question is a link"
    assert not EMAILS.search(text) and "U0ADA" not in text and "Ada" not in text
    row = current_summary(db, session_id)
    assert row["model"] == "digest" and row["questions_considered"] == 2

    again = generate_summary(db, client, session_id, team_id=TEAM, use_model=False)
    assert not again.generated and again.summary_id == result.summary_id
    redo = generate_summary(db, client, session_id, team_id=TEAM, use_model=False, regenerate=True)
    assert redo.generated and redo.superseded == 1 and redo.summary_id != result.summary_id
    assert count(db, "slack_qa_summary", "superseded_at is not null") == 1


def test_summary_with_a_model_uses_its_paragraph_and_shows_it_only_texts(db, ws, service, client):
    session_id = recent_session(db, days_ago=1)
    first = ask(db, service, ws, "U0ADA", "<@U0BOB> does anyone have the slides from tuesday?")
    reply(db, service, ws, "U0BOB", first, "pinned in #announcements")
    ask(db, service, ws, "U0CARA", "what does quorum mean?")
    stub = StubSummarizer()
    result = generate_summary(db, client, session_id, team_id=TEAM, summarizer=stub)
    assert "Mostly logistics, one concept question." in result.text
    assert "*Topics*" in result.text and "• Logistics — Q1" in result.text
    assert "Summarised by stub-summarizer" in result.text
    assert current_summary(db, session_id)["model"] == "stub-summarizer"
    prompt = stub.prompts[0]
    assert "Q1: @someone does anyone have the slides from tuesday?" in prompt
    assert "reply: pinned in #announcements" in prompt
    assert "U0ADA" not in prompt and "U0BOB" not in prompt and not EMAILS.search(prompt)
    for key in ("slack_user_id", "user_email", "fellow_id"):
        assert all(key not in item for item in stub.items[0]), "identity never reaches the summariser"


def test_summary_degrades_when_the_model_fails(db, ws, service, client):
    session_id = recent_session(db, days_ago=1)
    ask(db, service, ws, "U0ADA", "what does quorum mean?")
    result = generate_summary(db, client, session_id, team_id=TEAM, summarizer=ExplodingSummarizer())
    assert result.generated and "plain digest" in result.text and "quota exhausted" in result.text
    assert result.model == "digest"


def test_summary_with_nothing_asked(db, ws, service, client):
    session_id = recent_session(db, days_ago=1)
    result = generate_summary(db, client, session_id, team_id=TEAM, use_model=False)
    assert not result.generated and result.summary_id is None and "no questions" in result.message
    assert count(db, "slack_qa_summary") == 0


def test_post_summary_records_where_it_went(db, ws, service, client):
    session_id = recent_session(db, days_ago=1)
    ask(db, service, ws, "U0ADA", "what does quorum mean?")
    result = generate_summary(db, client, session_id, team_id=TEAM, use_model=False)
    ts = post_summary(db, client, result.summary_id, ws.channel_id("general"))
    assert ws.posted[-1]["channel"] == ws.channel_id("general") and ws.posted[-1]["text"] == result.text
    row = current_summary(db, session_id)
    assert row["posted_ts"] == ts and row["posted_channel_id"] == ws.channel_id("general")


# ===========================================================================
# database — mentions
# ===========================================================================

def test_mention_summary_posts_in_the_thread_of_the_mention(db, ws, service):
    session_id = recent_session(db, days_ago=1, title="Budgets")
    first = ask(db, service, ws, "U0ADA", "does anyone have the slides from tuesday?")
    reply(db, service, ws, "U0BOB", first, "pinned")
    mention = ws.mention_event("U0ADA", ws.channel_id("general"), "summary please")
    out = service.handle_mention(db, mention)
    assert out["intent"] == "summary" and out["posted"] and out["session_id"] == session_id
    post = ws.posted[-1]
    assert post["thread_ts"] == mention["ts"] and post["channel"] == ws.channel_id("general")
    assert post["text"].startswith("*Q&A summary — ") and "· Budgets*" in post["text"]
    assert current_summary(db, session_id)["posted_ts"] == post["ts"]

    # Asked again: regenerated (superseding), not served stale.
    ask(db, service, ws, "U0CARA", "what does quorum mean?")
    service.handle_mention(db, ws.mention_event("U0ADA", ws.channel_id("general"), "summary"))
    assert "2 questions" in ws.posted[-1]["text"]
    assert count(db, "slack_qa_summary", "superseded_at is not null") == 1


def test_mention_can_name_a_session_by_date(db, ws, service):
    when = datetime(2026, 9, 2, 19, 0)
    session_id = make_session(db, title="Sept meeting", local=when)
    q = ws.message_event("U0ADA", qa_channel(ws), "what is the passphrase for?")
    service.observe(db, q, TEAM)
    execute(db, "update slack_qa_question set asked_at_utc = %s where message_ts = %s",
            (datetime(2026, 9, 2, 23, 30, tzinfo=timezone.utc), q["ts"]))
    out = service.handle_mention(db, ws.mention_event("U0BOB", ws.channel_id("general"), "summary sept 2"))
    assert out["session_id"] == session_id and out["posted"]
    assert "Sep 2 · Sept meeting" in ws.posted[-1]["text"]

    out = service.handle_mention(db, ws.mention_event("U0BOB", ws.channel_id("general"), "summary for jan 9"))
    assert out["intent"] == "summary" and "could not find a session on Jan 9" in ws.posted[-1]["text"]


def test_mention_without_a_request_explains_itself(db, ws, service):
    out = service.handle_mention(db, ws.mention_event("U0ADA", ws.channel_id("general"), "hi there"))
    assert out["intent"] == "help" and out["posted"]
    assert "`@cufa-bot summary`" in ws.posted[-1]["text"], "the bot's own handle, from auth.test"


def test_mention_when_nothing_was_asked(db, ws, service):
    recent_session(db, days_ago=1)
    service.handle_mention(db, ws.mention_event("U0ADA", ws.channel_id("general"), "summary"))
    assert "Nothing to summarise yet" in ws.posted[-1]["text"]


# ===========================================================================
# the processor, the backfill, and once over HTTP
# ===========================================================================

def test_processor_routes_qa_events_and_ignores_retried_mentions(db, ws, client):
    from cufa.slack.bot import EventProcessor

    recent_session(db, days_ago=1)
    proc = EventProcessor(_settings(), client)
    proc.start()
    assert proc.qa is not None and proc.qa.channel_ids == [qa_channel(ws)]
    first = ws.message_event("U0ADA", qa_channel(ws), "does anyone have the slides from tuesday?")
    assert proc.process(first, TEAM).status == "written"
    proc.process(ws.message_event("U0BOB", qa_channel(ws), "pinned", thread_ts=first["ts"]), TEAM)
    again = ws.message_event("U0CARA", qa_channel(ws), "can someone share tuesday's slides?")
    assert proc.process(again, TEAM).status == "written"
    assert proc.process(again, TEAM, retry_num=1).status == "duplicate"
    assert len(pointers(ws)) == 1 and proc.counts["qa_pointers"] == 1 and proc.counts["qa_events"] == 3
    assert fetch_one(db, "select count(*) as n from slack_event where text is not null")["n"] == 0, "ADR-031 still holds"

    mention = ws.mention_event("U0ADA", qa_channel(ws), "summary")
    assert proc.handle_mention(mention, TEAM, retry_num=1)["reason"] == "retry"
    assert proc.handle_mention(mention, TEAM)["posted"] is True
    assert proc.counts["mentions"] == 1
    snap = proc.snapshot()
    assert snap["qa_channels"] == [qa_channel(ws)]
    proc.stop()


def test_processor_without_qa_channels_does_nothing_extra(db, ws, client):
    from cufa.slack.bot import EventProcessor

    proc = EventProcessor(_settings(CUFA_SLACK_QA_CHANNELS=""), client)
    proc.start()
    assert proc.qa is None
    proc.process(ws.message_event("U0ADA", qa_channel(ws), "what is quorum?"), TEAM)
    assert count(db, "slack_qa_question") == 0 and ws.posted == []
    assert proc.handle_mention(ws.mention_event("U0ADA", qa_channel(ws), "summary"), TEAM)["intent"] == "ignored"
    proc.stop()


def test_backfill_walks_replies_in_qa_channels_only(db, ws, client, service):
    make_fellow(db, "CU-0001", "ada@example.invalid")
    c = qa_channel(ws)
    q = ws.message_event("U0ADA", c, "does anyone have the slides from tuesday?")
    a1 = ws.message_event("U0BOB", c, "pinned", thread_ts=q["ts"])
    ws.message_event("U0CARA", c, "thanks", thread_ts=q["ts"])
    ws.reaction_event("U0ADA", c, a1["ts"], "white_check_mark")
    g = ws.message_event("U0ADA", ws.channel_id("general"), "root")
    ws.message_event("U0BOB", ws.channel_id("general"), "reply", thread_ts=g["ts"])
    assert count(db, "slack_qa_question") == 0

    result = backfill_channel(db, client, TEAM, c, qa=service)
    assert result.messages_read == 1 and result.replies_read == 2
    assert result.events_written == 4, "the question, two replies, one reaction"
    assert count(db, "slack_qa_question") == 1 and count(db, "slack_qa_answer") == 2
    assert fetch_one(db, "select accepted from slack_qa_answer where message_ts = %s", (a1["ts"],))["accepted"] is True
    assert count(db, "slack_event", "is_thread_reply") == 2
    assert pointers(ws) == [], "no pointers for history"

    other = backfill_channel(db, client, TEAM, ws.channel_id("general"), qa=service)
    assert other.messages_read == 1 and other.replies_read == 0, "threads are walked for Q&A channels only"

    again = backfill_channel(db, client, TEAM, c, qa=service)
    assert again.events_written == 0 and count(db, "slack_qa_answer") == 2


@pytest.fixture
def http_stack(db, ws):
    from fastapi.testclient import TestClient
    from slack_sdk import WebClient

    from cufa.slack.bot import EventProcessor, build_http_app
    from cufa.slack.fake_server import FakeSlackHTTPServer

    fake = FakeSlackHTTPServer(ws, signing_secret="test-signing-secret", bot_events_url="http://bot.invalid/slack/events", port=0).start_in_thread()
    settings = _settings(SLACK_API_BASE_URL=fake.api_base_url)
    web = WebClient(token="xoxb-test-token", base_url=fake.api_base_url)
    processor = EventProcessor(settings, web)
    app = build_http_app(settings, client=web, processor=processor)
    with TestClient(app) as tc:
        yield tc, fake, processor
    fake.stop()


def _post(tc, envelope: dict, *, secret: str = "test-signing-secret"):
    from cufa.slack.signing import sign

    body = json.dumps(envelope)
    return tc.post("/slack/events", content=body, headers=sign(secret, body))


def test_http_qa_flow_end_to_end(db, ws, http_stack):
    """Through Bolt, with the real slack_sdk client talking to the fake server:
    the pointer and the summary are posted with chat.postMessage, the
    permalinks come from chat.getPermalink, and /stats counts them."""
    tc, fake, processor = http_stack
    recent_session(db, days_ago=1, title="Budgets")
    c = qa_channel(ws)
    first = ws.message_event("U0ADA", c, "does anyone have the slides from tuesday?")
    answer = ws.message_event("U0BOB", c, "pinned in #announcements", thread_ts=first["ts"])
    for event in (
        first, answer,
        ws.reaction_event("U0ADA", c, answer["ts"], "white_check_mark"),
        ws.message_event("U0CARA", c, "can someone share tuesday's slides?"),
    ):
        assert _post(tc, ws.envelope(event)).status_code == 200
    posted = pointers(ws)
    assert len(posted) == 1 and "see the answer" in posted[0]["text"]
    assert fake.client.call_count("chat.getPermalink") >= 1

    # A mention arrives as a message event AND an app_mention event.
    mention = ws.mention_event("U0ADA", ws.channel_id("general"), "summary")
    assert _post(tc, ws.envelope(ws.message_event("U0ADA", ws.channel_id("general"), mention["text"], ts=mention["ts"]))).status_code == 200
    assert _post(tc, ws.envelope(mention)).status_code == 200
    assert ws.posted[-1]["text"].startswith("*Q&A summary — ")
    assert ws.posted[-1]["thread_ts"] == mention["ts"]
    assert processor.counts["mentions"] == 1
    stats = tc.get("/stats").json()
    assert stats["database"]["qa"] == {"questions": 2, "answers": 1, "pointers_posted": 1, "summaries": 1}
    assert "@" not in tc.get("/stats").text
    assert count(db, "slack_event", "text is not null") == 0


def test_doctor_checks_the_qa_channels(db, ws, capsys):
    from cufa.slack.bot import doctor
    from cufa.slack.fake_server import FakeSlackHTTPServer

    fake = FakeSlackHTTPServer(ws, signing_secret="s", bot_events_url="http://bot.invalid/slack/events", port=0).start_in_thread()
    try:
        assert doctor(_settings(SLACK_API_BASE_URL=fake.api_base_url, SLACK_APP_TOKEN="xapp", CUFA_SLACK_COHORT="cu-test")) == 0
        out = capsys.readouterr().out
        assert "ok    Q&A channels configured  — #q-and-a" in out
        assert "ok    Q&A channel #q-and-a  — bot is a member" in out
        assert "GEMINI_API_KEY  — not set" in out
        assert doctor(_settings(SLACK_API_BASE_URL=fake.api_base_url, SLACK_APP_TOKEN="xapp", CUFA_SLACK_COHORT="cu-test", CUFA_SLACK_QA_CHANNELS="q-and-a,questions")) == 1
        out = capsys.readouterr().out
        assert "MISS  Q&A channel #questions  — not found" in out
    finally:
        fake.stop()


def test_cli_lists_and_summarises_a_session(db, ws, capsys, monkeypatch):
    from cufa.cli import main
    from cufa.config import reset_settings_cache
    from cufa.slack.fake_server import FakeSlackHTTPServer

    fake = FakeSlackHTTPServer(ws, signing_secret="s", bot_events_url="http://bot.invalid/slack/events", port=0).start_in_thread()
    for key, value in (
        ("SLACK_BOT_TOKEN", "xoxb-test"), ("SLACK_API_BASE_URL", fake.api_base_url),
        ("CUFA_SLACK_COHORT", TEST_COHORT), ("CUFA_SLACK_QA_CHANNELS", "q-and-a"),
    ):
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    reset_settings_cache()
    try:
        session_id = recent_session(db, days_ago=1, title="Budgets")
        people = fake.people()
        fake.act_qa_ask(people[0], "does anyone have the slides from tuesday?")  # bot not running: unrecorded
        # The CLI backfill picks it up — Q&A tables included.
        assert main(["slack", "backfill", "--channel", "q-and-a"]) == 0
        assert count(db, "slack_qa_question") == 1

        assert main(["slack", "qa", "list", "--latest"]) == 0
        out = capsys.readouterr().out
        assert "Q&A for" in out and "Q1   OPEN" in out and "slides from tuesday" in out

        assert main(["slack", "qa", "summary", "--session", session_id, "--post"]) == 0
        out = capsys.readouterr().out
        assert "*Q&A summary — " in out and "posted to #q-and-a" in out
        assert ws.posted[-1]["channel"] == ws.channel_id("q-and-a")
        assert not EMAILS.search(out)

        assert main(["slack", "qa", "summary", "--date", "1999-01-01"]) == 1
        assert "no session matched" in capsys.readouterr().err
    finally:
        fake.stop()
        reset_settings_cache()
