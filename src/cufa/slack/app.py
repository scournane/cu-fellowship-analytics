"""The Bolt adapter: Socket Mode in, slash commands and events out.

This is the only module that imports ``slack_bolt``, and it is imported only
by ``cufa slack serve``. Everything it does is one line into a function that
already works with the fake client:

* a slash command → :func:`cufa.slack.commands.dispatch`
* ``team_join``   → :func:`cufa.slack.sync.observe_user` (and a roster alert)
* ``message``     → :func:`cufa.slack.sync.record_message`
* the *check in with me* button → the same path as ``/checkin``

A background thread runs :func:`cufa.slack.digest.tick` every few minutes, so
reminders, summaries and the Monday digest need no separate scheduler. Run
``cufa slack tick`` from cron instead if you would rather.

Socket Mode is used because it needs no public URL: the bot dials out to
Slack, which is the right shape for a laptop or a small box behind NAT.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from ..config import Settings, get_settings
from ..db import connection
from ..errors import ConfigError, CufaError
from ..logging_setup import get_logger
from .client import RealSlackClient, SlackClient, SlackMessage, SlackUser
from .commands import dispatch
from .digest import tick
from .sync import observe_user, record_message

log = get_logger(__name__)

CHECK_IN_ACTION_ID = "cufa_check_in_request"


def _require_bolt() -> tuple[Any, Any]:
    try:
        from slack_bolt import App
        from slack_bolt.adapter.socket_mode import SocketModeHandler
    except ImportError as exc:  # pragma: no cover - depends on the extra
        raise ConfigError(
            "slack_bolt is not installed. Install the Slack extra:\n"
            '    pip install -e ".[slack]"'
        ) from exc
    return App, SocketModeHandler


def build_app(settings: Settings | None = None, *, client: SlackClient | None = None) -> Any:
    """A configured Bolt app. Handlers close over ``client`` and ``settings``."""
    settings = settings or get_settings()
    if not settings.slack_bot_token or not settings.slack_app_token:
        raise ConfigError(
            "SLACK_BOT_TOKEN and SLACK_APP_TOKEN are both required for `cufa slack serve`. "
            "See docs/setup/slack-bot.md."
        )
    App, _ = _require_bolt()
    bolt = App(token=settings.slack_bot_token, signing_secret=settings.slack_signing_secret or None)
    slack: SlackClient = client or RealSlackClient(settings.slack_bot_token)

    @bolt.command(__import__("re").compile(r"^/.*"))
    def _slash(ack, command, respond):  # type: ignore[no-untyped-def]
        ack()
        with connection(settings) as conn:
            reply = dispatch(
                conn,
                slack,
                command=command["command"],
                text=command.get("text") or "",
                slack_user_id=command["user_id"],
                channel_id=command.get("channel_id"),
                settings=settings,
            )
        respond(text=reply.text, response_type="ephemeral" if reply.ephemeral else "in_channel")

    @bolt.event("team_join")
    def _team_join(event, say):  # type: ignore[no-untyped-def]
        user = SlackUser.from_api(event["user"])
        with connection(settings) as conn:
            observe_user(conn, user, joined_at=datetime.now(timezone.utc), staff_emails=settings.slack_admins)
            if settings.slack_staff_channel:
                from .digest import post_roster_alerts

                post_roster_alerts(conn, slack, channel_id=settings.slack_staff_channel)

    @bolt.event("message")
    def _message(event):  # type: ignore[no-untyped-def]
        if event.get("subtype") in ("message_changed", "message_deleted"):
            return
        message = SlackMessage.from_api(event["channel"], event)
        with connection(settings) as conn:
            record_message(conn, message)

    @bolt.event("member_joined_channel")
    def _member_joined(event):  # type: ignore[no-untyped-def]
        return  # the periodic sync marks membership; nothing to do live

    @bolt.action(CHECK_IN_ACTION_ID)
    def _check_in_button(ack, body, respond):  # type: ignore[no-untyped-def]
        ack()
        with connection(settings) as conn:
            reply = dispatch(conn, slack, command="/checkin", text="", slack_user_id=body["user"]["id"], settings=settings)
        respond(text=reply.text, response_type="ephemeral")

    return bolt


def check_in_button_blocks() -> list[dict[str, Any]]:
    """Block Kit for a DM that offers the check-in button."""
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": "Unsure about something, or just want to talk? Press the button and a staff member will reach out."}},
        {"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "Check in with me"}, "action_id": CHECK_IN_ACTION_ID, "style": "primary"}]},
    ]


def _tick_loop(settings: Settings, client: SlackClient, interval_s: int, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            with connection(settings) as conn:
                tick(conn, client, settings=settings)
        except CufaError as exc:
            log.warning("tick failed: %s", exc)
        except Exception:  # noqa: BLE001 — the loop must survive anything
            log.exception("tick crashed")
        stop.wait(interval_s)


def serve(settings: Settings | None = None, *, tick_interval_s: int = 300) -> None:  # pragma: no cover - network
    settings = settings or get_settings()
    bolt = build_app(settings)
    _, SocketModeHandler = _require_bolt()
    client = RealSlackClient(settings.slack_bot_token or "")
    stop = threading.Event()
    worker = threading.Thread(target=_tick_loop, args=(settings, client, tick_interval_s, stop), daemon=True)
    worker.start()
    log.info("slack bot starting (socket mode); tick every %ss", tick_interval_s)
    try:
        SocketModeHandler(bolt, settings.slack_app_token).start()
    finally:
        stop.set()


__all__ = ["CHECK_IN_ACTION_ID", "build_app", "check_in_button_blocks", "serve"]
