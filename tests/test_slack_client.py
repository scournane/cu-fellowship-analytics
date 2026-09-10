"""The client that actually talks to Slack.

Everything else in the bot is tested against ``FakeSlackClient``, which is an
in-memory dictionary. That proves the logic and proves nothing about the wire:
the adapter over ``slack_sdk`` is the code that runs in production, and until
this file existed it was the one module no test touched.

Three layers, narrowest first:

1. **The protocol holds.** Both implementations answer the same members. Add a
   method to ``SlackClient`` and forget one of them, and this fails.
2. **The adapter over the in-memory fake WebClient.** Every method, including
   pagination and thread replies, which is where a hand-written loop goes wrong.
3. **The adapter over a real ``slack_sdk.WebClient`` talking HTTP** to the fake
   Slack server the demo uses. This is the layer that catches a wrong encoding,
   a wrong response key, or a structured argument the SDK will not send.

No test here reaches slack.com.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from cufa.slack.client import (
    FakeSlackClient,
    Posted,
    SlackApiError,
    SlackClient,
    SlackMessage,
    SlackUser,
    WebClientAdapter,
)
from cufa.slack.fake import FakeSlackWebClient, FakeWorkspace


def _protocol_members() -> set[str]:
    return {name for name in vars(SlackClient) if not name.startswith("_")}


@pytest.fixture
def workspace() -> FakeWorkspace:
    ws = FakeWorkspace()
    ws.add_user(email="ada@example.invalid", real_name="Ada Testcase", user_id="U0ADA")
    ws.add_user(email="bo@example.invalid", real_name="Bo Night", user_id="U0BOB")
    ws.add_user(email=None, real_name="No Address", user_id="U0NONE")
    return ws


# ---------------------------------------------------------------------------
# 1. the protocol
# ---------------------------------------------------------------------------


def test_both_implementations_answer_the_whole_protocol(workspace):
    """The fake is only a useful stand-in while it matches the real thing."""
    expected = _protocol_members()
    assert expected, "the protocol declares nothing — this test would pass vacuously"
    for implementation in (FakeSlackClient(), WebClientAdapter(FakeSlackWebClient(workspace))):
        missing = [name for name in expected if not hasattr(implementation, name)]
        assert missing == [], f"{type(implementation).__name__} is missing {missing}"


def test_the_bot_never_imports_a_second_http_client():
    """One client, one token, one base URL. The demo's fake server depends on it."""
    import cufa.slack.client as module

    source = module.__file__
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    for forbidden in ("urllib.request", "import requests", "httpx."):
        assert forbidden not in text, f"{forbidden} is back in the Slack client"


# ---------------------------------------------------------------------------
# 2. the adapter over the in-memory WebClient
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter(workspace) -> WebClientAdapter:
    # page_size 2 forces the pagination loop to run more than once.
    return WebClientAdapter(FakeSlackWebClient(workspace, page_size=2))


def test_team_id_is_read_once_and_cached(adapter, workspace):
    web = adapter._web  # noqa: SLF001 — asserting the call count is the point
    assert adapter.team_id == workspace.team_id
    assert adapter.team_id == workspace.team_id
    assert web.call_count("auth.test") == 1, "one auth.test per process, not per use"


def test_list_users_pages_and_maps_every_field(adapter, workspace):
    users = {u.id: u for u in adapter.list_users()}
    assert {"U0ADA", "U0BOB", "U0NONE"} <= set(users), "pagination dropped someone"
    assert users["U0ADA"].email == "ada@example.invalid"
    assert users["U0ADA"].real_name == "Ada Testcase"
    assert users["U0NONE"].email is None, "no address is None, not an empty string"
    assert all(isinstance(u, SlackUser) for u in users.values())


def test_get_user_returns_none_for_an_unknown_id(adapter):
    assert adapter.get_user("U0ADA").email == "ada@example.invalid"
    assert adapter.get_user("U0NOBODY") is None, "a missing user is None, not an exception"


def test_list_channels_pages_and_carries_membership(adapter, workspace):
    channels = {c.name: c for c in adapter.list_channels()}
    assert "general" in channels and "q-and-a" in channels
    assert channels["general"].is_member is True
    private = [c for c in channels.values() if c.is_private]
    assert private, "private channels are listed too — the cohort channel is one"


def test_channel_history_includes_thread_replies_in_time_order(adapter, workspace):
    channel = workspace.channel_id("general")
    parent = workspace.message_event("U0ADA", channel, "root")
    workspace.message_event("U0BOB", channel, "a reply", thread_ts=parent["ts"])
    workspace.message_event("U0ADA", channel, "later root")

    messages = adapter.channel_history(channel)
    texts = [m.text for m in messages]
    assert "a reply" in texts, "a thread reply is participation and must come back"
    assert texts == sorted(texts, key=lambda t: [float(m.ts) for m in messages if m.text == t][0])
    assert [m.ts for m in messages] == sorted(m.ts for m in messages), "ordered by ts"

    without = adapter.channel_history(channel, include_replies=False)
    assert "a reply" not in [m.text for m in without]


def test_channel_history_honours_the_watermark(adapter, workspace):
    channel = workspace.channel_id("general")
    first = workspace.message_event("U0ADA", channel, "before")
    workspace.message_event("U0BOB", channel, "after")
    fresh = adapter.channel_history(channel, oldest=first["ts"])
    assert [m.text for m in fresh] == ["after"], "the watermark is exclusive"


def test_open_dm_then_post_and_ephemeral(adapter, workspace):
    channel = adapter.open_dm("U0ADA")
    assert channel.startswith("D"), "a DM channel, not the public one"
    assert adapter.open_dm("U0ADA") == channel, "opening twice is the same conversation"

    posted = adapter.post_message(channel, "your reminder", blocks=[{"type": "section"}])
    assert isinstance(posted, Posted) and posted.channel_id == channel and posted.ts

    adapter.post_ephemeral(workspace.channel_id("general"), "U0ADA", "only you")
    outbox = workspace.posted
    assert outbox[0]["to"] == "U0ADA" and outbox[0]["blocks"] == [{"type": "section"}]
    assert outbox[1]["ephemeral_to"] == "U0ADA"


def test_a_refused_post_raises_the_package_error_not_the_sdk_one(adapter):
    """Callers catch SlackApiError from `cufa.slack.client`; the SDK's must not leak."""
    with pytest.raises(SlackApiError) as excinfo:
        adapter.post_message("C0NOPE", "into the void")
    assert "chat.postMessage" in str(excinfo.value)


def test_message_metrics_survive_the_trip(adapter, workspace):
    channel = workspace.channel_id("general")
    workspace.message_event("U0ADA", channel, "two words")
    message = adapter.channel_history(channel)[0]
    assert isinstance(message, SlackMessage)
    assert message.user == "U0ADA"
    assert message.posted_at.tzinfo is timezone.utc, "RFC3339 UTC, not a naive local time"
    assert message.posted_at > datetime(2020, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 3. the adapter over real slack_sdk, over HTTP, against the fake server
# ---------------------------------------------------------------------------


@pytest.fixture
def over_http(workspace):
    """The production stack minus slack.com: real WebClient, real HTTP, fake Slack."""
    from slack_sdk import WebClient

    from cufa.slack.fake_server import FakeSlackHTTPServer

    server = FakeSlackHTTPServer(
        workspace, signing_secret="test-signing-secret", bot_events_url="http://bot.invalid/slack/events", port=0
    ).start_in_thread()
    try:
        web = WebClient(token="xoxb-test-token", base_url=server.api_base_url)
        yield WebClientAdapter(web), workspace, server
    finally:
        server.stop()


def test_the_real_sdk_can_drive_every_method_the_bot_uses(over_http):
    adapter, workspace, _server = over_http
    assert adapter.team_id == workspace.team_id

    emails = {u.email for u in adapter.list_users()}
    assert "ada@example.invalid" in emails

    names = {c.name for c in adapter.list_channels()}
    assert "general" in names

    channel = workspace.channel_id("general")
    parent = workspace.message_event("U0ADA", channel, "over the wire")
    workspace.message_event("U0BOB", channel, "replying over the wire", thread_ts=parent["ts"])
    texts = {m.text for m in adapter.channel_history(channel)}
    assert {"over the wire", "replying over the wire"} <= texts

    assert adapter.get_user("U0ADA").real_name == "Ada Testcase"
    assert adapter.get_user("U0GHOST") is None


def test_a_reminder_reaches_a_dm_over_http_with_its_blocks_intact(over_http):
    """Blocks are a list in Python and a JSON string on the wire. Both must arrive."""
    adapter, workspace, _server = over_http
    dm = adapter.open_dm("U0ADA")
    adapter.post_message(dm, "*Lesson 1* starts in 1 hour", blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "hi"}}])
    sent = [p for p in workspace.posted if p["channel"] == dm]
    assert len(sent) == 1
    assert sent[0]["to"] == "U0ADA"
    assert sent[0]["blocks"][0]["text"]["text"] == "hi", "the blocks survived JSON encoding"


def test_an_http_error_from_slack_becomes_a_package_error(over_http):
    adapter, _workspace, _server = over_http
    with pytest.raises(SlackApiError):
        adapter.post_message("C0DOESNOTEXIST", "nope")


def test_pagination_works_over_http_too(over_http, workspace):
    """The server caps a page; the adapter must follow the cursor, not stop."""
    adapter, workspace, server = over_http
    for i in range(7):
        workspace.add_user(email=f"extra{i}@example.invalid", real_name=f"Extra {i}")
    server.client.page_size = 2
    users = adapter.list_users()
    assert len({u.id for u in users}) >= 10, f"only {len(users)} users came back; the cursor was dropped"


# ---------------------------------------------------------------------------
# 4. portability — the bot runs on whatever machine CU has
# ---------------------------------------------------------------------------


def test_no_glibc_only_date_format_anywhere_in_the_package():
    """``%-d`` works on macOS and Linux and raises ValueError on Windows.

    Caught by a scan rather than by a test per call site: the failure is a crash
    in a staff member's weekly digest, and it would only ever be noticed on the
    one machine nobody develops on.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in list((root / "src").rglob("*.py")) + list((root / "src").rglob("*.html")) + [root / "tasks.py"]:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if "%-" in line and "not portable to Windows" not in line and "glibc" not in line:
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert offenders == [], "use cufa.timeutil.short_date / short_datetime instead:\n" + "\n".join(offenders)


def test_the_portable_date_helpers_drop_the_leading_zero():
    from datetime import datetime, timezone

    from cufa.timeutil import short_date, short_datetime

    moment = datetime(2026, 9, 5, 13, 5, tzinfo=timezone.utc)
    assert short_date(moment) == "Sep 5"
    assert short_datetime(moment) == "Sep 5 13:05"
    assert short_date(datetime(2026, 12, 25, tzinfo=timezone.utc)) == "Dec 25"
