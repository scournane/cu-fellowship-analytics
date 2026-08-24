"""``cufa`` — the command line entry point.

Everything the console can do is here too. That is not duplication for its own
sake: it keeps the system scriptable, keeps it testable without a browser, and
keeps it usable on the day the web app breaks. The console is a convenience
layer over these commands, not the only door.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from typing import Any

from . import __version__
from .config import get_settings
from .db import connection
from .errors import CufaError
from .logging_setup import configure_logging, get_logger

log = get_logger("cufa")


def _session_id(value: str) -> str:
    """Validate a UUID argument before it reaches SQL.

    Without this a typo'd id reaches Postgres and comes back as
    `InvalidTextRepresentation`, which surfaces as a psycopg traceback and reads
    like the tool is broken rather than like the argument is wrong.
    """
    import uuid as _uuid

    try:
        return str(_uuid.UUID(str(value).strip()))
    except (ValueError, AttributeError, TypeError):
        raise CufaError(
            f"{value!r} is not a session id. Ids are UUIDs — run "
            f"`cufa session list` to see them."
        ) from None


def _checkin_id(value: str) -> str:
    """Same, for check-in ids."""
    import uuid as _uuid

    try:
        return str(_uuid.UUID(str(value).strip()))
    except (ValueError, AttributeError, TypeError):
        raise CufaError(
            f"{value!r} is not a check-in id. Ids are UUIDs — run "
            f"`cufa review` to see them."
        ) from None


# --------------------------------------------------------------------------
# database
# --------------------------------------------------------------------------

def _tool_argv(name: str, *args: str) -> list[str] | None:
    """Argv that CreateProcess can actually launch for a PATH command.

    A .cmd/.bat shim -- which is what ``npm install -g supabase`` leaves on
    Windows -- cannot be launched by bare name, only through cmd.exe. The twin
    of this lives in tasks.py, which cannot import it: tasks.py has to run on a
    bare interpreter, before this package is installed.
    """
    path = shutil.which(name)
    if not path:
        return None
    extra = [str(a) for a in args]
    if os.name == "nt" and path.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", path, *extra]
    return [path, *extra]


def _require_supabase() -> list[str]:
    argv = _tool_argv("supabase")
    if argv is None:
        raise CufaError(
            "The Supabase CLI is not on PATH.\n"
            "Install it: https://supabase.com/docs/guides/local-development/cli/getting-started\n"
            "  macOS/Linux:  brew install supabase/tap/supabase\n"
            "  or npm:       npm install -g supabase"
        )
    return argv


def _docker_running() -> bool:
    argv = _tool_argv("docker", "info")
    if argv is None:
        return False
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
    return result.returncode == 0


def cmd_db(args: argparse.Namespace) -> int:
    """Start, stop or reset the local Supabase stack.

    Checks Docker explicitly before shelling out. `supabase start` against a
    stopped daemon fails with a message about the Docker socket, which tells a
    non-technical operator nothing they can act on.
    """
    supabase = _require_supabase()
    if args.db_action == "up":
        if not _docker_running():
            raise CufaError(
                "Docker is not running, and the local Supabase stack runs in Docker.\n"
                "\n"
                "  1. Start Docker Desktop (macOS/Windows) or `sudo systemctl start docker` (Linux).\n"
                "  2. Confirm with `docker ps`.\n"
                "  3. Re-run `cufa db up`.\n"
            )
        print("Starting the local Supabase stack (first run pulls images; give it a few minutes)…")
        subprocess.run([*supabase, "start"], check=True)
        print("\nPostgres  postgresql://postgres:postgres@localhost:64322/postgres")
        print("Studio    http://localhost:64323")
        return 0

    if args.db_action == "down":
        subprocess.run([*supabase, "stop"], check=True)
        return 0

    if args.db_action == "reset":
        subprocess.run([*supabase, "db", "reset"], check=True)
        return 0

    raise CufaError(f"unknown db action {args.db_action!r}")


# --------------------------------------------------------------------------
# google + template
# --------------------------------------------------------------------------

def cmd_google(args: argparse.Namespace) -> int:
    """Connect, inspect or revoke the Google account that will own the forms.

    `status` and `disconnect` exit non-zero when there is nothing usable
    connected, so a shell script can gate on them.
    """
    from .google.oauth import credential_status, disconnect, store_credential

    settings = get_settings()

    if args.google_action == "status":
        with connection() as conn:
            status = credential_status(conn)
        if not status.connected:
            print("Google: not connected.")
            print("Run `cufa google connect`, or open the console's Connect Google screen.")
            return 1
        print(f"Google: connected as {status.account_email}")
        print(f"  connected at   {status.connected_at}")
        print(f"  last refreshed {status.last_refreshed_at or '(not yet)'}")
        print(f"  scopes         {', '.join(status.scopes)}")
        if not status.has_required_scopes:
            print("  WARNING: missing a required scope; reconnect to grant both.")
            return 1
        return 0

    if args.google_action == "disconnect":
        with connection() as conn:
            disconnect(conn)
        print("Disconnected. Stored refresh token cleared.")
        return 0

    if args.google_action == "connect":
        # The console owns the redirect; here the whole round trip is one
        # process, so a single Flow instance carries the PKCE verifier from
        # the URL it prints to the fetch_token() call below — no state or
        # cookie needed, unlike the console's two-HTTP-request version of this.
        from .google.oauth import build_flow

        flow = build_flow(settings)
        url, _state = flow.authorization_url(
            access_type="offline", include_granted_scopes="true", prompt="consent"
        )
        print("Open this URL, sign in as the CU staff account that should own the forms:\n")
        print(f"  {url}\n")
        print("After approving you will be redirected to a URL containing `code=`.")
        code = input("Paste the value of `code` here: ").strip()
        if not code:
            raise CufaError("No code supplied; nothing was stored.")

        flow.fetch_token(code=code)
        credentials = flow.credentials
        if not credentials.refresh_token:
            raise CufaError(
                "Google returned no refresh token. This happens when the account "
                "has already granted consent. Remove this app at "
                "https://myaccount.google.com/permissions and connect again."
            )

        email = _google_account_email(credentials)
        with connection() as conn:
            store_credential(
                conn,
                account_email=email,
                refresh_token=credentials.refresh_token,
                scopes=list(credentials.scopes or []),
                settings=settings,
            )
        print(f"Connected as {email}. Refresh token stored encrypted.")
        return 0

    raise CufaError(f"unknown google action {args.google_action!r}")


def _google_account_email(credentials: Any) -> str:
    """Read the connected account's address from the userinfo endpoint."""
    import google.auth.transport.requests as gart

    session = gart.AuthorizedSession(credentials)
    response = session.get("https://www.googleapis.com/oauth2/v2/userinfo", timeout=30)
    response.raise_for_status()
    return str(response.json().get("email", "")).strip().lower() or "unknown@unknown"


def cmd_template(args: argparse.Namespace) -> int:
    """Create, verify or inspect the template form.

    Database work finishes and commits before anything is printed. Printing
    inside the transaction means a closed stdout (``| head``, a killed pager)
    aborts the process between the write and the commit, silently losing it.
    """
    from .errors import TemplateNotVerified
    from .google.factory import get_client
    from .template import (
        MANUAL_STEP,
        PART_LABELS,
        all_templates,
        create_template,
        get_template,
        replace_template,
        verify_template,
    )

    part = getattr(args, "part", "a")

    if args.template_action == "create":
        with connection() as conn:
            record = create_template(conn, get_client(conn), part)
        print(f"Template form ({record.label}): {record.form_id}")
        print(f"  edit:    {record.edit_url}")
        print(f"  respond: {record.form_url}")
        print()
        print("ONE MANUAL STEP IS REQUIRED:")
        print(MANUAL_STEP)
        print()
        print("Then run `cufa template verify`. Provisioning stays blocked until it passes.")
        return 0

    if args.template_action == "replace":
        # The recovery path when the stored template cannot be opened: deleted
        # from Drive, owned by a different Google account, or left behind by the
        # demo. The old row is retired rather than deleted, so session forms
        # copied from it keep their provenance.
        with connection() as conn:
            record = replace_template(conn, get_client(conn), part)
        print(f"New template form ({record.label}): {record.form_id}")
        print(f"  edit:    {record.edit_url}")
        print(f"  respond: {record.form_url}")
        print()
        print("The previous template was retired. It is a NEW form, so the one")
        print("manual step has to be done again on it:")
        print(MANUAL_STEP)
        print()
        print(f"Then run `cufa template verify --part {part}`.")
        return 0

    if args.template_action == "verify":
        error: str | None = None
        with connection() as conn:
            client = get_client(conn)
            if get_template(conn, part) is None:
                raise CufaError(
                    f"No template exists for part {part}. Run "
                    f"`cufa template create --part {part}` first."
                )
            try:
                state = verify_template(conn, client, part)
            except TemplateNotVerified as exc:
                # The verdict (verified_email_confirmed_at cleared) still has to
                # commit, so the failure is captured rather than raised through
                # the transaction.
                error = str(exc)
        if error:
            print(error, file=sys.stderr)
            return 1
        print(
            f"Template {state.form_id} (part {part}) verified: "
            "emailCollectionType=VERIFIED"
        )
        return 0

    if args.template_action == "status":
        from .template import PARTS

        with connection() as conn:
            records = {r.part: r for r in all_templates(conn)}
        # Both parts, always. "Part B does not exist yet" is the state a person
        # most needs to see on this screen, and printing only what exists hides
        # exactly that.
        all_verified = True
        for candidate in PARTS:
            record = records.get(candidate)
            print(f"[{candidate}] {PART_LABELS[candidate]}")
            if record is None:
                print("  not created yet")
                all_verified = False
                continue
            print(f"  form           {record.form_id}")
            print(f"  verified at    {record.verified_email_confirmed_at or '(never)'}")
            print(f"  last checked   {record.last_verified_at or '(never)'}")
            print(f"  settings seen  {json.dumps(record.settings_snapshot)}")
            all_verified = all_verified and record.is_verified
        return 0 if all_verified else 1

    raise CufaError(f"unknown template action {args.template_action!r}")


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def cmd_load_roster(args: argparse.Namespace) -> int:
    from .roster import load_roster

    with connection() as conn:
        summary = load_roster(conn, args.csv, args.cohort)
    print(f"roster: {summary}")
    return 0


def cmd_load_sessions(args: argparse.Namespace) -> int:
    from .roster import load_sessions

    with connection() as conn:
        summary = load_sessions(conn, args.csv)
    print(f"sessions: {summary}")
    return 0


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------

def cmd_session(args: argparse.Namespace) -> int:
    from .passphrase import GUIDANCE, check_reuse, suggest
    from .sessions import (
        SessionInput,
        announce_now,
        create_session,
        get_session,
        list_sessions,
        update_session,
    )

    if args.session_action == "suggest-passphrase":
        for word in suggest(args.count):
            print(word)
        print(f"\n{GUIDANCE}", file=sys.stderr)
        return 0

    if args.session_action == "list":
        with connection() as conn:
            rows = list_sessions(conn, args.cohort)
        if not rows:
            print("(no sessions)")
            return 0
        for row in rows:
            def state(form_id: object, verified: object) -> str:
                if verified:
                    return "ready"
                return "—" if not form_id else "UNPUBLISHED"

            ready_a = state(row["form_id"], row["publish_verified_at"])
            ready_b = state(row["b_form_id"], row["b_publish_verified_at"])
            week = row["week_index"]
            print(
                f"{row['session_id']}  {row['scheduled_at_local']}  "
                f"{row['timezone']:<20} wk={(week if week is not None else '—'):<3} "
                f"{row['title'][:30]:<30} "
                f"A={ready_a:<11} B={ready_b:<11} "
                f"in={row['response_count']}/{row['b_response_count']}"
            )
        return 0

    if args.session_action == "create":
        local = datetime.fromisoformat(args.scheduled_at)
        data = SessionInput(
            cohort_id=args.cohort,
            title=args.title,
            scheduled_at_local=local,
            timezone=args.timezone,
            duration_minutes=args.duration,
            grace_minutes=args.grace,
            passphrase=args.passphrase,
            week_index=args.week,
            teacher_question=args.teacher_question,
        )
        with connection() as conn:
            warnings = check_reuse(conn, args.cohort, args.passphrase)
            if warnings and not args.allow_reuse:
                for warning in warnings:
                    print(f"WARNING: {warning.message()}", file=sys.stderr)
                print("Refusing to save. Pass --allow-reuse to override.", file=sys.stderr)
                return 1
            session_id = create_session(conn, data)
        for warning in warnings:
            print(f"WARNING: {warning.message()}", file=sys.stderr)
        print(session_id)
        return 0

    if args.session_action == "edit":
        with connection() as conn:
            existing = get_session(conn, _session_id(args.session))
            if existing is None:
                raise CufaError(f"No session with id {args.session}")

            # Every field is optional: editing only the passphrase should not
            # require re-typing the schedule, and re-typing it is how a time
            # gets changed by accident.
            local_raw = args.scheduled_at
            local = (
                datetime.fromisoformat(local_raw)
                if local_raw
                else existing["scheduled_at_local"]
            )
            passphrase = (
                existing["passphrase"] if args.passphrase is None else args.passphrase
            )
            cohort_id = existing["cohort_id"]

            warnings = check_reuse(
                conn, cohort_id, passphrase, exclude_session_id=args.session
            )
            if warnings and not args.allow_reuse:
                for warning in warnings:
                    print(f"WARNING: {warning.message()}", file=sys.stderr)
                print("Refusing to save. Pass --allow-reuse to override.", file=sys.stderr)
                return 1

            update_session(
                conn,
                _session_id(args.session),
                SessionInput(
                    cohort_id=cohort_id,
                    title=args.title or existing["title"],
                    scheduled_at_local=local,
                    timezone=args.timezone or existing["timezone"],
                    duration_minutes=args.duration or existing["duration_minutes"],
                    grace_minutes=(
                        existing["grace_minutes"] if args.grace is None else args.grace
                    ),
                    passphrase=passphrase,
                    week_index=(
                        existing["week_index"] if args.week is None else args.week
                    ),
                    teacher_question=(
                        existing["teacher_question"]
                        if args.teacher_question is None
                        else args.teacher_question
                    ),
                ),
            )
        for warning in warnings:
            print(f"WARNING: {warning.message()}", file=sys.stderr)
        print(f"updated {_session_id(args.session)}")
        return 0

    if args.session_action == "announce":
        when = datetime.fromisoformat(args.at) if args.at else None
        with connection() as conn:
            stamped = announce_now(conn, _session_id(args.session), when)
        print(f"announced_at_utc = {stamped}")
        return 0

    raise CufaError(f"unknown session action {args.session_action!r}")


# --------------------------------------------------------------------------
# provisioning and ingest
# --------------------------------------------------------------------------

def cmd_provision(args: argparse.Namespace) -> int:
    """Provision the form for one session, or for every session in a cohort.

    Safe to re-run: a session that already has a verified-published form is
    reported and skipped rather than given a second one.
    """
    from .db import fetch_all
    from .google.factory import get_client
    from .provisioning import provision_session

    if not args.session and not args.cohort:
        raise CufaError("provision needs --session <id> or --cohort <id>")

    # The client is built once. It is independent of the connection that
    # loaded the credential, and building a real one fetches Google's discovery
    # documents — not something to repeat per session.
    with connection() as conn:
        client = get_client(conn)
        skipped: list[str] = []
        titles: dict[str, str] = {}
        if args.session:
            targets = [_session_id(args.session)]
        else:
            rows = fetch_all(
                conn,
                'select session_id, title, week_index from "session" '
                "where cohort_id = %s order by scheduled_at_utc",
                (args.cohort,),
            )
            targets = []
            for row in rows:
                titles[str(row["session_id"])] = row["title"]
                # A session with no week number is not part of the rotation, so
                # Part B is simply not run for it — a makeup session or a one-off
                # is a legal thing to have. Skipped rather than failed, because
                # failing the batch on one unnumbered session would block the
                # nine that are fine. Naming --session explicitly still fails
                # loudly: there the operator asked for that one.
                if args.part == "b" and row["week_index"] is None:
                    skipped.append(row["title"])
                    continue
                targets.append(str(row["session_id"]))

    # One transaction per session, and failures collected rather than raised.
    #
    # Both halves matter. A single transaction around the batch means one bad
    # session rolls back the sessions already provisioned — while their forms
    # stay in Drive, now orphaned, because Google has no transaction to roll
    # back. And aborting on the first failure hides how many of the others were
    # fine, which is exactly what an operator needs to know before a lesson.
    results = []
    failures: list[tuple[str, str]] = []
    for session_id in targets:
        try:
            with connection() as conn:
                results.append(
                    provision_session(
                        conn, client, session_id, part=args.part, dry_run=args.dry_run
                    )
                )
        except CufaError as exc:
            failures.append((session_id, str(exc)))
        except Exception as exc:  # noqa: BLE001 - one session must not stop the rest
            failures.append((session_id, f"{type(exc).__name__}: {exc}"))

    for title in skipped:
        print(
            f"skipped “{title}”: no week number, so it is not part of the rotation "
            "and has no Part B form. Set one on the session if it should have."
        )
    for result in results:
        print(f"session {result.session_id} (part {result.part}): {result.summary}")
        if result.rotating_text:
            print(f"  rotating question ({result.rotating_kind}): {result.rotating_text}")
        if result.help_field_omitted_reason:
            # Never swallowed. A staffer who expected the checkbox has to see
            # that it was left off, and why.
            print(f"  NOTE: {result.help_field_omitted_reason}")
        if result.form_url:
            print(f"  form: {result.form_url}")

    for session_id, message in failures:
        label = titles.get(session_id, session_id)
        print(f"\nFAILED — session “{label}” ({session_id}):", file=sys.stderr)
        print(message, file=sys.stderr)

    if failures:
        print(
            f"\n{len(results)} session(s) provisioned, {len(failures)} failed. "
            "The ones that succeeded are ready; re-run after fixing the rest — "
            "provisioning is safe to repeat.",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    """Pull new responses through the Forms API.

    Incremental — each form has a watermark, so pulling repeatedly during a
    lesson costs one request per form rather than re-reading everything.
    """
    from .google.factory import get_client
    from .ingest.forms_api import pull_cohort, pull_session
    from .ingest.forms_b import pull_cohort_b, pull_session_b

    if not args.session and not args.cohort:
        raise CufaError("pull needs --session <id> or --cohort <id>")

    part = args.part
    with connection() as conn:
        client = get_client(conn)
        if args.session:
            target = _session_id(args.session)
            result = (
                pull_session_b(conn, client, target)
                if part == "b"
                else pull_session(conn, client, target)
            )
        else:
            result = (
                pull_cohort_b(conn, client, args.cohort)
                if part == "b"
                else pull_cohort(conn, client, args.cohort)
            )

    print(f"pull (part {part}): {result}")
    for warning in result.warnings:
        print(f"  WARNING: {warning}")

    if result.sessions_failed:
        # Warnings alone do not fail the command — an overlapping window is
        # advisory and the run still collected everything. A session that could
        # not be read is different: its responses are still sitting in Google,
        # and a scheduled pull that half-worked has to be visible to whatever
        # runs it.
        print("", file=sys.stderr)
        print(
            f"{result.sessions_failed} session(s) could not be pulled; their "
            "responses are uncollected. The rest were collected — re-run after "
            "fixing them, pulling is safe to repeat.",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    """Import a CSV exported from a manually created form.

    The fallback path. `--sheet-timezone` is required here and deliberately has
    no default; see cufa.ingest.csv_path for why guessing it is unsafe.
    """
    from .ingest.csv_path import ingest_csv

    with connection() as conn:
        result = ingest_csv(conn, args.csv, args.cohort, args.sheet_timezone)
    print(f"ingest: {result}")
    for warning in result.warnings:
        print(f"  WARNING: {warning}")
    return 0


# --------------------------------------------------------------------------
# adjudication and review
# --------------------------------------------------------------------------

def cmd_adjudicate(args: argparse.Namespace) -> int:
    """Run the decision tiers over a cohort.

    Exits zero even when tier 2 was unavailable: that case is a completed run
    whose undecidable rows are in needs_review, not a failure.
    """
    from .adjudicate.engine import adjudicate_cohort

    with connection() as conn:
        result = adjudicate_cohort(
            conn, args.cohort, use_ai=not args.no_ai, force=args.force
        )
    print(f"adjudicate: {result}")
    for warning in result.warnings:
        print(f"  {warning}")
    if result.ai_unavailable:
        print(
            f"  note: {result.ai_unavailable} case(s) went to needs_review because "
            "tier 2 was unavailable. The pipeline completed."
        )
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    """Record a human decision, superseding whatever is current.

    Prints what was superseded, so the person making the call sees what their
    judgment replaced rather than only what it became.
    """
    from .decisions import current_decision, human_override

    with connection() as conn:
        checkin_id = _checkin_id(args.checkin)
        before = current_decision(conn, checkin_id)
        human_override(
            conn, checkin_id, status=args.status, by_email=args.by, note=args.note
        )

    if before:
        print(
            f"superseding: status={before['status']} by={before['decided_by']} "
            f"rule={before['rule_name']} ai={before['ai_model']}"
        )
    print(f"checkin {checkin_id} -> {args.status} (human)")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    """Print one of the three review queues.

    The `ai` queue matters as much as `needs_review`: tier 2 has to be auditable
    by a person sampling its judgments, not trusted because it is a model.
    """
    from .report import ai_decisions, needs_review_queue, unresolved_identities

    with connection() as conn:
        if args.status == "ai":
            rows = ai_decisions(conn, args.cohort)
        elif args.status == "unresolved-identity":
            rows = unresolved_identities(conn, args.cohort)
        else:
            rows = needs_review_queue(conn, args.cohort)

    if args.status == "ai":
        for row in rows:
            print(
                f"{row['checkin_id']}  {row['session_title'] or '(no session)'}\n"
                f"    typed:      {row['passphrase_raw']!r}\n"
                f"    status:     {row['status']} (confidence {row['confidence']})\n"
                f"    model:      {row['ai_model']} prompt={row['ai_prompt_version']}\n"
                f"    reasoning:  {row['ai_reasoning']}\n"
            )
        print(f"{len(rows)} AI decision(s). Spot-check these; do not assume them.")
        return 0

    if args.status == "unresolved-identity":
        for row in rows:
            print(
                f"{row['email']}  seen={row['occurrence_count']}  "
                f"first={row['first_seen_at']}  last={row['last_seen_at']}"
            )
        print(f"{len(rows)} unresolved address(es).")
        return 0

    for row in rows:
        print(
            f"{row['checkin_id']}  {row['submitted_at_utc']}  "
            f"{row['session_title'] or '(no session)'}\n"
            f"    fellow:  {row['full_name'] or '(not on roster)'}\n"
            f"    typed:   {row['passphrase_raw']!r}  match={row['passphrase_match']}\n"
            f"    why:     {row['rule_name'] or row['ai_reasoning'] or '(no reason recorded)'}\n"
        )
    print(f"{len(rows)} check-in(s) need review, oldest first.")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """The cohort report.

    ``--confidence`` prints the Part B trend instead of the full report: median
    and IQR per week, with the interpretation note beside it. That note is not
    decoration — reading a single low confidence score as a finding is the most
    likely way this particular number gets misused.
    """
    from .confidence import render_trend_text, trend
    from .report import cohort_report, render_report_text

    with connection() as conn:
        if args.confidence and not args.json:
            print(render_trend_text(args.cohort, trend(conn, args.cohort)))
            return 0
        report = cohort_report(conn, args.cohort)

    if args.json:
        payload = report.to_dict()
        if args.confidence:
            payload = {"cohort_id": report.cohort_id, "confidence": payload["confidence"]}
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(render_report_text(report))
    return 0


# --------------------------------------------------------------------------
# Part B
# --------------------------------------------------------------------------


def cmd_themes(args: argparse.Namespace) -> int:
    """Muddiest-point themes for one session.

    Exits zero when no themes could be generated. No API key is a configuration
    state, not a failure of the run, and the answers themselves are stored and
    readable either way.
    """
    from .themes import generate_themes

    with connection() as conn:
        result = generate_themes(
            conn, _session_id(args.session), regenerate=args.regenerate
        )

    if result.message:
        print(result.message)
    if result.superseded:
        print(f"  superseded {result.superseded} theme(s) from the previous run")
    for theme in result.themes:
        print()
        print(f"  {theme['label']}  ({len(theme['members'])} answer(s))")
        print(f"    {theme['summary']}")
        print(f"    model={theme['model']} prompt={theme['prompt_version']}")
        for member in theme["members"]:
            print(f"      - {member['rotating_text']}")
    if result.themes:
        print()
        print(
            "Themes are about the CONTENT, not the people. No model judged any "
            "individual answer, and no name, address or id was sent."
        )
    return 0


def cmd_shoutouts(args: argparse.Namespace) -> int:
    """Review and link peer shoutouts.

    There is deliberately no ranking, leaderboard or points subcommand. See
    docs/decisions.md ADR-028 — and note the finding recorded there, that
    recognition should be ranked by giving rather than receiving if it is ever
    ranked at all.
    """
    from .shoutouts import candidates_for, link, review_queue

    if args.shoutout_action == "review":
        with connection() as conn:
            rows = review_queue(conn, args.cohort)
            suggestions = {
                str(row["shoutout_id"]): candidates_for(
                    conn, row["raw_text"], row["cohort_id"]
                )
                for row in rows
            }
        for row in rows:
            print(f"{row['shoutout_id']}  {row['raw_text']!r}")
            print(f"    session: {row['session_title'] or '(none)'}")
            options = suggestions.get(str(row["shoutout_id"])) or []
            if options:
                print("    could be:")
                for option in options:
                    print(f"      {option['fellow_id']}  {option['full_name']}")
            else:
                print("    matches nobody on the roster — legal, not an error")
        print()
        print(
            f"{len(rows)} unresolved name(s). Ambiguity is never resolved "
            "automatically: attributing praise to the wrong person is worse than "
            "leaving it unattached, because a wrong link is invisible."
        )
        return 0

    if args.shoutout_action == "link":
        with connection() as conn:
            row = link(conn, args.shoutout, args.fellow, by_email=args.by)
        print(f"shoutout {row['shoutout_id']} -> fellow {row['named_fellow_id']} (manual)")
        return 0

    raise CufaError(f"unknown shoutouts action {args.shoutout_action!r}")


def cmd_help_requests(args: argparse.Namespace) -> int:
    """List and acknowledge help requests.

    The only command that reads ``help_request``. It writes nothing to a log,
    and the table appears in no report, export or aggregate — see
    docs/safeguarding.md.
    """
    from .help_requests import acknowledge, list_requests

    if args.help_action == "list":
        with connection() as conn:
            rows = list_requests(conn, status=args.status or None, cohort_id=args.cohort)
        for row in rows:
            stamp = row["submitted_at_utc"].strftime("%Y-%m-%d %H:%M")
            print(f"{row['help_request_id']}  {row['status']:<12} {stamp} UTC")
            print(f"    fellow:  {row['full_name'] or row['submitted_email']}")
            print(f"    session: {row['session_title'] or '(none matched)'}")
            if row["acknowledged_by"]:
                print(
                    f"    picked up by {row['acknowledged_by']} "
                    f"at {row['acknowledged_at']}"
                )
            if row["note"]:
                print(f"    note: {row['note']}")
        print()
        print(f"{len(rows)} request(s). This list is not exported anywhere.")
        return 0

    if args.help_action in ("ack", "close"):
        status = "closed" if args.help_action == "close" else "acknowledged"
        with connection() as conn:
            row = acknowledge(
                conn, args.id, by_email=args.by, note=args.note, status=status
            )
        print(f"help request {row['help_request_id']} -> {row['status']}")
        return 0

    raise CufaError(f"unknown help-requests action {args.help_action!r}")


def cmd_rotation(args: argparse.Namespace) -> int:
    """Show the rotation schedule, and what the coming weeks will ask.

    The preview exists so a teacher can see which weeks need a custom question
    written *before* the week arrives. Provisioning refuses on the day if one is
    missing, and finding that out at 6:55pm is not the plan.
    """
    from .db import fetch_all
    from .rotation import get_rotation

    rotation = get_rotation()
    print(f"Rotation schedule — {rotation.source}")
    print(f"  version {rotation.version}   owner {rotation.owner}")
    print(f"  status  {rotation.status}")
    print(f"  {rotation.weeks} weeks, wrap={'on' if rotation.wrap else 'off'}")
    print()

    supplied: dict[int, str] = {}
    if args.cohort:
        with connection() as conn:
            for row in fetch_all(
                conn,
                'select week_index, teacher_question from "session" '
                "where cohort_id = %s and week_index is not null "
                "order by week_index",
                (args.cohort,),
            ):
                supplied[int(row["week_index"])] = row["teacher_question"] or ""

    rows = rotation.preview(args.from_week, args.weeks, teacher_questions=supplied)
    print(f"  {'wk':>3}  {'kind':<26} question")
    for row in rows:
        if row["error"]:
            print(f"  {row['week_index']:>3}  {row['error']}")
            continue
        marker = " (wraps)" if row["wrapped"] else ""
        text = row["text"] or (
            "NOT SET — provisioning will refuse this week"
            if row["needs_teacher_question"]
            else ""
        )
        print(f"  {row['week_index']:>3}  {row['kind'] + marker:<26} {text}")
    print()
    print(
        "The teacher's question appears most often because it is the only "
        "genuinely unfakeable one — it depends on content that only someone "
        "present would know."
    )
    if any(r["needs_teacher_question"] for r in rows):
        print()
        print(
            "Weeks marked NOT SET have no teacher question. Set one on the "
            "session before that week: provisioning blocks rather than "
            "substituting something generic."
        )
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Run the console.

    Announces the fake-client mode loudly: a staff member should never be
    unsure whether the forms they are looking at are real.
    """
    import uvicorn

    settings = get_settings()
    host = args.host or settings.console_host
    port = args.port or settings.console_port
    print(f"Console on http://{host}:{port}")
    if settings.fake_google:
        print("CUFA_FAKE_GOOGLE=1 — no Google calls will be made.")
    uvicorn.run("cufa.console.app:app", host=host, port=port, reload=args.reload, factory=False)
    return 0


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cufa",
        description=(
            "Civic Innovators Fellowship — mid-session passphrase check-in "
            "(part a) and end-of-session check-in (part b)."
        ),
    )
    parser.add_argument("--version", action="version", version=f"cufa {__version__}")
    parser.add_argument(
        "--log-level", default=None, help="DEBUG shows raw email addresses; INFO never does."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("db", help="local Supabase stack")
    p.add_argument("db_action", choices=["up", "down", "reset"])
    p.set_defaults(func=cmd_db)

    p = sub.add_parser("serve", help="run the console")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("google", help="connect / inspect the Google account")
    p.add_argument("google_action", choices=["connect", "status", "disconnect"])
    p.set_defaults(func=cmd_google)

    p = sub.add_parser("template", help="the template form for each part")
    p.add_argument(
        "template_action", choices=["create", "verify", "status", "replace"]
    )
    p.add_argument("--part", default="a", choices=["a", "b"], help="a = mid-session passphrase check-in, b = end-of-session check-in. Each part has its own template and its own form per session.")
    p.set_defaults(func=cmd_template)

    p = sub.add_parser("load-roster", help="load fellows from CSV")
    p.add_argument("--csv", required=True)
    p.add_argument("--cohort", required=True)
    p.set_defaults(func=cmd_load_roster)

    p = sub.add_parser("load-sessions", help="bulk import sessions from CSV")
    p.add_argument("--csv", required=True)
    p.set_defaults(func=cmd_load_sessions)

    p = sub.add_parser("session", help="create, list and announce sessions")
    sp = p.add_subparsers(dest="session_action", required=True)

    q = sp.add_parser("list")
    q.add_argument("--cohort", default=None)
    q = sp.add_parser("create")
    q.add_argument("--cohort", required=True)
    q.add_argument("--title", required=True)
    q.add_argument("--scheduled-at", required=True, help="local time, e.g. 2026-09-15T19:00")
    q.add_argument("--timezone", required=True, help="IANA name, e.g. America/New_York")
    q.add_argument("--duration", type=int, required=True)
    q.add_argument("--grace", type=int, default=15)
    q.add_argument("--passphrase", default=None)
    q.add_argument("--allow-reuse", action="store_true", help="save despite a reuse warning")
    q.add_argument(
        "--week",
        type=int,
        default=None,
        help=(
            "week of the fellowship, 1-based. Drives Part B's rotating question. "
            "Typed in rather than derived from the date, because rescheduling a "
            "session must not change which question it asks."
        ),
    )
    q.add_argument(
        "--teacher-question",
        default=None,
        help="the teacher's own question, needed on teacher-question weeks",
    )
    q = sp.add_parser("edit", help="change a session; every field is optional")
    q.add_argument("--session", required=True)
    q.add_argument("--title", default=None)
    q.add_argument("--scheduled-at", default=None, help="local time, e.g. 2026-09-15T19:00")
    q.add_argument("--timezone", default=None)
    q.add_argument("--duration", type=int, default=None)
    q.add_argument("--grace", type=int, default=None)
    q.add_argument("--passphrase", default=None)
    q.add_argument("--allow-reuse", action="store_true")
    q.add_argument("--week", type=int, default=None)
    q.add_argument("--teacher-question", default=None)

    q = sp.add_parser("announce", help="stamp announced_at_utc — what latency is measured from")
    q.add_argument("--session", required=True)
    q.add_argument(
        "--at",
        default=None,
        help="ISO-8601 instant WITH offset, e.g. 2026-09-27T23:18:00+00:00. Defaults to now.",
    )
    q = sp.add_parser("suggest-passphrase")
    q.add_argument("--count", type=int, default=5)
    p.set_defaults(func=cmd_session)

    p = sub.add_parser("provision", help="create the Google Form for a session")
    p.add_argument("--session", default=None)
    p.add_argument("--cohort", default=None, help="provision every session in a cohort")
    p.add_argument("--part", default="a", choices=["a", "b"], help="a = mid-session passphrase check-in, b = end-of-session check-in. Each part has its own template and its own form per session.")
    p.add_argument("--dry-run", action="store_true", help="log the calls without making them")
    p.set_defaults(func=cmd_provision)

    p = sub.add_parser("pull", help="pull responses via the Forms API")
    p.add_argument("--session", default=None)
    p.add_argument("--cohort", default=None)
    p.add_argument("--part", default="a", choices=["a", "b"], help="a = mid-session passphrase check-in, b = end-of-session check-in. Each part has its own template and its own form per session.")
    p.set_defaults(func=cmd_pull)

    p = sub.add_parser("ingest", help="fallback CSV ingest")
    isub = p.add_subparsers(dest="ingest_kind", required=True)
    q = isub.add_parser("part-a")
    q.add_argument("--csv", required=True)
    q.add_argument("--cohort", required=True)
    q.add_argument(
        "--sheet-timezone",
        default=None,
        help="IANA zone of the SPREADSHEET. Required; there is no default.",
    )
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("adjudicate", help="run the decision tiers over a cohort")
    p.add_argument("--cohort", required=True)
    p.add_argument("--no-ai", action="store_true", help="skip tier 2 entirely")
    p.add_argument(
        "--force", action="store_true", help="also overwrite HUMAN decisions (loudly)"
    )
    p.set_defaults(func=cmd_adjudicate)

    p = sub.add_parser("decide", help="human override (tier 3)")
    p.add_argument("--checkin", required=True)
    p.add_argument("--status", required=True, choices=["attended", "not_attended", "needs_review"])
    p.add_argument("--by", required=True, help="the deciding person's email")
    p.add_argument("--note", default=None)
    p.set_defaults(func=cmd_decide)

    p = sub.add_parser("review", help="the queues")
    p.add_argument(
        "--status",
        default="needs_review",
        choices=["needs_review", "ai", "unresolved-identity"],
    )
    p.add_argument("--cohort", default=None)
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("report", help="attendance report")
    p.add_argument("--cohort", required=True)
    p.add_argument("--json", action="store_true")
    p.add_argument(
        "--confidence",
        action="store_true",
        help="the Part B confidence trend by week: median and IQR, never a mean",
    )
    p.set_defaults(func=cmd_report)

    p = sub.add_parser(
        "themes", help="muddiest-point themes for one session (Part B)"
    )
    p.add_argument("--session", required=True)
    p.add_argument(
        "--regenerate",
        action="store_true",
        help="re-cluster; the previous themes are superseded, not overwritten",
    )
    p.set_defaults(func=cmd_themes)

    p = sub.add_parser("shoutouts", help="review and link peer shoutouts")
    sp = p.add_subparsers(dest="shoutout_action", required=True)
    q = sp.add_parser("review", help="unresolved names, with candidates")
    q.add_argument("--cohort", default=None)
    q = sp.add_parser("link", help="attach one name to one fellow")
    q.add_argument("--shoutout", required=True)
    q.add_argument("--fellow", required=True)
    q.add_argument("--by", required=True, help="the resolving person's email")
    p.set_defaults(func=cmd_shoutouts)

    p = sub.add_parser(
        "help-requests",
        help="fellows who asked to be checked in with (access-restricted)",
    )
    sp = p.add_subparsers(dest="help_action", required=True)
    q = sp.add_parser("list")
    q.add_argument(
        "--status", default="open", choices=["open", "acknowledged", "closed", ""]
    )
    q.add_argument("--cohort", default=None)
    q = sp.add_parser("ack", help="record that someone has picked this up")
    q.add_argument("--id", required=True)
    q.add_argument("--by", required=True, help="the responding person's email")
    q.add_argument("--note", default=None)
    q = sp.add_parser("close", help="record that this has been dealt with")
    q.add_argument("--id", required=True)
    q.add_argument("--by", required=True)
    q.add_argument("--note", default=None)
    p.set_defaults(func=cmd_help_requests)

    p = sub.add_parser(
        "rotation", help="the rotating-question schedule, and what is coming"
    )
    p.add_argument("--from-week", type=int, default=1)
    p.add_argument("--weeks", type=int, default=10)
    p.add_argument(
        "--cohort",
        default=None,
        help="check this cohort's sessions for missing teacher questions",
    )
    p.set_defaults(func=cmd_rotation)

    return parser


def _force_utf8_output() -> None:
    """Print UTF-8 regardless of the console's default encoding.

    The report, the fixture names and the guidance text all contain em-dashes
    and curly quotes. A Windows console still defaults to a legacy code page in
    many setups, where printing those either raises UnicodeEncodeError — which
    reads as the pipeline crashing — or renders them as replacement characters.
    Neither is acceptable output for a tool whose job is to be readable.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                # A stream that cannot be reconfigured (already detached, or a
                # test harness substitute) is not worth failing the command for.
                pass


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns an exit code rather than raising.

    Expected failures (a missing template, an unverified one, a missing
    timezone flag) print their guidance and exit 1. Only genuinely unexpected
    exceptions reach the user as a traceback, so a traceback always means a bug.
    """
    _force_utf8_output()
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level or get_settings().log_level)
    try:
        return int(args.func(args) or 0)
    except CufaError as exc:
        print(f"\nerror: {exc}\n", file=sys.stderr)
        return 1
    except BrokenPipeError:
        # `cufa review | head` closes the pipe partway through. That is a normal
        # way to use these commands, not a failure, and a traceback here would
        # look like the query broke. Python flushes stdout at exit, which would
        # raise again, so stdout is redirected to devnull first.
        import os

        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
