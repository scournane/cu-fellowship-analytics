"""Telling simulated forms apart from real ones, before Google is asked.

``make demo`` resets the working database and fills it with forms created by
``FakeGoogleClient``. Those rows are indistinguishable from real ones to every
query in this codebase — same table, same columns — right up until somebody
connects a real Google account and the console asks Google for
``fake-form-0001``. Google answers ``404 Requested entity was not found``, which
is true and completely unhelpful: it names neither the form, nor the account,
nor the fact that the id was never real in the first place.

This module makes that state detectable **without an API call**, so the error
can explain itself. The check is cheap and worth running before every use of a
stored form id, because the alternative is a 404 that reads like a Google
outage.

The mirror case matters just as much: a real form id used while
``CUFA_FAKE_GOOGLE=1`` is set produces the same bare 404 from the fake, and the
fix is the opposite one.
"""

from __future__ import annotations

from typing import Any

from .errors import FormUnreachable

#: Every id ``FakeGoogleClient`` mints starts with this. It is not a valid
#: Google form id and cannot collide with one.
FAKE_FORM_PREFIX = "fake-form-"


def is_simulated_form_id(form_id: str | None) -> bool:
    """Whether this id came from the fake client rather than from Google."""
    return bool(form_id) and str(form_id).startswith(FAKE_FORM_PREFIX)


def client_is_fake(client: Any) -> bool:
    """Whether a client is the in-memory fake.

    Reads the ``is_fake`` flag both implementations declare, rather than
    inspecting the class name — a rename should not silently turn this check
    off.
    """
    return bool(getattr(client, "is_fake", False))


def describe_mismatch(form_id: str, client: Any, *, what: str, account: str | None = None) -> str | None:
    """The message for a form/client mismatch, or None when they agree."""
    simulated_form = is_simulated_form_id(form_id)
    fake_client = client_is_fake(client)

    if simulated_form == fake_client:
        return None

    if simulated_form and not fake_client:
        who = account or "the connected Google account"
        return (
            f"{what} is {form_id!r}, which was created by the demo's fake Google "
            f"client. It does not exist in Google Drive, so {who} cannot open it.\n"
            "\n"
            "This happens when `make demo` and the console share one database: the "
            "demo resets that database and fills it with simulated forms, and they "
            "are still there when you connect a real Google account afterwards.\n"
            "\n"
            "Nothing has been sent to Google. Two ways forward:\n"
            "\n"
            "  * Start clean — drops the demo's data entirely:\n"
            "        cufa db reset\n"
            "\n"
            "  * Keep the data and make a real template instead:\n"
            "        cufa template replace --part a\n"
            "        cufa template replace --part b\n"
            "    Each one creates a fresh form in Drive; you then do the one manual\n"
            "    Verified step on it and press Verify, exactly as on day one.\n"
            "\n"
            "Session forms left over from the demo repair themselves: provisioning "
            "notices a simulated form and copies a real one in its place."
        )

    return (
        f"{what} is {form_id!r}, which is a real Google form, but CUFA_FAKE_GOOGLE "
        "is set so no Google call will be made and the fake client has never heard "
        "of it.\n"
        "\n"
        "Either unset CUFA_FAKE_GOOGLE in .env to use the real account, or run "
        "`cufa db reset` to start from an empty database in simulation mode."
    )


def require_usable_form(
    form_id: str, client: Any, *, what: str, account: str | None = None
) -> None:
    """Raise ``FormUnreachable`` when a stored id cannot belong to this client."""
    message = describe_mismatch(form_id, client, what=what, account=account)
    if message is not None:
        raise FormUnreachable(message)


def explain_google_404(form_id: str, client: Any, *, what: str, account: str | None = None) -> str:
    """The message for a 404 that the provenance check did not predict.

    Reached when the id looks right for this client and Google still says the
    form is gone — deleted from Drive, or owned by a different account than the
    one now connected. Both are recoverable, and neither is obvious from
    "Requested entity was not found".
    """
    mismatch = describe_mismatch(form_id, client, what=what, account=account)
    if mismatch is not None:
        return mismatch

    who = account or "the connected account"
    return (
        f"Google says {what.lower()} — form {form_id} — does not exist.\n"
        "\n"
        f"The id is well formed, so this is not leftover demo data. Either the form "
        f"was deleted from Drive, or it belongs to a Google account other than {who}.\n"
        "\n"
        "To recover:\n"
        "\n"
        "  * If the form was deleted, check Drive's bin first — restoring it keeps "
        "every response already collected on it.\n"
        "  * If it belonged to someone else, either transfer ownership to "
        f"{who} in Drive, or reconnect as the account that owns it "
        "(`cufa google disconnect`, then `cufa google connect`).\n"
        "  * If it is genuinely gone, make a new template with "
        "`cufa template replace --part a` (or `--part b`) and do the one manual "
        "Verified step on it. Responses already ingested are unaffected — they live "
        "in this database, not in the form."
    )


__all__ = [
    "FAKE_FORM_PREFIX",
    "client_is_fake",
    "describe_mismatch",
    "explain_google_404",
    "is_simulated_form_id",
    "require_usable_form",
]
