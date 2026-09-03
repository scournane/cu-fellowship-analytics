"""Polls the bot posts, and the votes that come back.

A poll is a message with one button per option. Each button carries the poll
id in its ``block_id`` and the option in its ``value``; pressing one sends a
``block_actions`` payload that ``events.parse_interaction`` turns into a
``poll_vote`` observation. The question and options are staff-authored, so
storing them is not storing anything a fellow wrote.
"""

from __future__ import annotations

from typing import Any

import psycopg

from ..db import execute, fetch_one
from .events import POLL_ACTION_ID, POLL_BLOCK_PREFIX


def poll_blocks(poll_id: str, question: str, options: list[str]) -> list[dict[str, Any]]:
    """Block Kit for a poll. One ``actions`` block, one button per option."""
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{question}*"}},
        {
            "type": "actions",
            "block_id": f"{POLL_BLOCK_PREFIX}{poll_id}",
            "elements": [
                {
                    "type": "button",
                    "action_id": POLL_ACTION_ID,
                    "text": {"type": "plain_text", "text": option[:75]},
                    "value": option,
                }
                for option in options
            ],
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "Press an option to vote. Press another to change it. Results are shown as totals only."}],
        },
    ]


def create_poll(
    conn: psycopg.Connection,
    client: Any,
    *,
    team_id: str,
    channel_id: str,
    question: str,
    options: list[str],
    created_by: str | None = None,
) -> dict[str, Any]:
    """Record the poll, post it, and remember the message ts the votes will name.

    The row is written before ``chat.postMessage`` so a post that fails still
    leaves a poll on record, and the ts is filled in afterwards.
    """
    question = question.strip()
    cleaned = [o.strip() for o in options if o and o.strip()]
    if not question:
        raise ValueError("A poll needs a question.")
    if len(cleaned) < 2:
        raise ValueError("A poll needs at least two options.")
    if len(set(cleaned)) != len(cleaned):
        raise ValueError("Poll options must be distinct.")

    row = fetch_one(
        conn,
        """
        insert into slack_poll (team_id, channel_id, question, options, created_by)
        values (%s, %s, %s, %s::jsonb, %s)
        returning poll_id
        """,
        (team_id, channel_id, question, __import__("json").dumps(cleaned), created_by),
    )
    poll_id = str(row["poll_id"])
    response = client.chat_postMessage(
        channel=channel_id,
        text=f"Poll: {question}",
        blocks=poll_blocks(poll_id, question, cleaned),
    )
    ts = response.get("ts") if isinstance(response, dict) else getattr(response, "data", {}).get("ts")
    execute(conn, "update slack_poll set message_ts = %s where poll_id = %s", (ts, poll_id))
    return {"poll_id": poll_id, "channel_id": channel_id, "message_ts": ts, "question": question, "options": cleaned}


def close_poll(conn: psycopg.Connection, poll_id: str) -> bool:
    return bool(
        execute(conn, "update slack_poll set closed_at = now() where poll_id = %s and closed_at is null", (poll_id,))
    )
