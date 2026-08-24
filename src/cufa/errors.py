"""Failure modes that callers are expected to handle differently.

Each of these exists because something must fail *loudly*. The Google traps in
docs/google-api-traps.md all fail silently by default — a form that accepts no
responses still returns 200 and still resolves in a browser — so the codebase
converts each one into an exception at the point it is detected.
"""

from __future__ import annotations


class CufaError(Exception):
    """Base for every error this package raises deliberately."""


class ConfigError(CufaError):
    """Required configuration is missing or unusable."""


class DatabaseUnreachable(CufaError):
    """The local Postgres is not answering.

    Almost always means the Supabase stack is not running (or Docker is not),
    so the message points at that rather than at a psycopg traceback.
    """


class GoogleNotConnected(CufaError):
    """No usable Google credential is stored."""


class TemplateNotVerified(CufaError):
    """Trap 2: the template form's email collection is not confirmed VERIFIED.

    Provisioning is blocked entirely. Forms copied from an unverified template
    collect responder-typed addresses, which defeats the premise of the design
    while looking like it works.
    """


class PublishVerificationFailed(CufaError):
    """Trap 1: a form was created but did not read back as published.

    Since 2026-07-01 API-created forms start unpublished and refuse every
    submission. The link resolves, so nothing looks wrong until no responses
    arrive.
    """


class EmailCollectionRejected(CufaError):
    """Trap 2: the API rejected an emailCollectionType update.

    Raised so the caller can abandon the attempt instead of recording a
    half-provisioned form as ready.
    """


class FormUnreachable(CufaError):
    """A stored form id cannot be read by the client that is connected.

    Two causes, and both come back from Google as a bare 404 that says nothing
    useful:

    * **Simulated state in a real run.** ``make demo`` writes forms created by
      ``FakeGoogleClient`` into whatever database ``CUFA_DATABASE_URL`` points
      at. Connect a real Google account afterwards and the console asks Google
      for ``fake-form-0001``, which has never existed.
    * **A form that is genuinely gone**, or that belongs to a different Google
      account than the one now connected.

    Detected before the API call where possible, so the message can say which of
    the two it is instead of forwarding "Requested entity was not found".
    """


class AmbiguousSession(CufaError):
    """A timestamp fell inside more than one session window."""


class AiUnavailable(CufaError):
    """Tier 2 could not run: no key, no network, or quota exhausted.

    Never fatal. The caller degrades to needs_review.
    """
