"""Signed links to the fellow dashboard.

A fellow has no console account — the console allowlist is CU staff. So the
bot hands out a link that carries a signed, expiring token naming one fellow
id. The console verifies the signature with the same secret it signs its
session cookie with, and shows that fellow's data and nothing else. The token
is not a password: it expires, it names one person, and it can be re-issued
with ``/dashboard`` at any time.
"""

from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ..config import Settings

_SALT = "cufa-fellow-dashboard"
TOKEN_MAX_AGE = 7 * 24 * 60 * 60


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.console_secret, salt=_SALT)


def issue_token(settings: Settings, fellow_id: str) -> str:
    return _serializer(settings).dumps({"fellow_id": fellow_id})


def read_token(settings: Settings, token: str) -> str | None:
    """The fellow id the token names, or None when it is bad or expired."""
    try:
        payload = _serializer(settings).loads(token, max_age=TOKEN_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    fellow_id = payload.get("fellow_id") if isinstance(payload, dict) else None
    return str(fellow_id) if fellow_id else None


def fellow_dashboard_url(settings: Settings, fellow_id: str) -> str:
    return f"{settings.public_base_url}/me/{issue_token(settings, fellow_id)}"


__all__ = ["TOKEN_MAX_AGE", "fellow_dashboard_url", "issue_token", "read_token"]
