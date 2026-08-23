"""``cufa`` — the command line entry point.

Everything the console can do is here too. That is not duplication for its own
sake: it keeps the system scriptable, keeps it testable without a browser, and
keeps it usable on the day the web app breaks. The console is a convenience
layer over these commands, not the only door.
"""

from __future__ import annotations

import argparse
import json
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


# --------------------------------------------------------------------------
# database
# --------------------------------------------------------------------------

def _require_supabase() -> str:
    path = shutil.which("supabase")
    if not path:
        raise CufaError(
            "The Supabase CLI is not on PATH.\n"
            "Install it: https://supabase.com/docs/guides/local-development/cli/getting-started\n"
            "  macOS/Linux:  brew install supabase/tap/supabase\n"
            "  or npm:       npm install -g supabase"
        )
    return path


def _docker_running() -> bool:
    if not shutil.which("docker"):
        return False
    result = subprocess.run(
        ["docker", "info"], capture_output=True, text=True, timeout=60, check=False
    )
    return result.returncode == 0


def cmd_db(args: argparse.Namespace) -> int:
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
        subprocess.run([supabase, "start"], check=True)
        print("\nPostgres  postgresql://postgres:postgres@localhost:54322/postgres")
        print("Studio    http://localhost:54323")
        return 0

    if args.db_action == "down":
        subprocess.run([supabase, "stop"], check=True)
        return 0

    if args.db_action == "reset":
        subprocess.run([supabase, "db", "reset"], check=True)
        return 0

    raise CufaError(f"unknown db action {args.db_action!r}")


# --------------------------------------------------------------------------
# google + template
# --------------------------------------------------------------------------

def cmd_google(args: argparse.Namespace) -> int:
    from .google.oauth import authorization_url, credential_status, disconnect, store_credential

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
        # The console owns the redirect. On the CLI we print the URL and take
        # the pasted code, so a headless machine can still connect.
        from .google.oauth import build_flow

        url, _state = authorization_url(settings)
        print("Open this URL, sign in as the CU staff account that should own the forms:\n")
        print(f"  {url}\n")
        print("After approving you will be redirected to a URL containing `code=`.")
        code = input("Paste the value of `code` here: ").strip()
        if not code:
            raise CufaError("No code supplied; nothing was stored.")

        flow = build_flow(settings)
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
    from .google.factory import get_client
    from .template import MANUAL_STEP, create_template, get_template, verify_template
    from .errors import TemplateNotVerified

    with connection() as conn:
        client = get_client(conn)

        if args.template_action == "create":
            record = create_template(conn, client)
            print(f"Template form: {record.form_id}")
            print(f"  edit:    {record.edit_url}")
            print(f"  respond: {record.form_url}")
            print()
            print("ONE MANUAL STEP IS REQUIRED:")
            print(MANUAL_STEP)
            print()
            print("Then run `cufa template verify`. Provisioning stays blocked until it passes.")
            return 0

        if args.template_action == "verify":
            record = get_template(conn)
            if record is None:
                raise CufaError("No template exists. Run `cufa template create` first.")
            try:
                state = verify_template(conn, client)
            except TemplateNotVerified as exc:
                print(str(exc), file=sys.stderr)
                return 1
            print(f"Template {state.form_id} verified: emailCollectionType=VERIFIED")
            return 0

        if args.template_action == "status":
            record = get_template(conn)
            if record is None:
                print("No template created yet.")
                return 1
            print(f"Template form: {record.form_id}")
            print(f"  verified at    {record.verified_email_confirmed_at or '(never)'}")
            print(f"  last checked   {record.last_verified_at or '(never)'}")
            print(f"  settings seen  {json.dumps(record.settings_snapshot)}")
            return 0 if record.is_verified else 1

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
    from .sessions import SessionInput, announce_now, create_session, list_sessions

    if args.session_action == "suggest-passphrase":
        for word in suggest(args.count):
            print(word)
        print(f"\n{GUIDANCE}", file=sys.stderr)
        return 0

    with connection() as conn:
        if args.session_action == "list":
            rows = list_sessions(conn, args.cohort)
            if not rows:
                print("(no sessions)")
                return 0
            for row in rows:
                ready = "ready" if row["publish_verified_at"] else ("—" if not row["form_id"] else "UNPUBLISHED")
                print(
                    f"{row['session_id']}  {row['scheduled_at_local']}  "
                    f"{row['timezone']:<20} {row['title'][:34]:<34} form={ready:<11} "
                    f"responses={row['response_count']}"
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
            )
            warnings = check_reuse(conn, args.cohort, args.passphrase)
            for warning in warnings:
                print(f"WARNING: {warning.message()}", file=sys.stderr)
            if warnings and not args.allow_reuse:
                print("Refusing to save. Pass --allow-reuse to override.", file=sys.stderr)
                return 1
            session_id = create_session(conn, data)
            print(session_id)
            return 0

        if args.session_action == "announce":
            stamped = announce_now(conn, args.session)
            print(f"announced_at_utc = {stamped}")
            return 0

    raise CufaError(f"unknown session action {args.session_action!r}")


# --------------------------------------------------------------------------
# provisioning and ingest
# --------------------------------------------------------------------------

def cmd_provision(args: argparse.Namespace) -> int:
    from .google.factory import get_client
    from .provisioning import provision_session

    with connection() as conn:
        client = get_client(conn)
        result = provision_session(conn, client, args.session, dry_run=args.dry_run)

    print(f"session {result.session_id}: {result.summary}")
    if result.form_url:
        print(f"  form: {result.form_url}")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    from .google.factory import get_client
    from .ingest.forms_api import pull_cohort, pull_session

    if not args.session and not args.cohort:
        raise CufaError("pull needs --session <id> or --cohort <id>")

    with connection() as conn:
        client = get_client(conn)
        if args.session:
            result = pull_session(conn, client, args.session)
        else:
            result = pull_cohort(conn, client, args.cohort)

    print(f"pull: {result}")
    for warning in result.warnings:
        print(f"  WARNING: {warning}")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
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
    from .decisions import current_decision, human_override

    with connection() as conn:
        before = current_decision(conn, args.checkin)
        if before:
            print(
                f"superseding: status={before['status']} by={before['decided_by']} "
                f"rule={before['rule_name']} ai={before['ai_model']}"
            )
        human_override(
            conn, args.checkin, status=args.status, by_email=args.by, note=args.note
        )
    print(f"checkin {args.checkin} -> {args.status} (human)")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    from .report import ai_decisions, needs_review_queue, unresolved_identities

    with connection() as conn:
        if args.status == "ai":
            rows = ai_decisions(conn, args.cohort)
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
            rows = unresolved_identities(conn, args.cohort)
            for row in rows:
                print(
                    f"{row['email']}  seen={row['occurrence_count']}  "
                    f"first={row['first_seen_at']}  last={row['last_seen_at']}"
                )
            print(f"{len(rows)} unresolved address(es).")
            return 0

        rows = needs_review_queue(conn, args.cohort)
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
    from .report import cohort_report, render_report_text

    with connection() as conn:
        report = cohort_report(conn, args.cohort)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        print(render_report_text(report))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
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
        description="Civic Innovators Fellowship — mid-session passphrase check-in.",
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

    p = sub.add_parser("template", help="the one template form")
    p.add_argument("template_action", choices=["create", "verify", "status"])
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
    q = sp.add_parser("announce", help="stamp announced_at_utc — what latency is measured from")
    q.add_argument("--session", required=True)
    q = sp.add_parser("suggest-passphrase")
    q.add_argument("--count", type=int, default=5)
    p.set_defaults(func=cmd_session)

    p = sub.add_parser("provision", help="create the Google Form for a session")
    p.add_argument("--session", required=True)
    p.add_argument("--dry-run", action="store_true", help="log the calls without making them")
    p.set_defaults(func=cmd_provision)

    p = sub.add_parser("pull", help="pull responses via the Forms API")
    p.add_argument("--session", default=None)
    p.add_argument("--cohort", default=None)
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
    p.set_defaults(func=cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level or get_settings().log_level)
    try:
        return int(args.func(args) or 0)
    except CufaError as exc:
        print(f"\nerror: {exc}\n", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
