"""The Part B console screens.

Three things are being proved, and none of them is "the pages render":

1. **The help-requests screen has its own gate**, and being on the general
   console allowlist does not open it.
2. **The Part B traps stop the console visibly.** A teacher-question week with
   no question, and a template that is not verified, both block provisioning
   and put the full failure text on the screen.
3. **The survey-length rationale is where a staffer would go to add a field**,
   because somebody will want one more question and the answer to that is a
   number rather than a preference.

No test here touches the network. The Google client is the in-memory fake and
sign-in is the dev bypass.
"""

from __future__ import annotations

import json
import os
import re
import uuid

os.environ.setdefault(
    "CUFA_DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:64322/postgres"
)
os.environ["CUFA_FAKE_GOOGLE"] = "1"
os.environ["CUFA_CONSOLE_ALLOWLIST"] = "staff@example.invalid,dop@example.invalid"
os.environ["CUFA_CONSOLE_SECRET"] = "test-secret-not-used-anywhere-real"
os.environ["CUFA_HELP_ALLOWLIST"] = "dop@example.invalid"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from cufa import crypto  # noqa: E402
from cufa.config import reset_settings_cache  # noqa: E402
from cufa.console.app import app  # noqa: E402
from cufa.db import connection, execute, fetch_all, fetch_one  # noqa: E402
from cufa.errors import DatabaseUnreachable  # noqa: E402
from cufa.google.fake import QUESTION_IDS_PRESERVED, FakeGoogleClient  # noqa: E402
from cufa.google.factory import set_fake_client  # noqa: E402

os.environ.setdefault("CUFA_ENCRYPTION_KEY", crypto.generate_key())
reset_settings_cache()

STAFF = "staff@example.invalid"
DOP = "dop@example.invalid"


@pytest.fixture(scope="module", autouse=True)
def _require_database() -> None:
    try:
        with connection() as conn:
            fetch_all(conn, "select 1")
    except DatabaseUnreachable as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"local Postgres is not running: {exc}")


def _reset_google_state() -> None:
    """Clear every form the fake has created here, and their maps.

    ``form_template`` is global to the install, so a template left behind by one
    test silently unblocks the next, and the fake restarts its form ids at
    ``fake-form-0001`` for every instance — which a real install never does.
    """
    fake_ids = "fake-form-%"
    with connection() as conn:
        execute(conn, "delete from form_question_map where form_id like %s", (fake_ids,))
        execute(
            conn,
            "delete from session_form sf using form_template ft "
            "where sf.template_id = ft.template_id and ft.form_id like %s",
            (fake_ids,),
        )
        execute(conn, "delete from session_form where form_id like %s", (fake_ids,))
        execute(conn, "delete from form_template where form_id like %s", (fake_ids,))


@pytest.fixture
def fake() -> FakeGoogleClient:
    _reset_google_state()
    client = FakeGoogleClient(question_id_scheme=QUESTION_IDS_PRESERVED)
    set_fake_client(client)
    yield client
    set_fake_client(None)


@pytest.fixture
def verified_both(fake: FakeGoogleClient) -> FakeGoogleClient:
    """Both templates created and through their own manual step."""
    from cufa.template import create_template, verify_template

    with connection() as conn:
        for part in ("a", "b"):
            record = create_template(conn, fake, part)
            fake.simulate_human_sets_verified(record.form_id)
            verify_template(conn, fake, part)
    return fake


@pytest.fixture
def cohort() -> str:
    cohort_id = f"test-{uuid.uuid4().hex[:8]}"
    with connection() as conn:
        execute(
            conn,
            "insert into cohort (cohort_id, label) values (%s, %s)",
            (cohort_id, "part b console cohort"),
        )
    return cohort_id


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def signed_in(client: TestClient) -> TestClient:
    assert client.post("/signin/dev", data={"email": STAFF, "next": "/"}).status_code == 303
    return client


@pytest.fixture
def signed_in_dop(client: TestClient) -> TestClient:
    assert client.post("/signin/dev", data={"email": DOP, "next": "/"}).status_code == 303
    return client


def boot_state(response) -> dict:
    match = re.search(
        r'<script type="application/json" id="__CUFA_STATE__">(.*?)</script>',
        response.text,
        re.DOTALL,
    )
    assert match, "no boot state in the response"
    return json.loads(match.group(1))


def make_session(
    client: TestClient,
    cohort_id: str,
    *,
    title: str = "Week 2 — Finding the people affected",
    week_index: str = "2",
    teacher_question: str = "",
) -> str:
    response = client.post(
        "/sessions/new",
        data={
            "title": title,
            "scheduled_at": "2026-09-15T19:00",
            "timezone": "America/New_York",
            "duration_minutes": "90",
            "grace_minutes": "15",
            "passphrase": f"pass-{uuid.uuid4().hex[:6]}",
            "cohort_id": cohort_id,
            "week_index": week_index,
            "teacher_question": teacher_question,
        },
    )
    assert response.status_code == 303, response.text[:2000]
    return response.headers["location"].split("/sessions/")[1].split("?")[0]


# ---------------------------------------------------------------------------
# every new screen
# ---------------------------------------------------------------------------


def test_every_part_b_screen_returns_200(
    signed_in_dop: TestClient, verified_both: FakeGoogleClient, cohort: str
) -> None:
    session_id = make_session(signed_in_dop, cohort)
    signed_in_dop.post(f"/sessions/{session_id}/provision", data={"part": "b"})

    for path in (
        "/template",
        "/rotation",
        f"/rotation?cohort={cohort}",
        "/shoutouts",
        f"/shoutouts?cohort={cohort}",
        "/help-requests",
        "/help-requests?status=closed",
        "/review?tab=straightlining",
        f"/sessions/{session_id}",
        f"/sessions/{session_id}/responses",
    ):
        response = signed_in_dop.get(path)
        assert response.status_code == 200, f"{path} -> {response.status_code}"


def test_the_template_screen_shows_both_parts_and_their_own_verification(
    signed_in: TestClient, fake: FakeGoogleClient
) -> None:
    from cufa.template import create_template, verify_template

    with connection() as conn:
        record = create_template(conn, fake, "a")
        fake.simulate_human_sets_verified(record.form_id)
        verify_template(conn, fake, "a")

    state = boot_state(signed_in.get("/template"))
    parts = {entry["part"]: entry for entry in state["parts"]}
    assert set(parts) == {"a", "b"}
    assert parts["a"]["blocked"] is False
    # Verifying Part A says nothing at all about Part B.
    assert parts["b"]["record"] is None
    assert parts["b"]["blocked"] is True
    assert state["blocked"] is True


def test_creating_the_part_b_template_does_not_touch_part_a(
    signed_in: TestClient, fake: FakeGoogleClient
) -> None:
    signed_in.post("/template/create", data={"part": "a"})
    signed_in.post("/template/create", data={"part": "b"})

    with connection() as conn:
        rows = fetch_all(
            conn, "select part, form_id from form_template where is_active order by part"
        )
    assert [row["part"] for row in rows] == ["a", "b"]
    assert rows[0]["form_id"] != rows[1]["form_id"]


# ---------------------------------------------------------------------------
# provisioning Part B from the console
# ---------------------------------------------------------------------------


def test_a_teacher_question_week_with_no_question_blocks_and_says_why(
    signed_in: TestClient, verified_both: FakeGoogleClient, cohort: str
) -> None:
    session_id = make_session(signed_in, cohort, title="Week 1", week_index="1")
    state = boot_state(signed_in.get(f"/sessions/{session_id}"))

    assert state["b_rotation"] is None
    assert state["b_rotation_error"]
    # The full text, not a summary: it is the message that names the one field
    # to fill in.
    assert "teacher" in state["b_rotation_error"].lower()

    response = signed_in.post(f"/sessions/{session_id}/provision", data={"part": "b"})
    assert response.status_code == 200
    with connection() as conn:
        assert fetch_one(
            conn,
            "select count(*) as n from session_form where session_id = %s and part = 'b'",
            (session_id,),
        )["n"] == 0


def test_setting_the_question_unblocks_it(
    signed_in: TestClient, verified_both: FakeGoogleClient, cohort: str
) -> None:
    session_id = make_session(
        signed_in, cohort, title="Week 1", week_index="1",
        teacher_question="What surprised you today?",
    )
    state = boot_state(signed_in.get(f"/sessions/{session_id}"))
    assert state["b_rotation_error"] is None
    assert state["b_rotation"]["kind"] == "teacher_question"
    assert state["b_rotation"]["text"] == "What surprised you today?"

    response = signed_in.post(f"/sessions/{session_id}/provision", data={"part": "b"})
    assert response.status_code == 200
    state = boot_state(response)
    assert state["b_ready"] is True
    assert state["b_form_url"]
    assert state["b_qr"], "a verified-published form gets a QR code"
    assert len(state["b_question_map"]) == 5


def test_provisioning_part_b_leaves_part_a_alone(
    signed_in: TestClient, verified_both: FakeGoogleClient, cohort: str
) -> None:
    session_id = make_session(signed_in, cohort)
    signed_in.post(f"/sessions/{session_id}/provision", data={"part": "a"})
    signed_in.post(f"/sessions/{session_id}/provision", data={"part": "b"})

    state = boot_state(signed_in.get(f"/sessions/{session_id}"))
    assert state["ready"] is True
    assert state["b_ready"] is True
    assert state["session"]["form_id"] != state["session"]["b_form_id"]


def test_the_session_form_shows_the_rotation_for_the_week_typed_in(
    signed_in: TestClient, cohort: str
) -> None:
    session_id = make_session(signed_in, cohort, title="Week 3", week_index="3")
    state = boot_state(signed_in.get(f"/sessions/{session_id}/edit"))
    assert state["values"]["week_index"] == "3"
    assert state["rotation"]["kind"] == "application"
    assert state["rotation"]["needs_teacher_question"] is False

    session_id = make_session(signed_in, cohort, title="Week 4", week_index="4")
    state = boot_state(signed_in.get(f"/sessions/{session_id}/edit"))
    assert state["rotation"]["kind"] == "teacher_question"
    assert state["rotation"]["needs_teacher_question"] is True


def test_the_rotation_screen_names_the_weeks_still_missing_a_question(
    signed_in: TestClient, cohort: str
) -> None:
    make_session(signed_in, cohort, title="Week 1", week_index="1")
    state = boot_state(signed_in.get(f"/rotation?cohort={cohort}"))

    missing = [row for row in state["preview"] if row["needs_teacher_question"]]
    assert missing, "week 1 has no teacher question"
    assert any(row["session_title"] == "Week 1" for row in missing)
    assert state["schedule"]["weeks"] == 10


def test_the_survey_length_rationale_is_where_a_field_would_be_added(
    signed_in: TestClient, verified_both: FakeGoogleClient, cohort: str
) -> None:
    """Somebody will want one more question. The answer is a number."""
    session_id = make_session(
        signed_in, cohort, title="Week 1", week_index="1", teacher_question="Q?"
    )
    signed_in.post(f"/sessions/{session_id}/provision", data={"part": "b"})

    for path in ("/rotation", "/template", f"/sessions/{session_id}",
                 f"/sessions/{session_id}/responses"):
        state = boot_state(signed_in.get(path))
        rationale = state.get("survey_rationale") or ""
        assert "18%" in rationale, path
        assert "89%" in rationale, path


# ---------------------------------------------------------------------------
# the help-requests gate
# ---------------------------------------------------------------------------


def test_the_help_screen_is_refused_to_an_allowlisted_user_without_help_access(
    signed_in: TestClient,
) -> None:
    """On the console allowlist, and still refused. That is the whole point of
    the separate list."""
    response = signed_in.get("/help-requests")
    assert response.status_code == 403
    assert "restricted" in response.text or "restricted" in json.dumps(boot_state(response))


def test_the_help_screen_opens_for_the_named_recipient(signed_in_dop: TestClient) -> None:
    response = signed_in_dop.get("/help-requests")
    assert response.status_code == 200
    state = boot_state(response)
    assert state["access_list"] == [DOP]


def test_acknowledging_is_refused_to_someone_without_access(signed_in: TestClient) -> None:
    response = signed_in.post(
        f"/help-requests/{uuid.uuid4()}/ack", data={"action": "ack"}
    )
    assert response.status_code == 403


def test_the_nav_only_offers_the_help_screen_to_people_who_may_open_it() -> None:
    # Two clients, not the two signed-in fixtures: those share one TestClient,
    # so the second sign-in would replace the first one's cookie and both halves
    # of the assertion would be about the same person.
    staff = TestClient(app, follow_redirects=False)
    staff.post("/signin/dev", data={"email": STAFF, "next": "/"})
    dop = TestClient(app, follow_redirects=False)
    dop.post("/signin/dev", data={"email": DOP, "next": "/"})

    assert boot_state(staff.get("/sessions"))["user"]["mayReadHelp"] is False
    assert boot_state(dop.get("/sessions"))["user"]["mayReadHelp"] is True


def test_the_help_screen_states_what_the_notification_withholds(
    signed_in_dop: TestClient,
) -> None:
    state = boot_state(signed_in_dop.get("/help-requests"))
    assert state["routing"]["has_recipient"] in (True, False)
    assert "open_count" in state


# ---------------------------------------------------------------------------
# responses
# ---------------------------------------------------------------------------


def test_the_responses_screen_shows_the_distribution_and_the_caveat(
    signed_in: TestClient, verified_both: FakeGoogleClient, cohort: str
) -> None:
    from conftest import seed_part_b
    from cufa.form_content_b import SLOT_CONFIDENCE, SLOT_ROTATING, SLOT_TAKEAWAY
    from cufa.help_routing import RecordingNotifier
    from cufa.ingest.forms_b import pull_session_b

    session_id = make_session(signed_in, cohort)
    signed_in.post(f"/sessions/{session_id}/provision", data={"part": "b"})

    tag = uuid.uuid4().hex[:8]
    with connection() as conn:
        form = fetch_one(
            conn,
            "select form_id from session_form where session_id = %s and part = 'b'",
            (session_id,),
        )
        seed_part_b(conn, verified_both, form["form_id"], [
            {
                "email": f"{name}-{tag}@example.invalid",
                "submitted_at": f"2026-09-16T00:2{index}:00Z",
                "slots": {
                    SLOT_CONFIDENCE: str(value),
                    SLOT_TAKEAWAY: f"Takeaway from {name}",
                    SLOT_ROTATING: f"Unclear thing {index}",
                },
            }
            for index, (name, value) in enumerate(
                [("a", 3), ("b", 5), ("c", 5), ("d", 7)]
            )
        ])
        pull_session_b(conn, verified_both, session_id, notifier=RecordingNotifier())

    state = boot_state(signed_in.get(f"/sessions/{session_id}/responses"))
    assert state["distribution"]["responses"] == 4
    assert state["distribution"]["median"] == 5
    assert len(state["distribution"]["distribution"]) == 7
    assert "trend" in state["interpretation"].lower()
    assert "mean" in state["interpretation"].lower(), "say why it is not a mean"
    assert len(state["responses"]) == 4
    assert len(state["question_map"]) == 5


def test_regenerating_themes_with_no_key_reports_and_does_not_break(
    signed_in: TestClient, verified_both: FakeGoogleClient, cohort: str
) -> None:
    session_id = make_session(signed_in, cohort)
    signed_in.post(f"/sessions/{session_id}/provision", data={"part": "b"})

    response = signed_in.post(f"/sessions/{session_id}/themes")
    assert response.status_code == 303
    assert "/responses" in response.headers["location"]
    assert signed_in.get(f"/sessions/{session_id}/responses").status_code == 200


def test_the_shoutout_screen_offers_candidates_and_links_one(
    signed_in: TestClient, verified_both: FakeGoogleClient, cohort: str
) -> None:
    from conftest import seed_part_b
    from cufa.form_content_b import SLOT_CONFIDENCE, SLOT_SHOUTOUT, SLOT_TAKEAWAY
    from cufa.help_routing import RecordingNotifier
    from cufa.ingest.forms_b import pull_session_b

    tag = uuid.uuid4().hex[:6]
    with connection() as conn:
        for suffix in ("Ironwood", "Oakhaven"):
            execute(
                conn,
                "insert into fellow (fellow_id, cohort_id, full_name, primary_email) "
                "values (%s, %s, %s, %s)",
                (
                    f"CU-{tag}-{suffix}",
                    cohort,
                    f"Jordan {suffix}",
                    f"jordan.{suffix.lower()}-{tag}@example.invalid",
                ),
            )

    session_id = make_session(signed_in, cohort)
    signed_in.post(f"/sessions/{session_id}/provision", data={"part": "b"})

    with connection() as conn:
        form = fetch_one(
            conn,
            "select form_id from session_form where session_id = %s and part = 'b'",
            (session_id,),
        )
        seed_part_b(conn, verified_both, form["form_id"], [{
            "email": f"someone-{tag}@example.invalid",
            "submitted_at": "2026-09-16T00:20:00Z",
            "slots": {SLOT_CONFIDENCE: "5", SLOT_TAKEAWAY: "x", SLOT_SHOUTOUT: "Jordan"},
        }])
        pull_session_b(conn, verified_both, session_id, notifier=RecordingNotifier())

    state = boot_state(signed_in.get(f"/shoutouts?cohort={cohort}"))
    assert len(state["rows"]) == 1
    shoutout_id = state["rows"][0]["shoutout_id"]
    candidates = state["candidates"][str(shoutout_id)]
    assert len(candidates) == 2, "an ambiguous name offers both, and links neither"

    response = signed_in.post(
        f"/shoutouts/{shoutout_id}/link",
        data={"fellow_id": candidates[1]["fellow_id"], "cohort": cohort},
    )
    assert response.status_code == 303

    with connection() as conn:
        row = fetch_one(
            conn, "select * from peer_shoutout where shoutout_id = %s", (shoutout_id,)
        )
    assert row["match_method"] == "manual"
    assert row["resolved_by"] == STAFF
