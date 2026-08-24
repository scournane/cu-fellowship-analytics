"""The session console.

Audience: two or three CU staff who are not engineers, one of whom will be
running a lesson while using the session detail screen. The design follows from
that.

One screen is different in kind from the rest. **Help requests** lists young
people who asked to be checked in with; it has its own access list, separate
from the console allowlist, and it is styled to read as not-routine-data. Every
other screen here is about lessons and forms.

* **React screens over plain form posts.** Every screen renders client-side
  from Astryx components (StyleX, the neutral theme), but every *action* is
  still an ordinary form POST to the same endpoint, and the server still
  answers with a 303. Nothing was converted to a JSON API — which is why the
  Google OAuth round trip and the session cookie work exactly as before.
* **This no longer runs without JavaScript.** The previous version degraded to
  working HTML; this one does not. Each screen is handed its data as JSON in
  the document (see ``render_spa``) and drawn by the bundle. The command line
  is now the only no-JavaScript door.
* **A build step, and no CDN.** ``cd frontend && npm install && npm run build``
  is required before ``cufa serve`` will render anything; the bundle is built
  ahead of time into ``static/app`` and served from there, so no network fetch
  happens at page load. This is a trade the repo did not originally make, and
  the cost is real: a toolchain is a thing that breaks in November when nobody
  here still remembers it existed.
* **Failure is shown, never summarised.** When provisioning or verification
  fails, the exception text goes on the screen in full. These are the Google
  traps, and every one of them fails in a way that looks like success — so a
  friendly "something went wrong" would be actively harmful.

The console is a convenience layer over the same functions ``cufa`` exposes on
the command line. It never reimplements a rule.
"""

from __future__ import annotations

import os
import secrets
import uuid
from contextlib import contextmanager
from functools import lru_cache
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from ..config import Settings, get_settings
from ..confidence import INTERPRETATION, STRAIGHTLINE_NOTE
from ..confidence import for_session as confidence_for_session
from ..confidence import straightliners
from ..db import connection, fetch_all, fetch_one
from ..decisions import human_override
from ..errors import ConfigError, CufaError, DatabaseUnreachable
from ..form_content_b import HELP_OPTION, SURVEY_LENGTH_RATIONALE
from ..google.base import SCOPES
from ..google.factory import get_client
from ..google.oauth import authorization_url, credential_status, disconnect, store_credential
from ..help_requests import acknowledge as acknowledge_help
from ..help_requests import list_requests as list_help_requests
from ..help_requests import open_count as open_help_count
from ..help_routing import get_help_routing
from ..ingest.forms_api import pull_session
from ..ingest.forms_b import pull_session_b
from ..logging_setup import configure_logging, get_logger
from ..passphrase import ACCESSIBILITY_REMINDER, GUIDANCE, check_reuse, suggest
from ..provenance import is_simulated_form_id
from ..provisioning import is_ready, provision_session, resolve_rotating_slot
from ..question_map import map_rows
from ..report import ai_decisions, needs_review_queue, unresolved_identities
from ..rotation import RotationConfigError, TeacherQuestionMissing, get_rotation
from ..shoutouts import candidates_for, link as link_shoutout, review_queue as shoutout_queue
from ..themes import current_themes, generate_themes
from ..sessions import (
    SessionInput,
    announce_now,
    create_session,
    get_session,
    list_sessions,
    update_session,
)
from ..template import (
    MANUAL_STEP,
    connected_account,
    PART_LABELS,
    PARTS,
    all_templates,
    create_template,
    get_template,
    replace_template,
    verify_template,
)
from ..timeutil import TimezoneError, get_zone
from .auth import (
    COOKIE_NAME,
    NotPermitted,
    PKCE_COOKIE_NAME,
    SESSION_MAX_AGE,
    STATE_MAX_AGE,
    ConsoleUser,
    NotSignedIn,
    dev_signin_available,
    is_allowed,
    issue_session,
    read_code_verifier,
    read_session,
    read_state,
    sign_code_verifier,
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


def help_access_list(settings: Settings | None = None) -> tuple[str, ...]:
    """Who may read help requests.

    ``CUFA_HELP_ALLOWLIST`` when it is set. Otherwise the recipients named in
    ``config/help_routing.json`` — the people already being emailed these
    requests are the obvious people allowed to read them, and invariant 2
    guarantees that list is non-empty whenever the checkbox exists on a form at
    all. Falling back to the general console allowlist would be the wrong
    default: it would make a safeguarding record as widely readable as a
    timestamp, which is precisely the distinction this screen exists to draw.
    """
    settings = settings or get_settings()
    if settings.help_allowlist:
        return settings.help_allowlist
    return tuple(
        sorted(r.email.strip().lower() for r in get_help_routing().recipients)
    )


def may_read_help(user: ConsoleUser, settings: Settings | None = None) -> bool:
    return (user.email or "").strip().lower() in help_access_list(settings)


def require_help_access(request: Request) -> ConsoleUser:
    """Guard the help-requests screen specifically. Stricter than the rest."""
    user = require_user(request)
    if not may_read_help(user):
        log.warning("help requests screen refused: address not permitted")
        permitted = help_access_list()
        raise NotPermitted(
            "This screen lists fellows who asked to be checked in with, and is "
            "restricted to the people named for that. It is deliberately not the "
            "general console allowlist: a record that a young person asked for "
            "contact is not routine operational data.\n"
            "\n"
            "Right now it is open to: "
            + (", ".join(permitted) or "nobody — no recipient is configured")
            + ".\n"
            "\n"
            "To add yourself, set CUFA_HELP_ALLOWLIST in .env to the addresses "
            "that should have it — for example:\n"
            "\n"
            f"    CUFA_HELP_ALLOWLIST={user.email}\n"
            "\n"
            "then restart the console. With that unset, access falls back to the "
            "recipients named in config/help_routing.json, which is why the list "
            "above may be one person."
        )
    return user


APP_DIR = STATIC_DIR / "app"


@lru_cache(maxsize=1)
def _spa_assets() -> tuple[list[str], list[str]]:
    """Find the built bundle. Read once per process, not once per render.

    The filenames are read off disk rather than hardcoded: the StyleX plugin
    decides the stylesheet's name at build time and has been known to suffix
    it, so pinning a literal here would break on a rebuild rather than at the
    point where someone could see why.
    """
    if not APP_DIR.is_dir():
        return [], []
    css = sorted(f"/static/app/{p.name}" for p in APP_DIR.glob("*.css"))
    js = sorted(f"/static/app/{p.name}" for p in APP_DIR.glob("*.js"))
    return css, js


def render_spa(
    request: Request, screen: str, *, status_code: int = 200, title: str, **state: Any
) -> HTMLResponse:
    """Render a React screen shell, handing it its data as JSON."""
    css, js = _spa_assets()
    if not js:
        raise ConfigError(
            "The console front-end has not been built. "
            "Run: cd frontend && npm install && npm run build"
        )
    settings = get_settings()
    user = getattr(request.state, "user", None)
    payload: dict[str, Any] = {
        "screen": screen,
        "title": title,
        "path": request.url.path,
        "fakeGoogle": settings.fake_google,
        "devSignin": dev_signin_available(settings),
        "noAllowlist": not settings.console_allowlist,
        "allowlist": sorted(settings.console_allowlist),
        "user": (
            {
                "email": user.email,
                "isDevBypass": user.is_dev_bypass,
                # Drives whether the nav shows the Help requests link at all. The
                # server still enforces the gate on every request — this only
                # stops the console offering a door that would answer 403.
                "mayReadHelp": may_read_help(user, settings),
            }
            if user
            else None
        ),
        **state,
    }
    # jsonable_encoder is what turns datetimes, UUIDs and the record objects into
    # something json.dumps will take. Markup is a str subclass, so the QR SVG
    # comes through as a string and the screen renders it as markup.
    return templates.TemplateResponse(
        request,
        "app_shell.html",
        {"title": title, "state": jsonable_encoder(payload), "css": css, "js": js},
        status_code=status_code,
    )


@app.exception_handler(NotSignedIn)
def _not_signed_in(request: Request, exc: NotSignedIn) -> Response:
    """Send a browser to the sign-in page rather than showing it a 401 body."""
    target = quote(str(request.url.path), safe="/")
    return RedirectResponse(f"/signin?next={target}", status_code=303)


@app.exception_handler(NotPermitted)
def _not_permitted(request: Request, exc: NotPermitted) -> Response:
    """403 with the reason, not a redirect.

    Deliberately not bounced to sign-in: the person IS signed in, and sending
    them round the loop would read as a broken link rather than as a boundary.
    """
    return render_spa(
        request,
        "message",
        status_code=403,
        title="Not permitted",
        heading="You do not have access to this screen",
        body=str(exc),
        link="/sessions",
        link_label="Back to sessions",
    )


@app.exception_handler(DatabaseUnreachable)
def _database_down(request: Request, exc: DatabaseUnreachable) -> Response:
    """Every screen degrades to the same page: what broke and how to fix it."""
    if request.url.path.endswith(".json"):
        return JSONResponse({"error": "database_unreachable", "hint": str(exc)}, status_code=503)
    return render_spa(request, "dbDown",
        title="The database is not answering", status_code=503, hint=str(exc))


def _parse_uuid(raw: str) -> str | None:
    """Reject anything that is not a UUID before it reaches Postgres.

    Every id in a console URL is a uuid column, so a malformed one is a 404
    rather than a psycopg DataError surfacing as a 500.
    """
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
    return render_spa(
        request,
        "signin",
        title="Sign in",
        nextPath=next or "/",
        googleReady=google_ready,
        error=error,
    )


@app.post("/signin/dev")
def signin_dev(
    request: Request, email: str = Form(...), next: str = Form("/")
) -> Response:
    """The no-Google door. Allowlisted addresses only, and labelled as a bypass."""
    settings = get_settings()
    if not dev_signin_available(settings):
        return render_spa(
            request,
            "signin",
            status_code=403,
            title="Sign in",
            nextPath=next,
            googleReady=bool(settings.google_client_id),
            error=(
                "Developer sign-in is switched off. It is available only when "
                "CUFA_FAKE_GOOGLE=1 or when no allowlist is configured. Sign in "
                "with Google instead."
            ),
        )
    if not is_allowed(settings, email):
        log.warning("console sign-in refused: address not on the allowlist")
        return render_spa(
            request,
            "signin",
            status_code=403,
            title="Sign in",
            nextPath=next,
            googleReady=bool(settings.google_client_id),
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
        # No include_granted_scopes here. That flag is incremental authorisation:
        # it asks Google to fold every scope this client has ever been granted
        # into the new token. Sign-in wants identity and nothing else, so asking
        # for the union is the opposite of the intent documented on _signin_flow.
        url, _ = flow.authorization_url(prompt="select_account")
    except CufaError as exc:
        return render_spa(
            request,
            "signin",
            status_code=400,
            title="Sign in",
            nextPath=next,
            googleReady=False,
            error=str(exc),
        )
    response = RedirectResponse(url, status_code=303)
    _set_pkce_cookie(response, settings, flow.code_verifier)
    return response


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


def _set_pkce_cookie(response: Response, settings: Settings, code_verifier: str) -> None:
    response.set_cookie(
        PKCE_COOKIE_NAME,
        sign_code_verifier(settings, code_verifier),
        max_age=STATE_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
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


def _connect_status(status: Any) -> dict[str, Any]:
    """Flatten CredentialStatus for the browser.

    ``has_required_scopes`` is a computed property, and ``jsonable_encoder``
    serialises dataclass *fields* only — a property silently vanishes on the way
    out, arriving as ``undefined`` and reading as false. The template used to
    evaluate it on the live object, so this only became possible once the screen
    moved to the client. Anything computed has to be named here.
    """
    return {
        "connected": status.connected,
        "account_email": status.account_email,
        "scopes": list(status.scopes),
        "connected_at": status.connected_at,
        "last_refreshed_at": status.last_refreshed_at,
        "has_required_scopes": status.has_required_scopes,
    }


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
    return render_spa(
        request,
        "connect",
        title="Connect Google",
        status=_connect_status(status),
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
        url, _, code_verifier = authorization_url(settings, state=state)
    except CufaError as exc:
        return _connect_error(request, str(exc))
    response = RedirectResponse(url, status_code=303)
    _set_pkce_cookie(response, settings, code_verifier)
    return response


def _connect_error(request: Request, message: str) -> Response:
    with connection() as conn:
        status = credential_status(conn)
    settings = get_settings()
    return render_spa(
        request,
        "connect",
        status_code=400,
        title="Connect Google",
        status=_connect_status(status),
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
        # A state this console did not sign, but Google still sent a code. That
        # is `cufa google connect`: the CLI runs the whole round trip in one
        # process, so it uses a plain random state and expects the human to copy
        # the code out of the address bar. It only lands here because the
        # redirect URI it was registered with is this console's, and the console
        # happens to be running. Handing the code over is the whole job — it is
        # already in the address bar of the person reading this, so showing it
        # discloses nothing, and refusing to would strand a working grant.
        if code:
            return render_spa(
                request,
                "message",
                title="Your authorization code",
                heading="This code belongs to your terminal",
                body=(
                    "You started this from `cufa google connect`, which finishes the "
                    "exchange itself. Copy the code below and paste it at the "
                    "“Paste the value of code here:” prompt. It is single-use "
                    "and expires within a few minutes. Nothing was connected by this "
                    "page — the console did not use the code."
                ),
                code=code,
                link="/",
                link_label="Connect from the console instead",
            )
        return render_spa(
            request,
            "message",
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


@contextmanager
def _relaxed_token_scope() -> Any:
    """Let the sign-in token come back carrying more scope than it asked for.

    Once someone has connected the Forms account, Google holds a standing grant
    for this client covering forms.body and drive.file, and it can return those
    on the identity request too. oauthlib treats a widened scope as an error,
    which would lock out exactly the people who finished setting the console up.

    Relaxing the check is safe here and nowhere else: this credential is used
    for one userinfo call and then dropped, it is never stored, and the address
    it reports is still checked against the allowlist. ``_finish_connect`` keeps
    the strict check, because that credential is persisted.
    """
    key = "OAUTHLIB_RELAX_TOKEN_SCOPE"
    previous = os.environ.get(key)
    os.environ[key] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def _finish_signin(
    request: Request, settings: Settings, *, code: str, state: str | None, payload: dict[str, Any]
) -> Response:
    code_verifier = read_code_verifier(settings, request.cookies.get(PKCE_COOKIE_NAME))
    if not code_verifier:
        return RedirectResponse(
            "/signin?error="
            + quote("Your sign-in session expired or cookies are blocked. Enable cookies and try again."),
            status_code=303,
        )

    try:
        flow = _signin_flow(settings, state=state)
        flow.code_verifier = code_verifier
        with _relaxed_token_scope():
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

    code_verifier = read_code_verifier(settings, request.cookies.get(PKCE_COOKIE_NAME))
    if not code_verifier:
        return _connect_error(
            request, "Your connect session expired or cookies are blocked. Enable cookies and try again."
        )

    try:
        flow = build_flow(settings, state=state)
        flow.code_verifier = code_verifier
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
    except CufaError as exc:
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
    """Both parts, always — including one that does not exist yet.

    Each part has its own template and its own one-time human Verified step,
    because email collection is a property of a form and is carried only by a
    Drive copy. Showing them side by side is the point: "Part B has no template"
    is the state a person most needs to see here, and a screen that rendered only
    what exists would hide exactly that.
    """
    records = {record.part: record for record in all_templates(conn)}
    parts = []
    for part in PARTS:
        record = records.get(part)
        parts.append(
            {
                "part": part,
                "label": PART_LABELS[part],
                "record": record,
                "blocked": record is None or not record.is_verified,
                # Detected without an API call, and without building a client:
                # `get_client` constructs the real Google client, which fetches
                # discovery documents over the network — not something to do on
                # every render of a screen that is only reading the database.
                # Whether the client would be the fake is a setting, and that is
                # all this comparison needs.
                "unreachable": bool(
                    record
                    and is_simulated_form_id(record.form_id) != get_settings().fake_google
                ),
            }
        )
    return {
        "parts": parts,
        "manual_step": MANUAL_STEP,
        "blocked": any(entry["blocked"] for entry in parts),
        "survey_rationale": SURVEY_LENGTH_RATIONALE,
        "connected_account": connected_account(conn),
    }


def _valid_part(raw: str) -> str:
    return raw if raw in PARTS else "a"


@app.get("/template", response_class=HTMLResponse)
def template_screen(request: Request, user: ConsoleUser = Depends(require_user)) -> Response:
    with connection() as conn:
        context = _template_context(conn)
    return render_spa(request, "template", title="Template setup", **context)


@app.post("/template/create")
def template_create(
    request: Request,
    user: ConsoleUser = Depends(require_user),
    part: str = Form("a"),
) -> Response:
    part = _valid_part(part)
    notice: str | None = None
    error: str | None = None
    with connection() as conn:
        try:
            create_template(conn, get_client(conn), part)
            notice = (
                f"Template form created for {PART_LABELS[part]}. Now do the one "
                "manual step below, then press Verify template."
            )
        except CufaError as exc:
            error = str(exc)

    # A second connection deliberately: the first one's work is committed, and a
    # failure that left its transaction aborted must not take the page with it.
    with connection() as conn:
        context = _template_context(conn)
    return render_spa(
        request, "template", title="Template setup", notice=notice, error=error, **context
    )


@app.post("/template/replace")
def template_replace(
    request: Request,
    user: ConsoleUser = Depends(require_user),
    part: str = Form("a"),
) -> Response:
    """Retire a template that cannot be opened and create a fresh one.

    Offered only after verification has actually failed to reach the form —
    making a new template silently would drop the human Verified step on the
    floor while the screen still looked green.
    """
    part = _valid_part(part)
    notice: str | None = None
    error: str | None = None
    with connection() as conn:
        try:
            record = replace_template(conn, get_client(conn), part)
            notice = (
                f"A new {PART_LABELS[part]} template was created ({record.form_id}) "
                "and the old one retired. It is a new form, so the one manual step "
                "below has to be done again on it before provisioning is unblocked."
            )
        except CufaError as exc:
            error = str(exc)
        except Exception as exc:
            log.warning("template replace failed part=%s error=%s", part, type(exc).__name__)
            error = f"Creating a replacement template failed: {exc}"

    with connection() as conn:
        context = _template_context(conn)
    return render_spa(
        request, "template", title="Template setup", notice=notice, error=error, **context
    )


@app.post("/template/verify")
def template_verify(
    request: Request,
    user: ConsoleUser = Depends(require_user),
    part: str = Form("a"),
) -> Response:
    """Green only when the API itself says VERIFIED. The human's word is not evidence."""
    part = _valid_part(part)
    notice: str | None = None
    error: str | None = None
    with connection() as conn:
        try:
            state = verify_template(conn, get_client(conn), part)
            notice = (
                f"{PART_LABELS[part]} verified: the API reports "
                f"emailCollectionType={state.email_collection_type}. "
                "Provisioning is unblocked for this part."
            )
        except CufaError as exc:
            # verify_template clears a stale confirmation before it raises, and
            # that clearing has to survive — hence catching inside the block that
            # commits.
            error = str(exc)

    with connection() as conn:
        context = _template_context(conn)
    return render_spa(
        request, "template", title="Template setup", notice=notice, error=error, **context
    )


# --------------------------------------------------------------------------
# rotation preview
# --------------------------------------------------------------------------


@app.get("/rotation", response_class=HTMLResponse)
def rotation_screen(
    request: Request,
    user: ConsoleUser = Depends(require_user),
    cohort: str | None = Query(default=None),
    weeks: int = Query(default=12),
) -> Response:
    """What each upcoming week will ask, so the teacher can prepare.

    Exists because provisioning REFUSES a teacher-question week with no question
    written, and discovering that at 6:55pm on the night is not a plan.
    """
    error: str | None = None
    preview: list[dict[str, Any]] = []
    schedule: dict[str, Any] = {}
    with connection() as conn:
        cohorts = _cohorts(conn)
        supplied: dict[int, str] = {}
        rows = fetch_all(
            conn,
            'select week_index, title, teacher_question, session_id '
            'from "session" '
            "where (%s::text is null or cohort_id = %s::text) "
            "  and week_index is not null "
            "order by week_index",
            (cohort or None, cohort or None),
        )
        for row in rows:
            supplied[int(row["week_index"])] = row["teacher_question"] or ""
        sessions_by_week = {int(r["week_index"]): r for r in rows}

    try:
        rotation = get_rotation()
        schedule = rotation.to_dict()
        preview = rotation.preview(
            1, max(1, min(weeks, 52)), teacher_questions=supplied
        )
        for row in preview:
            session = sessions_by_week.get(row["week_index"])
            row["session_id"] = str(session["session_id"]) if session else None
            row["session_title"] = session["title"] if session else None
    except CufaError as exc:
        error = str(exc)

    return render_spa(
        request,
        "rotation",
        title="Rotation",
        schedule=schedule,
        preview=preview,
        cohorts=cohorts,
        selected_cohort=cohort or "",
        survey_rationale=SURVEY_LENGTH_RATIONALE,
        error=error,
    )


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
    return render_spa(
        request,
        "sessions",
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
        "week_index": "",
        "teacher_question": "",
    }


def _rotation_hint(week_raw: str, teacher_question: str) -> dict[str, Any]:
    """What the rotating slot will ask for the week that has been typed in.

    Drives the session form's conditional teacher-question field: it appears
    only on the weeks the schedule assigns to the teacher, with a warning when
    it is required and empty. Never raises — this is a form hint, and a form
    that refuses to render because a config file is malformed helps nobody.
    """
    hint: dict[str, Any] = {
        "week_index": None,
        "kind": None,
        "text": None,
        "needs_teacher_question": False,
        "wrapped": False,
        "error": None,
    }
    raw = (week_raw or "").strip()
    if not raw:
        return hint
    try:
        week = int(raw)
    except ValueError:
        hint["error"] = "Week must be a whole number, for example 3."
        return hint
    try:
        rotation = get_rotation()
        rows = rotation.preview(
            week, 1, teacher_questions={week: teacher_question or ""}
        )
    except CufaError as exc:
        hint["error"] = str(exc)
        return hint
    return {**hint, **(rows[0] if rows else {})}


@app.get("/sessions/new", response_class=HTMLResponse)
def session_new_form(request: Request, user: ConsoleUser = Depends(require_user)) -> Response:
    with connection() as conn:
        cohorts = _cohorts(conn)
    values = _blank_form(cohorts)
    return render_spa(
        request,
        "sessionForm",
        title="New session",
        heading="New session",
        action="/sessions/new",
        values=values,
        cohorts=cohorts,
        guidance=GUIDANCE,
        errors=[],
        reuse_warnings=[],
        rotation=_rotation_hint(values["week_index"], values["teacher_question"]),
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
    week_index: str = "",
    teacher_question: str = "",
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
        "week_index": week_index,
        "teacher_question": teacher_question,
    }
    errors: list[str] = []

    week: int | None = None
    week_raw = (week_index or "").strip()
    if week_raw:
        try:
            week = int(week_raw)
        except ValueError:
            errors.append("Week must be a whole number, for example 3.")
        else:
            if week < 1:
                errors.append("Week numbering starts at 1.")
                week = None

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
            week_index=week,
            # Saved even on a week that does not currently need it. Rotation
            # schedules get edited, and a question typed once should not be lost
            # because the week it belonged to moved.
            teacher_question=teacher_question.strip() or None,
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
    week_index: str = Form(""),
    teacher_question: str = Form(""),
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
        week_index=week_index,
        teacher_question=teacher_question,
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

    return render_spa(
        request,
        "sessionForm",
        status_code=400 if errors else 200,
        title="New session",
        heading="New session",
        action="/sessions/new",
        values=values,
        cohorts=cohorts,
        guidance=GUIDANCE,
        errors=errors,
        reuse_warnings=warnings,
        rotation=_rotation_hint(week_index, teacher_question),
    )


@app.get("/sessions/{session_id}/edit", response_class=HTMLResponse)
def session_edit_form(
    request: Request, session_id: str, user: ConsoleUser = Depends(require_user)
) -> Response:
    parsed = _parse_uuid(session_id)
    if parsed is None:
        return _not_found(request)
    with connection() as conn:
        cohorts = _cohorts(conn)
        row = get_session(conn, parsed)
    if row is None:
        return _not_found(request)

    local = row["scheduled_at_local"]
    return render_spa(
        request,
        "sessionForm",
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
            "week_index": "" if row["week_index"] is None else str(row["week_index"]),
            "teacher_question": row["teacher_question"] or "",
        },
        cohorts=cohorts,
        guidance=GUIDANCE,
        errors=[],
        reuse_warnings=[],
        session_id=parsed,
        rotation=_rotation_hint(
            "" if row["week_index"] is None else str(row["week_index"]),
            row["teacher_question"] or "",
        ),
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
    week_index: str = Form(""),
    teacher_question: str = Form(""),
    confirm_reuse: str = Form(""),
) -> Response:
    parsed = _parse_uuid(session_id)
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
        week_index=week_index,
        teacher_question=teacher_question,
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

    return render_spa(
        request,
        "sessionForm",
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
        rotation=_rotation_hint(week_index, teacher_question),
    )


# --------------------------------------------------------------------------
# screen 4 — session detail (the mid-lesson view)
# --------------------------------------------------------------------------


def _qr_for(url: str | None, ready: bool, title: str) -> tuple[Markup | None, str | None]:
    """A QR code, but only for a form the API has confirmed is accepting.

    No link and no code is shown until then. A QR pointing at an unpublished form
    scans perfectly and collects nothing, which is trap 1 wearing a disguise.
    """
    if not (ready and url):
        return None, None
    try:
        return Markup(qr_svg(url, title=f"QR code for {title}")), None
    except QrTooLong as exc:
        return None, str(exc)


def _part_b_context(conn: Any, row: dict[str, Any]) -> dict[str, Any]:
    """Everything the Part B card on the session detail screen needs."""
    session_id = str(row["session_id"])
    routing = get_help_routing()

    rotation_error: str | None = None
    slot: dict[str, Any] | None = None
    try:
        resolved = resolve_rotating_slot(row)
        slot = {
            "kind": resolved.kind,
            "text": resolved.text,
            "week_index": resolved.week_index,
            "wrapped": resolved.wrapped,
        }
    except (TeacherQuestionMissing, RotationConfigError) as exc:
        # Shown in full rather than summarised. This is the message that says
        # "provisioning will refuse and here is the one field to fill in", and a
        # friendly abbreviation of it would cost someone the evening.
        rotation_error = str(exc)

    ready = is_ready(conn, session_id, "b")
    qr, qr_error = _qr_for(row.get("b_form_url"), ready, row["title"])

    return {
        "b_ready": ready,
        "b_form_url": row.get("b_form_url"),
        "b_qr": qr,
        "b_qr_error": qr_error,
        "b_rotation": slot,
        "b_rotation_error": rotation_error,
        "b_question_map": map_rows(conn, row["b_form_id"]) if row.get("b_form_id") else [],
        "help_routing": routing.to_dict(),
        "help_option": HELP_OPTION,
        "survey_rationale": SURVEY_LENGTH_RATIONALE,
    }


def _detail_context(conn: Any, session_id: str) -> dict[str, Any] | None:
    row = get_session(conn, session_id)
    if row is None:
        return None
    template = get_template(conn, "a")
    template_b = get_template(conn, "b")
    ready = is_ready(conn, session_id, "a")
    form_url = row.get("form_url")

    qr_markup, qr_error = _qr_for(form_url, ready, row["title"])

    return {
        "session": row,
        "template": template,
        "template_blocked": template is None or not template.is_verified,
        "template_b": template_b,
        "template_b_blocked": template_b is None or not template_b.is_verified,
        "ready": ready,
        "form_url": form_url,
        "qr": qr_markup,
        "qr_error": qr_error,
        "accessibility_reminder": ACCESSIBILITY_REMINDER,
        **_part_b_context(conn, row),
        "provisioning_log": fetch_all(
            conn,
            """
            select action, outcome, error, at
              from provisioning_log
             where session_id = %s
             order by at desc
             limit 12
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
    parsed = _parse_uuid(session_id)
    if parsed is None:
        return _not_found(request)
    with connection() as conn:
        context = _detail_context(conn, parsed)
    if context is None:
        return _not_found(request)
    return render_spa(
        request, "sessionDetail", title=context["session"]["title"], notice=notice, **context
    )


@app.post("/sessions/{session_id}/provision")
def session_provision(
    request: Request,
    session_id: str,
    user: ConsoleUser = Depends(require_user),
    part: str = Form("a"),
    dry_run: str = Form(""),
) -> Response:
    parsed = _parse_uuid(session_id)
    if parsed is None:
        return _not_found(request)
    part = _valid_part(part)

    notice: str | None = None
    error: str | None = None
    with connection() as conn:
        try:
            client = get_client(conn)
            result = provision_session(
                conn, client, parsed, part=part, dry_run=bool(dry_run)
            )
            notice = f"Part {part.upper()} provisioning {result.outcome}: {result.summary}"
            if result.rotating_text:
                notice += (
                    f" · this week's rotating question ({result.rotating_kind}): "
                    f"“{result.rotating_text}”"
                )
            if result.help_field_omitted_reason:
                # Surfaced, never swallowed. Someone expecting the checkbox has
                # to be told it was left off and why.
                notice += " · the help checkbox was NOT included on this form."
        except CufaError as exc:
            # TemplateNotVerified and PublishVerificationFailed arrive here.
            # Both get their full text on the screen, in red, and neither is
            # followed by a form link: a form that has not been verified as
            # ready must never be shown as ready.
            error = str(exc)
        except Exception as exc:  # an unexpected Google failure is still the user's problem
            log.warning("provisioning failed session=%s error=%s", parsed, type(exc).__name__)
            error = f"Provisioning failed: {exc}"

    # Fresh connection: the attempt above committed its provisioning_log rows,
    # and if it failed mid-statement its transaction is no use for reading.
    with connection() as conn:
        context = _detail_context(conn, parsed)

    if context is None:
        return _not_found(request)
    return render_spa(
        request,
        "sessionDetail",
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
    parsed = _parse_uuid(session_id)
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
    request: Request,
    session_id: str,
    user: ConsoleUser = Depends(require_user),
    part: str = Form("a"),
) -> Response:
    parsed = _parse_uuid(session_id)
    if parsed is None:
        return _not_found(request)
    part = _valid_part(part)

    notice: str | None = None
    error: str | None = None
    warnings: list[str] = []
    with connection() as conn:
        try:
            client = get_client(conn)
            result = (
                pull_session_b(conn, client, parsed)
                if part == "b"
                else pull_session(conn, client, parsed)
            )
            notice = (
                f"Pulled part {part.upper()}: {result.rows_read} read, "
                f"{result.rows_written} written, "
                f"{result.rows_skipped} already recorded."
            )
            warnings = list(result.warnings)
        except LookupError as exc:
            error = str(exc)
        except CufaError as exc:
            error = str(exc)
        except Exception as exc:
            log.warning("pull failed session=%s error=%s", parsed, type(exc).__name__)
            error = f"Pulling responses failed: {exc}"

    with connection() as conn:
        context = _detail_context(conn, parsed)

    if context is None:
        return _not_found(request)
    return render_spa(
        request,
        "sessionDetail",
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
    parsed = _parse_uuid(session_id)
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
        row_b = fetch_one(
            conn,
            """
            select count(*) as responses, max(submitted_at_utc) as latest
              from checkin_b
             where session_id = %s
            """,
            (parsed,),
        )
        session_row = get_session(conn, parsed)
    if session_row is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    latest = (row or {}).get("latest")
    latest_b = (row_b or {}).get("latest")
    return JSONResponse(
        {
            "session_id": parsed,
            "responses": int((row or {}).get("responses") or 0),
            "latest_submission_utc": latest.isoformat() if latest else None,
            # Reported alongside, never summed. Part A and Part B are independent
            # observations and a combined "responses" number would read as a
            # participation total that means nothing.
            "responses_b": int((row_b or {}).get("responses") or 0),
            "latest_submission_b_utc": latest_b.isoformat() if latest_b else None,
            "announced_at_utc": (
                session_row["announced_at_utc"].isoformat()
                if session_row["announced_at_utc"]
                else None
            ),
            "form_ready": session_row["publish_verified_at"] is not None,
            "form_b_ready": session_row["b_publish_verified_at"] is not None,
        }
    )


@app.post("/sessions/{session_id}/pull.json")
def session_pull_json(
    request: Request, session_id: str, user: ConsoleUser = Depends(require_user)
) -> Response:
    """The same pull as the button, for the optional auto-pull loop."""
    parsed = _parse_uuid(session_id)
    if parsed is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    part = _valid_part(request.query_params.get("part") or "a")
    with connection() as conn:
        try:
            client = get_client(conn)
            result = (
                pull_session_b(conn, client, parsed)
                if part == "b"
                else pull_session(conn, client, parsed)
            )
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

_REVIEW_TABS = ("needs_review", "ai", "identities", "straightlining")


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
        elif tab == "straightlining":
            # A data-quality flag on the responses, shown here and nowhere else.
            # It is not a judgment about the person and it enters no count, rate
            # or score — see docs/decisions.md ADR-027.
            rows = straightliners(conn, cohort or None)
        else:
            rows = unresolved_identities(conn, cohort or None)

        # The expected word is what makes a needs_review row judgeable; it lives
        # on the session, not on the observation.
        expected = {
            str(row["session_id"]): row["passphrase"]
            for row in list_sessions(conn, cohort or None)
        }

    return render_spa(
        request,
        "review",
        title="Review",
        tab=tab,
        rows=rows,
        expected=expected,
        cohorts=cohorts,
        selected_cohort=cohort or "",
        straightline_note=STRAIGHTLINE_NOTE,
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
    parsed = _parse_uuid(checkin_id)
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
# screen 6 — Part B responses for one session
# --------------------------------------------------------------------------


@app.get("/sessions/{session_id}/responses", response_class=HTMLResponse)
def session_responses(
    request: Request,
    session_id: str,
    user: ConsoleUser = Depends(require_user),
    notice: str | None = None,
) -> Response:
    """What the end-of-session form collected: distribution, takeaways, themes.

    Deliberately three separate blocks rather than one table per fellow. A table
    with a name, a score and a sentence on each row reads as a scorecard, which
    is exactly what this data is not — the takeaways are counted, never graded,
    and the confidence number is only meaningful as a trend.
    """
    parsed = _parse_uuid(session_id)
    if parsed is None:
        return _not_found(request)

    with connection() as conn:
        row = get_session(conn, parsed)
        if row is None:
            return _not_found(request)
        distribution = confidence_for_session(conn, parsed)
        responses = fetch_all(
            conn,
            """
            select checkin_b_id, full_name, submitted_email, confidence_raw,
                   takeaway_text, rotating_kind, rotating_text, shoutout_text,
                   submitted_at_utc, has_takeaway
              from v_checkin_b_resolved
             where session_id = %s
             order by submitted_at_utc
            """,
            (parsed,),
        )
        themes = current_themes(conn, parsed)
        question_map = map_rows(conn, row["b_form_id"]) if row.get("b_form_id") else []

    return render_spa(
        request,
        "responses",
        title=f"Responses — {row['title']}",
        session=row,
        distribution=distribution,
        interpretation=INTERPRETATION,
        responses=responses,
        themes=themes,
        question_map=question_map,
        survey_rationale=SURVEY_LENGTH_RATIONALE,
        notice=notice,
    )


@app.post("/sessions/{session_id}/themes")
def session_themes(
    request: Request,
    session_id: str,
    user: ConsoleUser = Depends(require_user),
) -> Response:
    """Regenerate the muddiest-point themes.

    Degrades rather than failing: with no API key the page comes back with a
    message and the answers still readable underneath it.
    """
    parsed = _parse_uuid(session_id)
    if parsed is None:
        return _not_found(request)
    with connection() as conn:
        result = generate_themes(conn, parsed, regenerate=True)
    return RedirectResponse(
        f"/sessions/{parsed}/responses?notice=" + quote(result.message),
        status_code=303,
    )


# --------------------------------------------------------------------------
# screen 7 — shoutout review
# --------------------------------------------------------------------------


@app.get("/shoutouts", response_class=HTMLResponse)
def shoutouts_screen(
    request: Request,
    user: ConsoleUser = Depends(require_user),
    cohort: str | None = Query(default=None),
    notice: str | None = None,
) -> Response:
    """Names that resolved to nobody, or to more than one person.

    Both kinds are legal. An ambiguous name is never auto-linked — attributing
    praise to the wrong person is worse than leaving it unattached, because a
    wrong link is invisible — and a name matching nobody is usually a guest
    speaker or a member of staff.
    """
    with connection() as conn:
        cohorts = _cohorts(conn)
        rows = shoutout_queue(conn, cohort or None)
        candidates = {
            str(row["shoutout_id"]): candidates_for(
                conn, row["raw_text"], row["cohort_id"]
            )
            for row in rows
        }

    return render_spa(
        request,
        "shoutouts",
        title="Shoutout review",
        rows=rows,
        candidates=candidates,
        cohorts=cohorts,
        selected_cohort=cohort or "",
        notice=notice,
    )


@app.post("/shoutouts/{shoutout_id}/link")
def shoutout_link(
    request: Request,
    shoutout_id: str,
    user: ConsoleUser = Depends(require_user),
    fellow_id: str = Form(...),
    cohort: str = Form(""),
) -> Response:
    """One-click linking. Records the resolving human's identity."""
    parsed = _parse_uuid(shoutout_id)
    if parsed is None:
        return _not_found(request)
    with connection() as conn:
        try:
            link_shoutout(conn, parsed, fellow_id.strip(), by_email=user.email)
        except LookupError:
            return _not_found(request)
    destination = "/shoutouts"
    if cohort:
        destination += f"?cohort={quote(cohort)}&"
    else:
        destination += "?"
    destination += "notice=" + quote("Linked. Recorded against your address.")
    return RedirectResponse(destination, status_code=303)


# --------------------------------------------------------------------------
# screen 8 — help requests. Access-gated, and visually distinct.
# --------------------------------------------------------------------------


@app.get("/help-requests", response_class=HTMLResponse)
def help_requests_screen(
    request: Request,
    user: ConsoleUser = Depends(require_help_access),
    status: str = Query(default="open"),
    cohort: str | None = Query(default=None),
    notice: str | None = None,
) -> Response:
    """Fellows who asked to be checked in with.

    Its own screen, its own access list, and marked as not routine data — the
    rest of this console is about lessons and forms, and this is not.
    """
    wanted = status if status in ("open", "acknowledged", "closed", "") else "open"
    with connection() as conn:
        cohorts = _cohorts(conn)
        rows = list_help_requests(
            conn, status=wanted or None, cohort_id=cohort or None
        )
        open_now = open_help_count(conn)

    routing = get_help_routing()
    settings = get_settings()
    return render_spa(
        request,
        "helpRequests",
        title="Help requests",
        rows=rows,
        status=wanted,
        cohorts=cohorts,
        selected_cohort=cohort or "",
        open_count=open_now,
        routing=routing.to_dict(),
        access_list=list(help_access_list()),
        # Whether the list came from an explicit allowlist or fell back to the
        # routing recipients. The screen says which, because "why can I not open
        # this?" has two different answers.
        access_from_allowlist=bool(settings.help_allowlist),
        notice=notice,
    )


@app.post("/help-requests/{help_request_id}/ack")
def help_request_ack(
    request: Request,
    help_request_id: str,
    user: ConsoleUser = Depends(require_help_access),
    action: str = Form("ack"),
    note: str = Form(""),
    status: str = Form("open"),
) -> Response:
    parsed = _parse_uuid(help_request_id)
    if parsed is None:
        return _not_found(request)
    if action not in ("ack", "close"):
        return _not_found(request)

    with connection() as conn:
        try:
            acknowledge_help(
                conn,
                parsed,
                by_email=user.email,
                note=note.strip() or None,
                status="closed" if action == "close" else "acknowledged",
            )
        except LookupError:
            return _not_found(request)

    return RedirectResponse(
        f"/help-requests?status={quote(status)}&notice="
        + quote("Recorded against your address."),
        status_code=303,
    )


# --------------------------------------------------------------------------
# misc
# --------------------------------------------------------------------------


def _not_found(request: Request) -> Response:
    return render_spa(
        request,
        "message",
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
