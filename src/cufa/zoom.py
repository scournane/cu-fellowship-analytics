"""Who spoke, and how much, from a Zoom cloud-recording transcript.

Zoom writes a WebVTT file next to each cloud recording, with every cue tagged
by the speaker's *display name*. That is the whole basis of this feature and
its whole weakness: a fellow who joined as "iPhone" is invisible, which is why
the session reminder asks everyone to use their real name and why the teacher
should say it again at the top of each call.

Nothing here needs a Zoom bot, a Zoom API key, or a live meeting. Download the
``.vtt`` and run ``cufa zoom ingest --session <id> --vtt <file>``. Turns are
matched to the roster by name — exact, case-insensitive, then first+last from
the Slack profile — and unmatched names are listed for a human to link.

What comes out is *speaking share*: turns, seconds, and words per fellow,
against the session total. It says who talked most and least. It says nothing
about what they said.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg

from .db import execute, fetch_all, fetch_one
from .errors import CufaError
from .logging_setup import get_logger
from .shoutouts import normalize_name

log = get_logger(__name__)

_TIMING_RE = re.compile(
    r"^(?P<start>\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}|\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}|\d{1,2}:\d{2}[.,]\d{1,3})"
)
_VOICE_RE = re.compile(r"^\s*<v\s+(?P<name>[^>]+)>(?P<text>.*?)(?:</v>)?\s*$")
_SPEAKER_RE = re.compile(r"^\s*(?P<name>[^:<>]{1,80}?)\s*:\s*(?P<text>.*)$")
_WORD_RE = re.compile(r"[\w'’-]+", re.UNICODE)


@dataclass(frozen=True)
class Turn:
    speaker: str
    start_s: float
    end_s: float
    text: str

    @property
    def seconds(self) -> float:
        return max(0.0, self.end_s - self.start_s)

    @property
    def words(self) -> int:
        return len(_WORD_RE.findall(self.text))


def _seconds(stamp: str) -> float:
    parts = stamp.replace(",", ".").split(":")
    parts = [float(p) for p in parts]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    h, m, s = parts
    return h * 3600 + m * 60 + s


def parse_vtt(text: str) -> list[Turn]:
    """Every cue with a recognisable speaker. Cues without one are skipped."""
    turns: list[Turn] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        match = _TIMING_RE.match(lines[i].strip())
        if not match:
            i += 1
            continue
        start, end = _seconds(match["start"]), _seconds(match["end"])
        i += 1
        body: list[str] = []
        while i < len(lines) and lines[i].strip():
            body.append(lines[i].strip())
            i += 1
        if not body:
            continue
        first = _VOICE_RE.match(body[0]) or _SPEAKER_RE.match(body[0])
        if not first:
            continue
        name = (first["name"] or "").strip()
        spoken = " ".join([first["text"], *body[1:]]).replace("</v>", "").strip()
        if name:
            turns.append(Turn(speaker=name, start_s=start, end_s=end, text=spoken))
    return turns


def ingest_transcript(conn: psycopg.Connection, session_id: str, path: str | Path) -> dict[str, Any]:
    """Write the turns for one session. Idempotent per (file, cue, speaker)."""
    if fetch_one(conn, 'select 1 from "session" where session_id = %s', (session_id,)) is None:
        raise CufaError(f"No session with id {session_id}")
    raw = Path(path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    turns = parse_vtt(raw.decode("utf-8-sig", errors="replace"))
    if not turns:
        raise CufaError(f"{path} contains no cues with a speaker name. Is it a Zoom transcript (.vtt)?")
    written = 0
    for turn in turns:
        row = fetch_one(
            conn,
            """
            insert into zoom_transcript_turn
                (session_id, speaker_name, started_at_s, ended_at_s, word_count, source_sha256)
            values (%s, %s, %s, %s, %s, %s)
            on conflict do nothing
            returning turn_id
            """,
            (session_id, turn.speaker, round(turn.start_s, 3), round(turn.end_s, 3), turn.words, digest),
        )
        if row is not None:
            written += 1
    log.info("transcript ingested session=%s turns=%d written=%d", session_id, len(turns), written)
    return {"session_id": session_id, "turns": len(turns), "written": written, "sha256": digest}


@dataclass
class SpeakingShare:
    speaker_name: str
    fellow_id: str | None
    full_name: str | None
    turns: int
    seconds: float
    words: int
    share_of_seconds: float
    matched_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _name_index(conn: psycopg.Connection, cohort_id: str) -> dict[str, tuple[str, str, str]]:
    """normalized name → (fellow_id, full_name, how). Roster names and Slack names."""
    index: dict[str, tuple[str, str, str]] = {}
    for r in fetch_all(conn, "select fellow_id, full_name from fellow where cohort_id = %s", (cohort_id,)):
        index.setdefault(normalize_name(r["full_name"]), (r["fellow_id"], r["full_name"], "roster_name"))
    for r in fetch_all(
        conn,
        "select fellow_id, full_name, display_name, real_name from v_slack_user_resolved where cohort_id = %s and fellow_id is not null",
        (cohort_id,),
    ):
        for candidate in (r["display_name"], r["real_name"]):
            key = normalize_name(candidate)
            if key:
                index.setdefault(key, (r["fellow_id"], r["full_name"], "slack_name"))
    return index


def speaking_share(conn: psycopg.Connection, session_id: str) -> list[SpeakingShare]:
    session = fetch_one(conn, 'select cohort_id from "session" where session_id = %s', (session_id,))
    if session is None:
        raise CufaError(f"No session with id {session_id}")
    rows = fetch_all(
        conn,
        """
        select speaker_name, count(*) as turns,
               sum(ended_at_s - started_at_s) as seconds, sum(word_count) as words
          from zoom_transcript_turn where session_id = %s
         group by speaker_name order by seconds desc
        """,
        (session_id,),
    )
    total = float(sum(float(r["seconds"]) for r in rows)) or 1.0
    index = _name_index(conn, session["cohort_id"])
    out: list[SpeakingShare] = []
    for r in rows:
        hit = index.get(normalize_name(r["speaker_name"]))
        out.append(
            SpeakingShare(
                speaker_name=r["speaker_name"],
                fellow_id=hit[0] if hit else None,
                full_name=hit[1] if hit else None,
                turns=int(r["turns"]),
                seconds=round(float(r["seconds"]), 1),
                words=int(r["words"]),
                share_of_seconds=round(float(r["seconds"]) / total, 3),
                matched_by=hit[2] if hit else None,
            )
        )
    return out


def silent_fellows(conn: psycopg.Connection, session_id: str) -> list[dict[str, Any]]:
    """Fellows who attended (Part A) but have no transcript turn."""
    spoke = {s.fellow_id for s in speaking_share(conn, session_id) if s.fellow_id}
    attended = fetch_all(
        conn,
        "select distinct fellow_id, full_name from v_checkin_resolved where session_id = %s and status = 'attended' and fellow_id is not null order by full_name",
        (session_id,),
    )
    return [r for r in attended if r["fellow_id"] not in spoke]


def render_text(session_title: str, shares: list[SpeakingShare]) -> str:
    lines = [f"Speaking share — {session_title}", ""]
    if not shares:
        lines.append("  No transcript ingested for this session.")
        return "\n".join(lines)
    for s in shares:
        who = f"{s.full_name} ({s.fellow_id})" if s.fellow_id else f"{s.speaker_name}  [unmatched]"
        bar = "█" * round(30 * s.share_of_seconds)
        lines.append(f"  {who:<36} {bar:<30} {s.share_of_seconds * 100:5.1f}%  {s.turns:>3} turns  {s.words:>5} words")
    unmatched = [s for s in shares if not s.fellow_id]
    if unmatched:
        lines.append("")
        lines.append("  Unmatched Zoom names (ask them to use their real name, or link a Slack profile with that name):")
        for s in unmatched:
            lines.append(f"    {s.speaker_name}")
    return "\n".join(lines)


__all__ = [
    "SpeakingShare",
    "Turn",
    "ingest_transcript",
    "parse_vtt",
    "render_text",
    "silent_fellows",
    "speaking_share",
]
