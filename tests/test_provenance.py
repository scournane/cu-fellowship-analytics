"""Simulated forms in a real run, and real forms Google says are gone.

`make demo` resets the working database and fills it with forms created by
``FakeGoogleClient``. Connect a real Google account afterwards — which is what
anybody does after trying the demo — and the console asks Google for
``fake-form-0001``. Google answers ``404 Requested entity was not found``, which
is true, unhelpful, and looks like an outage.

That is not a hypothetical: it is what happened on the first real install. These
tests hold the recovery path in place.

No test here touches the network. ``_RealShapedClient`` behaves like Google
would towards ids it has never issued.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from cufa.db import execute, fetch_all, fetch_one
from cufa.errors import FormUnreachable
from cufa.google.base import (
    EMAIL_COLLECTION_VERIFIED,
    FormDefinition,
    FormRef,
    FormState,
    GoogleApiError,
    ResponsePage,
)
from cufa.google.fake import FakeGoogleClient
from cufa.provenance import (
    client_is_fake,
    describe_mismatch,
    is_simulated_form_id,
    require_usable_form,
)
from cufa.provisioning import get_session_form, provision_session
from cufa.template import (
    connected_account,
    create_template,
    get_template,
    replace_template,
    verify_template,
)

from conftest import make_session

SESSION_LOCAL = datetime(2026, 9, 15, 19, 0)


class _RealShapedClient:
    """A client that answers like Google: 404 for anything it did not issue.

    Not a subclass of the fake — the point is that ``is_fake`` is False, so the
    provenance check treats its ids as real ones and a stored ``fake-form-…``
    becomes detectable without a call.
    """

    is_fake = False

    def __init__(self) -> None:
        self.forms: dict[str, dict] = {}
        self.calls: list[str] = []
        self._next = 1

    def _mint(self) -> str:
        form_id = f"1FAIpQLSe-real-{self._next:04d}"
        self._next += 1
        return form_id

    def _require(self, form_id: str) -> dict:
        self.calls.append(form_id)
        if form_id not in self.forms:
            raise GoogleApiError("Requested entity was not found", status=404)
        return self.forms[form_id]

    def create_template(self, title: str, description: str = "") -> FormRef:
        form_id = self._mint()
        self.forms[form_id] = {
            "title": title,
            "email": EMAIL_COLLECTION_VERIFIED,
            "published": False,
            "items": [],
        }
        return FormRef(form_id, f"https://forms.gle/{form_id}", f"https://x/{form_id}/edit")

    def read_settings(self, form_id: str) -> FormState:
        form = self._require(form_id)
        return FormState(
            form_id=form_id,
            email_collection_type=form["email"],
            is_published=form["published"],
            is_accepting_responses=form["published"],
            title=form["title"],
            raw={"settings": {"emailCollectionType": form["email"]}},
        )

    def get_form(self, form_id: str) -> FormDefinition:
        self._require(form_id)
        return FormDefinition(form_id=form_id, title="", items=())

    def copy_form(self, source_form_id: str, new_title: str) -> FormRef:
        source = self._require(source_form_id)
        form_id = self._mint()
        self.forms[form_id] = dict(source, title=new_title, published=False)
        return FormRef(form_id, f"https://forms.gle/{form_id}", f"https://x/{form_id}/edit")

    def batch_update(self, form_id: str, requests: list) -> dict:
        self._require(form_id)
        return {"form": {"formId": form_id}}

    def set_publish_settings(self, form_id: str, **kwargs) -> dict:
        self._require(form_id)["published"] = True
        return {}

    def list_responses(self, form_id: str, **kwargs) -> ResponsePage:
        self._require(form_id)
        return ResponsePage(responses=())


# ---------------------------------------------------------------------------
# the check itself
# ---------------------------------------------------------------------------


def test_a_simulated_id_is_recognisable_without_calling_google():
    assert is_simulated_form_id("fake-form-0001")
    assert not is_simulated_form_id("1FAIpQLSe-real-0001")
    assert not is_simulated_form_id(None)
    assert client_is_fake(FakeGoogleClient())
    assert not client_is_fake(_RealShapedClient())


def test_a_matching_pair_produces_no_complaint():
    assert describe_mismatch("fake-form-0001", FakeGoogleClient(), what="x") is None
    assert describe_mismatch("1FAIpQLSe-x", _RealShapedClient(), what="x") is None


def test_a_simulated_form_with_a_real_client_says_where_it_came_from():
    message = describe_mismatch(
        "fake-form-0001",
        _RealShapedClient(),
        what="The stored template form",
        account="staff@civicsunplugged.org",
    )
    assert message
    assert "fake Google client" in message
    assert "staff@civicsunplugged.org" in message
    # And it names the two ways out, rather than only the diagnosis.
    assert "cufa db reset" in message
    assert "cufa template replace" in message
    assert "Nothing has been sent to Google" in message


def test_a_real_form_with_the_fake_client_says_the_opposite_thing():
    message = describe_mismatch("1FAIpQLSe-x", FakeGoogleClient(), what="The form")
    assert message
    assert "CUFA_FAKE_GOOGLE" in message


def test_require_usable_form_raises_the_message():
    with pytest.raises(FormUnreachable) as excinfo:
        require_usable_form("fake-form-0001", _RealShapedClient(), what="The template")
    assert "fake Google client" in str(excinfo.value)


# ---------------------------------------------------------------------------
# the state a first real install actually lands in
# ---------------------------------------------------------------------------


def _demo_leftovers(db) -> str:
    """Reproduce it exactly: run the demo's setup, then connect a real account."""
    fake = FakeGoogleClient()
    record = create_template(db, fake, "a")
    fake.simulate_human_sets_verified(record.form_id)
    verify_template(db, fake, "a")
    assert record.form_id.startswith("fake-form-")
    return record.form_id


def test_verifying_a_leftover_demo_template_explains_itself(db):
    """The bug as reported: `Provisioning failed: [404] Requested entity was not
    found`, with nothing to act on."""
    _demo_leftovers(db)
    execute(
        db,
        "insert into google_credential (account_email, refresh_token_enc, scopes) "
        "values (%s, %s, %s)",
        ("scournane@civicsunplugged.org", b"x", ["forms.body"]),
    )

    real = _RealShapedClient()
    with pytest.raises(FormUnreachable) as excinfo:
        verify_template(db, real, "a")

    message = str(excinfo.value)
    assert "fake Google client" in message
    assert "scournane@civicsunplugged.org" in message
    assert "cufa template replace --part a" in message
    # Detected offline: Google is never asked about a form it cannot have.
    assert real.calls == []


def test_provisioning_over_leftover_demo_state_explains_itself(db):
    _demo_leftovers(db)
    session_id = make_session(db, local=SESSION_LOCAL)

    real = _RealShapedClient()
    with pytest.raises(FormUnreachable) as excinfo:
        provision_session(db, real, session_id, part="a")
    assert "fake Google client" in str(excinfo.value)


def test_replacing_the_template_recovers_and_re_requires_the_manual_step(db):
    old_form_id = _demo_leftovers(db)
    real = _RealShapedClient()

    record = replace_template(db, real, "a")

    assert not record.form_id.startswith("fake-form-")
    assert record.form_id in real.forms
    # A NEW form, so the human Verified step is owed again — replacing must not
    # inherit the old form's green tick.
    assert record.verified_email_confirmed_at is None
    assert not record.is_verified

    # The old row is retired, not deleted: session forms copied from it still
    # point at it through template_id.
    rows = fetch_all(
        db, "select form_id, is_active from form_template order by created_at"
    )
    assert {r["form_id"]: r["is_active"] for r in rows} == {
        old_form_id: False,
        record.form_id: True,
    }
    assert get_template(db, "a").form_id == record.form_id


def test_after_replacing_and_verifying_provisioning_works(db):
    _demo_leftovers(db)
    real = _RealShapedClient()
    replace_template(db, real, "a")
    verify_template(db, real, "a")  # this client reports VERIFIED on create

    session_id = make_session(db, local=SESSION_LOCAL)
    result = provision_session(db, real, session_id, part="a")

    assert result.created
    assert not result.form_id.startswith("fake-form-")
    assert get_session_form(db, session_id, "a")["publish_verified_at"] is not None


def test_a_leftover_session_form_is_discarded_and_re_provisioned(db):
    """A session form pointing at a simulated id has nothing behind it.

    No response can be attached to a form Google never issued, so the row is
    provably worthless and clearing it is safe — unlike a real form that 404s,
    which might be in Drive's bin with responses on it.
    """
    _demo_leftovers(db)
    session_id = make_session(db, local=SESSION_LOCAL)

    fake = FakeGoogleClient()
    fake.forms.clear()
    execute(
        db,
        "insert into session_form (session_id, part, form_id, form_url, publish_verified_at) "
        "values (%s, 'a', 'fake-form-0099', 'https://x', now())",
        (session_id,),
    )
    execute(
        db,
        "insert into form_question_map "
        "(form_id, question_id, slot, question_text, item_index) "
        "values ('fake-form-0099', 'q1', 'confidence', 'x', 0)",
    )

    real = _RealShapedClient()
    replace_template(db, real, "a")
    verify_template(db, real, "a")

    result = provision_session(db, real, session_id, part="a")

    assert result.created, "a fresh form is copied in place of the unusable one"
    assert not result.form_id.startswith("fake-form-")
    assert fetch_one(
        db, "select count(*) as n from session_form where form_id = 'fake-form-0099'"
    )["n"] == 0
    assert fetch_one(
        db, "select count(*) as n from form_question_map where form_id = 'fake-form-0099'"
    )["n"] == 0
    # And it is recorded, not silent.
    logged = fetch_all(
        db,
        "select action, error from provisioning_log where session_id = %s "
        "and action = 'discard_stale_form'",
        (session_id,),
    )
    assert len(logged) == 1
    assert "simulated" in logged[0]["error"]


def test_a_real_form_that_google_has_lost_is_never_silently_discarded(db):
    """It might be in Drive's bin with every response still on it."""
    real = _RealShapedClient()
    create_template(db, real, "a")
    verify_template(db, real, "a")
    session_id = make_session(db, local=SESSION_LOCAL)
    result = provision_session(db, real, session_id, part="a")

    # Google loses the form. The row must survive so it can be restored.
    del real.forms[result.form_id]
    execute(
        db,
        "update session_form set publish_verified_at = null where session_id = %s",
        (session_id,),
    )

    with pytest.raises(FormUnreachable) as excinfo:
        provision_session(db, real, session_id, part="a")

    message = str(excinfo.value)
    assert "bin" in message, "restoring from the bin keeps the responses"
    assert get_session_form(db, session_id, "a") is not None, "the row was kept"


def test_the_connected_account_is_named_in_the_message(db):
    assert connected_account(db) is None
    execute(
        db,
        "insert into google_credential (account_email, refresh_token_enc, scopes) "
        "values (%s, %s, %s)",
        ("someone@civicsunplugged.org", b"x", ["forms.body"]),
    )
    assert connected_account(db) == "someone@civicsunplugged.org"


# ---------------------------------------------------------------------------
# config edits, without a restart
# ---------------------------------------------------------------------------


def test_naming_a_recipient_takes_effect_without_restarting_the_console(tmp_path):
    """The console is long-running.

    Caching this forever meant somebody could name a recipient, reload the page,
    and still be told nobody was configured — with nothing on screen suggesting
    that a restart was what was missing.
    """
    import json

    from cufa.help_routing import get_help_routing, reset_help_routing_cache

    reset_help_routing_cache()
    config = tmp_path / "help_routing.json"

    config.write_text(json.dumps({"recipients": []}), encoding="utf-8")
    assert get_help_routing(config).has_recipient is False

    config.write_text(
        json.dumps({"recipients": [{"name": "DoP", "email": "dop@example.invalid"}]}),
        encoding="utf-8",
    )
    routing = get_help_routing(config)
    assert routing.has_recipient is True
    assert routing.recipients[0].email == "dop@example.invalid"


def test_a_config_that_appears_later_is_picked_up(tmp_path):
    import json

    from cufa.help_routing import get_help_routing, reset_help_routing_cache

    reset_help_routing_cache()
    config = tmp_path / "not_there_yet.json"

    assert get_help_routing(config).has_recipient is False
    config.write_text(
        json.dumps({"recipients": ["dop@example.invalid"]}), encoding="utf-8"
    )
    assert get_help_routing(config).has_recipient is True


def test_editing_the_rotation_schedule_takes_effect_without_a_restart(tmp_path):
    import json

    from cufa.rotation import MUDDIEST_POINT, get_rotation, reset_rotation_cache

    reset_rotation_cache()
    config = tmp_path / "rotation.json"
    config.write_text(
        json.dumps(
            {
                "schedule": {"muddiest_point": [1, 2]},
                "fixed_text": {"muddiest_point": "What's still unclear?"},
                "wrap": True,
            }
        ),
        encoding="utf-8",
    )
    assert get_rotation(config).weeks == 2

    config.write_text(
        json.dumps(
            {
                "schedule": {"muddiest_point": [1, 2, 3]},
                "fixed_text": {"muddiest_point": "Reworded question"},
                "wrap": True,
            }
        ),
        encoding="utf-8",
    )
    updated = get_rotation(config)
    assert updated.weeks == 3
    assert updated.fixed_text[MUDDIEST_POINT] == "Reworded question"


# ---------------------------------------------------------------------------
# a failed copy has to stay findable
# ---------------------------------------------------------------------------


def test_a_form_copied_before_a_failure_is_recorded_despite_a_rollback(db, settings):
    """The orphan-is-findable promise, under the transaction callers actually use.

    `cufa provision` wraps the call in a transaction and rolls it back when this
    raises, which used to take the bookkeeping row with it. The copy stayed in
    Drive with nothing pointing at it, and the next run copied a second one.
    Found on the first real install by listing Drive.
    """
    from cufa.db import connection as db_connection
    from cufa.provisioning import provision_session as provision

    real = _RealShapedClient()
    create_template(db, real, "a")
    verify_template(db, real, "a")
    session_id = make_session(db, local=SESSION_LOCAL)

    # Content application fails after the copy has already happened.
    def explode(form_id, requests):
        real._require(form_id)
        raise GoogleApiError("Invalid requests[1]", status=400)

    real.batch_update = explode

    # The caller's transaction, and its rollback, exactly as the CLI does it.
    with pytest.raises(GoogleApiError):
        with db_connection(settings) as caller:
            provision(caller, real, session_id, part="a")
            raise AssertionError("unreachable")

    # A separate connection, so this reads committed state only.
    with db_connection(settings) as reader:
        row = fetch_one(
            reader,
            "select form_id, publish_verified_at from session_form "
            "where session_id = %s and part = 'a'",
            (session_id,),
        )
        logged = fetch_all(
            reader,
            "select action, outcome from provisioning_log where session_id = %s",
            (session_id,),
        )

    assert row is not None, "the copied form must still be findable after a rollback"
    assert row["form_id"] in real.forms
    assert row["publish_verified_at"] is None, "and must not read as ready"
    assert any(
        entry["action"] == "batch_update" and entry["outcome"] == "failure"
        for entry in logged
    ), "the failure has to be visible too"


def test_the_next_run_resumes_that_form_rather_than_copying_another(db, settings):
    from cufa.db import connection as db_connection
    from cufa.provisioning import provision_session as provision

    real = _RealShapedClient()
    create_template(db, real, "a")
    verify_template(db, real, "a")
    session_id = make_session(db, local=SESSION_LOCAL)

    original_batch = real.batch_update

    def explode(form_id, requests):
        real._require(form_id)
        raise GoogleApiError("Invalid requests[1]", status=400)

    real.batch_update = explode
    with pytest.raises(GoogleApiError):
        with db_connection(settings) as caller:
            provision(caller, real, session_id, part="a")

    copies_before = len(real.forms)
    real.batch_update = original_batch

    with db_connection(settings) as caller:
        result = provision(caller, real, session_id, part="a")

    assert result.resumed, "the second run resumes rather than copying again"
    assert len(real.forms) == copies_before, "no second copy was made"
