"""Turning "Marisol and Kestrel!!" into fellow ids — conservatively.

Field 5 asks who helped you today. It is free text, optional, and answered by a
teenager on a phone, so it arrives as anything: one name, three names, a
nickname, a staff member, a guest speaker, an emoji.

The resolution rules are deliberately timid, and the reason is asymmetric cost:

* **Ambiguity is never resolved automatically.** Two fellows named Jordan means
  ``unresolved`` and a review-queue entry, never a coin flip. A wrong link is
  worse than no link, because an unlinked fragment sits visibly in a queue while
  a wrongly linked one is invisible — it attributes someone's praise to a person
  who did not earn it, and nothing ever surfaces the mistake.
* **A name matching nobody is legal, not an error.** Guest speakers, teachers,
  a sibling who helped with the homework, a person outside the cohort entirely.
  Recording ``unresolved`` and moving on is the correct outcome, not a parse
  failure.
* **Matching is scoped to the cohort.** A first name that is unique this year and
  shared with last year's roster is unique for this purpose.

**Out of scope, deliberately:** any leaderboard, ranking, points total, streak
or public display. Shoutouts are collected and resolved, and that is all. When
gamification is designed later, the research is explicit that recognition should
be ranked by **giving, not receiving** — ranking on recognition received builds
a popularity contest and rewards the already-visible. See ADR-028.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

import psycopg

from .db import execute, fetch_all, fetch_one
from .logging_setup import get_logger

log = get_logger(__name__)

MATCH_EXACT = "exact_name"
MATCH_MANUAL = "manual"
MATCH_UNRESOLVED = "unresolved"

# Separators, in one expression: commas, ampersands, newlines, semicolons,
# slashes, and the word "and" on its own. `\band\b` rather than "and" so
# Alexander, Sandy and Amanda survive intact — the whole point of the split is to
# find several people, not to cut one person in half.
_SPLIT_RE = re.compile(r"[,;/&\n\r]+|\band\b|\+", flags=re.IGNORECASE)

_WS_RE = re.compile(r"\s+")
# Trailing decoration: "Marisol!!", "@kestrel", "- Lorne". Stripped for matching
# only; the raw fragment is stored exactly as typed.
_EDGE_JUNK_RE = re.compile(r"^[\s\-–—@:*\"'“”‘’.]+|[\s\-–—@:*\"'“”‘’.!?]+$")


def split_names(value: str | None) -> list[str]:
    """Split a shoutout answer into the fragments a person meant as names.

    Returns the fragments **as typed**, minus surrounding punctuation. Empty
    fragments are dropped: "Marisol and " is one name, not one name and a blank.
    """
    if not value or not value.strip():
        return []
    fragments = []
    for piece in _SPLIT_RE.split(value):
        cleaned = _EDGE_JUNK_RE.sub("", piece or "").strip()
        if cleaned:
            fragments.append(cleaned)
    return fragments


def normalize_name(value: str | None) -> str:
    """Trim, collapse whitespace, casefold, and normalize unicode.

    NFKC first so a name typed with a full-width character or a combining accent
    on a phone keyboard compares equal to the roster's version of it.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value)
    text = _WS_RE.sub(" ", text)
    return text.strip().casefold()


@dataclass(frozen=True)
class Resolution:
    """What one fragment resolved to, and how confident that is."""

    raw_text: str
    fellow_id: str | None
    match_method: str
    confidence: float | None
    #: Populated when a fragment matched more than one fellow. Recorded so the
    #: review screen can show a human what the choice actually was rather than
    #: making them search the roster.
    candidates: tuple[str, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.fellow_id is not None


def roster_index(conn: psycopg.Connection, cohort_id: str | None) -> dict[str, list[dict[str, Any]]]:
    """Two lookup tables in one: normalized full name, and normalized first name.

    Both map to *lists*, because a shared first name is the normal case and a
    shared full name is a real one. Nothing about this collapses to a single
    answer before the ambiguity has been seen.
    """
    rows = fetch_all(
        conn,
        """
        select fellow_id, full_name, cohort_id
          from fellow
         where (%s::text is null or cohort_id = %s::text)
        """,
        (cohort_id, cohort_id),
    )

    index: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        full = normalize_name(row["full_name"])
        if full:
            index.setdefault(f"full:{full}", []).append(row)
            first = full.split(" ")[0]
            if first:
                index.setdefault(f"first:{first}", []).append(row)
    return index


def resolve_fragment(fragment: str, index: dict[str, list[dict[str, Any]]]) -> Resolution:
    """Match one fragment against the cohort roster.

    Full name first, then first name. A full-name hit beats a first-name hit even
    when the first name is ambiguous — "Marisol Mossgate" is unambiguous whatever
    else is on the roster.
    """
    normalized = normalize_name(fragment)
    if not normalized:
        return Resolution(fragment, None, MATCH_UNRESOLVED, None)

    for key in (f"full:{normalized}", f"first:{normalized}"):
        matches = index.get(key) or []
        if len(matches) == 1:
            return Resolution(fragment, matches[0]["fellow_id"], MATCH_EXACT, 1.0)
        if len(matches) > 1:
            # Two people answer to this. Guessing which one is not a 50/50 bet
            # worth taking — the loser never finds out.
            return Resolution(
                fragment,
                None,
                MATCH_UNRESOLVED,
                None,
                candidates=tuple(sorted(m["fellow_id"] for m in matches)),
            )

    # Matched nobody. Legal: guest speakers and staff get thanked too.
    return Resolution(fragment, None, MATCH_UNRESOLVED, None)


def resolve_text(
    text: str | None, index: dict[str, list[dict[str, Any]]]
) -> list[Resolution]:
    """Split an answer and resolve each fragment independently."""
    return [resolve_fragment(fragment, index) for fragment in split_names(text)]


def record_shoutouts(
    conn: psycopg.Connection,
    checkin_b_id: str,
    text: str | None,
    index: dict[str, list[dict[str, Any]]],
) -> list[Resolution]:
    """Write one row per extracted name. A blank answer writes nothing.

    Blank writes nothing rather than writing an empty row: the shoutout field is
    optional, and "did not name anyone" is the absence of data, not a datum.
    """
    resolutions = resolve_text(text, index)
    for resolution in resolutions:
        execute(
            conn,
            """
            insert into peer_shoutout
                (checkin_b_id, raw_text, named_fellow_id, match_method, confidence)
            values (%s, %s, %s, %s, %s)
            """,
            (
                checkin_b_id,
                resolution.raw_text,
                resolution.fellow_id,
                resolution.match_method,
                resolution.confidence,
            ),
        )
    if resolutions:
        log.info(
            "shoutouts recorded checkin_b=%s names=%d resolved=%d",
            checkin_b_id,
            len(resolutions),
            sum(1 for r in resolutions if r.resolved),
        )
    return resolutions


def review_queue(
    conn: psycopg.Connection, cohort_id: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    """Fragments awaiting a human, oldest first."""
    return fetch_all(
        conn,
        """
        select * from v_shoutout_review
         where (%s::text is null or cohort_id = %s::text)
         order by created_at asc
         limit %s
        """,
        (cohort_id, cohort_id, limit),
    )


def link(
    conn: psycopg.Connection, shoutout_id: str, fellow_id: str, *, by_email: str
) -> dict[str, Any]:
    """Attach an unresolved fragment to a fellow. Records who decided.

    A manual link carries the resolving human's identity because it is a
    judgment, not a computation — six months later "why is this Kestrel and not
    Kestrel B?" has to have an answerable owner.
    """
    fellow = fetch_one(
        conn, "select fellow_id, full_name from fellow where fellow_id = %s", (fellow_id,)
    )
    if fellow is None:
        raise LookupError(f"No fellow with id {fellow_id}")

    row = fetch_one(
        conn,
        """
        update peer_shoutout
           set named_fellow_id = %s,
               match_method    = 'manual',
               confidence      = 1.0,
               resolved_by     = %s,
               resolved_at     = now()
         where shoutout_id = %s
        returning shoutout_id, checkin_b_id, raw_text, named_fellow_id
        """,
        (fellow_id, (by_email or "").strip().lower() or None, shoutout_id),
    )
    if row is None:
        raise LookupError(f"No shoutout with id {shoutout_id}")
    log.info("shoutout linked shoutout=%s by=%s", shoutout_id, "<redacted>")
    return row


def unlink(conn: psycopg.Connection, shoutout_id: str) -> None:
    """Put a fragment back in the queue. Used when a manual link was wrong."""
    execute(
        conn,
        """
        update peer_shoutout
           set named_fellow_id = null,
               match_method    = 'unresolved',
               confidence      = null,
               resolved_by     = null,
               resolved_at     = null
         where shoutout_id = %s
        """,
        (shoutout_id,),
    )


def candidates_for(
    conn: psycopg.Connection, raw_text: str, cohort_id: str | None
) -> list[dict[str, Any]]:
    """Roster entries a fragment could plausibly be, for the review screen.

    Advisory only — it populates a picker, it never links anything. The human
    chooses; this just saves them scrolling a roster of thirty.
    """
    normalized = normalize_name(raw_text)
    if not normalized:
        return []
    first_token = normalized.split(" ")[0]
    return fetch_all(
        conn,
        """
        select fellow_id, full_name
          from fellow
         where (%s::text is null or cohort_id = %s::text)
           and (lower(full_name) like %s or lower(full_name) like %s)
         order by full_name
         limit 25
        """,
        (cohort_id, cohort_id, f"%{normalized}%", f"{first_token}%"),
    )


__all__ = [
    "MATCH_EXACT",
    "MATCH_MANUAL",
    "MATCH_UNRESOLVED",
    "Resolution",
    "candidates_for",
    "link",
    "normalize_name",
    "record_shoutouts",
    "resolve_fragment",
    "resolve_text",
    "review_queue",
    "roster_index",
    "split_names",
    "unlink",
]
