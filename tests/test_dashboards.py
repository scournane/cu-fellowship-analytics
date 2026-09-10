"""The staff dashboard and the fellow's own page.

Two access properties matter more than the rendering: the staff page is
behind the console allowlist, and the fellow page is reachable only with a
signed, expiring token that names one fellow and shows nobody else.
"""

from __future__ import annotations

import os
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
    assert "Ada Dash" in page.text and "Bo Dash" in page.text and "Solvathon" in page.text
    assert "overall attendance" in page.text and "Funnel" in page.text
    export = signed_in.get(f"/dashboard/export.csv?cohort={cohort_id}")
    assert export.status_code == 200 and export.headers["content-type"].startswith("text/csv")
    assert "Ada Dash" in export.text and "attention_index" in export.text.splitlines()[0]


def test_outreach_toggle_and_score_entry_from_the_dashboard(signed_in: TestClient, cohort) -> None:
    cohort_id, a, b = cohort
    response = signed_in.post(f"/dashboard/fellow/{b}/outreach", data={"action": "mark", "note": "emailed", "cohort": cohort_id})
    assert response.status_code == 303
    with connection() as conn:
        assert reached_out(conn, b) is True
        assignment_id = str(conn.execute("select assignment_id from assignment where cohort_id = %s", (cohort_id,)).fetchone()["assignment_id"])
    response = signed_in.post("/dashboard/score", data={"assignment_id": assignment_id, "fellow_id": a, "score": "88.5", "note": "", "cohort": cohort_id})
    assert response.status_code == 303
    page = signed_in.get(f"/dashboard?cohort={cohort_id}")
    assert "88.50" in page.text
    bad = signed_in.post("/dashboard/score", data={"assignment_id": assignment_id, "fellow_id": a, "score": "lots", "note": "", "cohort": cohort_id})
    assert bad.status_code == 400


def test_fellow_page_needs_a_valid_token_and_shows_one_person(client: TestClient, cohort) -> None:
    cohort_id, a, b = cohort
    settings = get_settings()
    assert client.get("/me/not-a-token").status_code == 403
    url = fellow_dashboard_url(settings, a)
    path = url[len(settings.public_base_url):]
    page = client.get(path)
    assert page.status_code == 200
    assert "Ada Dash" in page.text and "Bo Dash" not in page.text
    assert "Export my data" in page.text
    export = client.get(path + "/export.csv")
    assert export.status_code == 200 and "session" in export.text.splitlines()[0]
    assert read_token(settings, issue_token(settings, a)) == a
    assert read_token(settings, issue_token(settings, a) + "x") is None


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
    page = client.get(path)
    assert "10 min on" in page.text
    assert client.post(path + "/prefs", data={"kind": "session", "offset": "10", "enabled": "off"}).status_code == 303
    assert client.post(path + "/prefs", data={"kind": "gamification", "offset": "", "enabled": "off"}).status_code == 303
    with connection() as conn:
        prefs = get_preferences(conn, "UDASH")
    assert prefs.session_reminders == (1440, 60) and prefs.gamification is False
    page = client.get(path)
    assert "10 min off" in page.text
    assert client.post("/me/bogus/prefs", data={"kind": "session", "offset": "10", "enabled": "off"}).status_code == 403


def test_staff_fellow_detail_page(signed_in: TestClient, client: TestClient, cohort) -> None:
    cohort_id, a, b = cohort
    page = signed_in.get(f"/dashboard/fellow/{a}")
    assert page.status_code == 200
    assert "Ada Dash" in page.text and "Attention index" in page.text and "Interventions" in page.text
    assert "Export my data" not in page.text, "the staff view is not the fellow's export link"
    assert signed_in.get("/dashboard/fellow/CU-nope").status_code == 404
    assert TestClient(app, follow_redirects=False).get(f"/dashboard/fellow/{a}").status_code in (302, 303, 401)
