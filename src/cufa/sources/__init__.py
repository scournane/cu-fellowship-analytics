"""Source adapters.

Each adapter's only job: read one export format and emit RawEvents. No
adapter knows about scoring, thresholds, or Fellows -- that keeps a change
to Slack's export format from touching anything but slack.py.
"""
from __future__ import annotations

from ..model import Event, RawEvent, Unresolved


def resolve(raws: list[RawEvent], identity_map: dict[tuple[str, str], str]
            ) -> tuple[list[Event], list[Unresolved]]:
    """Split RawEvents into those attachable to a Fellow and those not."""
    events, orphans = [], []
    for raw in raws:
        fellow_id = identity_map.get((raw.source, raw.source_user_id))
        if fellow_id is None:
            orphans.append(Unresolved(raw.source, raw.source_user_id,
                                      raw.kind, raw.occurred_at))
        else:
            events.append(Event.make(fellow_id, raw))
    return events, orphans
