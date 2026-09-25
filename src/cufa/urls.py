"""Small URL validation shared by staff-authored reminder links."""

from __future__ import annotations

from urllib.parse import urlsplit


def optional_http_url(value: str | None, *, label: str = "URL") -> str | None:
    """Return a stripped HTTP(S) URL, or ``None`` for an empty value."""
    clean = (value or "").strip()
    if not clean:
        return None
    parsed = urlsplit(clean)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{label} must be a complete http:// or https:// URL.")
    return clean
