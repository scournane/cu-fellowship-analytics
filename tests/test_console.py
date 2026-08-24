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
from urllib.parse import parse_qs, urlparse

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


@pytest.fixture
def cohort() -> str:
    """A cohort of this test's own.

    Check-in rows cannot be deleted — the immutability trigger blocks it — so
    isolation is by fresh key rather than by cleanup.
    """
    cohort_id = f"test-{uuid.uuid4().hex[:8]}"
    with connection() as conn:
        execute(
            conn,
            "insert into cohort (cohort_id, label) values (%s, %s)",
            (cohort_id, "console test cohort"),
        )
    return cohort_id


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
    passphrase: str = "justice",
    confirm_reuse: str = "",
) -> str:
    response = client.post(
        "/sessions/new",
        data={
            "title": title,
            "scheduled_at": scheduled_at,
            "timezone": "America/New_York",
            "duration_minutes": "60",
            "grace_minutes": "15",
            "passphrase": passphrase,
            "cohort_id": cohort_id,
            "confirm_reuse": confirm_reuse,
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
        "/review?tab=ai",
        "/review?tab=identities",
        f"/sessions/{session_id}/responses.json",
        "/api/passphrase/suggest",
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
    verified_template.seed_responses(
        form["form_id"],
        [
            (f"one-{tag}@example.invalid", "2026-09-15T17:10:00Z", "justice"),
            (f"two-{tag}@example.invalid", "2026-09-15T17:12:00Z", "the word was justice"),
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


def test_the_accessibility_reminder_is_in_the_ui(
    signed_in: TestClient, verified_template: FakeGoogleClient, cohort: str
) -> None:
    from cufa.passphrase import ACCESSIBILITY_REMINDER

    session_id = make_session(signed_in, cohort, passphrase="lantern")
    page = signed_in.get(f"/sessions/{session_id}").text
    assert ACCESSIBILITY_REMINDER.split(".")[0] in page
    assert "aloud" in page.lower() and "screen" in page.lower()
    # And the word itself is displayed for the teacher to read out.
    assert "lantern" in page


# --------------------------------------------------------------------------
# screen 3 — sessions
# --------------------------------------------------------------------------


def test_the_passphrase_guidance_is_inline_on_the_form(signed_in: TestClient) -> None:
    from cufa.passphrase import GUIDANCE

    state = boot_state(signed_in.get("/sessions/new"))
    assert GUIDANCE.split(".")[0] in state["guidance"]
    # The zone is deliberately left empty here: the screen fills it from the
    # browser. That now happens inside the bundle, so it is proved by the
    # browser walk-through rather than by this request.
    assert state["values"]["timezone"] == ""


def test_suggest_returns_a_word_from_the_curated_list(signed_in: TestClient) -> None:
    from cufa.passphrase import wordlist

    payload = signed_in.get("/api/passphrase/suggest").json()
    assert payload["passphrase"] in wordlist()


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


def test_a_reused_passphrase_warns_and_refuses_to_save_until_confirmed(
    signed_in: TestClient, cohort: str
) -> None:
    make_session(signed_in, cohort, title="Week 1", passphrase="justice")

    response = signed_in.post(
        "/sessions/new",
        data={
            "title": "Week 2",
            "scheduled_at": "2026-09-22T13:05",
            "timezone": "America/New_York",
            "duration_minutes": "60",
            "grace_minutes": "15",
            # Normalized comparison: same word to a fellow typing it.
            "passphrase": "  Justice. ",
            "cohort_id": cohort,
        },
    )
    assert response.status_code == 200
    # The warning and the confirm box are drawn from this list.
    assert boot_state(response)["reuse_warnings"]

    with connection() as conn:
        rows = fetch_all(conn, 'select title from "session" where cohort_id = %s', (cohort,))
    assert [row["title"] for row in rows] == ["Week 1"]

    confirmed = make_session(
        signed_in, cohort, title="Week 2", scheduled_at="2026-09-22T13:05",
        passphrase="  Justice. ", confirm_reuse="1",
    )
    assert confirmed


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
            "passphrase": "",
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
            "passphrase": "justice",
            "cohort_id": cohort,
            # Editing must not warn about the session's own passphrase.
            "confirm_reuse": "",
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
# screen 5 — review
# --------------------------------------------------------------------------


def _seed_checkin(cohort_id: str, session_id: str, email: str, passphrase: str) -> str:
    """One observation with a needs_review decision on it."""
    with connection() as conn:
        row = fetch_all(
            conn,
            """
            insert into checkin (
                source_event_id, source, submitted_email, submitted_at_utc,
                submitted_at_raw, session_id, session_match, passphrase_raw,
                passphrase_match
            )
            values (%s, 'forms_api', %s, %s, %s, %s, 'matched', %s, 'mismatch')
            returning checkin_id
            """,
            (
                uuid.uuid4().hex,
                email,
                "2026-09-15T17:10:00Z",
                "2026-09-15T17:10:00Z",
                session_id,
                passphrase,
            ),
        )
        checkin_id = str(row[0]["checkin_id"])
        record_decision(
            conn,
            checkin_id,
            status="needs_review",
            decided_by="rule",
            rule_name="ai_unavailable",
            confidence=0.0,
        )
    return checkin_id


def test_the_review_queue_lists_and_one_click_records_a_human_decision(
    signed_in: TestClient, cohort: str
) -> None:
    session_id = make_session(signed_in, cohort)
    checkin_id = _seed_checkin(cohort, session_id, "unknown@example.invalid", "jushtis")

    queue = signed_in.get(f"/review?tab=needs_review&cohort={cohort}")
    assert queue.status_code == 200
    assert "jushtis" in queue.text
    assert "justice" in queue.text  # the expected word, so a human can judge
    assert checkin_id in queue.text

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
    session_id = make_session(signed_in, cohort)
    checkin_id = _seed_checkin(cohort, session_id, "fellow@example.invalid", "the word was justice")
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
    checkin_id = _seed_checkin(cohort, session_id, "fellow@example.invalid", "nope")
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
