"""Slack activity.

Reads a standard Slack workspace export: one directory per channel, one
JSON file per day inside it. Counts messages a Fellow sent and reactions
they gave. Channel names are recorded so a future definition of
participation can weight #introductions differently from #general.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from ..model import RawEvent

# Subtypes that are Slack talking about itself, not a person participating.
IGNORED_SUBTYPES = {
    "channel_join", "channel_leave", "channel_topic", "channel_purpose",
    "channel_name", "bot_message", "channel_archive", "tombstone",
}


def _ts(raw: str) -> dt.datetime:
    return dt.datetime.fromtimestamp(float(raw), tz=dt.timezone.utc)


def read(export_dir: Path | str) -> list[RawEvent]:
    """Walk a Slack export directory and emit message + reaction events."""
    export_dir = Path(export_dir)
    events: list[RawEvent] = []

    for channel_dir in sorted(p for p in export_dir.iterdir() if p.is_dir()):
        channel = channel_dir.name
        for day_file in sorted(channel_dir.glob("*.json")):
            messages = json.loads(day_file.read_text(encoding="utf-8"))
            for msg in messages:
                if msg.get("subtype") in IGNORED_SUBTYPES:
                    continue
                user = msg.get("user")
                ts = msg.get("ts")
                if not user or not ts:
                    continue
                when = _ts(ts)
                events.append(RawEvent(
                    "slack", user, "slack_message", when,
                    meta={"channel": channel,
                          "thread": bool(msg.get("thread_ts")),
                          "chars": len(msg.get("text", ""))},
                ))
                # A reaction is credited to the person who gave it, stamped
                # at the reacted-to message. Slack's export does not record
                # when the reaction itself was added.
                for reaction in msg.get("reactions", []) or []:
                    for reactor in reaction.get("users", []) or []:
                        events.append(RawEvent(
                            "slack", reactor, "slack_reaction", when,
                            meta={"channel": channel, "emoji": reaction.get("name")},
                        ))
    return events
