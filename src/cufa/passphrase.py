"""Choosing a passphrase, and warning about the ways a choice goes wrong.

The passphrase is the only part of a check-in that a person not in the room
cannot produce. Three failure modes make it stop working, and all three are
choices made when the session is created rather than problems at submission
time:

  * **Homophones.** A fellow who *heard* the word and types the other spelling
    is present and gets marked wrong. ``their``/``there``, ``flour``/``flower``.
  * **Words from the materials.** If the word appears in the slides or the
    reading, someone who never attended can guess it from the materials they
    were sent.
  * **Reuse.** A word used in week 2 is known to everyone in week 5, including
    whoever did not come to week 5.

The curated list below is filtered for the first: no word on it has a common
English homophone or near-homophone. The other two are warnings the console
raises, because only a human knows what is in this week's slides.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import psycopg

from .db import fetch_all
from .text import normalize_answer

GUIDANCE = (
    "One word, roughly 5–10 letters. Avoid homophones (their/there, "
    "flour/flower) — a fellow who heard the word and typed the other spelling "
    "was still in the room. Avoid words that appear in this week's slides or "
    "readings, which are guessable from the materials. Never reuse a word from "
    "an earlier session."
)

ACCESSIBILITY_REMINDER = (
    "Say the passphrase ALOUD and DISPLAY it on screen. Audio only excludes "
    "deaf and hard-of-hearing fellows and anyone whose audio drops. Showing it "
    "widens who could copy it down — which is exactly why the passphrase is one "
    "signal among several and never proof on its own."
)


@lru_cache(maxsize=1)
def wordlist() -> tuple[str, ...]:
    """The curated list, loaded once."""
    path = Path(__file__).with_name("data") / "passphrase_words.txt"
    words = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    return tuple(w for w in words if w)


def suggest(count: int = 1) -> list[str]:
    """Suggest passphrases using the OS CSPRNG.

    ``secrets`` rather than ``random`` — not because an attacker is modelling
    our PRNG, but because a seeded generator would eventually repeat a word
    across sessions, which is the exact failure the reuse warning exists for.
    """
    words = wordlist()
    if count >= len(words):
        return list(words)
    chosen: list[str] = []
    seen: set[str] = set()
    while len(chosen) < count:
        word = secrets.choice(words)
        if word not in seen:
            seen.add(word)
            chosen.append(word)
    return chosen


@dataclass(frozen=True)
class ReuseWarning:
    """A previous session in the cohort used this word."""

    session_id: str
    title: str
    scheduled_at_utc: object

    def message(self) -> str:
        when = getattr(self.scheduled_at_utc, "date", lambda: self.scheduled_at_utc)()
        return (
            f"This passphrase was already used for “{self.title}” ({when}). "
            "Everyone who attended that session knows it, including anyone who "
            "misses this one."
        )


def check_reuse(
    conn: psycopg.Connection,
    cohort_id: str,
    passphrase: str | None,
    *,
    exclude_session_id: str | None = None,
) -> list[ReuseWarning]:
    """Find earlier sessions in the cohort using the same passphrase.

    Compared on the normalized form, so ``Justice`` and ``justice.`` count as
    reuse — they are the same word to a fellow typing it.
    """
    normalized = normalize_answer(passphrase)
    if not normalized:
        return []

    rows = fetch_all(
        conn,
        """
        select session_id, title, scheduled_at_utc, passphrase
          from "session"
         where cohort_id = %s
           and passphrase is not null
           and (%s::uuid is null or session_id <> %s::uuid)
         order by scheduled_at_utc
        """,
        (cohort_id, exclude_session_id, exclude_session_id),
    )
    return [
        ReuseWarning(
            session_id=str(row["session_id"]),
            title=row["title"],
            scheduled_at_utc=row["scheduled_at_utc"],
        )
        for row in rows
        if normalize_answer(row["passphrase"]) == normalized
    ]
