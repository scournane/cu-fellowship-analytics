"""Deliverable 10, tests 11-17: the four Google traps.

Each trap fails silently in production — the call returns 200, the link
resolves, and nothing arrives. So each test here drives FakeGoogleClient into
the failing state and asserts that this codebase *refuses*, rather than
asserting that the happy path works.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from conftest import TEST_COHORT, make_session

from cufa.errors import PublishVerificationFailed, TemplateNotVerified
from cufa.google.base import (
    EMAIL_COLLECTION_RESPONDER_INPUT,
    EMAIL_COLLECTION_VERIFIED,
    GoogleApiError,
)
from cufa.google.fake import FakeGoogleClient
from cufa.ingest.forms_api import pull_session
from cufa.provisioning import get_session_form, is_ready, provision_session
from cufa.template import create_template, try_set_verified_email, verify_template
from cufa.db import fetch_all, fetch_one


# --- 11. an unpublished form is detected -----------------------------------

def test_11_unpublished_form_raises_and_is_not_reported_ready(db):
    """Trap 1: setPublishSettings returned 200 and the state did not change."""
    fake = FakeGoogleClient(publish_readback_fails=True)
    record = create_template(db, fake)
    fake.simulate_human_sets_verified(record.form_id)
    verify_template(db, fake)

    session_id = make_session(db)

    with pytest.raises(PublishVerificationFailed) as excinfo:
        provision_session(db, fake, session_id)

    message = str(excinfo.value)
    assert "isPublished=False" in message
    assert "NOT been reported as ready" in message

    assert is_ready(db, session_id) is False
    row = get_session_form(db, session_id)
    assert row is not None, "the copied form is recorded so a retry resumes it"
    assert row["publish_verified_at"] is None, "an unverified form is never 'ready'"

    failures = fetch_all(
        db,
        "select action, outcome, error from provisioning_log where outcome = 'failure'",
    )
    assert any(f["action"] == "publish" for f in failures)


# --- 12. publish is actually called ----------------------------------------

def test_12_publish_is_called_after_every_form_creation(db, fake, verified_template):
    session_a = make_session(db, title="A", local=datetime(2026, 9, 15, 19, 0))
    session_b = make_session(db, title="B", local=datetime(2026, 9, 22, 19, 0))

    result_a = provision_session(db, fake, session_a)
    result_b = provision_session(db, fake, session_b)

    published = {call["form_id"] for call in fake.calls("set_publish_settings")}
    assert result_a.form_id in published
    assert result_b.form_id in published

    for call in fake.calls("set_publish_settings"):
        assert call["is_published"] is True
        assert call["is_accepting_responses"] is True

    # And the state was read back, not assumed.
    read = {call["form_id"] for call in fake.calls("read_settings")}
    assert result_a.form_id in read and result_b.form_id in read


# --- 13. template verification fails closed --------------------------------

def test_13_responder_input_template_blocks_provisioning_entirely(db, fake):
    record = create_template(db, fake)
    fake.simulate_human_sets_verified(record.form_id)
    verify_template(db, fake)

    # Someone opens the template later and turns email collection back down.
    fake.simulate_human_breaks_verified(record.form_id)
    session_id = make_session(db)

    with pytest.raises(TemplateNotVerified) as excinfo:
        provision_session(db, fake, session_id)

    assert EMAIL_COLLECTION_RESPONDER_INPUT in str(excinfo.value)
    assert "Provisioning is blocked" in str(excinfo.value)

    assert get_session_form(db, session_id) is None, "no form is created at all"
    assert fake.calls("copy_form") == []

    # The stored confirmation is cleared, not left stale.
    stored = fetch_one(db, "select verified_email_confirmed_at from form_template")
    assert stored["verified_email_confirmed_at"] is None


def test_13b_template_is_reverified_on_every_provisioning_run(db, fake, verified_template):
    session_a = make_session(db, title="A", local=datetime(2026, 9, 15, 19, 0))
    provision_session(db, fake, session_a)

    fake.simulate_human_breaks_verified(verified_template)
    session_b = make_session(db, title="B", local=datetime(2026, 9, 22, 19, 0))

    with pytest.raises(TemplateNotVerified):
        provision_session(db, fake, session_b)


# --- 14. emailCollectionType 400 is handled --------------------------------

def test_14_email_collection_400_is_expected_and_leaves_nothing_half_done(db):
    """Trap 2: the API rejects the settings update; the manual step covers it."""
    fake = FakeGoogleClient(reject_email_collection=True)

    record = create_template(db, fake)
    # The rejection did not abort template creation...
    assert record.form_id in fake.forms
    # ...but it also did not mark the template usable.
    assert record.is_verified is False

    session_id = make_session(db)
    with pytest.raises(TemplateNotVerified):
        provision_session(db, fake, session_id)
    assert get_session_form(db, session_id) is None


def test_14b_try_set_verified_email_returns_false_on_400(db):
    fake = FakeGoogleClient(reject_email_collection=True)
    ref = fake.create_template("t")
    assert try_set_verified_email(fake, ref.form_id) is False


def test_14c_try_set_verified_email_succeeds_if_google_ever_fixes_it(db):
    """If the API starts accepting it, the manual step disappears for free."""
    fake = FakeGoogleClient(reject_email_collection=False)
    ref = fake.create_template("t")
    assert try_set_verified_email(fake, ref.form_id) is True
    assert fake.read_settings(ref.form_id).email_collection_type == EMAIL_COLLECTION_VERIFIED


def test_14d_unexpected_api_failure_is_not_swallowed(db):
    """A 400 is trap 2. Anything else is a new problem and must surface."""
    from cufa.errors import EmailCollectionRejected

    class Broken(FakeGoogleClient):
        def batch_update(self, form_id, requests):
            if any("updateSettings" in r for r in requests):
                raise GoogleApiError("server exploded", status=500)
            return super().batch_update(form_id, requests)

    fake = Broken()
    ref = fake.create_template("t")
    with pytest.raises(EmailCollectionRejected):
        try_set_verified_email(fake, ref.form_id)


# --- 15. pagination ---------------------------------------------------------

def test_15_multi_page_response_list_is_fully_consumed(db, tmp_path):
    fake = FakeGoogleClient(page_size=2)
    record = create_template(db, fake)
    fake.simulate_human_sets_verified(record.form_id)
    verify_template(db, fake)

    session_id = make_session(db, local=datetime(2026, 9, 15, 19, 0))
    result = provision_session(db, fake, session_id)

    fake.seed_responses(
        result.form_id,
        [
            (f"f{i}@example.invalid", f"2026-09-15T23:2{i}:00Z", "justice")
            for i in range(7)
        ],
    )

    pulled = pull_session(db, fake, session_id)
    assert pulled.rows_read == 7, "every page must be consumed"
    assert pulled.rows_written == 7
    # 7 rows at 2 per page => 4 list calls.
    assert len(fake.calls("list_responses")) == 4


def test_15b_rate_limit_is_retried_then_succeeds(db, monkeypatch):
    monkeypatch.setattr("cufa.ingest.forms_api.time.sleep", lambda _s: None)

    fake = FakeGoogleClient(page_size=10, rate_limit_calls=1)
    record = create_template(db, fake)
    fake.simulate_human_sets_verified(record.form_id)
    verify_template(db, fake)

    session_id = make_session(db, local=datetime(2026, 9, 15, 19, 0))
    result = provision_session(db, fake, session_id)
    fake.seed_responses(
        result.form_id, [("a@example.invalid", "2026-09-15T23:20:00Z", "justice")]
    )

    pulled = pull_session(db, fake, session_id)
    assert pulled.rows_written == 1
    assert len(fake.calls("list_responses")) == 2, "one 429, then success"


# --- 16. watermark safety ---------------------------------------------------

def test_16_watermark_only_advances_after_a_complete_pull(db):
    fake = FakeGoogleClient(page_size=2)
    record = create_template(db, fake)
    fake.simulate_human_sets_verified(record.form_id)
    verify_template(db, fake)

    session_id = make_session(db, local=datetime(2026, 9, 15, 19, 0))
    result = provision_session(db, fake, session_id)
    fake.seed_responses(
        result.form_id,
        [
            (f"f{i}@example.invalid", f"2026-09-15T23:2{i}:00Z", "justice")
            for i in range(6)
        ],
    )

    # Fail on the second page, mid-pull.
    fake.fail_on_response_page = 2
    with pytest.raises(GoogleApiError):
        pull_session(db, fake, session_id)

    row = get_session_form(db, session_id)
    assert row["response_watermark"] is None, "a mid-pull failure must not advance it"

    load = fetch_one(
        db, "select status, error from load_run order by started_at desc limit 1"
    )
    assert load["status"] == "failed"

    # Recover: the whole set is re-read, and the rows already written collide.
    fake.fail_on_response_page = None
    fake._list_calls = 0
    recovered = pull_session(db, fake, session_id)
    assert recovered.rows_read == 6
    assert fetch_one(db, "select count(*) as n from checkin")["n"] == 6

    row = get_session_form(db, session_id)
    assert row["response_watermark"] == "2026-09-15T23:25:00Z"


def test_16b_watermark_makes_the_next_pull_incremental(db, fake, verified_template):
    session_id = make_session(db, local=datetime(2026, 9, 15, 19, 0))
    result = provision_session(db, fake, session_id)
    fake.seed_responses(
        result.form_id, [("a@example.invalid", "2026-09-15T23:20:00Z", "justice")]
    )
    pull_session(db, fake, session_id)

    fake.seed_responses(
        result.form_id, [("b@example.invalid", "2026-09-15T23:30:00Z", "justice")]
    )
    second = pull_session(db, fake, session_id)

    assert second.rows_read == 1, "only the new response is fetched"
    assert second.rows_written == 1
    filters = [c["response_filter"] for c in fake.calls("list_responses") if c["response_filter"]]
    assert any("timestamp > 2026-09-15T23:20:00Z" == f for f in filters)


# --- 17. provisioning idempotency ------------------------------------------

def test_17_second_provision_creates_no_second_form(db, fake, verified_template):
    session_id = make_session(db)

    first = provision_session(db, fake, session_id)
    second = provision_session(db, fake, session_id)

    assert first.created is True
    assert second.created is False
    assert second.already_ready is True
    assert second.form_id == first.form_id
    assert len(fake.calls("copy_form")) == 1, "no second form is created"

    outcomes = [
        row["outcome"]
        for row in fetch_all(
            db,
            "select outcome from provisioning_log where action = 'provision' order by at",
        )
    ]
    assert outcomes == ["success", "skipped"]


def test_17b_failed_provision_resumes_rather_than_copying_again(db):
    fake = FakeGoogleClient(publish_readback_fails=True)
    record = create_template(db, fake)
    fake.simulate_human_sets_verified(record.form_id)
    verify_template(db, fake)
    session_id = make_session(db)

    with pytest.raises(PublishVerificationFailed):
        provision_session(db, fake, session_id)
    first_form = get_session_form(db, session_id)["form_id"]

    # Whatever was wrong is fixed; retry.
    fake.publish_readback_fails = False
    result = provision_session(db, fake, session_id)

    assert result.resumed is True
    assert result.form_id == first_form
    assert len(fake.calls("copy_form")) == 1, "the orphan is reused, not duplicated"
    assert is_ready(db, session_id) is True


# --- dry run ----------------------------------------------------------------

def test_dry_run_touches_nothing_on_google(db, fake, verified_template):
    session_id = make_session(db)
    before = len(fake.call_log)

    result = provision_session(db, fake, session_id, dry_run=True)

    assert result.dry_run is True
    assert get_session_form(db, session_id) is None
    assert fake.calls("copy_form") == []
    assert fake.calls("set_publish_settings") == []
    # Only the template read-back happened, which is a read.
    assert all(
        action in {"read_settings"} for action, _ in fake.call_log[before:]
    )
    logged = fetch_all(db, "select outcome from provisioning_log where outcome = 'dry_run'")
    assert len(logged) == 1


def test_dry_run_is_blocked_by_an_unverified_template_too(db, fake):
    """A dry run reports what would happen — and what would happen is "blocked".

    A dry run that printed a clean plan while the template was unverified would
    be lying about the outcome, which is the one thing a dry run must not do.
    """
    create_template(db, fake)  # never taken through the manual Verified step
    session_id = make_session(db)

    with pytest.raises(TemplateNotVerified):
        provision_session(db, fake, session_id, dry_run=True)

    assert get_session_form(db, session_id) is None
