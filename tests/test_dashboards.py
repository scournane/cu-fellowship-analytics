"""The staff dashboard and the fellow's own page.

Three access properties matter more than the rendering: the staff page is
behind the console allowlist, the fellow page is reachable only with a signed,
expiring token that names one fellow and shows nobody else, and the fellow
page carries none of the staff configuration the console screens are handed.

Both are React screens, so the words on them are in the bundle rather than in
the response. What the server is responsible for is the data it ships, which
is what these tests read — the same `boot_state` the console tests use.
"""

from __future__ import annotations

import json
import os
import re
import uuid

os.environ.setdefault("CUFA_DATABASE_URL", "postgresql://postgres:postgres@localhost:64322/cufa_test")
os.environ["CUFA_FAKE_GOOGLE"] = "1"
os.environ["CUFA_FAKE_SLACK"] = "1"
# setdefault, not assignment: the other console test modules set these at
# import time too, and whichever module pytest imports last must not silently
# narrow the allowlist the earlier ones rely on.
os.environ.setdefault("CUFA_CONSOLE_ALLOWLIST", "staff@example.invalid,second@example.invalid")
os.environ.setdefault("CUFA_CONSOLE_SECRET", "test-secret-not-used-anywhere-real")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from cufa.config import get_settings, reset_settings_cache  # noqa: E402
from cufa.console.app import app  # noqa: E402
from cufa.db import connection, execute  # noqa: E402
from cufa.interventions import reached_out  # noqa: E402
from cufa.slack.dashboard_links import fellow_dashboard_url, issue_token, read_token  # noqa: E402

reset_settings_cache()

STAFF = "staff@example.invalid"


def boot_state(response) -> dict:
    """The JSON the server hands the React screen."""
    match = re.search(
        r'<script type="application/json" id="__CUFA_STATE__">(.*?)</script>',
        response.text,
        re.DOTALL,
    )
    assert match, "no boot state in the response"
    return json.loads(match.group(1))


@pytest.fixture
def cohort() -> tuple[str, str, str]:
    cohort_id = f"dash-{uuid.uuid4().hex[:8]}"
    a, b = f"CU-{uuid.uuid4().hex[:6]}", f"CU-{uuid.uuid4().hex[:6]}"
    with connection() as conn:
        execute(conn, "insert into cohort (cohort_id, label) values (%s, %s)", (cohort_id, "dashboard test"))
        execute(
            conn,
            "insert into fellow (fellow_id, cohort_id, full_name, primary_email) values (%s, %s, %s, %s), (%s, %s, %s, %s)",
            (a, cohort_id, "Ada Dash", f"{a}@example.invalid", b, cohort_id, "Bo Dash", f"{b}@example.invalid"),
        )
        execute(
            conn,
            "insert into assignment (cohort_id, title, kind, due_at_utc, max_score) values (%s, 'Solvathon', 'solvathon', now() + interval '5 days', 100)",
            (cohort_id,),
        )
    return cohort_id, a, b


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def signed_in(client: TestClient) -> TestClient:
    assert client.post("/signin/dev", data={"email": STAFF, "next": "/"}).status_code == 303
    return client


def test_staff_dashboard_needs_a_signed_in_staffer(client: TestClient, cohort) -> None:
    response = client.get(f"/dashboard?cohort={cohort[0]}")
    assert response.status_code in (302, 303, 401)
    assert client.get(f"/dashboard/export.csv?cohort={cohort[0]}").status_code in (302, 303, 401)


def test_staff_dashboard_renders_and_exports(signed_in: TestClient, cohort) -> None:
    cohort_id, a, b = cohort
    page = signed_in.get(f"/dashboard?cohort={cohort_id}")
    assert page.status_code == 200
    state = boot_state(page)
    assert state["screen"] == "dashboard" and state["frame"] == "staff"
    assert state["cohort_id"] == cohort_id
    assert [e["full_name"] for e in state["engagement"]] == ["Ada Dash", "Bo Dash"]
    assert [x["title"] for x in state["assignments"]] == ["Solvathon"]
    # The three blocks the page is built out of, each shipped rather than
    # computed in the browser.
    assert state["attendance"]["active_fellows"] == 2
    assert set(state["funnel"]["counts"]) == set(state["stage_labels"])
    assert set(state["received"]) >= {"part_a", "part_b"}
    export = signed_in.get(f"/dashboard/export.csv?cohort={cohort_id}")
    assert export.status_code == 200 and export.headers["content-type"].startswith("text/csv")
    assert "Ada Dash" in export.text and "attention_index" in export.text.splitlines()[0]


def test_the_cohort_picker_is_given_labels_not_bare_ids(signed_in: TestClient, cohort) -> None:
    cohort_id, _, _ = cohort
    state = boot_state(signed_in.get(f"/dashboard?cohort={cohort_id}"))
    picked = [c for c in state["cohorts"] if c["cohort_id"] == cohort_id]
    assert picked and picked[0]["label"] == "dashboard test"


def test_outreach_toggle_and_score_entry_from_the_dashboard(signed_in: TestClient, cohort) -> None:
    cohort_id, a, b = cohort
    response = signed_in.post(f"/dashboard/fellow/{b}/outreach", data={"action": "mark", "note": "emailed", "cohort": cohort_id})
    assert response.status_code == 303
    with connection() as conn:
        assert reached_out(conn, b) is True
        assignment_id = str(conn.execute("select assignment_id from assignment where cohort_id = %s", (cohort_id,)).fetchone()["assignment_id"])
    # The redirect carries what happened, rather than dropping the reader back
    # on the page with no sign the write landed.
    assert "notice=" in response.headers["location"]
    response = signed_in.post("/dashboard/score", data={"assignment_id": assignment_id, "fellow_id": a, "score": "88.5", "note": "", "cohort": cohort_id})
    assert response.status_code == 303
    state = boot_state(signed_in.get(f"/dashboard?cohort={cohort_id}"))
    scored = {r["fellow_id"]: r["score"] for r in state["assignments"][0]["rows"]}
    assert scored[a] == 88.5
    bad = signed_in.post("/dashboard/score", data={"assignment_id": assignment_id, "fellow_id": a, "score": "lots", "note": "", "cohort": cohort_id})
    assert bad.status_code == 400
    assert boot_state(bad)["screen"] == "message", "a refused score explains itself"


def test_fellow_page_needs_a_valid_token_and_shows_one_person(client: TestClient, cohort) -> None:
    cohort_id, a, b = cohort
    settings = get_settings()
    assert client.get("/me/not-a-token").status_code == 403
    url = fellow_dashboard_url(settings, a)
    path = url[len(settings.public_base_url):]
    page = client.get(path)
    assert page.status_code == 200
    state = boot_state(page)
    assert state["screen"] == "me" and state["staff_view"] is False
    assert state["fellow"]["full_name"] == "Ada Dash"
    assert "Bo Dash" not in page.text, "one fellow's page names one fellow"
    assert state["token"], "the export link and the preference forms are built from it"
    export = client.get(path + "/export.csv")
    assert export.status_code == 200 and "session" in export.text.splitlines()[0]
    assert read_token(settings, issue_token(settings, a)) == a
    assert read_token(settings, issue_token(settings, a) + "x") is None


def test_the_fellow_page_carries_no_staff_configuration(client: TestClient, cohort) -> None:
    """A fellow holds a link, not an account.

    Every console screen is handed the allowlist, the signed-in account and
    which sign-in doors exist. None of that is theirs to see, and a link can be
    forwarded — so the fellow frame is handed the screen's own data and nothing
    else.
    """
    settings = get_settings()
    path = fellow_dashboard_url(settings, cohort[1])[len(settings.public_base_url):]
    page = client.get(path)
    state = boot_state(page)
    assert state["frame"] == "fellow"
    assert not {"allowlist", "user", "devSignin", "noAllowlist"} & set(state)
    assert STAFF not in page.text


def test_the_fellow_page_carries_none_of_the_staff_triage_numbers(client: TestClient, cohort) -> None:
    """The attention index is a triage order for staff, not a grade (ADR-035).

    A React screen ships its data in the page source, so "the screen does not
    draw it" is not the same as "the fellow cannot read it". Their own counts
    go; everything computed to rank them against the cohort stays behind.
    """
    settings = get_settings()
    path = fellow_dashboard_url(settings, cohort[1])[len(settings.public_base_url):]
    state = boot_state(client.get(path))
    assert set(state["engagement"]) == {
        "attended", "sessions_held", "needs_review",
        "forms_submitted", "forms_expected", "form_completeness",
        "messages", "messages_7d",
    }
    assert "attention_index" not in client.get(path).text


def test_fellow_can_toggle_preferences_from_the_page(client: TestClient, cohort) -> None:
    from cufa.slack.client import FakeSlackClient
    from cufa.slack.preferences import get_preferences
    from cufa.slack.sync import sync_all

    cohort_id, a, b = cohort
    fake = FakeSlackClient()
    fake.add_user("UDASH", email=f"{a}@example.invalid", name="Ada Dash")
    with connection() as conn:
        sync_all(conn, fake)
    settings = get_settings()
    path = fellow_dashboard_url(settings, a)[len(settings.public_base_url):]
    assert boot_state(client.get(path))["preferences"]["session_reminders"] == [1440, 60, 10]
    assert client.post(path + "/prefs", data={"kind": "session", "offset": "10", "enabled": "off"}).status_code == 303
    assert client.post(path + "/prefs", data={"kind": "gamification", "offset": "", "enabled": "off"}).status_code == 303
    with connection() as conn:
        prefs = get_preferences(conn, "UDASH")
    assert prefs.session_reminders == (1440, 60) and prefs.gamification is False
    state = boot_state(client.get(path))
    assert state["preferences"]["session_reminders"] == [1440, 60]
    assert state["preferences"]["gamification"] is False
    expired = client.post("/me/bogus/prefs", data={"kind": "session", "offset": "10", "enabled": "off"})
    assert expired.status_code == 403 and boot_state(expired)["frame"] == "fellow"


def test_staff_fellow_detail_page(signed_in: TestClient, client: TestClient, cohort) -> None:
    cohort_id, a, b = cohort
    page = signed_in.get(f"/dashboard/fellow/{a}")
    assert page.status_code == 200
    state = boot_state(page)
    assert state["screen"] == "me" and state["staff_view"] is True and state["frame"] == "staff"
    assert state["fellow"]["full_name"] == "Ada Dash"
    assert state["engagement"]["attention_index"] is not None
    # The staff-only rows, and no token: the export link on a fellow's own page
    # is built from one, and the staff view is not that page.
    assert state["interventions"] == [] and state["airtime"] == []
    assert state["token"] is None
    assert signed_in.get("/dashboard/fellow/CU-nope").status_code == 404
    assert TestClient(app, follow_redirects=False).get(f"/dashboard/fellow/{a}").status_code in (302, 303, 401)
