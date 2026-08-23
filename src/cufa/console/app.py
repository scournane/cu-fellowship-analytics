"""The session console: five screens, server-rendered.

Audience: two or three CU staff who are not engineers, one of whom will be
running a lesson while using screen four. The design follows from that.

* **Server-rendered HTML with plain forms.** Every action is a form POST that
  works without JavaScript. The small amount of script on the pages — the live
  response count, the copy button, the passphrase suggestion — is enhancement,
  and each one has a working fallback if it never runs.
* **No CDN, no build step, no npm.** One `pip install` produces a running
  console. A build toolchain is a thing that breaks in November when nobody
  here still remembers it existed.
* **Failure is shown, never summarised.** When provisioning or verification
  fails, the exception text goes on the screen in full. These are the Google
  traps, and every one of them fails in a way that looks like success — so a
  friendly "something went wrong" would be actively harmful.

The console is a convenience layer over the same functions ``cufa`` exposes on
the command line. It never reimplements a rule.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from ..config import Settings, get_settings
from ..db import connection, fetch_all, fetch_one
from ..decisions import human_override
from ..errors import (
    ConfigError,
    CufaError,
    DatabaseUnreachable,
    GoogleNotConnected,
    PublishVerificationFailed,
    TemplateNotVerified,
)
from ..google.base import SCOPES
from ..google.factory import get_client
from ..google.oauth import authorization_url, credential_status, disconnect, store_credential
from ..ingest.forms_api import pull_session
from ..logging_setup import configure_logging, get_logger
from ..passphrase import ACCESSIBILITY_REMINDER, GUIDANCE, check_reuse, suggest
from ..provisioning import get_session_form, is_ready, provision_session
from ..report import ai_decisions, needs_review_queue, unresolved_identities
from ..sessions import SessionInput, announce_now, create_session, get_session, list_sessions, update_session
from ..template import MANUAL_STEP, create_template, get_template, verify_template
from ..timeutil import TimezoneError, get_zone
from .auth import (
    COOKIE_NAME,
    SESSION_MAX_AGE,
    ConsoleUser,
    NotSignedIn,
    dev_signin_available,
    is_allowed,
    issue_session,
    read_session,
    read_state,
    sign_state,
)
from .qr import QrTooLong, qr_svg

log = get_logger(__name__)

_HERE = Path(__file__).parent
TEMPLATES_DIR = _HERE / "templates"
STATIC_DIR = _HERE / "static"

# The account recorded when the fake client stands in for Google. It is an
# `.invalid` domain by RFC 2606 so it can never be a real address.
FAKE_ACCOUNT_EMAIL = "fake-google-client@example.invalid"

configure_logging(get_settings().log_level)

app = FastAPI(title="CU check-in console", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# --------------------------------------------------------------------------
# request plumbing
# --------------------------------------------------------------------------


def require_user(request: Request) -> ConsoleUser:
    """Guard every console screen. Raises ``NotSignedIn`` for the handler."""
    settings = get_settings()
    user = read_session(settings, request.cookies.get(COOKIE_NAME))
    if user is None:
        raise NotSignedIn()
    request.state.user = user
    return user


def render(
    request: Request, name: str, *, status_code: int = 200, **context: Any
) -> HTMLResponse:
    """Render a template with the context every page needs."""
    settings = get_settings()
    base: dict[str, Any] = {
        "settings": settings,
        "user": getattr(request.state, "user", None),
        "path": request.url.path,
        "dev_signin": dev_signin_available(settings),
        "no_allowlist": not settings.console_allowlist,
    }
    base.update(context)
    return templates.TemplateResponse(request, name, base, status_code=status_code)


@app.exception_handler(NotSignedIn)
def _not_signed_in(request: Request, exc: NotSignedIn) -> Response:
    """Send a browser to the sign-in page rather than showing it a 401 body."""
    target = quote(str(request.url.path), safe="/")
    return RedirectResponse(f"/signin?next={target}", status_code=303)


@app.exception_handler(DatabaseUnreachable)
def _database_down(request: Request, exc: DatabaseUnreachable) -> Response:
    """Every screen degrades to the same page: what broke and how to fix it."""
    if request.url.path.endswith(".json"):
        return JSONResponse({"error": "database_unreachable", "hint": str(exc)}, status_code=503)
    return render(request, "db_down.html", status_code=503, hint=str(exc))


def _parse_session_id(raw: str) -> str | None:
    """Reject anything that is not a UUID before it reaches Postgres."""
    try:
        return str(uuid.UUID(raw))
    except (ValueError, AttributeError):
        return None


def _cohorts(conn: Any) -> list[dict[str, Any]]:
    return fetch_all(conn, "select cohort_id, label from cohort order by cohort_id")


# --------------------------------------------------------------------------
# sign in / sign out
# --------------------------------------------------------------------------


@app.get("/signin", response_class=HTMLResponse)
def signin_page(request: Request, next: str = "/", error: str | None = None) -> Response:
    settings = get_settings()
    user = read_session(settings, request.cookies.get(COOKIE_NAME))
    if user is not None:
        return RedirectResponse(next or "/", status_code=303)
    google_ready = bool(settings.google_client_id and settings.google_client_secret)
    return render(
        request,
        "signin.html",
        title="Sign in",
        next_path=next or "/",
        google_ready=google_ready,
        error=error,
    )


@app.post("/signin/dev")
def signin_dev(
    request: Request, email: str = Form(...), next: str = Form("/")
) -> Response:
    """The no-Google door. Allowlisted addresses only, and labelled as a bypass."""
    settings = get_settings()
    if not dev_signin_available(settings):
        return render(
            request,
            "signin.html",
            status_code=403,
            title="Sign in",
            next_path=next,
            google_ready=bool(settings.google_client_id),
            error=(
                "Developer sign-in is switched off. It is available only when "
                "CUFA_FAKE_GOOGLE=1 or when no allowlist is configured. Sign in "
                "with Google instead."
            ),
        )
    if not is_allowed(settings, email):
        log.warning("console sign-in refused: address not on the allowlist")
        return render(
            request,
            "signin.html",
            status_code=403,
            title="Sign in",
            next_path=next,
            google_ready=bool(settings.google_client_id),
            error=(
                f"{email.strip()} is not on the console allowlist. Ask whoever "
                "runs this install to add it to CUFA_CONSOLE_ALLOWLIST in .env, "
                "then try again."
            ),
        )

    user = ConsoleUser(email=email.strip().lower(), via="dev")
    response = RedirectResponse(next or "/", status_code=303)
    _set_session_cookie(response, settings, user)
    log.info("console sign-in via dev bypass user=%s", user.masked_email)
    return response


@app.get("/signin/google")
def signin_google(request: Request, next: str = "/") -> Response:
    """Start the Google sign-in round trip for the console itself."""
    settings = get_settings()
    try:
        state = sign_state(
            settings, {"purpose": "signin", "next": next, "nonce": secrets.token_urlsafe(12)}
        )
        flow = _signin_flow(settings, state=state)
        url, _ = flow.authorization_url(prompt="select_account", include_granted_scopes="true")
    except (ConfigError, CufaError) as exc:
        return render(
            request,
            "signin.html",
            status_code=400,
            title="Sign in",
            next_path=next,
            google_ready=False,
            error=str(exc),
        )
    return RedirectResponse(url, status_code=303)


@app.post("/signout")
def signout(request: Request) -> Response:
    response = RedirectResponse("/signin", status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


def _set_session_cookie(response: Response, settings: Settings, user: ConsoleUser) -> None:
    response.set_cookie(
        COOKIE_NAME,
        issue_session(settings, user),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
        # The console is documented as local-only, and forcing Secure would make
        # it unusable over plain http on 127.0.0.1. Behind TLS the deployment
        # sets this; it is not something to guess from inside the process.
        secure=False,
    )


def _signin_flow(settings: Settings, *, state: str | None = None) -> Any:
    """An OAuth flow that asks for identity only.

    Deliberately separate from ``cufa.google.oauth.build_flow``: connecting the
    Forms account and proving who is at the keyboard are different grants, and
    the sign-in one must not ask for Drive or Forms scope.
    """
    from google_auth_oauthlib.flow import Flow

    from ..google.oauth import client_config

    flow = Flow.from_client_config(
        client_config(settings),
        scopes=["openid", "https://www.googleapis.com/auth/userinfo.email"],
        state=state,
    )
    flow.redirect_uri = settings.google_redirect_uri
    return flow


def _account_email(credentials: Any) -> str:
    """Ask Google which account just consented."""
    import google.auth.transport.requests as gart

    session = gart.AuthorizedSession(credentials)
    response = session.get("https://www.googleapis.com/oauth2/v2/userinfo", timeout=30)
    response.raise_for_status()
    return str(response.json().get("email", "")).strip().lower()


# --------------------------------------------------------------------------
# screen 1 — connect Google
# --------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def connect_screen(
    request: Request,
    user: ConsoleUser = Depends(require_user),
    notice: str | None = None,
    error: str | None = None,
) -> Response:
    settings = get_settings()
    with connection() as conn:
        status = credential_status(conn)
    return render(
        request,
        "connect.html",
        title="Connect Google",
        status=status,
        required_scopes=SCOPES,
        notice=notice,
        error=error,
        google_ready=bool(settings.google_client_id and settings.google_client_secret),
    )


@app.post("/google/connect")
def google_connect(request: Request, user: ConsoleUser = Depends(require_user)) -> Response:
    settings = get_settings()

    if settings.fake_google:
        # Simulated connect: records the same row a real consent would, so every
        # downstream screen behaves identically, and makes no network call.
        try:
            with connection() as conn:
                store_credential(
                    conn,
                    account_email=FAKE_ACCOUNT_EMAIL,
                    refresh_token="fake-refresh-token-not-a-credential",
                    scopes=list(SCOPES),
                    settings=settings,
                )
            return RedirectResponse(
                "/?notice=" + quote("Simulated connection recorded. No Google call was made."),
                status_code=303,
            )
        except ConfigError as exc:
            return _connect_error(
                request,
                f"{exc}\n\nIn fake-Google mode nothing is sent to Google, but the "
                "credential row is still written encrypted, so the key is still "
                "required. Every other screen works without it.",
            )

    try:
        state = sign_state(
            settings, {"purpose": "connect", "nonce": secrets.token_urlsafe(12)}
        )
        url, _ = authorization_url(settings, state=state)
    except (ConfigError, CufaError) as exc:
        return _connect_error(request, str(exc))
    return RedirectResponse(url, status_code=303)


def _connect_error(request: Request, message: str) -> Response:
    with connection() as conn:
        status = credential_status(conn)
    settings = get_settings()
    return render(
        request,
        "connect.html",
        status_code=400,
        title="Connect Google",
        status=status,
        required_scopes=SCOPES,
        error=message,
        google_ready=bool(settings.google_client_id and settings.google_client_secret),
    )


@app.get("/google/callback")
def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    """One redirect URI, two flows, told apart by the signed state parameter."""
    settings = get_settings()
    payload = read_state(settings, state)
    if payload is None:
        return render(
            request,
            "message.html",
            status_code=400,
            title="Sign-in failed",
            heading="That sign-in link did not verify",
            body=(
                "The state parameter Google returned was missing, altered or more "
                "than ten minutes old. Start again from the sign-in page."
            ),
            link="/signin",
            link_label="Back to sign in",
        )

    purpose = str(payload.get("purpose") or "")

    if error:
        message = f"Google returned an error instead of a code: {error}"
        if purpose == "signin":
            return RedirectResponse("/signin?error=" + quote(message), status_code=303)
        return _connect_error(request, message)

    if not code:
        return _connect_error(request, "Google redirected back without an authorization code.")

    if purpose == "signin":
        return _finish_signin(request, settings, code=code, state=state, payload=payload)
    return _finish_connect(request, settings, code=code, state=state)


def _finish_signin(
    request: Request, settings: Settings, *, code: str, state: str | None, payload: dict[str, Any]
) -> Response:
    try:
        flow = _signin_flow(settings, state=state)
        flow.fetch_token(code=code)
        email = _account_email(flow.credentials)
    except Exception as exc:  # network, consent, or clock problems all land here
        log.warning("console Google sign-in failed: %s", type(exc).__name__)
        return RedirectResponse(
            "/signin?error=" + quote(f"Google sign-in did not complete: {exc}"), status_code=303
        )

    if not is_allowed(settings, email):
        log.warning("console sign-in refused: address not on the allowlist")
        return RedirectResponse(
            "/signin?error="
            + quote(
                "That Google account is not on the console allowlist. Ask whoever "
                "runs this install to add it to CUFA_CONSOLE_ALLOWLIST."
            ),
            status_code=303,
        )

    user = ConsoleUser(email=email, via="google")
    destination = str(payload.get("next") or "/")
    if not destination.startswith("/"):
        destination = "/"
    response = RedirectResponse(destination, status_code=303)
    _set_session_cookie(response, settings, user)
    log.info("console sign-in via Google user=%s", user.masked_email)
    return response


def _finish_connect(
    request: Request, settings: Settings, *, code: str, state: str | None
) -> Response:
    from ..google.oauth import build_flow

    try:
        flow = build_flow(settings, state=state)
        flow.fetch_token(code=code)
        credentials = flow.credentials
        if not credentials.refresh_token:
            return _connect_error(
                request,
                "Google returned no refresh token. That happens when this account "
                "has already granted consent. Remove the app at "
                "https://myaccount.google.com/permissions and connect again.",
            )
        email = _account_email(credentials)
        with connection() as conn:
            store_credential(
                conn,
                account_email=email,
                refresh_token=credentials.refresh_token,
                scopes=list(credentials.scopes or SCOPES),
                settings=settings,
            )
    except (CufaError, ConfigError) as exc:
        return _connect_error(request, str(exc))
    except Exception as exc:
        log.warning("Google connection failed: %s", type(exc).__name__)
        return _connect_error(request, f"Connecting to Google failed: {exc}")

    return RedirectResponse(
        "/?notice=" + quote("Connected. The refresh token is stored encrypted."), status_code=303
    )


@app.post("/google/disconnect")
def google_disconnect(request: Request, user: ConsoleUser = Depends(require_user)) -> Response:
    with connection() as conn:
        disconnect(conn)
    log.info("google credential disconnected by=%s", user.masked_email)
    return RedirectResponse(
        "/?notice=" + quote("Disconnected. The stored refresh token was cleared."),
        status_code=303,
    )


# --------------------------------------------------------------------------
# screen 2 — template setup
# --------------------------------------------------------------------------


def _template_context(conn: Any) -> dict[str, Any]:
    record = get_template(conn)
    return {
        "record": record,
        "manual_step": MANUAL_STEP,
        "blocked": record is None or not record.is_verified,
    }


@app.get("/template", response_class=HTMLResponse)
def template_screen(request: Request, user: ConsoleUser = Depends(require_user)) -> Response:
    with connection() as conn:
        context = _template_context(conn)
    return render(request, "template.html", title="Template setup", **context)


@app.post("/template/create")
def template_create(request: Request, user: ConsoleUser = Depends(require_user)) -> Response:
    with connection() as conn:
        try:
            client = get_client(conn)
            create_template(conn, client)
            context = _template_context(conn)
            context["notice"] = (
                "Template form created. Now do the one manual step below, then "
                "press Verify template."
            )
        except (CufaError, GoogleNotConnected) as exc:
            context = _template_context(conn)
            context["error"] = str(exc)
    return render(request, "template.html", title="Template setup", **context)


@app.post("/template/verify")
def template_verify(request: Request, user: ConsoleUser = Depends(require_user)) -> Response:
    """Green only when the API itself says VERIFIED. The human's word is not evidence."""
    with connection() as conn:
        try:
            client = get_client(conn)
            state = verify_template(conn, client)
            context = _template_context(conn)
            context["verified_state"] = state
            context["notice"] = (
                f"Verified: the API reports emailCollectionType="
                f"{state.email_collection_type}. Provisioning is unblocked."
            )
        except TemplateNotVerified as exc:
            context = _template_context(conn)
            context["error"] = str(exc)
        except (CufaError, GoogleNotConnected) as exc:
            context = _template_context(conn)
            context["error"] = str(exc)
    return render(request, "template.html", title="Template setup", **context)


# --------------------------------------------------------------------------
# screen 3 — sessions
# --------------------------------------------------------------------------


@app.get("/sessions", response_class=HTMLResponse)
def sessions_screen(
    request: Request,
    user: ConsoleUser = Depends(require_user),
    cohort: str | None = Query(default=None),
    notice: str | None = None,
) -> Response:
    with connection() as conn:
        cohorts = _cohorts(conn)
        rows = list_sessions(conn, cohort or None)
    return render(
        request,
        "sessions.html",
        title="Sessions",
        sessions=rows,
        cohorts=cohorts,
        selected_cohort=cohort or "",
        notice=notice,
    )


def _blank_form(cohorts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "title": "",
        "scheduled_at": "",
        "timezone": "",
        "duration_minutes": "60",
        "grace_minutes": "15",
        "passphrase": "",
        "cohort_id": cohorts[0]["cohort_id"] if cohorts else "",
    }


@app.get("/sessions/new", response_class=HTMLResponse)
def session_new_form(request: Request, user: ConsoleUser = Depends(require_user)) -> Response:
    with connection() as conn:
        cohorts = _cohorts(conn)
    return render(
        request,
        "session_form.html",
        title="New session",
        heading="New session",
        action="/sessions/new",
        values=_blank_form(cohorts),
        cohorts=cohorts,
        guidance=GUIDANCE,
        errors=[],
        reuse_warnings=[],
    )


def _read_session_form(
    *,
    title: str,
    scheduled_at: str,
    timezone: str,
    duration_minutes: str,
    grace_minutes: str,
    passphrase: str,
    cohort_id: str,
) -> tuple[SessionInput | None, list[str], dict[str, Any]]:
    """Validate the posted form, returning the input, the errors, and the echo."""
    values = {
        "title": title,
        "scheduled_at": scheduled_at,
        "timezone": timezone,
        "duration_minutes": duration_minutes,
        "grace_minutes": grace_minutes,
        "passphrase": passphrase,
        "cohort_id": cohort_id,
    }
    errors: list[str] = []

    if not title.strip():
        errors.append("Title is required.")

    local: datetime | None = None
    try:
        local = datetime.fromisoformat(scheduled_at.strip())
    except ValueError:
        errors.append("Scheduled at must be a date and time, for example 2026-09-15 13:05.")

    zone_name = timezone.strip()
    if not zone_name:
        errors.append("Timezone is required — it is what turns the local time into an instant.")
    else:
        try:
            get_zone(zone_name)
        except TimezoneError as exc:
            errors.append(str(exc))

    def _positive(raw: str, label: str, minimum: int) -> int | None:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            errors.append(f"{label} must be a whole number of minutes.")
            return None
        if value < minimum:
            errors.append(f"{label} must be {minimum} or more.")
            return None
        return value

    duration = _positive(duration_minutes, "Duration", 1)
    grace = _positive(grace_minutes, "Grace", 0)

    if not cohort_id.strip():
        errors.append("Cohort is required — everything is keyed to one.")

    if errors or local is None or duration is None or grace is None:
        return None, errors, values

    return (
        SessionInput(
            cohort_id=cohort_id.strip(),
            title=title.strip(),
            scheduled_at_local=local,
            timezone=zone_name,
            duration_minutes=duration,
            grace_minutes=grace,
            passphrase=passphrase.strip() or None,
        ),
        [],
        values,
    )


@app.post("/sessions/new")
def session_create(
    request: Request,
    user: ConsoleUser = Depends(require_user),
    title: str = Form(""),
    scheduled_at: str = Form(""),
    timezone: str = Form(""),
    duration_minutes: str = Form("60"),
    grace_minutes: str = Form("15"),
    passphrase: str = Form(""),
    cohort_id: str = Form(""),
    confirm_reuse: str = Form(""),
) -> Response:
    data, errors, values = _read_session_form(
        title=title,
        scheduled_at=scheduled_at,
        timezone=timezone,
        duration_minutes=duration_minutes,
        grace_minutes=grace_minutes,
        passphrase=passphrase,
        cohort_id=cohort_id,
    )

    with connection() as conn:
        cohorts = _cohorts(conn)
        warnings: list[str] = []
        if data is not None:
            if not confirm_reuse:
                warnings = [w.message() for w in check_reuse(conn, data.cohort_id, data.passphrase)]
            if not warnings:
                session_id = create_session(conn, data)
                return RedirectResponse(
                    f"/sessions/{session_id}?notice=" + quote("Session created."),
                    status_code=303,
                )

    return render(
        request,
        "session_form.html",
        status_code=400 if errors else 200,
        title="New session",
        heading="New session",
        action="/sessions/new",
        values=values,
        cohorts=cohorts,
        guidance=GUIDANCE,
        errors=errors,
        reuse_warnings=warnings,
    )


@app.get("/sessions/{session_id}/edit", response_class=HTMLResponse)
def session_edit_form(
    request: Request, session_id: str, user: ConsoleUser = Depends(require_user)
) -> Response:
    parsed = _parse_session_id(session_id)
    if parsed is None:
        return _not_found(request)
    with connection() as conn:
        cohorts = _cohorts(conn)
        row = get_session(conn, parsed)
    if row is None:
        return _not_found(request)

    local = row["scheduled_at_local"]
    return render(
        request,
        "session_form.html",
        title=f"Edit {row['title']}",
        heading=f"Edit “{row['title']}”",
        action=f"/sessions/{parsed}/edit",
        values={
            "title": row["title"],
            "scheduled_at": local.strftime("%Y-%m-%dT%H:%M") if local else "",
            "timezone": row["timezone"],
            "duration_minutes": str(row["duration_minutes"]),
            "grace_minutes": str(row["grace_minutes"]),
            "passphrase": row["passphrase"] or "",
            "cohort_id": row["cohort_id"],
        },
        cohorts=cohorts,
        guidance=GUIDANCE,
        errors=[],
        reuse_warnings=[],
        session_id=parsed,
    )


@app.post("/sessions/{session_id}/edit")
def session_edit(
    request: Request,
    session_id: str,
    user: ConsoleUser = Depends(require_user),
    title: str = Form(""),
    scheduled_at: str = Form(""),
    timezone: str = Form(""),
    duration_minutes: str = Form("60"),
    grace_minutes: str = Form("15"),
    passphrase: str = Form(""),
    cohort_id: str = Form(""),
    confirm_reuse: str = Form(""),
) -> Response:
    parsed = _parse_session_id(session_id)
    if parsed is None:
        return _not_found(request)

    data, errors, values = _read_session_form(
        title=title,
        scheduled_at=scheduled_at,
        timezone=timezone,
        duration_minutes=duration_minutes,
        grace_minutes=grace_minutes,
        passphrase=passphrase,
        cohort_id=cohort_id,
    )

    with connection() as conn:
        cohorts = _cohorts(conn)
        warnings: list[str] = []
        if data is not None:
            if not confirm_reuse:
                warnings = [
                    w.message()
                    for w in check_reuse(
                        conn, data.cohort_id, data.passphrase, exclude_session_id=parsed
                    )
                ]
            if not warnings:
                update_session(conn, parsed, data)
                return RedirectResponse(
                    f"/sessions/{parsed}?notice=" + quote("Session updated."), status_code=303
                )

    return render(
        request,
        "session_form.html",
        status_code=400 if errors else 200,
        title="Edit session",
        heading="Edit session",
        action=f"/sessions/{parsed}/edit",
        values=values,
        cohorts=cohorts,
        guidance=GUIDANCE,
        errors=errors,
        reuse_warnings=warnings,
        session_id=parsed,
    )


# --------------------------------------------------------------------------
# screen 4 — session detail (the mid-lesson view)
# --------------------------------------------------------------------------


def _detail_context(conn: Any, session_id: str) -> dict[str, Any] | None:
    row = get_session(conn, session_id)
    if row is None:
        return None
    template = get_template(conn)
    ready = is_ready(conn, session_id)
    form_url = row.get("form_url")

    qr_markup: Markup | None = None
    qr_error: str | None = None
    if ready and form_url:
        try:
            qr_markup = Markup(qr_svg(form_url, title=f"QR code for {row['title']}"))
        except QrTooLong as exc:
            qr_error = str(exc)

    return {
        "session": row,
        "template": template,
        "template_blocked": template is None or not template.is_verified,
        "ready": ready,
        "form_url": form_url,
        "qr": qr_markup,
        "qr_error": qr_error,
        "accessibility_reminder": ACCESSIBILITY_REMINDER,
        "provisioning_log": fetch_all(
            conn,
            """
            select action, outcome, error, at
              from provisioning_log
             where session_id = %s
             order by at desc
             limit 8
            """,
            (session_id,),
        ),
    }


@app.get("/sessions/{session_id}", response_class=HTMLResponse)
def session_detail(
    request: Request,
    session_id: str,
    user: ConsoleUser = Depends(require_user),
    notice: str | None = None,
) -> Response:
    parsed = _parse_session_id(session_id)
    if parsed is None:
        return _not_found(request)
    with connection() as conn:
        context = _detail_context(conn, parsed)
    if context is None:
        return _not_found(request)
    return render(
        request, "session_detail.html", title=context["session"]["title"], notice=notice, **context
    )


@app.post("/sessions/{session_id}/provision")
def session_provision(
    request: Request,
    session_id: str,
    user: ConsoleUser = Depends(require_user),
    dry_run: str = Form(""),
) -> Response:
    parsed = _parse_session_id(session_id)
    if parsed is None:
        return _not_found(request)

    notice: str | None = None
    error: str | None = None
    with connection() as conn:
        try:
            client = get_client(conn)
            result = provision_session(conn, client, parsed, dry_run=bool(dry_run))
            notice = f"Provisioning {result.outcome}: {result.summary}"
        except (TemplateNotVerified, PublishVerificationFailed) as exc:
            # The two traps. Full text, in red, and no form link is offered —
            # a form that is not verified as ready must never look ready.
            error = str(exc)
        except (CufaError, GoogleNotConnected) as exc:
            error = str(exc)
        except Exception as exc:  # an unexpected Google failure is still the user's problem
            log.warning("provisioning failed session=%s error=%s", parsed, type(exc).__name__)
            error = f"Provisioning failed: {exc}"
        context = _detail_context(conn, parsed)

    if context is None:
        return _not_found(request)
    return render(
        request,
        "session_detail.html",
        status_code=200,
        title=context["session"]["title"],
        notice=notice,
        error=error,
        **context,
    )


@app.post("/sessions/{session_id}/announce")
def session_announce(
    request: Request, session_id: str, user: ConsoleUser = Depends(require_user)
) -> Response:
    parsed = _parse_session_id(session_id)
    if parsed is None:
        return _not_found(request)
    with connection() as conn:
        if get_session(conn, parsed) is None:
            return _not_found(request)
        stamped = announce_now(conn, parsed)
    return RedirectResponse(
        f"/sessions/{parsed}?notice=" + quote(f"Announced at {stamped:%Y-%m-%d %H:%M:%S} UTC."),
        status_code=303,
    )


@app.post("/sessions/{session_id}/pull")
def session_pull(
    request: Request, session_id: str, user: ConsoleUser = Depends(require_user)
) -> Response:
    parsed = _parse_session_id(session_id)
    if parsed is None:
        return _not_found(request)

    notice: str | None = None
    error: str | None = None
    warnings: list[str] = []
    with connection() as conn:
        try:
            client = get_client(conn)
            result = pull_session(conn, client, parsed)
            notice = (
                f"Pulled: {result.rows_read} read, {result.rows_written} written, "
                f"{result.rows_skipped} already recorded."
            )
            warnings = list(result.warnings)
        except LookupError as exc:
            error = str(exc)
        except (CufaError, GoogleNotConnected) as exc:
            error = str(exc)
        except Exception as exc:
            log.warning("pull failed session=%s error=%s", parsed, type(exc).__name__)
            error = f"Pulling responses failed: {exc}"
        context = _detail_context(conn, parsed)

    if context is None:
        return _not_found(request)
    return render(
        request,
        "session_detail.html",
        title=context["session"]["title"],
        notice=notice,
        error=error,
        ingest_warnings=warnings,
        **context,
    )


@app.get("/sessions/{session_id}/responses.json")
def session_responses_json(
    request: Request, session_id: str, user: ConsoleUser = Depends(require_user)
) -> Response:
    """The polled endpoint behind the live count. Database only, no Google call."""
    parsed = _parse_session_id(session_id)
    if parsed is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    with connection() as conn:
        row = fetch_one(
            conn,
            """
            select count(*) as responses, max(submitted_at_utc) as latest
              from checkin
             where session_id = %s
            """,
            (parsed,),
        )
        session_row = get_session(conn, parsed)
    if session_row is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    latest = (row or {}).get("latest")
    return JSONResponse(
        {
            "session_id": parsed,
            "responses": int((row or {}).get("responses") or 0),
            "latest_submission_utc": latest.isoformat() if latest else None,
            "announced_at_utc": (
                session_row["announced_at_utc"].isoformat()
                if session_row["announced_at_utc"]
                else None
            ),
            "form_ready": session_row["publish_verified_at"] is not None,
        }
    )


@app.post("/sessions/{session_id}/pull.json")
def session_pull_json(
    request: Request, session_id: str, user: ConsoleUser = Depends(require_user)
) -> Response:
    """The same pull as the button, for the optional auto-pull loop."""
    parsed = _parse_session_id(session_id)
    if parsed is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    with connection() as conn:
        try:
            client = get_client(conn)
            result = pull_session(conn, client, parsed)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(
        {
            "rows_read": result.rows_read,
            "rows_written": result.rows_written,
            "rows_skipped": result.rows_skipped,
            "warnings": list(result.warnings),
        }
    )


@app.get("/api/passphrase/suggest")
def passphrase_suggest(
    request: Request, user: ConsoleUser = Depends(require_user)
) -> Response:
    """One word from the curated list — no homophones, no near-homophones."""
    return JSONResponse({"passphrase": suggest(1)[0], "guidance": GUIDANCE})


# --------------------------------------------------------------------------
# screen 5 — review
# --------------------------------------------------------------------------

_REVIEW_TABS = ("needs_review", "ai", "identities")


@app.get("/review", response_class=HTMLResponse)
def review_screen(
    request: Request,
    user: ConsoleUser = Depends(require_user),
    tab: str = Query(default="needs_review"),
    cohort: str | None = Query(default=None),
    notice: str | None = None,
) -> Response:
    if tab not in _REVIEW_TABS:
        tab = "needs_review"

    with connection() as conn:
        cohorts = _cohorts(conn)
        rows: list[dict[str, Any]]
        if tab == "needs_review":
            rows = needs_review_queue(conn, cohort or None)
        elif tab == "ai":
            rows = ai_decisions(conn, cohort or None)
        else:
            rows = unresolved_identities(conn, cohort or None)

        # The expected word is what makes a needs_review row judgeable; it lives
        # on the session, not on the observation.
        expected = {
            str(row["session_id"]): row["passphrase"]
            for row in list_sessions(conn, cohort or None)
        }

    return render(
        request,
        "review.html",
        title="Review",
        tab=tab,
        rows=rows,
        expected=expected,
        cohorts=cohorts,
        selected_cohort=cohort or "",
        notice=notice,
    )


@app.post("/review/{checkin_id}/decide")
def review_decide(
    request: Request,
    checkin_id: str,
    user: ConsoleUser = Depends(require_user),
    status: str = Form(...),
    note: str = Form(""),
    tab: str = Form("needs_review"),
    cohort: str = Form(""),
) -> Response:
    """Tier 3. Supersedes whatever the rules or the model decided."""
    parsed = _parse_session_id(checkin_id)
    if parsed is None:
        return _not_found(request)
    if status not in {"attended", "not_attended"}:
        return _not_found(request)

    with connection() as conn:
        human_override(
            conn, parsed, status=status, by_email=user.email, note=note.strip() or None
        )

    destination = f"/review?tab={quote(tab)}"
    if cohort:
        destination += f"&cohort={quote(cohort)}"
    destination += "&notice=" + quote(f"Recorded “{status}” for one check-in.")
    return RedirectResponse(destination, status_code=303)


# --------------------------------------------------------------------------
# misc
# --------------------------------------------------------------------------


def _not_found(request: Request) -> Response:
    return render(
        request,
        "message.html",
        status_code=404,
        title="Not found",
        heading="Not found",
        body="That page or record does not exist. It may have been removed.",
        link="/sessions",
        link_label="Back to sessions",
    )


@app.get("/healthz")
def healthz() -> Response:
    """Liveness only. Says nothing about the database on purpose — the screens do."""
    return JSONResponse({"status": "ok"})


__all__ = ["app"]
