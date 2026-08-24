"""Who may use the console, and how that is remembered between requests.

Two rules, and everything here follows from them.

**No password system.** CU staff already have Google accounts, and a password
store is a liability that has to be operated: reset flows, hashing choices,
breach response. Sign-in is Google, and the allowlist decides who is let
through afterwards.

**A dev sign-in that touches no network.** ``make demo-console`` has to let
someone click through every screen with zero Google calls, and the test suite
has to sign in without a browser. So when the fake Google client is switched on,
or when no allowlist has been configured at all, a second door opens — labelled
as a bypass everywhere it is visible, because a bypass nobody notices is a
bypass that reaches production.

The session itself is an ``itsdangerous`` signed cookie. Nothing is stored
server-side: the cookie carries the address and the signature proves the console
issued it. Tampering fails the signature; age is checked on every read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ..config import Settings
from ..logging_setup import get_logger, mask_email

log = get_logger(__name__)

COOKIE_NAME = "cufa_console_session"
SESSION_MAX_AGE = 12 * 60 * 60  # one working day; re-signing in is cheap
STATE_MAX_AGE = 10 * 60  # an OAuth round trip, generously
PKCE_COOKIE_NAME = "cufa_console_pkce"

_SESSION_SALT = "cufa-console-session"
_STATE_SALT = "cufa-console-oauth-state"
_PKCE_SALT = "cufa-console-oauth-pkce"


class NotSignedIn(Exception):
    """Raised by the guard so the app can redirect rather than 401 a browser."""


@dataclass(frozen=True)
class ConsoleUser:
    """The signed-in operator, as carried in the cookie."""

    email: str
    via: str  # 'google' or 'dev'

    @property
    def is_dev_bypass(self) -> bool:
        return self.via == "dev"

    @property
    def masked_email(self) -> str:
        """Safe to put in a log line at INFO."""
        return mask_email(self.email)


def dev_signin_available(settings: Settings) -> bool:
    """Whether the no-Google door is open.

    Open when the fake client is in use (the demo and the tests) or when no
    allowlist exists yet (a fresh checkout, where requiring Google would mean
    nobody can see the screen that explains how to configure Google).
    """
    return settings.fake_google or not settings.console_allowlist


def is_allowed(settings: Settings, email: str) -> bool:
    """Allowlist check. An address not on the list never gets a session."""
    candidate = (email or "").strip().lower()
    if not candidate or "@" not in candidate:
        return False
    if settings.console_allowlist:
        return candidate in settings.console_allowlist
    # No allowlist configured at all. The console says so loudly on every page;
    # refusing everyone instead would lock a new installation out of the very
    # screens that explain how to configure it.
    return dev_signin_available(settings)


def _session_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.console_secret, salt=_SESSION_SALT)


def _state_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.console_secret, salt=_STATE_SALT)


def _pkce_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.console_secret, salt=_PKCE_SALT)


def issue_session(settings: Settings, user: ConsoleUser) -> str:
    """Mint the signed cookie value for a user who has already been checked."""
    return _session_serializer(settings).dumps({"email": user.email, "via": user.via})


def read_session(settings: Settings, token: str | None) -> ConsoleUser | None:
    """Return the user a cookie names, or None if it is absent, stale or forged.

    The allowlist is re-checked on every read, not only at sign-in: removing
    someone from ``CUFA_CONSOLE_ALLOWLIST`` has to take effect on their next
    request, not when their cookie happens to expire.
    """
    if not token:
        return None
    try:
        payload: Any = _session_serializer(settings).loads(token, max_age=SESSION_MAX_AGE)
    except SignatureExpired:
        return None
    except BadSignature:
        log.warning("rejected a console cookie with a bad signature")
        return None

    if not isinstance(payload, dict):
        return None
    email = str(payload.get("email") or "")
    via = str(payload.get("via") or "google")
    if not is_allowed(settings, email):
        return None
    return ConsoleUser(email=email.strip().lower(), via=via)


def sign_state(settings: Settings, payload: dict[str, Any]) -> str:
    """Sign the OAuth ``state`` parameter.

    State is doing two jobs: it is the CSRF token for the redirect, and it
    carries which flow is coming back — console sign-in or Google connection —
    because both share the single redirect URI that Google has registered.
    """
    return _state_serializer(settings).dumps(payload)


def read_state(settings: Settings, token: str | None) -> dict[str, Any] | None:
    """Verify a returned ``state``, or None if it did not come from us."""
    if not token:
        return None
    try:
        payload = _state_serializer(settings).loads(token, max_age=STATE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        log.warning("rejected an OAuth state parameter that did not verify")
        return None
    return payload if isinstance(payload, dict) else None


def sign_code_verifier(settings: Settings, code_verifier: str) -> str:
    """Sign the PKCE ``code_verifier`` for its own short-lived cookie.

    It rides in a cookie rather than as an extra key on ``state``: ``state``
    becomes a URL query parameter that Google echoes straight back, so it ends
    up in browser history and web server access logs, while a cookie only ever
    travels in a request header between the browser and this origin — a
    smaller footprint for a value that stands in for the authorization code.
    """
    return _pkce_serializer(settings).dumps(code_verifier)


def read_code_verifier(settings: Settings, token: str | None) -> str | None:
    """Verify a PKCE cookie value, or None if it is absent, stale or forged."""
    if not token:
        return None
    try:
        value = _pkce_serializer(settings).loads(token, max_age=STATE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        log.warning("rejected a PKCE cookie that did not verify")
        return None
    return value if isinstance(value, str) else None


__all__ = [
    "COOKIE_NAME",
    "PKCE_COOKIE_NAME",
    "SESSION_MAX_AGE",
    "ConsoleUser",
    "NotSignedIn",
    "dev_signin_available",
    "is_allowed",
    "issue_session",
    "read_code_verifier",
    "read_session",
    "read_state",
    "sign_code_verifier",
    "sign_state",
]
