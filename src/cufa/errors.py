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


class QuestionSetMissing(CufaError):
    """No Part A question set resolves for a session.

    Neither a per-session override nor a cohort default exists. Provisioning is
    blocked — dry run included — rather than falling back to some built-in list:
    a form nobody chose is exactly the kind of thing that ships unnoticed and
    then has to be explained to fellows. The message names the command (and the
    console screen) that seeds the default.
    """


class QuestionsLocked(CufaError):
    """A session's Part A questions can no longer be edited.

    Once the session's Part A form has been published, fellows may already be
    answering it. Rewriting the set then would make the stored questions
    disagree with what was actually asked — and the published form is never
    written to anyway, so the edit could not take effect. The past is kept
    exactly as it was asked.
    """


class StaleQuestionSet(CufaError):
    """An edit was based on a version that is no longer current.

    Two people editing the same set at once: the second save would silently
    discard the first. Refused instead, so the second person reloads and sees
    what changed.
    """


class InvalidQuestionSet(CufaError):
    """A question set failed validation. ``errors`` lists every problem found.

    All problems at once rather than the first one, so a staff member fixing a
    long form does not play whack-a-mole one save at a time.
    """

    def __init__(self, errors: list[str] | tuple[str, ...], message: str | None = None) -> None:
        self.errors = list(errors)
        super().__init__(
            message
            or "The question set is not valid:\n  - " + "\n  - ".join(self.errors or ["(no detail)"])
        )


class AmbiguousSession(CufaError):
    """A timestamp fell inside more than one session window."""


class AiUnavailable(CufaError):
    """An AI call could not run: no key, no network, or quota exhausted.

    Never fatal. The caller degrades — themes are skipped, a Q&A summary falls
    back to the plain digest. (Attendance no longer calls an AI at all.)
    """
