"""Shared test fixtures.

Tests never touch the network. The Google client is always the fake, tier 2 is
always an injected stub, and the only external thing is the local Postgres the
Supabase stack provides — which is where the schema constraints being tested
actually live, so testing against a real database rather than a mock is the
point, not a compromise.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import pytest

# A SEPARATE database, deliberately — not the one the console uses.
#
# The `db` fixture truncates every table in _TABLES before each test, and that
# list includes `google_credential`. Pointed at the working database, a single
# `pytest` run silently destroys real local state: a connected Google account,
# a loaded roster, a finished `make demo`. That is not a hypothetical — it cost
# three separate restores before this line was written.
#
# `load_dotenv(override=False)` means os.environ beats .env, so setting it here
# wins even when .env names the working database. Export CUFA_DATABASE_URL to
# aim the suite somewhere else on purpose.
os.environ.setdefault(
    "CUFA_DATABASE_URL", "postgresql://postgres:postgres@localhost:64322/cufa_test"
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

TEST_COHORT = "test-cohort"
TEST_TZ = "America/New_York"

# Order matters: children before parents, so a cascade is not relied on.
_TABLES = (
    # Slack: events reference workspace, so they go first.
    "slack_event",
    "slack_user",
    "slack_channel",
    "slack_workspace",
    "attendance_decision",
    "checkin",
    "muddiest_theme_member",
    "muddiest_theme",
    "peer_shoutout",
    "help_request",
    "checkin_b",
    "ai_adjudication_cache",
    "identity_unresolved",
    "provisioning_log",
    "form_question_map",
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
            f"{exc}\n\n"
            "Run `make db-up` (or `supabase start`), then `make db-test` to create\n"
            "the separate test database this suite uses. It is not the database the\n"
            "console runs on: the suite truncates every table, so it is kept apart\n"
            "from your roster, sessions and connected Google account.",
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
    """A Part A template that has been through the manual step and verified."""
    return verify_template_for(db, fake, "a")


@pytest.fixture
def verified_template_b(db, fake) -> str:
    """The same, for Part B.

    A separate fixture rather than a parameter on the first one, because the
    manual step is genuinely per part: email collection lives on a form and is
    carried only by a Drive copy, so Part A being verified says nothing at all
    about Part B.
    """
    return verify_template_for(db, fake, "b")


def verify_template_for(conn, fake, part: str) -> str:
    from cufa.template import create_template, verify_template

    record = create_template(conn, fake, part)
    fake.simulate_human_sets_verified(record.form_id)
    verify_template(conn, fake, part)
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
    week_index: int | None = None,
    teacher_question: str | None = None,
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
            week_index=week_index,
            teacher_question=teacher_question,
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


# ---------------------------------------------------------------------------
# Part B helpers
# ---------------------------------------------------------------------------


def seed_part_b(conn, fake, form_id: str, rows: list[dict]) -> None:
    """Seed Part B responses, keyed by SLOT rather than by question id.

    Which id a field ends up with depends on whether the Drive copy preserved
    them, and the whole point of the suite running both schemes is that no test
    may know. So each row names slots, and this resolves them through
    ``form_question_map`` — the same table ingest resolves through.
    """
    from cufa.db import fetch_all
    from cufa.form_content_b import HELP_OPTION, SLOT_HELP

    by_slot = {
        row["slot"]: row["question_id"]
        for row in fetch_all(
            conn,
            "select slot, question_id from form_question_map where form_id = %s",
            (form_id,),
        )
    }

    seeded = []
    for row in rows:
        answers = {}
        for slot, value in (row.get("slots") or {}).items():
            question_id = by_slot.get(slot)
            if question_id is not None:
                answers[question_id] = value
        # Answers to questions this application did not create — a teacher
        # adding one in the Forms UI. Passed through by question id, because
        # they have no slot to name.
        answers.update(row.get("answers_by_id") or {})
        if row.get("help") and SLOT_HELP in by_slot:
            answers[by_slot[SLOT_HELP]] = HELP_OPTION
        seeded.append(
            {
                "email": row["email"],
                "submitted_at": row["submitted_at"],
                "answers_by_id": answers,
            }
        )
    fake.seed_responses(form_id, seeded)


class StubClusterer:
    """A theme clusterer that records exactly what it was asked to cluster.

    Records the payload so a test can assert no name, address or id was in it —
    which is the whole privacy claim about the AI tier in Part B.
    """

    model_name = "stub-clusterer"
    prompt_version = "test"

    def __init__(self, themes=None):
        self.calls: list[list[str]] = []
        self.prompts: list[str] = []
        self._themes = themes

    def cluster(self, texts):
        from cufa.themes import ThemeDraft, build_prompt

        self.calls.append(list(texts))
        self.prompts.append(build_prompt(texts))
        if self._themes is not None:
            return self._themes
        half = max(1, len(texts) // 2)
        return [
            ThemeDraft(
                label="First half",
                summary="The earlier answers.",
                answer_numbers=tuple(range(1, half + 1)),
            ),
            ThemeDraft(
                label="Second half",
                summary="The later answers.",
                answer_numbers=tuple(range(half + 1, len(texts) + 1)),
            ),
        ]


class ExplodingClusterer:
    """Clustering that always fails, to prove the pipeline degrades."""

    model_name = "exploding-clusterer"
    prompt_version = "test"

    def cluster(self, texts):
        from cufa.errors import AiUnavailable

        raise AiUnavailable("quota exhausted")
