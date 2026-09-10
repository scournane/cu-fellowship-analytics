"""The self-contained HTML report.

What it must contain (every fellow, every session, all three signals) and,
more importantly, what it must not: an email address, anything from the help
table, anything fetched from a network.
"""

from __future__ import annotations

import re
from datetime import datetime

from conftest import TEST_COHORT, TEST_TZ, make_fellow, make_session, write_csv

from cufa.adjudicate.engine import adjudicate_cohort
from cufa.cli import main
from cufa.db import execute
from cufa.ingest.csv_path import ingest_csv
from cufa.report_html import fellow_grid, render_report_html, slack_summary
from cufa.slack.events import parse_event
from cufa.slack.fake import FakeSlackWebClient, FakeWorkspace
from cufa.slack.store import ensure_workspace, resolve_and_record

HEADERS = ["Timestamp", "Email Address", "Today's passphrase"]


def _populate(db, tmp_path) -> None:
    """Two fellows, two sessions, one attended check-in, one mismatch, one Slack message."""
    make_fellow(db, "CU-0001", "ada@example.invalid", "Ada Testcase")
    make_fellow(db, "CU-0002", "bob@example.invalid", "Bob Fixture")
    make_session(db, title="Week 1 — Foundations", local=datetime(2026, 9, 15, 19, 0), week_index=1)
    make_session(db, title="Week 2 — Coalitions", local=datetime(2026, 9, 22, 19, 0), week_index=2, passphrase="harbor")
    path = write_csv(
        tmp_path / "r.csv",
        [
            {"Timestamp": "2026-09-15 19:20:00", "Email Address": "ada@example.invalid", "Today's passphrase": "justice"},
            {"Timestamp": "2026-09-15 19:21:00", "Email Address": "bob@example.invalid", "Today's passphrase": "wrong word"},
        ],
        HEADERS,
    )
    ingest_csv(db, path, TEST_COHORT, TEST_TZ)
    adjudicate_cohort(db, TEST_COHORT, use_ai=False)

    ws = FakeWorkspace()
    ws.add_user(email="ada@example.invalid", real_name="Ada Testcase", user_id="U0ADA")
    client = FakeSlackWebClient(ws)
    ensure_workspace(db, client, TEST_COHORT)
    general = ws.channel_id("general")
    msg = ws.message_event("U0ADA", general, "hello from slack")
    resolve_and_record(db, client, parse_event(msg, ws.team_id), cohort_id=TEST_COHORT, load_id=None)
    resolve_and_record(db, client, parse_event(ws.reaction_event("U0ADA", general, msg["ts"], "tada"), ws.team_id), cohort_id=TEST_COHORT, load_id=None)


def test_renders_on_an_empty_cohort(db):
    out = render_report_html(db, TEST_COHORT)
    assert out.startswith("<!doctype html>")
    assert "No fellows on the roster" in out
    assert "No confidence responses yet" in out
    assert "No Slack activity recorded yet" in out


def test_every_fellow_and_session_appears(db, tmp_path):
    _populate(db, tmp_path)
    out = render_report_html(db, TEST_COHORT)
    for needle in ("Ada Testcase", "Bob Fixture", "Week 1 — Foundations", "Week 2 — Coalitions"):
        assert needle in out, needle


def test_attendance_states_render_as_cells(db, tmp_path):
    _populate(db, tmp_path)
    grid = fellow_grid(db, TEST_COHORT)
    by = {f["fellow_id"]: f for f in grid["fellows"]}
    assert by["CU-0001"]["states"] == ["attended", "none"]
    assert by["CU-0002"]["states"][0] == "needs_review", "a mismatch with no AI lands in needs_review"
    assert by["CU-0002"]["states"][1] == "none"
    out = render_report_html(db, TEST_COHORT)
    assert 'class="cell s-attended"' in out
    assert 'class="cell s-needs_review"' in out
    assert 'class="cell s-none"' in out


def test_slack_numbers_reach_the_grid(db, tmp_path):
    _populate(db, tmp_path)
    grid = fellow_grid(db, TEST_COHORT)
    ada = next(f for f in grid["fellows"] if f["fellow_id"] == "CU-0001")
    assert ada["slack_messages"] == 1 and ada["slack_reactions"] == 1 and ada["slack_active_days"] == 1
    weekly = slack_summary(db, TEST_COHORT)["weekly"]
    assert len(weekly) == 1 and weekly[0]["messages"] == 1 and weekly[0]["reactions"] == 1


def test_no_email_address_anywhere(db, tmp_path):
    """The report goes to the whole team. Names, never addresses."""
    _populate(db, tmp_path)
    execute(
        db,
        "insert into identity_unresolved (cohort_id, email) values (%s, %s)",
        (TEST_COHORT, "stranger@example.invalid"),
    )
    out = render_report_html(db, TEST_COHORT)
    # The only "@" allowed is CSS: @media. Anything address-shaped fails.
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", out), "an email-shaped string reached the report"
    assert "example.invalid" not in out
    assert "Addresses not on the roster" in out, "the count is shown; the addresses are not"


def test_self_contained_no_network(db, tmp_path):
    _populate(db, tmp_path)
    out = render_report_html(db, TEST_COHORT)
    assert "<script src=" not in out
    assert "<link " not in out
    assert "http://" not in out and "https://" not in out
    assert "@import" not in out


def test_theme_tokens_are_complete(db):
    out = render_report_html(db, TEST_COHORT)
    assert "prefers-color-scheme: dark" in out
    assert ':root[data-theme="dark"]' in out
    assert ':root:not([data-theme="light"])' in out
    # every token defined on bare :root is also defined in both dark scopes
    light = set(re.findall(r"--([a-z0-9-]+):", out.split("@media")[0]))
    dark_media = set(re.findall(r"--([a-z0-9-]+):", out.split("@media")[1].split("}")[0]))
    assert light == dark_media, light ^ dark_media


def test_every_chart_has_a_table_twin(db, tmp_path):
    _populate(db, tmp_path)
    out = render_report_html(db, TEST_COHORT)
    assert out.count("<summary>Table view</summary>") >= 3


def test_no_combined_score(db, tmp_path):
    _populate(db, tmp_path)
    out = render_report_html(db, TEST_COHORT)
    assert "not combined into a score" in out
    assert "participation score" not in out.lower()


def test_help_checkbox_is_named_as_excluded(db):
    out = render_report_html(db, TEST_COHORT)
    assert "help checkbox appears nowhere" in out
    assert "help_request" not in out
    assert "check in with me" not in out


def test_cli_writes_the_file(db, tmp_path):
    target = tmp_path / "nested" / "report.html"
    assert main(["report", "--cohort", TEST_COHORT, "--html", str(target)]) == 0
    assert target.exists()
    assert target.read_text(encoding="utf-8").startswith("<!doctype html>")
