"""User OAuth: connecting one CU staff account, and storing its token safely.

Trap 4: a service account cannot own a Google Form. The alternatives are
domain-wide delegation, which needs Workspace admin console access CU may not
have, and ordinary user OAuth, which needs one person to click through a consent
screen once. This module implements the second — and the forms end up owned by
that staff member's Drive, which is where CU wants its work product anyway.

The refresh token is encrypted before it is written and decrypted only in
memory. A ``select * from google_credential`` returns ciphertext.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg

from ..config import Settings, get_settings
from ..crypto import decrypt_secret, encrypt_secret
from ..db import execute, fetch_one
from ..errors import ConfigError, GoogleNotConnected
from ..logging_setup import get_logger, mask_email
from .base import SCOPES

log = get_logger(__name__)

TOKEN_URI = "https://oauth2.googleapis.com/token"
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"


@dataclass(frozen=True)
class CredentialStatus:
    """What the console shows on the Connect Google screen."""

    connected: bool
    account_email: str | None = None
    scopes: tuple[str, ...] = ()
    connected_at: datetime | None = None
    last_refreshed_at: datetime | None = None

    @property
    def has_required_scopes(self) -> bool:
        return set(SCOPES).issubset(set(self.scopes))


def client_config(settings: Settings) -> dict[str, Any]:
    """The installed-app client config google-auth-oauthlib expects."""
    if not settings.google_client_id or not settings.google_client_secret:
        raise ConfigError(
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are not set. Create an "
            "OAuth client as described in docs/setup/google-cloud.md, then add "
            "both to .env."
        )
    return {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": AUTH_URI,
            "token_uri": TOKEN_URI,
            "redirect_uris": [settings.google_redirect_uri],
        }
    }


def build_flow(settings: Settings | None = None, *, state: str | None = None) -> Any:
    """Construct the OAuth flow for the console's Connect screen."""
    from google_auth_oauthlib.flow import Flow

    settings = settings or get_settings()
    flow = Flow.from_client_config(
        client_config(settings),
        scopes=list(SCOPES) + ["openid", "https://www.googleapis.com/auth/userinfo.email"],
        state=state,
    )
    flow.redirect_uri = settings.google_redirect_uri
    return flow


def authorization_url(
    settings: Settings | None = None, *, state: str | None = None
) -> tuple[str, str, str]:
    """Return ``(url, state, code_verifier)`` for the consent screen.

    ``access_type=offline`` plus ``prompt=consent`` is what actually yields a
    refresh token; without both, a re-consent returns only an access token and
    the connection silently stops working an hour later.

    The verifier is PKCE's proof that whoever exchanges the code for a token is
    the same party that started this request. Flow generates it internally the
    moment ``authorization_url()`` runs, and that only lives on this ``flow``
    object — the token exchange happens on a separate later request, against a
    freshly built Flow, so the caller must carry this value there itself.
    """
    flow = build_flow(settings, state=state)
    url, returned_state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )
    return url, returned_state, flow.code_verifier


def store_credential(
    conn: psycopg.Connection,
    *,
    account_email: str,
    refresh_token: str,
    scopes: list[str] | tuple[str, ...],
    settings: Settings | None = None,
) -> None:
    """Encrypt and upsert the connected account's refresh token."""
    settings = settings or get_settings()
    key = settings.require_encryption_key()
    ciphertext = encrypt_secret(refresh_token, key)

    execute(
        conn,
        """
        insert into google_credential (account_email, refresh_token_enc, scopes, connected_at)
        values (%s, %s, %s, now())
        on conflict (account_email) do update
           set refresh_token_enc = excluded.refresh_token_enc,
               scopes            = excluded.scopes,
               connected_at      = now(),
               revoked_at        = null
        """,
        (account_email.strip().lower(), ciphertext, list(scopes)),
    )
    log.info("google account connected account=%s scopes=%d", mask_email(account_email), len(scopes))


def credential_status(conn: psycopg.Connection) -> CredentialStatus:
    """Read the current connection state without decrypting anything."""
    row = fetch_one(
        conn,
        """
        select account_email, scopes, connected_at, last_refreshed_at
          from google_credential
         where revoked_at is null
         order by connected_at desc
         limit 1
        """,
    )
    if not row:
        return CredentialStatus(connected=False)
    return CredentialStatus(
        connected=True,
        account_email=row["account_email"],
        scopes=tuple(row["scopes"] or ()),
        connected_at=row["connected_at"],
        last_refreshed_at=row["last_refreshed_at"],
    )


def load_credentials(conn: psycopg.Connection, settings: Settings | None = None) -> Any:
    """Rebuild a live ``google.oauth2.credentials.Credentials`` from storage."""
    from google.oauth2.credentials import Credentials

    settings = settings or get_settings()
    row = fetch_one(
        conn,
        """
        select account_email, refresh_token_enc, scopes
          from google_credential
         where revoked_at is null
         order by connected_at desc
         limit 1
        """,
    )
    if not row:
        raise GoogleNotConnected(
            "No Google account is connected. Run `cufa google connect`, or open "
            "the console and use the Connect Google screen."
        )

    refresh_token = decrypt_secret(row["refresh_token_enc"], settings.require_encryption_key())
    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=list(row["scopes"] or SCOPES),
    )

    from google.auth.transport.requests import Request

    credentials.refresh(Request())
    execute(
        conn,
        "update google_credential set last_refreshed_at = now() where account_email = %s",
        (row["account_email"],),
    )
    return credentials


def disconnect(conn: psycopg.Connection) -> int:
    """Mark every stored credential revoked.

    The row is kept rather than deleted so that "who connected this, and when"
    survives a disconnect — but the ciphertext is cleared, because a revoked
    token has no reason to remain readable.
    """
    return execute(
        conn,
        """
        update google_credential
           set revoked_at = now(),
               refresh_token_enc = '\\x'::bytea
         where revoked_at is null
        """,
    )
