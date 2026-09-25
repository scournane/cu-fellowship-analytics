"""The slash commands and the button, registered on the participation bot's Bolt app.

``cufa.slack.bot`` builds the Bolt app and runs it (HTTP or Socket Mode);
this module adds to it, in :func:`register_handlers`:

* every slash command  → :func:`cufa.slack.commands.dispatch`
* ``team_join``        → :func:`cufa.slack.sync.observe_user` and a roster alert
* the *check in with me* button → the same path as ``/checkin``

and :func:`start_tick_thread` runs :func:`cufa.slack.digest.tick` every few
minutes, so welcomes, badges, summaries and the Monday staff digest need no
separate scheduler. Reminders go through ``cufa.slack.reminders`` — the one
engine — from the automation loop when the bot is up, or from this tick when
``cufa slack tick`` runs from cron. Message capture itself stays with the
participation bot.
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from typing import Any

from ..config import Settings, get_settings
from ..db import connection
from ..errors import CufaError
from ..logging_setup import get_logger
from .client import SlackClient, SlackUser, WebClientAdapter
from .commands import dispatch
from .digest import tick
from .sync import observe_user, seen_event
from .welcome import CHECK_IN_ACTION_ID, welcome_blocks

log = get_logger(__name__)


def register_handlers(bolt: Any, settings: Settings, web_client: Any) -> SlackClient:
    """Attach the commands, the join handler and the button to an existing Bolt app."""
    slack: SlackClient = WebClientAdapter(web_client)
    def _run(name: str, fn):  # type: ignore[no-untyped-def]
        """One handler body: a connection, a commit on success, a log line on failure.

        A handler that raises leaves Slack showing a spinner and the person
        with nothing. Every path here answers something, and the traceback goes
        to the log where it can be read.
        """
        try:
            with connection(settings) as conn:
                return fn(conn)
        except Exception:  # noqa: BLE001 — see above
            log.exception("slack handler %s failed", name)
            return None

    @bolt.command(re.compile(r"^/.*"))
    def _slash(ack, command, respond):  # type: ignore[no-untyped-def]
        ack()
        reply = _run(
            "slash",
            lambda conn: dispatch(
                conn,
                slack,
                command=command["command"],
                text=command.get("text") or "",
                slack_user_id=command["user_id"],
                channel_id=command.get("channel_id"),
                settings=settings,
            ),
        )
        if reply is None:
            respond(text="⚠️ That hit an error on our side. It has been logged; try again in a minute.", response_type="ephemeral")
        else:
            respond(text=reply.text, response_type="ephemeral" if reply.ephemeral else "in_channel")

    @bolt.event("team_join")
    def _team_join(event, body):  # type: ignore[no-untyped-def]
        def handle(conn):  # type: ignore[no-untyped-def]
            if seen_event(conn, body.get("event_id"), "team_join"):
                return
            user = SlackUser.from_api(event["user"])
            observe_user(conn, user, joined_at=datetime.now(timezone.utc), staff_emails=settings.slack_admins)
            if settings.slack_staff_channel:
                from .digest import post_roster_alerts

                post_roster_alerts(conn, slack, channel_id=settings.slack_staff_channel)

        _run("team_join", handle)

    @bolt.action(CHECK_IN_ACTION_ID)
    def _check_in_button(ack, body, respond):  # type: ignore[no-untyped-def]
        ack()
        reply = _run(
            "check_in_button",
            lambda conn: dispatch(conn, slack, command="/checkin", text="", slack_user_id=body["user"]["id"], settings=settings),
        )
        respond(text=reply.text if reply else "⚠️ That hit an error on our side; type `/checkin` to try again.", response_type="ephemeral")

    return slack


def start_tick_thread(
    settings: Settings, client: SlackClient, *, interval_s: int = 300, reminders: bool = True
) -> threading.Event:
    """Run ``tick`` every ``interval_s`` seconds in a daemon thread. Returns the stop flag.

    ``reminders=False`` when the automation loop is running in the same
    process — it sends reminders every minute through the same engine.
    """
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                with connection(settings) as conn:
                    # The live handler captures messages; the tick must not race it.
                    tick(conn, client, settings=settings, sync_messages=False, reminders=reminders)
            except CufaError as exc:
                log.warning("tick failed: %s", exc)
            except Exception:  # noqa: BLE001 — the loop must survive anything
                log.exception("tick crashed")
            stop.wait(interval_s)

    threading.Thread(target=loop, name="cufa-slack-tick", daemon=True).start()
    log.info("scheduler thread started; tick every %ss", interval_s)
    return stop


def check_in_button_blocks(full_name: str = "") -> list[dict[str, Any]]:
    """Block Kit for a DM that offers the check-in button (the welcome message)."""
    return welcome_blocks(full_name)


__all__ = ["CHECK_IN_ACTION_ID", "check_in_button_blocks", "register_handlers", "start_tick_thread"]
