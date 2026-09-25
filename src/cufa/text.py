"""String normalization, email normalization and the idempotency hash.

``normalize_answer`` is shared wherever two typed strings have to compare as
the same text regardless of case, punctuation or a phone keyboard's smart
characters — the Slack Q&A matcher uses it. ``normalize_email`` is the only
address normalization in the codebase, and deliberately a minimal one.
"""

from __future__ import annotations

import re
import unicodedata

_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_answer(value: str | None) -> str:
    """Trim, collapse whitespace, lowercase, strip punctuation.

    Unicode is normalized to NFKC first so a smart apostrophe or a full-width
    character typed on a phone keyboard does not read as a different word.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value)
    text = text.casefold()
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text)
    return text.strip()


def normalize_email(value: str | None) -> str:
    """Trim and lowercase — nothing else.

    Gmail dots and ``+suffix`` are deliberately preserved. Stripping them is a
    guess about one provider's routing, and collapsing one fellow's address into
    another's is worse than leaving an address unmatched: an unmatched address
    shows up in the review queue, a wrongly merged one never shows up at all.
    """
    if not value:
        return ""
    return value.strip().casefold()


def sha256_hex(*parts: str) -> str:
    """Stable hash over ordered parts, joined by a separator that cannot occur.

    Used for the ingest idempotency key, so the separator matters: without one,
    ``("ab", "c")`` and ``("a", "bc")`` would hash identically.
    """
    import hashlib

    joined = "\x1f".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
