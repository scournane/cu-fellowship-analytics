"""String normalization and edit distance for passphrase comparison.

Normalization is shared by tier 1 and by the AI cache key, so a cache hit means
the same comparison, not a coincidentally similar one.
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


def levenshtein(a: str, b: str, *, max_distance: int | None = None) -> int:
    """Edit distance between two strings.

    Implemented directly rather than pulled in as a dependency: it is twenty
    lines, and every dependency is maintenance CU inherits without a data
    manager to carry it.

    ``max_distance`` short-circuits once every cell in a row exceeds the bound,
    which is the only case tier 1 cares about.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    if max_distance is not None and abs(len(a) - len(b)) > max_distance:
        return max_distance + 1

    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + 1,        # deletion
                    current[j - 1] + 1,     # insertion
                    previous[j - 1] + (ca != cb),  # substitution
                )
            )
        if max_distance is not None and min(current) > max_distance:
            return max_distance + 1
        previous = current
    return previous[-1]


def sha256_hex(*parts: str) -> str:
    """Stable hash over ordered parts, joined by a separator that cannot occur.

    Used for the ingest idempotency key, so the separator matters: without one,
    ``("ab", "c")`` and ``("a", "bc")`` would hash identically.
    """
    import hashlib

    joined = "\x1f".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
