"""Shared test fixtures.

Tests never touch the network. The Google client is always the fake, tier 2 is
always an injected stub, and the only external thing is the local Postgres the
Supabase stack provides — which is where the schema constraints being tested
actually live, so testing against a real database rather than a mock is the
point, not a compromise.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

import pytest

os.environ.setdefault(
    "CUFA_DATABASE_URL", "postgresql://postgres:postgres@localhost:54322/postgres"
)
os.environ["CUFA_FAKE_GOOGLE"] = "1"
os.environ.setdefault("CUFA_LOG_LEVEL", "WARNING")
# Never let a test reach Gemini, even if a developer has a key exported.
os.environ.pop("GEMINI_API_KEY", None)

from cufa.config import load_settings, reset_settings_cache  # noqa: E402
from cufa.db import connection, execute, fetch_one  # noqa: E402
from cufa.errors import DatabaseUnreachable  # noqa: E402
from cufa.google.fake import FakeGoogleClient  # noqa: E402
from cufa.google.factory import set_fake_client  # noqa: E402
from cufa.sessions import SessionInput, create_session  # noqa: E402
from cufa.timeutil import UTC  # noqa: E402

TEST_COHORT = "test-cohort"
TEST_TZ = "America/New_York"

# Order matters: children before parents, so a cascade is not relied on.
_TABLES = (
    "attendance_decision",
    "checkin",
    "ai_adjudication_cache",
    "identity_unresolved",
    "provisioning_log",
    "session_form",
    "load_run",
    "form_template",
    "google_credential",
    '"session"',
    "fellow",
    "cohort",
)


@pytest.fixture(scope="session")
def settings():
    reset_settings_cache()
    return load_settings()


@pytest.fixture
def db(settings) -> Iterator[Any]:
    """A clean database for one test.

    TRUNCATE rather than DELETE: `checkin` has a trigger that refuses row
    deletion, which is exactly the behaviour under test elsewhere. TRUNCATE is a
    statement-level operation and does not fire per-row DELETE triggers, so the
    test harness does not need an exemption the application does not have.
    """
    try:
        with connection(settings, autocommit=True) as conn:
            execute(conn, f"truncate {', '.join(_TABLES)} restart identity cascade")
            execute(
                conn,
                "insert into cohort (cohort_id, label) values (%s, %s) "
                "on conflict do nothing",
                (TEST_COHORT, "Test cohort"),
            )
            yield conn
    except DatabaseUnreachable as exc:
        pytest.fail(
            f"{exc}\n\nRun `make db-up` (or `supabase start`) before `make test`.",
            pytrace=False,
        )


@pytest.fixture
def fake() -> Iterator[FakeGoogleClient]:
    """A fresh in-memory fake, registered as the process-wide client."""
    client = FakeGoogleClient()
    set_fake_client(client)
    yield client
    set_fake_client(None)


@pytest.fixture
def verified_template(db, fake) -> str:
    """A template that has been through the one manual step and verified."""
    from cufa.template import create_template, verify_template

    record = create_template(db, fake)
    fake.simulate_human_sets_verified(record.form_id)
    verify_template(db, fake)
    return record.form_id


def make_session(
    conn,
    *,
    title: str = "Test session",
    local: datetime | None = None,
    duration: int = 90,
    grace: int = 15,
    passphrase: str | None = "justice",
    cohort_id: str = TEST_COHORT,
) -> str:
    """Create a session at a fixed local time. Never reads the clock."""
    return create_session(
        conn,
        SessionInput(
            cohort_id=cohort_id,
            title=title,
            scheduled_at_local=local or datetime(2026, 9, 15, 19, 0),
            timezone=TEST_TZ,
            duration_minutes=duration,
            grace_minutes=grace,
            passphrase=passphrase,
        ),
    )


def make_fellow(
    conn, fellow_id: str = "CU-0001", email: str = "ada@example.invalid",
    name: str = "Ada Testcase", cohort_id: str = TEST_COHORT,
) -> str:
    execute(
        conn,
        """
        insert into fellow (fellow_id, cohort_id, full_name, primary_email)
        values (%s, %s, %s, %s)
        on conflict (fellow_id) do update set primary_email = excluded.primary_email
        """,
        (fellow_id, cohort_id, name, email),
    )
    return fellow_id


def count(conn, table: str, where: str = "true", params: tuple = ()) -> int:
    row = fetch_one(conn, f"select count(*) as n from {table} where {where}", params)
    return int((row or {}).get("n", 0))


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> Path:
    import csv

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


class StubAdjudicator:
    """A tier 2 stand-in that records every call. Never touches a network."""

    def __init__(self, verdict: bool = True, confidence: float = 0.88) -> None:
        self.model_name = "stub-model"
        self.calls: list[tuple[str, str]] = []
        self._verdict = verdict
        self._confidence = confidence

    def judge(self, expected: str, submitted: str):
        from cufa.adjudicate.ai import AiVerdict

        self.calls.append((expected, submitted))
        return AiVerdict(
            heard_the_passphrase=self._verdict,
            confidence=self._confidence,
            reasoning="stubbed verdict",
        )


class ExplodingAdjudicator:
    """Tier 2 that always fails, to prove the pipeline degrades rather than stops."""

    model_name = "exploding-model"

    def judge(self, expected: str, submitted: str):
        from cufa.errors import AiUnavailable

        raise AiUnavailable("quota exhausted")
