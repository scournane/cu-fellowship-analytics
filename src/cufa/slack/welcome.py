"""The one DM every fellow gets when the bot first knows who they are.

It says what the bot does, how to switch each part of it off, and carries the
*check in with me* button. Sent once per Slack account, recorded on the row,
so a re-sync never repeats it. Sent only to accounts that resolve to an
active fellow — a staffer or a stranger gets nothing.
"""

from __future__ import annotations

from typing import Any

import psycopg

from ..db import execute, fetch_all
from ..logging_setup import get_logger
from .client import SlackApiError, SlackClient

log = get_logger(__name__)

CHECK_IN_ACTION_ID = "cufa_check_in_request"


def welcome_text(full_name: str) -> str:
    first = (full_name or "there").split(" ")[0]
    return (
        f"Hi {first} 👋 I'm the fellowship bot.\n"
        "• I'll DM you a reminder 24 hours, 1 hour and 10 minutes before each session, with the Zoom link, "
        "and before each assignment is due. `/reminders` changes or stops them.\n"
        "• I keep track of badges and streaks for you, privately. `/badges` shows them; `/badges off` stops the messages.\n"
        "• `/me` shows your own attendance and activity; `/dashboard` gives you a private link with an export button.\n"
        "• Unsure about something, or want a staff member to reach out? Press the button below or type `/checkin`.\n"
        "Please use your real name on Zoom so your attendance is recorded."
    )


def welcome_blocks(full_name: str) -> list[dict[str, Any]]:
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": welcome_text(full_name)}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Check in with me"},
                    "action_id": CHECK_IN_ACTION_ID,
                    "style": "primary",
                }
            ],
        },
    ]


def send_welcomes(conn: psycopg.Connection, client: SlackClient, *, cohort_id: str) -> int:
    """DM every newly resolved fellow who has not been welcomed. Returns how many."""
    pending = fetch_all(
        conn,
        """
        select u.slack_user_id, u.full_name
          from v_slack_user_resolved u
          join slack_user s on s.slack_user_id = u.slack_user_id
         where u.cohort_id = %s and u.fellow_id is not null and u.fellow_status = 'active'
           and not u.is_bot and not u.deleted and s.welcomed_at is null
         order by u.full_name
        """,
        (cohort_id,),
    )
    sent = 0
    for row in pending:
        try:
            dm = client.open_dm(row["slack_user_id"])
            client.post_message(dm, welcome_text(row["full_name"]), blocks=welcome_blocks(row["full_name"]))
        except SlackApiError as exc:
            log.warning("welcome DM to %s failed: %s", row["slack_user_id"], exc.error)
            continue
        execute(conn, "update slack_user set welcomed_at = now() where slack_user_id = %s", (row["slack_user_id"],))
        sent += 1
    return sent


__all__ = ["CHECK_IN_ACTION_ID", "send_welcomes", "welcome_blocks", "welcome_text"]
