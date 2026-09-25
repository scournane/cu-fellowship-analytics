"""Tests for the web console.

Three things are being proved here, and they are not "the pages render".

1. **Nobody gets in without being on the allowlist**, including through the dev
   bypass that exists so this suite can run without Google.
2. **The two Google traps stop the console**, visibly. An unverified template
   blocks provisioning; a form whose publish state does not read back never gets
   shown as ready. Both cases must put the full failure text on the screen,
   because both of them look like success from the outside.
3. **The QR encoder produces real QR codes.** It is hand-written, so it is
   checked against a published Reed-Solomon vector, the standard format
   information table, and a decode of its own output.

No test here touches the network. The Google client is the in-memory fake and
sign-in is the dev bypass.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import os
import uuid
from urllib.parse import parse_qs, unquote, urlparse

# Settings are read once and cached, and importing the app reads them, so the
# environment has to be right before any cufa import happens.
os.environ.setdefault("CUFA_DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:64322/postgres")
os.environ["CUFA_FAKE_GOOGLE"] = "1"
os.environ["CUFA_CONSOLE_ALLOWLIST"] = "staff@example.invalid,second@example.invalid"
os.environ["CUFA_CONSOLE_SECRET"] = "test-secret-not-used-anywhere-real"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from cufa import crypto  # noqa: E402
from cufa.config import get_settings, reset_settings_cache  # noqa: E402
from cufa.console import qr  # noqa: E402
from cufa.console.app import app  # noqa: E402
from cufa.console.auth import read_code_verifier  # noqa: E402
from cufa.db import connection, execute, fetch_all  # noqa: E402
from cufa.decisions import current_decision, record_decision  # noqa: E402
from cufa.errors import DatabaseUnreachable  # noqa: E402
from cufa.google.factory import set_fake_client  # noqa: E402
from cufa.google.fake import FakeGoogleClient  # noqa: E402
from cufa.provisioning import get_session_form  # noqa: E402
from cufa import question_sets  # noqa: E402

os.environ.setdefault("CUFA_ENCRYPTION_KEY", crypto.generate_key())
reset_settings_cache()

STAFF = "staff@example.invalid"
OUTSIDER = "not-staff@example.invalid"


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _require_database() -> None:
    """Skip the whole module rather than fail 20 times if Postgres is down."""
    try:
        with connection() as conn:
            fetch_all(conn, "select 1")
    except DatabaseUnreachable as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"local Postgres is not running: {exc}")


def _reset_google_state() -> None:
    """Clear every form the fake Google client has ever created here.

    Two reasons this is necessary rather than tidy. ``form_template`` is global
    to the install, so a template left behind by one test silently unblocks the
    next one. And ``FakeGoogleClient`` restarts its form ids at ``fake-form-0001``
    for every instance, so yesterday's row collides with today's on the unique
    index — a real install never sees this, because real Google form ids are
    unique.

    Only rows whose form id carries the fake's prefix are removed. Real forms,
    check-ins and decisions are never touched, and check-ins could not be
    deleted anyway.
    """
    fake_ids = "fake-form-%"
    with connection() as conn:
        # The Part A question map is keyed by form id, and the fake reuses them.
        execute(conn, "delete from part_a_form_question where form_id like %s", (fake_ids,))
        execute(
            conn,
            """
            delete from session_form sf
             using form_template ft
             where sf.template_id = ft.template_id and ft.form_id like %s
            """,
            (fake_ids,),
        )
        execute(conn, "delete from session_form where form_id like %s", (fake_ids,))
        execute(conn, "delete from form_template where form_id like %s", (fake_ids,))


@pytest.fixture
def fake() -> FakeGoogleClient:
    """A fresh fake Google client against a cleared template."""
    _reset_google_state()
    client = FakeGoogleClient()
    set_fake_client(client)
    yield client
    set_fake_client(None)


@pytest.fixture
def verified_template(fake: FakeGoogleClient) -> FakeGoogleClient:
    """A template that has been through the one manual step."""
    from cufa.template import create_template, verify_template

    with connection() as conn:
        record = create_template(conn, fake)
        fake.simulate_human_sets_verified(record.form_id)
        verify_template(conn, fake)
    return fake


#: The smallest exit ticket there is: one question. Enough to provision.
ONE_QUESTION = {
    "schema_version": 1,
    "title": "Exit ticket — lesson {lesson}",
    "description": "",
    "questions": [
        {
            "key": "q_takeaway",
            "type": "short_answer",
            "title": "One thing you learned in {session_title}",
            "description": "",
            "required": True,
        }
    ],
}


def _new_cohort(label: str = "console test cohort") -> str:
    cohort_id = f"test-{uuid.uuid4().hex[:8]}"
    with connection() as conn:
        execute(
            conn,
            "insert into cohort (cohort_id, label) values (%s, %s)",
            (cohort_id, label),
        )
    return cohort_id


@pytest.fixture
def cohort() -> str:
    """A cohort of this test's own, with a default exit ticket to provision.

    Check-in rows cannot be deleted — the immutability trigger blocks it — so
    isolation is by fresh key rather than by cleanup.
    """
    cohort_id = _new_cohort()
    with connection() as conn:
        question_sets.save_default(conn, cohort_id, ONE_QUESTION, created_by=STAFF)
    return cohort_id


@pytest.fixture
def bare_cohort() -> str:
    """A cohort nobody has written exit ticket questions for yet."""
    return _new_cohort("console test cohort, no questions")


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def signed_in(client: TestClient) -> TestClient:
    response = client.post("/signin/dev", data={"email": STAFF, "next": "/"})
    assert response.status_code == 303
    return client


def make_session(
    client: TestClient,
    cohort_id: str,
    *,
    title: str = "Week 3 — Deliberation",
    scheduled_at: str = "2026-09-15T13:05",
    week_index: str = "",
    zoom_url: str = "",
    agenda: str = "",
    slack_channel_id: str = "",
) -> str:
    response = client.post(
        "/sessions/new",
        data={
            "title": title,
            "scheduled_at": scheduled_at,
            "timezone": "America/New_York",
            "duration_minutes": "60",
            "grace_minutes": "15",
            "cohort_id": cohort_id,
            "week_index": week_index,
            "zoom_url": zoom_url,
            "agenda": agenda,
            "slack_channel_id": slack_channel_id,
        },
    )
    assert response.status_code == 303, response.text[:2000]
    return response.headers["location"].split("/sessions/")[1].split("?")[0]


# --------------------------------------------------------------------------
# authentication
# --------------------------------------------------------------------------


def test_every_screen_redirects_when_not_signed_in(client: TestClient) -> None:
    for path in ("/", "/template", "/sessions", "/sessions/new", "/review"):
        response = client.get(path)
        assert response.status_code == 303, path
        assert response.headers["location"].startswith("/signin"), path


def boot_state(response) -> dict:
    """The JSON the server hands the React screen.

    The sign-in screen renders client-side, so its text is not in the response.
    What the server is actually responsible for is the state it ships, and that
    is what these tests assert on.
    """
    match = re.search(
        r'<script type="application/json" id="__CUFA_STATE__">(.*?)</script>',
        response.text,
        re.DOTALL,
    )
    assert match, "no boot state in the response"
    return json.loads(match.group(1))


def test_signin_page_is_public(client: TestClient) -> None:
    response = client.get("/signin")
    assert response.status_code == 200
    # The dev bypass door is open under the test settings, and the screen is
    # told so. Whether it draws a form is the front-end's business.
    assert boot_state(response)["devSignin"] is True


def test_dev_signin_rejects_an_address_not_on_the_allowlist(client: TestClient) -> None:
    response = client.post("/signin/dev", data={"email": OUTSIDER, "next": "/"})
    assert response.status_code == 403
    assert "not on the console allowlist" in response.text
    assert "cufa_console_session" not in client.cookies

    # And the rejection is real: the screens are still closed.
    assert client.get("/sessions").status_code == 303


def test_a_forged_cookie_does_not_sign_anyone_in(client: TestClient) -> None:
    client.cookies.set("cufa_console_session", "eyJlbWFpbCI6ICJhdHRhY2tlckBleGFtcGxlLmludmFsaWQifQ.fake")
    assert client.get("/sessions").status_code == 303


def test_removing_someone_from_the_allowlist_ends_their_session(
    signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert signed_in.get("/sessions").status_code == 200
    monkeypatch.setenv("CUFA_CONSOLE_ALLOWLIST", "someone-else@example.invalid")
    reset_settings_cache()
    try:
        assert signed_in.get("/sessions").status_code == 303
    finally:
        monkeypatch.undo()
        reset_settings_cache()


# --------------------------------------------------------------------------
# the shared-password door
# --------------------------------------------------------------------------

SITE_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def password_unset(monkeypatch: pytest.MonkeyPatch):
    """The developer's own .env may configure a password; this test is about the
    default, so the variable is emptied rather than assumed absent (F-13).

    Set to "" rather than deleted: load_dotenv runs with override=False, so a
    deleted name is simply read back out of .env on the next load, while an
    empty one already present is left alone."""
    monkeypatch.setenv("CUFA_CONSOLE_PASSWORD", "")
    reset_settings_cache()
    yield
    monkeypatch.undo()
    reset_settings_cache()


@pytest.fixture
def password_configured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CUFA_CONSOLE_PASSWORD", SITE_PASSWORD)
    reset_settings_cache()
    yield
    monkeypatch.undo()
    reset_settings_cache()


def test_password_door_is_shut_unless_one_is_configured(client: TestClient, password_unset) -> None:
    """No CUFA_CONSOLE_PASSWORD, no door — not even an empty one that matches ''."""
    assert boot_state(client.get("/signin"))["passwordSignin"] is False
    assert client.post("/signin/password", data={"password": "", "next": "/"}).status_code == 403
    assert client.get("/sessions").status_code == 303


def test_the_right_password_opens_the_console(client: TestClient, password_configured) -> None:
    assert boot_state(client.get("/signin"))["passwordSignin"] is True
    response = client.post("/signin/password", data={"password": SITE_PASSWORD, "next": "/"})
    assert response.status_code == 303
    assert client.get("/sessions").status_code == 200


def test_a_wrong_password_opens_nothing(client: TestClient, password_configured) -> None:
    response = client.post(
        "/signin/password", data={"password": SITE_PASSWORD + "x", "next": "/"}
    )
    assert response.status_code == 403
    assert client.get("/sessions").status_code == 303


def test_clearing_the_password_ends_the_sessions_it_issued(
    client: TestClient, password_configured, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rotating the secret has to log people out now, not when their cookie ages out."""
    assert client.post(
        "/signin/password", data={"password": SITE_PASSWORD, "next": "/"}
    ).status_code == 303
    assert client.get("/sessions").status_code == 200

    monkeypatch.setenv("CUFA_CONSOLE_PASSWORD", "")
    reset_settings_cache()
    try:
        assert client.get("/sessions").status_code == 303
    finally:
        monkeypatch.undo()
        reset_settings_cache()


def test_the_shared_password_does_not_open_help_requests(
    client: TestClient, password_configured
) -> None:
    """The whole point of keeping the email allowlist: a shared secret cannot
    say who read a safeguarding record, so it does not get to read one."""
    assert client.post(
        "/signin/password", data={"password": SITE_PASSWORD, "next": "/"}
    ).status_code == 303
    response = client.get("/help-requests")
    assert response.status_code == 403, response.status_code
    assert client.get("/sessions").status_code == 200  # and nothing else was revoked


def test_signout_clears_the_session(signed_in: TestClient) -> None:
    assert signed_in.post("/signout").status_code == 303
    assert signed_in.get("/sessions").status_code == 303


# --------------------------------------------------------------------------
# every screen, signed in
# --------------------------------------------------------------------------


def test_every_screen_returns_200_for_an_allowlisted_user(
    signed_in: TestClient, verified_template: FakeGoogleClient, cohort: str
) -> None:
    session_id = make_session(signed_in, cohort)
    paths = [
        "/",
        "/template",
        "/sessions",
        f"/sessions?cohort={cohort}",
        "/sessions/new",
        f"/sessions/{session_id}",
        f"/sessions/{session_id}/edit",
        "/review",
        "/review?tab=needs_review",
        "/review?tab=outside_window",
        "/review?tab=ai",
        "/review?tab=identities",
        f"/sessions/{session_id}/responses.json",
        "/template/questions",
        f"/template/questions?cohort={cohort}",
        f"/template?cohort={cohort}",
        f"/sessions/{session_id}/questions",
        "/healthz",
    ]
    for path in paths:
        response = signed_in.get(path)
        assert response.status_code == 200, f"{path} -> {response.status_code}"


def test_fake_google_banner_is_shown(signed_in: TestClient) -> None:
    response = signed_in.get("/")
    # The banner copy lives in the front-end now; what the server owes the
    # screen is the flag that makes it appear.
    assert boot_state(response)["fakeGoogle"] is True


def test_connect_screen_simulates_the_connection_without_google(
    signed_in: TestClient,
) -> None:
    response = signed_in.post("/google/connect")
    assert response.status_code == 303
    page = signed_in.get("/")
    assert "connected" in page.text
    assert "Simulated" in signed_in.get("/?notice=Simulated+connection+recorded.").text

    response = signed_in.post("/google/disconnect")
    assert response.status_code == 303


def test_the_connect_screen_is_told_the_scopes_are_sufficient(
    signed_in: TestClient,
) -> None:
    """Regression: a computed property does not survive the trip on its own.

    ``CredentialStatus.has_required_scopes`` is a ``@property``, and the encoder
    that builds the boot state serialises dataclass *fields* only. When it went
    missing the screen read ``undefined``, took it for false, and told a
    correctly connected account that a required scope was missing — while
    listing both required scopes directly above the warning.
    """
    assert signed_in.post("/google/connect").status_code == 303
    status = boot_state(signed_in.get("/"))["status"]
    assert status["connected"] is True
    assert status["has_required_scopes"] is True, (
        "the screen hides its missing-scope warning on this value; absent, "
        "every connected account is told a scope is missing"
    )


def test_a_cli_authorization_code_is_handed_back_not_swallowed(
    client: TestClient,
) -> None:
    """`cufa google connect` redirects into this console, by design of the URI.

    The CLI signs no state — it keeps the whole round trip in one process — so
    the console cannot verify what comes back and used to answer with "that
    sign-in link did not verify". The code was sitting in the address bar the
    whole time, but the page said the opposite. It now hands the code over.
    """
    response = client.get(
        "/google/callback", params={"code": "4/CLI-ISSUED-CODE", "state": "unsigned-cli-state"}
    )
    assert response.status_code == 200
    state = boot_state(response)
    assert state["code"] == "4/CLI-ISSUED-CODE"
    assert "terminal" in state["heading"].lower()


def test_a_bad_state_with_no_code_is_still_refused(client: TestClient) -> None:
    """The forgery path must not be softened by the convenience above."""
    response = client.get("/google/callback", params={"state": "tampered"})
    assert response.status_code == 400
    state = boot_state(response)
    assert state.get("code") is None
    assert "did not verify" in state["heading"]


# --------------------------------------------------------------------------
# PKCE: the verifier must survive the redirect, not just the redirect itself
# --------------------------------------------------------------------------
#
# Regression coverage for a bug where the console built a fresh Flow object on
# the callback request, so it never had the code_verifier the first Flow
# generated — Google's token endpoint then rejected every real sign-in and
# connect attempt with "(invalid_grant) Missing code verifier". Nothing above
# this line would have caught it: /google/connect only exercises the
# CUFA_FAKE_GOOGLE branch, which never builds a Flow at all. These don't call
# Google — authorization_url() is pure local URL construction — they just
# prove the verifier recoverable from the callback side reproduces the
# code_challenge already baked into the redirect Google was sent.


def _assert_pkce_round_trips(response) -> None:
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["code_challenge_method"][0] == "S256"
    code_challenge = query["code_challenge"][0]

    cookie = response.cookies.get("cufa_console_pkce")
    assert cookie, "no PKCE cookie was set alongside the redirect"
    verifier = read_code_verifier(get_settings(), cookie)
    assert verifier, "the PKCE cookie did not verify"

    recomputed = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert recomputed.decode().rstrip("=") == code_challenge


def test_signin_google_carries_its_pkce_verifier_to_the_callback(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    reset_settings_cache()
    try:
        response = client.get("/signin/google")
        assert response.status_code == 303
        _assert_pkce_round_trips(response)
    finally:
        monkeypatch.undo()
        reset_settings_cache()


def test_google_connect_carries_its_pkce_verifier_to_the_callback(
    signed_in: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("CUFA_FAKE_GOOGLE", "0")
    reset_settings_cache()
    try:
        response = signed_in.post("/google/connect")
        assert response.status_code == 303
        _assert_pkce_round_trips(response)
    finally:
        monkeypatch.undo()
        reset_settings_cache()


def test_an_unknown_session_id_is_a_404_not_a_500(signed_in: TestClient) -> None:
    assert signed_in.get("/sessions/not-a-uuid").status_code == 404
    assert signed_in.get(f"/sessions/{uuid.uuid4()}").status_code == 404


# --------------------------------------------------------------------------
# screen 2 — the template gate (trap 2)
# --------------------------------------------------------------------------


def test_template_screen_blocks_downstream_work_until_verified(
    signed_in: TestClient, fake: FakeGoogleClient
) -> None:
    response = signed_in.get("/template")
    assert response.status_code == 200
    assert boot_state(response)["blocked"] is True

    created = signed_in.post("/template/create")
    assert created.status_code == 200
    state = boot_state(created)
    assert state["blocked"] is True
    # The one manual step is on the screen, not in a document somewhere.
    assert "Collect email addresses" in state["manual_step"]


def test_verify_fails_red_when_the_api_does_not_say_verified(
    signed_in: TestClient, fake: FakeGoogleClient
) -> None:
    signed_in.post("/template/create")
    response = signed_in.post("/template/verify")
    assert response.status_code == 200
    state = boot_state(response)
    assert state["blocked"] is True
    # The exception text itself, not a summary of it.
    assert "emailCollectionType=" in state["error"]
    assert "DO_NOT_COLLECT" in state["error"]


def test_verify_goes_green_only_after_the_api_confirms_it(
    signed_in: TestClient, fake: FakeGoogleClient
) -> None:
    signed_in.post("/template/create")
    with connection() as conn:
        from cufa.template import get_template

        record = get_template(conn)
    assert record is not None

    fake.simulate_human_sets_verified(record.form_id)
    response = signed_in.post("/template/verify")
    assert response.status_code == 200
    assert "Provisioning is unblocked" in response.text
    assert "Downstream work is blocked" not in response.text


def test_an_unverified_part_a_template_can_be_replaced_with_a_clean_one(
    signed_in: TestClient, fake: FakeGoogleClient
) -> None:
    """A template made before the exit ticket still asks for a passphrase.

    Replacing it while unverified loses nothing (the manual step was never
    done) and leaves a form with a title and notice but no questions, which
    is what every Part A session copy is rebuilt from.
    """
    from cufa.template import get_template

    signed_in.post("/template/create", data={"part": "a"})
    with connection() as conn:
        old = get_template(conn, "a")
    assert old is not None

    response = signed_in.post("/template/replace", data={"part": "a"})
    assert response.status_code == 200
    assert "the old one retired" in boot_state(response)["notice"]

    with connection() as conn:
        new = get_template(conn, "a")
    assert new is not None and new.form_id != old.form_id
    assert not fake.get_form(new.form_id).items
    # Still unverified: a new form needs the human step again.
    assert boot_state(response)["blocked"] is True


def test_provisioning_is_refused_while_the_template_is_unverified(
    signed_in: TestClient, fake: FakeGoogleClient, cohort: str
) -> None:
    signed_in.post("/template/create")
    session_id = make_session(signed_in, cohort)

    detail = signed_in.get(f"/sessions/{session_id}")
    assert boot_state(detail)["template_blocked"] is True

    response = signed_in.post(f"/sessions/{session_id}/provision")
    assert response.status_code == 200
    state = boot_state(response)
    assert state["ready"] is False
    assert "Provisioning is blocked until this reads back as VERIFIED" in state["error"]


# --------------------------------------------------------------------------
# screen 4 — provisioning (trap 1), announce, pull
# --------------------------------------------------------------------------


def test_provisioning_success_shows_the_link_and_a_qr_code(
    signed_in: TestClient, verified_template: FakeGoogleClient, cohort: str
) -> None:
    session_id = make_session(signed_in, cohort)
    response = signed_in.post(f"/sessions/{session_id}/provision")

    assert response.status_code == 200
    state = boot_state(response)
    assert state["ready"] is True
    assert "forms.example.invalid" in state["form_url"]
    assert state["qr"].lstrip().startswith("<svg")
    # Publishing was actually called, not assumed.
    assert verified_template.calls("set_publish_settings")


def test_a_publish_that_does_not_read_back_never_looks_ready(
    signed_in: TestClient, cohort: str
) -> None:
    """Trap 1. The call returns 200 and the link resolves; the state is false."""
    _reset_google_state()
    client = FakeGoogleClient(publish_readback_fails=True)
    set_fake_client(client)
    try:
        with connection() as conn:
            from cufa.template import create_template, verify_template

            record = create_template(conn, client)
            client.simulate_human_sets_verified(record.form_id)
            verify_template(conn, client)

        session_id = make_session(signed_in, cohort)
        response = signed_in.post(f"/sessions/{session_id}/provision")

        assert response.status_code == 200
        state = boot_state(response)
        assert "isAcceptingResponses=False" in state["error"]
        assert "accepts no responses while its link still resolves" in state["error"]
        # No link, no QR, no green tick.
        assert state["ready"] is False
        assert state["qr"] is None

        with connection() as conn:
            row = get_session_form(conn, session_id)
        assert row is not None and row["publish_verified_at"] is None

        # The failed attempt is recorded and handed to the screen, not just raised.
        assert any(entry["outcome"] == "failure" for entry in state["provisioning_log"])
    finally:
        set_fake_client(None)


def test_provisioning_twice_does_not_create_a_second_form(
    signed_in: TestClient, verified_template: FakeGoogleClient, cohort: str
) -> None:
    session_id = make_session(signed_in, cohort)
    signed_in.post(f"/sessions/{session_id}/provision")
    with connection() as conn:
        first = get_session_form(conn, session_id)
    signed_in.post(f"/sessions/{session_id}/provision")
    with connection() as conn:
        second = get_session_form(conn, session_id)
    assert first is not None and second is not None
    assert first["form_id"] == second["form_id"]
    assert len(verified_template.calls("copy_form")) == 1


def test_dry_run_makes_no_form(
    signed_in: TestClient, verified_template: FakeGoogleClient, cohort: str
) -> None:
    session_id = make_session(signed_in, cohort)
    response = signed_in.post(f"/sessions/{session_id}/provision", data={"dry_run": "1"})
    assert response.status_code == 200
    assert "dry run — no Google calls were made" in boot_state(response)["notice"]
    assert not verified_template.calls("copy_form")


def test_announce_stamps_and_shows_the_utc_instant(
    signed_in: TestClient, verified_template: FakeGoogleClient, cohort: str
) -> None:
    session_id = make_session(signed_in, cohort)
    response = signed_in.post(f"/sessions/{session_id}/announce")
    assert response.status_code == 303

    detail = signed_in.get(f"/sessions/{session_id}")
    assert boot_state(detail)["session"]["announced_at_utc"] is not None
    payload = signed_in.get(f"/sessions/{session_id}/responses.json").json()
    assert payload["announced_at_utc"] is not None


def test_pull_reports_counts_and_the_live_count_follows(
    signed_in: TestClient, verified_template: FakeGoogleClient, cohort: str
) -> None:
    session_id = make_session(signed_in, cohort)
    signed_in.post(f"/sessions/{session_id}/provision")
    with connection() as conn:
        form = get_session_form(conn, session_id)
    assert form is not None

    # Unique addresses per run: source_event_id hashes (form id, email, second),
    # and the fake reuses form ids, so a fixed pair would be correctly skipped as
    # a duplicate of the previous run.
    tag = uuid.uuid4().hex[:8]
    question_id = _question_id(form["form_id"], "q_takeaway")
    verified_template.seed_answers(
        form["form_id"],
        [
            {
                "respondent_email": f"one-{tag}@example.invalid",
                "create_time": "2026-09-15T17:10:00Z",
                "answers": {question_id: ["Deliberation takes longer than voting"]},
            },
            {
                "respondent_email": f"two-{tag}@example.invalid",
                "create_time": "2026-09-15T17:12:00Z",
                "answers": {question_id: ["Listen before arguing"]},
            },
        ],
    )

    response = signed_in.post(f"/sessions/{session_id}/pull")
    assert response.status_code == 200
    assert "2 read, 2 written" in response.text

    payload = signed_in.get(f"/sessions/{session_id}/responses.json").json()
    assert payload["responses"] == 2
    assert payload["form_ready"] is True

    # Idempotent: a second pull writes nothing new.
    again = signed_in.post(f"/sessions/{session_id}/pull")
    assert "0 written" in again.text


def test_the_responses_screen_counts_exit_ticket_answers_and_names_nobody(
    signed_in: TestClient, verified_template: FakeGoogleClient, cohort: str
) -> None:
    content = json.loads(json.dumps(ONE_QUESTION))
    content["questions"] += [
        {"key": "q_rating", "type": "linear_scale", "title": "How was it?", "description": "",
         "required": True, "scale": {"low": 1, "high": 5, "low_label": "Rough", "high_label": "Great"}},
        {"key": "q_parts", "type": "checkboxes", "title": "Which parts helped?", "description": "",
         "required": False, "options": ["Reading", "Discussion", "Game"]},
    ]
    with connection() as conn:
        question_sets.save_default(conn, cohort, content, created_by=STAFF)

    session_id = make_session(signed_in, cohort)
    signed_in.post(f"/sessions/{session_id}/provision")
    with connection() as conn:
        form = get_session_form(conn, session_id)
    ids = {key: _question_id(form["form_id"], key) for key in ("q_takeaway", "q_rating", "q_parts")}

    tag = uuid.uuid4().hex[:8]
    verified_template.seed_answers(
        form["form_id"],
        [
            {
                "respondent_email": f"ada-{tag}@example.invalid",
                "create_time": "2026-09-15T17:40:00Z",
                "answers": {
                    ids["q_takeaway"]: ["Budgets are moral documents"],
                    ids["q_rating"]: ["4"],
                    ids["q_parts"]: ["Reading", "Game"],
                },
            },
            {
                "respondent_email": f"bo-{tag}@example.invalid",
                "create_time": "2026-09-15T17:41:00Z",
                "answers": {ids["q_rating"]: ["4"], ids["q_parts"]: ["Game"]},
            },
        ],
    )
    assert signed_in.post(f"/sessions/{session_id}/pull").status_code == 200

    part_a = boot_state(signed_in.get(f"/sessions/{session_id}/responses"))["part_a"]
    assert part_a["responses"] == 2
    by_key = {q["key"]: q for q in part_a["questions"]}
    assert by_key["q_takeaway"]["answered"] == 1
    assert by_key["q_takeaway"]["answers"] == ["Budgets are moral documents"]
    # Every point on the scale is shown, the unpicked ones as zero.
    assert by_key["q_rating"]["counts"] == [
        {"value": v, "count": 2 if v == "4" else 0} for v in ("1", "2", "3", "4", "5")
    ]
    assert {c["value"]: c["count"] for c in by_key["q_parts"]["counts"]} == {
        "Reading": 1, "Discussion": 0, "Game": 2,
    }
    # Counted and listed, never attached to the person who wrote it.
    assert tag not in json.dumps(part_a)


def _question_id(form_id: str, key: str) -> str:
    """Which question id a key landed on in this copy — never known in advance."""
    with connection() as conn:
        rows = question_sets.form_map(conn, form_id)
    by_key = {row["question_key"]: row["question_id"] for row in rows}
    assert key in by_key, f"{key} is not on form {form_id}: {sorted(by_key)}"
    return by_key[key]


def test_the_link_reminder_is_in_the_ui(
    signed_in: TestClient, verified_template: FakeGoogleClient, cohort: str
) -> None:
    from cufa.console.app import LINK_REMINDER

    session_id = make_session(signed_in, cohort)
    state = boot_state(signed_in.get(f"/sessions/{session_id}"))
    assert state["link_reminder"] == LINK_REMINDER
    assert "screen" in LINK_REMINDER.lower() and "chat" in LINK_REMINDER.lower()
    # Nothing is left of the word a teacher used to read out.
    assert "passphrase" not in state["session"] or state["session"]["passphrase"] is None
    assert "accessibility_reminder" not in state


# --------------------------------------------------------------------------
# screen 3 — sessions
# --------------------------------------------------------------------------


def test_the_session_form_asks_for_no_passphrase(signed_in: TestClient) -> None:
    state = boot_state(signed_in.get("/sessions/new"))
    assert "passphrase" not in state["values"]
    assert "guidance" not in state and "reuse_warnings" not in state
    # The zone is deliberately left empty here: the screen fills it from the
    # browser. That now happens inside the bundle, so it is proved by the
    # browser walk-through rather than by this request.
    assert state["values"]["timezone"] == ""


def test_the_passphrase_suggestion_endpoint_is_gone(signed_in: TestClient) -> None:
    assert signed_in.get("/api/passphrase/suggest").status_code == 404


def test_a_posted_passphrase_is_ignored_rather_than_refused(
    signed_in: TestClient, cohort: str
) -> None:
    """An old bookmarked form, or a script, may still send one. It saves."""
    response = signed_in.post(
        "/sessions/new",
        data={
            "title": "Week 2",
            "scheduled_at": "2026-09-22T13:05",
            "timezone": "America/New_York",
            "duration_minutes": "60",
            "grace_minutes": "15",
            "passphrase": "justice",
            "confirm_reuse": "1",
            "cohort_id": cohort,
        },
    )
    assert response.status_code == 303


def test_creating_a_session_stores_the_local_time_and_the_zone(
    signed_in: TestClient, cohort: str
) -> None:
    session_id = make_session(signed_in, cohort, scheduled_at="2026-09-15T13:05")
    with connection() as conn:
        from cufa.sessions import get_session

        row = get_session(conn, session_id)
    assert row is not None
    assert row["timezone"] == "America/New_York"
    assert row["scheduled_at_local"].strftime("%Y-%m-%d %H:%M") == "2026-09-15 13:05"
    # 13:05 in New York in September is 17:05Z.
    assert row["scheduled_at_utc"].strftime("%Y-%m-%dT%H:%MZ") == "2026-09-15T17:05Z"


def test_session_delivery_fields_round_trip_through_console(
    signed_in: TestClient, cohort: str
) -> None:
    session_id = make_session(
        signed_in,
        cohort,
        zoom_url="https://zoom.example.invalid/j/123",
        agenda="Welcome\nSmall groups",
        slack_channel_id="fellowship-live",
    )
    with connection() as conn:
        from cufa.sessions import get_session

        row = get_session(conn, session_id)
    assert row["zoom_url"] == "https://zoom.example.invalid/j/123"
    assert row["agenda"] == "Welcome\nSmall groups"
    assert row["slack_channel_id"] == "fellowship-live"

    state = boot_state(signed_in.get(f"/sessions/{session_id}/edit"))
    assert state["values"]["zoom_url"] == row["zoom_url"]
    assert state["values"]["agenda"] == row["agenda"]
    assert state["values"]["slack_channel_id"] == row["slack_channel_id"]


def test_console_rejects_an_invalid_zoom_link(
    signed_in: TestClient, cohort: str
) -> None:
    response = signed_in.post(
        "/sessions/new",
        data={
            "title": "Week 3",
            "scheduled_at": "2026-09-15T13:05",
            "timezone": "America/New_York",
            "duration_minutes": "60",
            "grace_minutes": "15",
            "cohort_id": cohort,
            "zoom_url": "zoom dot example dot org",
        },
    )
    assert response.status_code == 400
    assert "complete http:// or https:// URL" in response.text


def test_invalid_session_input_is_rejected_with_reasons(
    signed_in: TestClient, cohort: str
) -> None:
    response = signed_in.post(
        "/sessions/new",
        data={
            "title": "",
            "scheduled_at": "not a date",
            "timezone": "Mars/Olympus_Mons",
            "duration_minutes": "0",
            "grace_minutes": "-1",
            "cohort_id": cohort,
        },
    )
    assert response.status_code == 400
    assert "Title is required." in response.text
    assert "must be a date and time" in response.text
    assert "not a known IANA timezone" in response.text
    assert "Duration must be 1 or more." in response.text


def test_editing_a_session_updates_it(signed_in: TestClient, cohort: str) -> None:
    session_id = make_session(signed_in, cohort, title="Before")
    response = signed_in.post(
        f"/sessions/{session_id}/edit",
        data={
            "title": "After",
            "scheduled_at": "2026-10-01T09:00",
            "timezone": "UTC",
            "duration_minutes": "45",
            "grace_minutes": "20",
            "cohort_id": cohort,
        },
    )
    assert response.status_code == 303
    with connection() as conn:
        from cufa.sessions import get_session

        row = get_session(conn, session_id)
    assert row is not None
    assert row["title"] == "After"
    assert row["grace_minutes"] == 20


# --------------------------------------------------------------------------
# Part A — the exit ticket's questions
# --------------------------------------------------------------------------
#
# A cohort default plus a per-session snapshot, both append-only, both edited by
# one form post carrying the whole set as JSON. What is being proved: a save
# makes a version, a session can leave the default and come back, publishing
# locks a session, and every refusal re-draws what was posted instead of
# throwing the edit away.


def _with_question(content: dict, **question) -> dict:
    """``content`` plus one more question, as the editor would post it."""
    edited = json.loads(json.dumps(content))
    edited["questions"].append(
        {"type": "paragraph", "title": "", "description": "", "required": False, **question}
    )
    return edited


def _post_default(client: TestClient, cohort_id: str, content: dict | str, base_id: str = ""):
    return client.post(
        "/template/questions",
        data={
            "cohort": cohort_id,
            "questions_json": content if isinstance(content, str) else json.dumps(content),
            "base_id": base_id,
        },
    )


def _post_session(client: TestClient, session_id: str, content: dict | str, base_id: str = ""):
    return client.post(
        f"/sessions/{session_id}/questions",
        data={
            "questions_json": content if isinstance(content, str) else json.dumps(content),
            "base_id": base_id,
        },
    )


def _notice(response) -> str:
    query = parse_qs(urlparse(response.headers["location"]).query)
    return (query.get("notice") or query.get("error") or [""])[0]


def test_staff_save_a_cohort_default(signed_in: TestClient, bare_cohort: str) -> None:
    empty = boot_state(signed_in.get(f"/template/questions?cohort={bare_cohort}"))
    assert empty["scope"] == "default"
    assert empty["current"] is None and empty["base_id"] == ""
    assert {t["type"] for t in empty["question_types"]} >= {"short_answer", "linear_scale"}

    content = _with_question(
        ONE_QUESTION,
        type="linear_scale",
        title="How was today?",
        required=True,
        scale={"low": 1, "high": 5, "low_label": "", "high_label": ""},
    )
    response = _post_default(signed_in, bare_cohort, content)
    assert response.status_code == 303
    assert "version 1" in _notice(response)

    with connection() as conn:
        saved = question_sets.current_default(conn, bare_cohort)
    assert saved is not None and saved.version == 1
    assert saved.source == "console" and saved.created_by == STAFF
    assert [q["type"] for q in saved.questions] == ["short_answer", "linear_scale"]
    assert all(q["key"].startswith("q_") for q in saved.questions), "new keys are minted"

    state = boot_state(signed_in.get(f"/template/questions?cohort={bare_cohort}"))
    assert state["current"]["label"] == "default v1"
    assert state["base_id"] == saved.question_set_id
    assert [h["version"] for h in state["history"]] == [1]

    # The same content again is not a new version.
    again = _post_default(signed_in, bare_cohort, saved.content, base_id=saved.question_set_id)
    assert again.status_code == 303
    assert "Nothing changed" in _notice(again)

    summary = boot_state(signed_in.get(f"/template?cohort={bare_cohort}"))
    assert summary["selected_cohort"] == bare_cohort
    assert summary["part_a_default"]["version"] == 1
    assert summary["part_a_default"]["answerable_count"] == 2


def test_a_session_gets_its_own_questions_and_can_go_back(
    signed_in: TestClient, cohort: str
) -> None:
    session_id = make_session(signed_in, cohort, week_index="3")
    with connection() as conn:
        default = question_sets.current_default(conn, cohort)

    state = boot_state(signed_in.get(f"/sessions/{session_id}/questions"))
    assert state["scope"] == "session" and state["locked"] is False
    assert state["override"] is None
    assert state["base_default"]["question_set_id"] == default.question_set_id
    # Customising starts from the default, and is based on it.
    assert state["content"]["questions"] == default.content["questions"]
    assert state["base_id"] == default.question_set_id

    custom = _with_question(default.content, title="What should we change?")
    response = _post_session(signed_in, session_id, custom, base_id=state["base_id"])
    assert response.status_code == 303
    assert "own questions" in _notice(response)

    with connection() as conn:
        override = question_sets.current_override(conn, session_id)
    assert override is not None and override.version == 1
    assert override.based_on_id == default.question_set_id

    detail = boot_state(signed_in.get(f"/sessions/{session_id}"))
    assert detail["a_questions"]["scope"] == "session"
    assert detail["a_questions"]["set"]["label"] == "custom v1"
    # Shown filled in, the way a fellow will read it.
    assert detail["a_questions"]["rendered"]["title"] == "Exit ticket — lesson 3"
    assert detail["a_blocked"] is False

    listed = boot_state(signed_in.get(f"/sessions?cohort={cohort}"))["sessions"]
    row = next(r for r in listed if r["session_id"] == session_id)
    assert row["part_a_questions"] == {"scope": "custom", "version": 1, "provisioned": False}

    reverted = signed_in.post(
        f"/sessions/{session_id}/questions/revert", data={"return_to": "detail"}
    )
    assert reverted.status_code == 303
    assert reverted.headers["location"].startswith(f"/sessions/{session_id}?notice=")
    with connection() as conn:
        assert question_sets.current_override(conn, session_id) is None
        # Reverting keeps the override in the history rather than deleting it.
        assert [q.version for q in question_sets.history(conn, session_id=session_id)] == [1]
    detail = boot_state(signed_in.get(f"/sessions/{session_id}"))
    assert detail["a_questions"]["scope"] == "default"


def test_saving_the_default_unchanged_does_not_pin_a_session_to_a_copy(
    signed_in: TestClient, cohort: str
) -> None:
    session_id = make_session(signed_in, cohort)
    state = boot_state(signed_in.get(f"/sessions/{session_id}/questions"))
    response = _post_session(signed_in, session_id, state["content"], base_id=state["base_id"])
    assert response.status_code == 303
    assert "still follows" in _notice(response)
    with connection() as conn:
        assert question_sets.current_override(conn, session_id) is None


def test_a_session_with_no_questions_cannot_be_provisioned_and_says_so(
    signed_in: TestClient, verified_template: FakeGoogleClient, bare_cohort: str
) -> None:
    session_id = make_session(signed_in, bare_cohort)
    state = boot_state(signed_in.get(f"/sessions/{session_id}"))
    assert state["a_blocked"] is True
    assert "seed-default" in state["a_questions"]["missing"]

    listed = boot_state(signed_in.get(f"/sessions?cohort={bare_cohort}"))["sessions"]
    assert listed[0]["part_a_questions"]["scope"] is None

    response = signed_in.post(f"/sessions/{session_id}/provision")
    assert response.status_code == 200
    assert "no Part A" in boot_state(response)["error"]
    assert not verified_template.calls("copy_form")


def test_questions_lock_once_the_form_is_published(
    signed_in: TestClient, verified_template: FakeGoogleClient, cohort: str
) -> None:
    session_id = make_session(signed_in, cohort)
    provisioned = boot_state(signed_in.post(f"/sessions/{session_id}/provision"))
    assert provisioned["ready"] is True
    assert provisioned["a_questions"]["provisioned"]["label"] == "default v1"
    assert [row["question_key"] for row in provisioned["a_question_map"]] == ["q_takeaway"]

    state = boot_state(signed_in.get(f"/sessions/{session_id}/questions"))
    assert state["locked"] is True
    assert "published" in state["lock_reason"]

    custom = _with_question(ONE_QUESTION, title="Too late for this one")
    refused = _post_session(signed_in, session_id, custom, base_id=state["base_id"])
    assert refused.status_code == 409
    refused_state = boot_state(refused)
    assert refused_state["locked"] is True
    assert any("locked" in e for e in refused_state["errors"])
    # What was posted comes back, so the edit is not lost to the refusal.
    assert refused_state["content"] == custom

    revert = signed_in.post(f"/sessions/{session_id}/questions/revert")
    assert "error=" in revert.headers["location"]
    with connection() as conn:
        assert question_sets.current_override(conn, session_id) is None

    # The default can still change; the published form keeps what it was built from.
    default_state = boot_state(signed_in.get(f"/template/questions?cohort={cohort}"))
    moved = _post_default(
        signed_in, cohort, _with_question(ONE_QUESTION, title="Anything else?"),
        base_id=default_state["base_id"],
    )
    assert moved.status_code == 303
    listed = boot_state(signed_in.get(f"/sessions?cohort={cohort}"))["sessions"]
    row = next(r for r in listed if r["session_id"] == session_id)
    assert row["part_a_questions"] == {"scope": "default", "version": 1, "provisioned": True}
    detail = boot_state(signed_in.get(f"/sessions/{session_id}"))
    assert detail["a_questions"]["locked"] is True


def test_unreadable_json_is_refused_and_the_page_redrawn(
    signed_in: TestClient, cohort: str
) -> None:
    response = _post_default(signed_in, cohort, "{this is not json")
    assert response.status_code == 400
    state = boot_state(response)
    assert any("JSON" in e for e in state["errors"])
    # Drawn from what is stored, since nothing readable was posted.
    assert state["content"]["questions"] == ONE_QUESTION["questions"]
    with connection() as conn:
        assert len(question_sets.history(conn, cohort_id=cohort)) == 1


def test_an_invalid_set_is_refused_and_what_was_posted_comes_back(
    signed_in: TestClient, cohort: str
) -> None:
    broken = _with_question(ONE_QUESTION, type="multiple_choice", title="Pick one", options=[])
    response = _post_default(signed_in, cohort, broken)
    assert response.status_code == 400
    state = boot_state(response)
    assert state["errors"], "the reasons are on the screen"
    assert state["content"] == broken
    with connection() as conn:
        assert question_sets.current_default(conn, cohort).version == 1


def test_a_save_from_a_version_someone_replaced_is_refused(
    signed_in: TestClient, cohort: str
) -> None:
    base = boot_state(signed_in.get(f"/template/questions?cohort={cohort}"))["base_id"]
    theirs = _with_question(ONE_QUESTION, title="Their question")
    mine = _with_question(ONE_QUESTION, title="My question")

    assert _post_default(signed_in, cohort, theirs, base_id=base).status_code == 303
    refused = _post_default(signed_in, cohort, mine, base_id=base)
    assert refused.status_code == 409
    state = boot_state(refused)
    assert any("changed while you were editing" in e for e in state["errors"])
    assert state["content"] == mine

    with connection() as conn:
        current = question_sets.current_default(conn, cohort)
    assert current.version == 2
    assert current.questions[-1]["title"] == "Their question"

    # The redrawn page is based on their version and says so, so pressing Save
    # again replaces it knowingly rather than silently.
    assert state["base_id"] == current.question_set_id
    assert any("replaces default v2" in e for e in state["errors"])
    again = _post_default(signed_in, cohort, mine, base_id=state["base_id"])
    assert again.status_code == 303


def test_the_approved_exit_ticket_seeds_a_default(
    signed_in: TestClient, bare_cohort: str
) -> None:
    response = signed_in.post("/template/questions/seed", data={"cohort": bare_cohort})
    assert response.status_code == 303
    assert "version 1" in _notice(response)
    with connection() as conn:
        seeded = question_sets.current_default(conn, bare_cohort)
    assert seeded.source == "seed_file"
    assert "q_biggest_takeaway" in {q["key"] for q in seeded.questions}

    again = signed_in.post("/template/questions/seed", data={"cohort": bare_cohort})
    assert "already" in _notice(again)


def test_importing_a_form_makes_it_the_default(
    signed_in: TestClient, fake: FakeGoogleClient, bare_cohort: str
) -> None:
    form_id = fake.simulate_form_created_by_hand(
        [
            {
                "title": "What stuck with you?",
                "questionItem": {"question": {"required": True, "textQuestion": {"paragraph": True}}},
            },
            {
                "title": "How was it?",
                "questionItem": {
                    "question": {"scaleQuestion": {"low": 1, "high": 5}}
                },
            },
        ]
    )
    response = signed_in.post(
        "/template/questions/import",
        data={"cohort": bare_cohort, "form": f"https://docs.google.com/forms/d/{form_id}/edit"},
    )
    assert response.status_code == 303
    assert "Imported" in _notice(response), _notice(response)
    with connection() as conn:
        imported = question_sets.current_default(conn, bare_cohort)
    assert imported.source == "import_form" and imported.source_ref == form_id
    assert [q["type"] for q in imported.questions] == ["paragraph", "linear_scale"]


def test_importing_a_responder_link_is_refused_with_the_reason(
    signed_in: TestClient, fake: FakeGoogleClient, cohort: str
) -> None:
    response = signed_in.post(
        "/template/questions/import",
        data={
            "cohort": cohort,
            "form": "https://docs.google.com/forms/d/e/1FAIpQLSexampleexampleexample/viewform",
        },
    )
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert "/edit" in _notice(response)
    assert not fake.calls("get_form")


def test_an_unknown_cohort_or_session_is_a_404(signed_in: TestClient) -> None:
    assert _post_default(signed_in, "no-such-cohort", ONE_QUESTION).status_code == 404
    assert signed_in.get(f"/sessions/{uuid.uuid4()}/questions").status_code == 404
    assert signed_in.get("/sessions/not-a-uuid/questions").status_code == 404


# --------------------------------------------------------------------------
# screen 5 — review
# --------------------------------------------------------------------------


def _seed_checkin(
    cohort_id: str,
    session_id: str | None,
    email: str,
    *,
    submitted_at: str = "2026-09-15T17:10:00Z",
    source: str = "csv",
    status: str = "needs_review",
    rule_name: str = "unverified_email_in_window",
    confidence: float = 0.0,
) -> str:
    """One observation with a rule's decision on it.

    The defaults are the commonest needs_review case now: a CSV row whose
    address was typed, submitted inside the only window it fits.
    """
    with connection() as conn:
        # A load run carries the cohort for a row that matched no session.
        load = fetch_all(
            conn,
            "insert into load_run (source, origin, cohort_id) values (%s, 'test', %s) "
            "returning load_id",
            (source, cohort_id),
        )
        row = fetch_all(
            conn,
            """
            insert into checkin (
                source_event_id, source, submitted_email, submitted_at_utc,
                submitted_at_raw, session_id, session_match, load_id
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            returning checkin_id
            """,
            (
                uuid.uuid4().hex,
                source,
                email,
                submitted_at,
                submitted_at,
                session_id,
                "matched" if session_id else "none",
                load[0]["load_id"],
            ),
        )
        checkin_id = str(row[0]["checkin_id"])
        record_decision(
            conn,
            checkin_id,
            status=status,
            decided_by="rule",
            rule_name=rule_name,
            confidence=confidence,
        )
    return checkin_id


def test_the_review_queue_lists_and_one_click_records_a_human_decision(
    signed_in: TestClient, cohort: str
) -> None:
    session_id = make_session(signed_in, cohort)
    checkin_id = _seed_checkin(cohort, session_id, "unknown@example.invalid")

    queue = signed_in.get(f"/review?tab=needs_review&cohort={cohort}")
    assert queue.status_code == 200
    state = boot_state(queue)
    assert "expected" not in state, "no passphrase to compare against any more"
    row = next(r for r in state["rows"] if r["checkin_id"] == checkin_id)
    # What a human judges by now: when it arrived against the window.
    assert row["timing"]["relation"] == "inside"

    response = signed_in.post(
        f"/review/{checkin_id}/decide",
        data={"status": "attended", "note": "seen on the call", "tab": "needs_review", "cohort": cohort},
    )
    assert response.status_code == 303

    with connection() as conn:
        decision = current_decision(conn, checkin_id)
    assert decision is not None
    assert decision["status"] == "attended"
    assert decision["decided_by"] == "human"
    assert decision["human_email"] == STAFF
    assert decision["note"] == "seen on the call"
    assert float(decision["confidence"]) == 1.0


def test_the_ai_tab_shows_the_models_reasoning(signed_in: TestClient, cohort: str) -> None:
    """Tier 2 is retired, but decisions it made are still readable and overridable."""
    session_id = make_session(signed_in, cohort)
    checkin_id = _seed_checkin(cohort, session_id, "fellow@example.invalid")
    with connection() as conn:
        record_decision(
            conn,
            checkin_id,
            status="attended",
            decided_by="ai",
            confidence=0.92,
            ai_model="gemini-2.5-flash",
            ai_prompt_version="v1",
            ai_reasoning="The answer states the passphrase in a sentence.",
        )

    page = signed_in.get(f"/review?tab=ai&cohort={cohort}")
    assert page.status_code == 200
    assert "The answer states the passphrase in a sentence." in page.text
    assert "gemini-2.5-flash" in page.text
    assert "v1" in page.text
    assert boot_state(page)["has_ai_decisions"] is True


def test_the_ai_tab_is_hidden_when_there_is_nothing_legacy_to_show(
    signed_in: TestClient, db
) -> None:
    """`db` truncates, so no AI decision exists anywhere: the tab is not offered,
    and asking for it by URL lands on the queue instead."""
    state = boot_state(signed_in.get("/review?tab=ai"))
    assert state["has_ai_decisions"] is False
    assert state["tab"] == "needs_review"


def test_the_outside_the_window_tab_says_how_far_out(
    signed_in: TestClient, cohort: str
) -> None:
    # 13:05 New York on 15 September is 17:05Z; 60 minutes and 15 of grace
    # either side make the window 16:50Z to 18:20Z.
    session_id = make_session(signed_in, cohort)
    late = _seed_checkin(
        cohort, session_id, "late@example.invalid",
        submitted_at="2026-09-15T18:32:00Z", source="forms_api",
        status="not_attended", rule_name="outside_session_window", confidence=0.6,
    )
    early = _seed_checkin(
        cohort, session_id, "early@example.invalid",
        submitted_at="2026-09-15T16:40:00Z", source="forms_api",
        status="not_attended", rule_name="outside_session_window", confidence=0.6,
    )
    nowhere = _seed_checkin(
        cohort, None, "nowhere@example.invalid",
        submitted_at="2026-09-20T09:00:00Z",
        status="not_attended", rule_name="outside_all_windows", confidence=0.6,
    )
    inside = _seed_checkin(cohort, session_id, "inside@example.invalid")

    state = boot_state(signed_in.get(f"/review?tab=outside_window&cohort={cohort}"))
    assert state["tab"] == "outside_window"
    rows = {row["checkin_id"]: row for row in state["rows"]}
    assert set(rows) == {late, early, nowhere}, "only the rules' outside-window calls"
    assert inside not in rows
    assert rows[late]["timing"] == {"relation": "after", "minutes": 12}
    assert rows[early]["timing"] == {"relation": "before", "minutes": 10}
    assert rows[nowhere]["timing"]["relation"] is None

    # A person's override takes it off the list: it is no longer the rule's call.
    response = signed_in.post(
        f"/review/{late}/decide",
        data={"status": "attended", "tab": "outside_window", "cohort": cohort},
    )
    assert response.status_code == 303
    state = boot_state(signed_in.get(f"/review?tab=outside_window&cohort={cohort}"))
    assert late not in {row["checkin_id"] for row in state["rows"]}


def test_an_unresolved_address_appears_on_the_identities_tab(
    signed_in: TestClient, cohort: str
) -> None:
    with connection() as conn:
        execute(
            conn,
            "insert into identity_unresolved (cohort_id, email, occurrence_count) values (%s, %s, 3)",
            (cohort, "typo@example.invalid"),
        )
    page = signed_in.get(f"/review?tab=identities&cohort={cohort}")
    assert page.status_code == 200
    state = boot_state(page)
    assert state["tab"] == "identities"
    assert any(row["email"] == "typo@example.invalid" for row in state["rows"])


def test_an_invalid_review_status_is_refused(signed_in: TestClient, cohort: str) -> None:
    session_id = make_session(signed_in, cohort)
    checkin_id = _seed_checkin(cohort, session_id, "fellow@example.invalid")
    response = signed_in.post(
        f"/review/{checkin_id}/decide", data={"status": "definitely_attended"}
    )
    assert response.status_code == 404
    with connection() as conn:
        decision = current_decision(conn, checkin_id)
    assert decision is not None and decision["decided_by"] == "rule"


# --------------------------------------------------------------------------
# degradation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/", "/template", "/sessions", "/sessions/new", "/review"]
)
def test_every_screen_degrades_when_the_database_is_unreachable(
    signed_in: TestClient, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    monkeypatch.setenv("CUFA_DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:1/postgres")
    reset_settings_cache()
    try:
        response = signed_in.get(path)
        assert response.status_code == 503, path
        assert "The database is not answering" in response.text
        assert "supabase start" in response.text  # the hint, not a stack trace
    finally:
        monkeypatch.undo()
        reset_settings_cache()


def test_the_json_endpoint_degrades_as_json(
    signed_in: TestClient, monkeypatch: pytest.MonkeyPatch, cohort: str
) -> None:
    session_id = make_session(signed_in, cohort)
    monkeypatch.setenv("CUFA_DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:1/postgres")
    reset_settings_cache()
    try:
        response = signed_in.get(f"/sessions/{session_id}/responses.json")
        assert response.status_code == 503
        assert response.json()["error"] == "database_unreachable"
    finally:
        monkeypatch.undo()
        reset_settings_cache()


# --------------------------------------------------------------------------
# logging
# --------------------------------------------------------------------------


def test_no_email_address_is_logged_at_info(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("INFO"):
        client.post("/signin/dev", data={"email": STAFF, "next": "/"})
    assert any("sign-in" in record.getMessage() for record in caplog.records)
    for record in caplog.records:
        if record.levelno >= 20:
            assert STAFF not in record.getMessage()


# --------------------------------------------------------------------------
# the QR encoder
# --------------------------------------------------------------------------


def test_reed_solomon_matches_the_published_vector() -> None:
    """The worked example from ISO 18004 / the standard QR tutorials.

    "HELLO WORLD" at version 1-Q: these thirteen data codewords must produce
    exactly these ten error correction codewords. If the field arithmetic is
    wrong, every code this module makes is unreadable while still looking like a
    QR code, so this is the check that matters most.
    """
    data = bytes([32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236])
    expected = bytes([168, 72, 22, 82, 217, 54, 156, 0, 46, 15, 180, 122, 16])
    assert qr.error_correction_codewords(data, 13) == expected


def test_format_information_matches_the_standard_table() -> None:
    """The 32 published format strings; these are the level M row."""
    expected = {
        0: 0x5412, 1: 0x5125, 2: 0x5E7C, 3: 0x5B4B,
        4: 0x45F9, 5: 0x40CE, 6: 0x4F97, 7: 0x4AA0,
    }
    for mask, bits in expected.items():
        canvas = qr._Canvas(1)
        canvas.draw_function_patterns()
        canvas.draw_format_info(mask)
        # Read the fifteen bits back out of the top-left copy.
        read = 0
        for i in range(6):
            read |= int(canvas.modules[i][8]) << i
        read |= int(canvas.modules[7][8]) << 6
        read |= int(canvas.modules[8][8]) << 7
        read |= int(canvas.modules[8][7]) << 8
        for i in range(9, 15):
            read |= int(canvas.modules[8][14 - i]) << i
        assert read == bits, f"mask {mask}"


def test_the_matrix_has_the_structure_a_scanner_looks_for() -> None:
    modules = qr.qr_matrix("https://forms.example.invalid/d/e/fake-form-0002/viewform")
    size = len(modules)
    assert size == 4 * 4 + 17  # 57 bytes needs version 4

    for origin_row, origin_col in ((0, 0), (0, size - 7), (size - 7, 0)):
        for row in range(7):
            for col in range(7):
                distance = max(abs(row - 3), abs(col - 3))
                assert modules[origin_row + row][origin_col + col] == (distance != 2)

    for i in range(8, size - 8):
        assert modules[6][i] == (i % 2 == 0)
        assert modules[i][6] == (i % 2 == 0)

    assert modules[size - 8][8] is True  # the module that is always dark


def test_the_encoder_round_trips_through_an_independent_read() -> None:
    """Decode the matrix without reusing the encoder's placement code.

    This walks the symbol the way a reader does — undo the mask the format bits
    name, follow the zigzag, de-interleave the blocks — and recovers the string.
    It catches placement, masking and interleaving errors that a structural
    check cannot see.
    """
    for text in (
        "a",
        "https://forms.example.invalid/d/e/fake-form-0002/viewform",
        "https://docs.google.com/forms/d/e/1FAIpQLSf7xK9mQ2ZzL0pR4tVnB8/viewform",
        "x" * 213,
    ):
        assert _decode(qr.qr_matrix(text)) == text


def test_the_svg_is_self_contained_and_black_on_white() -> None:
    svg = qr.qr_svg("https://forms.example.invalid/d/e/fake-form-0002/viewform")
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "http://www.w3.org/2000/svg" in svg
    assert 'fill="#000000"' in svg and 'fill="#ffffff"' in svg
    assert 'role="img"' in svg and "<title>" in svg
    # No external anything: no script, no href, no remote reference.
    assert "<script" not in svg and "href" not in svg and "http://www.w3.org/1999/xlink" not in svg


def test_a_payload_that_will_not_fit_raises_rather_than_truncating() -> None:
    with pytest.raises(qr.QrTooLong):
        qr.qr_matrix("x" * 400)


def _decode(modules: list[list[bool]]) -> str:
    """A minimal QR reader, used only to check the encoder."""
    size = len(modules)
    version = (size - 17) // 4

    # Format information names the mask; read the top-left copy back.
    raw = 0
    for i in range(6):
        raw |= int(modules[i][8]) << i
    raw |= int(modules[7][8]) << 6
    raw |= int(modules[8][8]) << 7
    raw |= int(modules[8][7]) << 8
    for i in range(9, 15):
        raw |= int(modules[8][14 - i]) << i
    mask = ((raw ^ 0x5412) >> 10) & 0b111

    scratch = qr._Canvas(version)
    scratch.draw_function_patterns()

    bits: list[int] = []
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vertical in range(size):
            for offset in range(2):
                col = right - offset
                upward = ((right + 1) & 2) == 0
                row = (size - 1 - vertical) if upward else vertical
                if scratch.function[row][col]:
                    continue
                value = modules[row][col]
                if qr._mask_condition(mask, row, col):
                    value = not value
                bits.append(int(value))
        right -= 2

    stream = bytearray()
    for index in range(0, len(bits) - 7, 8):
        stream.append(int("".join(str(bit) for bit in bits[index : index + 8]), 2))

    layout = qr._BLOCKS_M[version]
    sizes = [layout.group1_data] * layout.group1_blocks + [layout.group2_data] * layout.group2_blocks
    blocks: list[bytearray] = [bytearray() for _ in sizes]
    position = 0
    for i in range(max(sizes)):
        for block_index, block_size in enumerate(sizes):
            if i < block_size:
                blocks[block_index].append(stream[position])
                position += 1

    payload = bytearray()
    for block in blocks:
        payload.extend(block)

    count_bits = 8 if version <= 9 else 16
    header = int.from_bytes(payload[: (4 + count_bits) // 8 + 1], "big")
    total = (4 + count_bits) % 8
    mode = payload[0] >> 4
    assert mode == 0b0100, "byte mode expected"
    length = (header >> (8 - total)) & ((1 << count_bits) - 1) if total else 0
    if count_bits == 8:
        length = ((payload[0] & 0x0F) << 4) | (payload[1] >> 4)
        data = bytes(((payload[i + 1] & 0x0F) << 4) | (payload[i + 2] >> 4) for i in range(length))
    else:
        length = ((payload[0] & 0x0F) << 12) | (payload[1] << 4) | (payload[2] >> 4)
        data = bytes(((payload[i + 2] & 0x0F) << 4) | (payload[i + 3] >> 4) for i in range(length))
    return data.decode("utf-8")


# --------------------------------------------------------------------------
# assignments and roster — the two things that used to be CLI-only
# --------------------------------------------------------------------------


def test_an_assignment_can_be_created_from_the_console(
    signed_in: TestClient, cohort: str
) -> None:
    response = signed_in.post(
        "/assignments/new",
        data={
            "title": "Community interview notes",
            "due_at": "2026-09-18T17:00",
            "timezone": "America/Chicago",
            "cohort_id": cohort,
            "url": "https://classroom.example.org/interview",
            "description": "Bring two quotes you did not expect.",
            "status": "active",
        },
    )
    assert response.status_code == 303

    with connection() as conn:
        rows = fetch_all(
            conn, "select * from assignment where cohort_id = %s", (cohort,)
        )
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "Community interview notes"
    assert row["timezone"] == "America/Chicago"
    # The wall-clock value that was typed is kept as typed; the UTC instant is
    # derived from it and the zone, not from the browser.
    assert row["due_at_local"].strftime("%Y-%m-%dT%H:%M") == "2026-09-18T17:00"
    assert row["due_at_utc"].hour == 22  # 17:00 America/Chicago in September


def test_an_assignment_with_an_unknown_zone_saves_nothing(
    signed_in: TestClient, cohort: str
) -> None:
    response = signed_in.post(
        "/assignments/new",
        data={
            "title": "Never saved",
            "due_at": "2026-09-18T17:00",
            "timezone": "Mars/Olympus_Mons",
            "cohort_id": cohort,
            "status": "active",
        },
    )
    assert response.status_code == 400
    state = boot_state(response)
    assert state["errors"], "the screen has to say why"
    # And the typing is handed back rather than thrown away.
    assert state["values"]["title"] == "Never saved"

    with connection() as conn:
        rows = fetch_all(
            conn, "select 1 from assignment where cohort_id = %s", (cohort,)
        )
    assert rows == []


def test_editing_an_assignment_changes_it(signed_in: TestClient, cohort: str) -> None:
    signed_in.post(
        "/assignments/new",
        data={
            "title": "First title",
            "due_at": "2026-09-18T17:00",
            "timezone": "America/Chicago",
            "cohort_id": cohort,
            "status": "active",
        },
    )
    with connection() as conn:
        row = fetch_all(
            conn, "select assignment_id from assignment where cohort_id = %s", (cohort,)
        )[0]
    assignment_id = str(row["assignment_id"])

    # The edit screen offers back what is stored, in the shape the input wants.
    state = boot_state(signed_in.get(f"/assignments/{assignment_id}/edit"))
    assert state["values"]["due_at"] == "2026-09-18T17:00"
    assert state["values"]["title"] == "First title"

    response = signed_in.post(
        f"/assignments/{assignment_id}/edit",
        data={
            "title": "Second title",
            "due_at": "2026-09-19T09:30",
            "timezone": "America/New_York",
            "cohort_id": cohort,
            "status": "cancelled",
        },
    )
    assert response.status_code == 303

    with connection() as conn:
        updated = fetch_all(
            conn, "select * from assignment where assignment_id = %s", (assignment_id,)
        )[0]
    assert updated["title"] == "Second title"
    assert updated["status"] == "cancelled"
    assert updated["timezone"] == "America/New_York"


def test_a_fellows_timezone_can_be_set_from_the_roster(
    signed_in: TestClient, cohort: str
) -> None:
    with connection() as conn:
        execute(
            conn,
            "insert into fellow (fellow_id, cohort_id, full_name, primary_email) "
            "values (%s, %s, %s, %s)",
            (f"CU-{uuid.uuid4().hex[:6]}", cohort, "Ada Testcase", "ada@example.invalid"),
        )
        fellow_id = fetch_all(
            conn, "select fellow_id from fellow where cohort_id = %s", (cohort,)
        )[0]["fellow_id"]

    # The screen is told who has no zone, because that is the thing worth acting on.
    state = boot_state(signed_in.get(f"/roster?cohort={cohort}"))
    assert [f["fellow_id"] for f in state["fellows"]] == [fellow_id]
    assert state["fellows"][0]["timezone"] is None

    response = signed_in.post(
        f"/roster/{fellow_id}/timezone",
        data={"timezone": "America/Chicago", "cohort": cohort},
    )
    assert response.status_code == 303

    with connection() as conn:
        row = fetch_all(
            conn, "select timezone from fellow where fellow_id = %s", (fellow_id,)
        )[0]
    assert row["timezone"] == "America/Chicago"


def test_an_unknown_zone_is_refused_and_the_old_one_kept(
    signed_in: TestClient, cohort: str
) -> None:
    """The console must not be a way around the validation the CSV loader applies."""
    fellow_id = f"CU-{uuid.uuid4().hex[:6]}"
    with connection() as conn:
        execute(
            conn,
            "insert into fellow (fellow_id, cohort_id, full_name, primary_email, timezone) "
            "values (%s, %s, %s, %s, %s)",
            (fellow_id, cohort, "Ada Testcase", "ada@example.invalid", "America/Denver"),
        )

    response = signed_in.post(
        f"/roster/{fellow_id}/timezone",
        data={"timezone": "Mars/Olympus_Mons", "cohort": cohort},
    )
    assert response.status_code == 303
    assert "error=" in response.headers["location"]

    with connection() as conn:
        row = fetch_all(
            conn, "select timezone from fellow where fellow_id = %s", (fellow_id,)
        )[0]
    assert row["timezone"] == "America/Denver", "a rejected zone must not clear the old one"


def test_a_blank_timezone_clears_it(signed_in: TestClient, cohort: str) -> None:
    """Legal on purpose: reminders then fall back to the session's zone."""
    fellow_id = f"CU-{uuid.uuid4().hex[:6]}"
    with connection() as conn:
        execute(
            conn,
            "insert into fellow (fellow_id, cohort_id, full_name, primary_email, timezone) "
            "values (%s, %s, %s, %s, %s)",
            (fellow_id, cohort, "Ada Testcase", "ada@example.invalid", "America/Denver"),
        )

    assert signed_in.post(
        f"/roster/{fellow_id}/timezone", data={"timezone": "  ", "cohort": cohort}
    ).status_code == 303

    with connection() as conn:
        row = fetch_all(
            conn, "select timezone from fellow where fellow_id = %s", (fellow_id,)
        )[0]
    assert row["timezone"] is None


# --------------------------------------------------------------------------
# loading a roster from the console
# --------------------------------------------------------------------------
#
# The roster is the first thing anybody does and it used to need a terminal.
# These tests are about the failure a file picker introduces and a command line
# does not: somebody choosing the wrong file out of a folder. A wrong file must
# be refused whole, because a partly-applied roster is worse than none — it
# looks like a loaded cohort and is missing people.


ROSTER_CSV = (
    "fellow_id,full_name,email,timezone\n"
    "CU-9001,Ada Nwosu,ada@example.invalid,America/New_York\n"
    "CU-9002,Bo Salas,bo@example.invalid,\n"
)


def _upload(client: TestClient, cohort: str, body: str | bytes, name: str = "roster.csv"):
    payload = body.encode() if isinstance(body, str) else body
    return client.post(
        "/roster/upload",
        data={"cohort": cohort},
        files={"file": (name, payload, "text/csv")},
    )


def test_a_roster_loads_from_the_console(signed_in: TestClient, db) -> None:
    response = _upload(signed_in, "console-roster", ROSTER_CSV)
    assert response.status_code == 303
    assert "notice=" in response.headers["location"]

    page = signed_in.get("/roster?cohort=console-roster")
    ids = {f["fellow_id"] for f in boot_state(page)["fellows"]}
    assert {"CU-9001", "CU-9002"} <= ids


def test_loading_the_same_roster_twice_updates_rather_than_duplicates(
    signed_in: TestClient, db
) -> None:
    _upload(signed_in, "console-twice", ROSTER_CSV)
    corrected = ROSTER_CSV.replace("Ada Nwosu", "Ada Nwosu-Bell")
    assert _upload(signed_in, "console-twice", corrected).status_code == 303

    fellows = boot_state(signed_in.get("/roster?cohort=console-twice"))["fellows"]
    ada = [f for f in fellows if f["fellow_id"] == "CU-9001"]
    assert len(ada) == 1, "upsert, not insert"
    assert ada[0]["full_name"] == "Ada Nwosu-Bell"


def test_the_wrong_spreadsheet_is_refused_and_writes_nothing(
    signed_in: TestClient, db
) -> None:
    """The case a file picker makes possible and a command line does not."""
    response = _upload(signed_in, "console-wrong", "invoice_no,amount\n1,20.00\n")
    assert response.status_code == 303
    location = response.headers["location"]
    assert "error=" in location
    # The message names the columns it wanted, in the words a spreadsheet uses.
    assert "fellow_id" in unquote(location) and "email" in unquote(location)

    page = signed_in.get("/roster?cohort=console-wrong")
    assert boot_state(page)["fellows"] == [], "nothing may be written by a refused file"


def test_a_bad_timezone_is_refused_before_any_row_is_written(
    signed_in: TestClient, db
) -> None:
    """`load_roster` raises on a bad zone mid-file, which would leave the rows
    before it written and the rows after it not. The check runs first."""
    body = (
        "fellow_id,full_name,email,timezone\n"
        "CU-9101,Fine Person,fine@example.invalid,America/New_York\n"
        "CU-9102,Bad Zone,bad@example.invalid,Mars/Olympus\n"
    )
    response = _upload(signed_in, "console-badzone", body)
    assert "error=" in response.headers["location"]
    assert "Mars/Olympus" in unquote(response.headers["location"])

    page = signed_in.get("/roster?cohort=console-badzone")
    assert boot_state(page)["fellows"] == [], "not even the good row before it"


def test_an_empty_file_says_so(signed_in: TestClient, db) -> None:
    response = _upload(signed_in, "console-empty", "")
    assert "error=" in response.headers["location"]
    assert "empty" in unquote(response.headers["location"]).lower()


def test_uploading_a_roster_needs_a_session(client: TestClient, db) -> None:
    assert _upload(client, "console-anon", ROSTER_CSV).status_code in (303, 401, 403)
    assert "/signin" in _upload(client, "console-anon", ROSTER_CSV).headers.get(
        "location", "/signin"
    )


# --------------------------------------------------------------------------
# loading a schedule from the console
# --------------------------------------------------------------------------
#
# Same danger as the roster, plus one: `_parse_local` raises partway down a
# file, and a half-created schedule is a term with a hole in it that nobody
# notices until a form does not go out.


SESSIONS_CSV = (
    "cohort_id,title,scheduled_at_local,timezone,duration_minutes\n"
    "console-sched,Week 1 — Openings,2026-10-06 19:00,America/New_York,60\n"
    "console-sched,Week 2 — Evidence,2026-10-13 19:00,America/New_York,60\n"
)


def _upload_sessions(client: TestClient, body: str, name: str = "schedule.csv"):
    return client.post(
        "/sessions/upload", files={"file": (name, body.encode(), "text/csv")}
    )


def test_a_schedule_loads_from_the_console(signed_in: TestClient, db) -> None:
    response = _upload_sessions(signed_in, SESSIONS_CSV)
    assert response.status_code == 303
    assert "notice=" in response.headers["location"]

    titles = {
        s["title"] for s in boot_state(signed_in.get("/sessions?cohort=console-sched"))["sessions"]
    }
    assert {"Week 1 — Openings", "Week 2 — Evidence"} <= titles


def test_reloading_a_schedule_adds_what_is_new_and_leaves_the_rest(
    signed_in: TestClient, db
) -> None:
    _upload_sessions(signed_in, SESSIONS_CSV)
    extended = SESSIONS_CSV + (
        "console-sched,Week 3 — Coalitions,2026-10-20 19:00,America/New_York,60\n"
    )
    response = _upload_sessions(signed_in, extended)
    assert response.status_code == 303

    sessions = boot_state(signed_in.get("/sessions?cohort=console-sched"))["sessions"]
    assert len([s for s in sessions if s["title"] == "Week 1 — Openings"]) == 1
    assert any(s["title"] == "Week 3 — Coalitions" for s in sessions)


def test_an_unreadable_start_time_creates_nothing(signed_in: TestClient, db) -> None:
    """The row order matters: the good session comes first, so a loader that
    wrote as it went would leave it behind."""
    body = (
        "cohort_id,title,scheduled_at_local,timezone,duration_minutes\n"
        "console-badtime,Good One,2026-10-06 19:00,America/New_York,60\n"
        "console-badtime,Bad One,next tuesday,America/New_York,60\n"
    )
    response = _upload_sessions(signed_in, body)
    assert "error=" in response.headers["location"]
    assert "next tuesday" in unquote(response.headers["location"])

    assert boot_state(signed_in.get("/sessions?cohort=console-badtime"))["sessions"] == []


def test_a_schedule_missing_its_columns_says_which(signed_in: TestClient, db) -> None:
    response = _upload_sessions(signed_in, "title,notes\nWeek 3,hello\n")
    location = unquote(response.headers["location"])
    assert "error=" in response.headers["location"]
    for wanted in ("cohort_id", "scheduled_at_local", "timezone", "duration_minutes"):
        assert wanted in location


def test_the_session_template_is_a_file_this_loader_accepts(
    signed_in: TestClient, db
) -> None:
    """A template that does not round-trip is worse than none: it teaches the
    wrong columns with the console's own authority."""
    template = signed_in.get("/sessions/template.csv")
    assert template.status_code == 200
    assert "attachment" in template.headers["content-disposition"]

    response = _upload_sessions(signed_in, template.text)
    assert response.status_code == 303
    assert "error=" not in response.headers["location"], response.headers["location"]


# --------------------------------------------------------------------------
# Zoom transcripts from the console
# --------------------------------------------------------------------------
#
# `cufa zoom ingest --session <uuid> --vtt <path>` asked somebody to find a
# session's UUID, which this console knows and a person does not.


VTT = """WEBVTT

1
00:00:01.000 --> 00:00:09.000
<v Ada Nwosu>I think the budget line is the place to start.</v>

2
00:00:10.000 --> 00:00:14.000
<v Bo Salas>Agreed, and the committee minutes back it up.</v>
"""


def _upload_vtt(client: TestClient, session_id: str, body: str, name: str = "t.vtt"):
    return client.post(
        f"/sessions/{session_id}/transcript",
        files={"file": (name, body.encode(), "text/vtt")},
    )


def test_a_transcript_uploads_and_reports_what_it_read(
    signed_in: TestClient, db, cohort
) -> None:
    session_id = make_session(signed_in, cohort, title="Transcript session")
    response = _upload_vtt(signed_in, session_id, VTT)
    assert response.status_code == 303
    assert "notice=" in response.headers["location"]
    assert "2 turns" in unquote(response.headers["location"])


def test_uploading_the_same_transcript_twice_writes_nothing_new(
    signed_in: TestClient, db, cohort
) -> None:
    session_id = make_session(signed_in, cohort, title="Twice session")
    _upload_vtt(signed_in, session_id, VTT)
    again = unquote(_upload_vtt(signed_in, session_id, VTT).headers["location"])
    assert "nothing changed" in again


def test_a_file_with_no_speakers_is_refused_in_words_a_person_can_act_on(
    signed_in: TestClient, db, cohort
) -> None:
    session_id = make_session(signed_in, cohort, title="No speakers session")
    response = _upload_vtt(signed_in, session_id, "WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.000\nmumbling\n")
    location = unquote(response.headers["location"])
    assert "error=" in response.headers["location"]
    assert ".vtt" in location


def test_an_empty_transcript_says_so(signed_in: TestClient, db, cohort) -> None:
    session_id = make_session(signed_in, cohort, title="Empty transcript session")
    response = _upload_vtt(signed_in, session_id, "")
    assert "error=" in response.headers["location"]
    assert "empty" in unquote(response.headers["location"]).lower()


def test_the_transcript_stores_no_spoken_words(signed_in: TestClient, db, cohort) -> None:
    """The claim the screen makes to the person deciding whether to upload."""
    session_id = make_session(signed_in, cohort, title="No words session")
    _upload_vtt(signed_in, session_id, VTT)

    from cufa.db import connection as db_connection

    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("select * from zoom_transcript_turn limit 1")
            columns = [c.name for c in cur.description]
            row = cur.fetchone()
    assert row is not None
    stored = " ".join(str(v) for v in row)
    assert "budget line" not in stored
    assert not any("text" in c or "body" in c or "words" == c for c in columns if c != "word_count")


def _roster_for(client: TestClient, cohort: str) -> None:
    """Two fellows whose names match the speakers in VTT, so the matching side
    of `speaking_share` is exercised rather than assumed."""
    body = (
        "fellow_id,full_name,email\n"
        "CU-8001,Ada Nwosu,ada.t@example.invalid\n"
        "CU-8002,Bo Salas,bo.t@example.invalid\n"
    )
    assert _upload(client, cohort, body).status_code == 303


def test_the_session_page_renders_after_a_transcript_is_uploaded(
    signed_in: TestClient, db, cohort
) -> None:
    """The upload route and the screen read the same objects from opposite
    ends, and nothing above loads the page afterwards — which is how a wrong
    attribute name on SpeakingShare got as far as a browser."""
    _roster_for(signed_in, cohort)
    session_id = make_session(signed_in, cohort, title="Renders session")
    _upload_vtt(signed_in, session_id, VTT)

    page = signed_in.get(f"/sessions/{session_id}")
    assert page.status_code == 200
    speaking = boot_state(page)["speaking"]
    assert len(speaking) == 2
    for row in speaking:
        assert row["name"], "every speaker needs something to show"
        assert row["turns"] >= 1
        assert 0.0 <= row["share"] <= 1.0


def test_an_unmatched_speaker_is_shown_as_one_rather_than_dropped(
    signed_in: TestClient, db, cohort
) -> None:
    """A guest, a co-teacher and a fellow who renamed themselves in Zoom all
    look alike. None of them is counted for, and none of them vanishes."""
    _roster_for(signed_in, cohort)
    session_id = make_session(signed_in, cohort, title="Guest session")
    with_guest = VTT + (
        "\n3\n00:00:15.000 --> 00:00:19.000\n"
        "<v Guest Facilitator>Say more about the minutes.</v>\n"
    )
    _upload_vtt(signed_in, session_id, with_guest)

    speaking = boot_state(signed_in.get(f"/sessions/{session_id}"))["speaking"]
    guests = [r for r in speaking if not r["matched"]]
    assert [g["speaker_name"] for g in guests] == ["Guest Facilitator"]
    assert all(g["fellow_id"] is None for g in guests)
