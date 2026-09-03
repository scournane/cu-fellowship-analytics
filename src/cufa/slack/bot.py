"""The bot: Bolt for the transport, ``EventProcessor`` for everything else.

Bolt is deliberately thin here. It verifies the signature, answers the
url_verification challenge, and hands each event to ``EventProcessor.process``.
That split is what makes the pipeline testable without Slack: the processor is
exercised directly with payloads from ``FakeWorkspace``, and Bolt is exercised
once, over HTTP, against the fake server — with its real signature check on.

Two transports, one processor:

* ``cufa slack serve``   — HTTP. Slack POSTs to ``/slack/events``; needs a
                           public URL and the signing secret. Also serves
                           ``/stats`` and a one-page status view at ``/``.
* ``cufa slack socket``  — Socket Mode. No public URL; needs the app-level
                           token. Slack documents this as the development
                           transport rather than the production one, but for a
                           bot that a nonprofit runs on a laptop it is the one
                           that does not require hosting.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import Counter
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from slack_bolt import App
from slack_bolt.request import BoltRequest
from slack_sdk import WebClient

from ..config import Settings, get_settings
from ..db import connection
from ..errors import ConfigError
from ..ingest.common import IngestResult, finish_load_run, start_load_run
from ..logging_setup import configure_logging, get_logger
from .events import Skipped, parse_event
from .qa import QaService, build_qa_service
from .store import RecordOutcome, WorkspaceInfo, ensure_workspace, resolve_and_record, stats, touch_load_run

log = get_logger(__name__)

#: One listener, matched on event type alone. Bolt's string form is
#: subtype-agnostic too, but a pattern makes the set explicit in one place.
#: ``app_mention`` is the only one that is not a participation event: it is
#: how a teacher asks for a Q&A summary, and it is routed to the Q&A service.
EVENT_PATTERN = re.compile(
    r"^(message|reaction_added|reaction_removed|member_joined_channel|member_left_channel|app_mention)$"
)


def make_web_client(settings: Settings) -> WebClient:
    """A ``slack_sdk.WebClient`` for these settings.

    ``SLACK_API_BASE_URL`` is how the demo points the *real* client at the
    fake server. Nothing else changes: same library, same calls, same
    response objects.
    """
    if not settings.slack_bot_token:
        raise ConfigError(
            "SLACK_BOT_TOKEN is not set. Create the app at https://api.slack.com/apps, "
            "install it to the workspace, and copy the Bot User OAuth Token (xoxb-…) "
            "into .env. See docs/setup/slack-bot.md."
        )
    kwargs: dict[str, Any] = {"token": settings.slack_bot_token}
    if settings.slack_api_base_url:
        base = settings.slack_api_base_url
        if not base.endswith("/"):
            base += "/"
        kwargs["base_url"] = base
    client = WebClient(**kwargs)
    # Real Slack answers 429 with Retry-After when a backfill walks history
    # faster than the method's tier allows. The SDK honours that header if
    # asked to; without this the backfill would abort mid-channel instead.
    from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler

    client.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=5))
    return client


class EventProcessor:
    """Everything that happens to an event after Bolt has verified it.

    One instance per bot process. Opens a ``load_run`` at start so every row
    it writes carries provenance, bumps that run's counters per event, and
    closes it on a clean stop. A run left in ``running`` after a crash is
    itself the record that the process died — which is the failure mode of
    a collector that matters most, and the one nobody notices otherwise.
    """

    def __init__(self, settings: Settings, client: Any, *, qa: QaService | None = None) -> None:
        self.settings = settings
        self.client = client
        self.workspace: WorkspaceInfo | None = None
        self.load_id: str | None = None
        self.started_at: float | None = None
        self.counts: Counter[str] = Counter()
        self._lock = threading.Lock()
        #: The Q&A service, when CUFA_SLACK_QA_CHANNELS names any channel.
        #: Injectable so a test can hand in one with a stub matcher.
        self.qa: QaService | None = qa

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> WorkspaceInfo:
        with connection(self.settings) as conn:
            self.workspace = ensure_workspace(conn, self.client, self.settings.slack_cohort)
            self.load_id = start_load_run(
                conn,
                source="slack_bot",
                origin=self.workspace.team_id,
                cohort_id=self.workspace.cohort_id,
            )
            if self.qa is None:
                self.qa = build_qa_service(self.settings, self.client, self.workspace)
            qa_ids = self.qa.resolve_channels(conn) if self.qa is not None else set()
        self.started_at = time.time()
        log.info(
            "slack bot connected team=%s (%s) cohort=%s store_text=%s qa_channels=%d load=%s",
            self.workspace.team_id, self.workspace.team_name,
            self.workspace.cohort_id, self.settings.slack_store_text, len(qa_ids), self.load_id,
        )
        return self.workspace

    def stop(self) -> None:
        if not self.load_id:
            return
        with connection(self.settings) as conn:
            finish_load_run(
                conn,
                self.load_id,
                IngestResult(
                    rows_read=self.counts["written"] + self.counts["duplicate"] + self.counts["skipped"],
                    rows_written=self.counts["written"],
                    rows_skipped=self.counts["duplicate"] + self.counts["skipped"],
                ),
            )
        log.info("slack bot stopped %s", dict(self.counts))
        self.load_id = None

    # -- the per-event path ---------------------------------------------------

    def process(self, event: dict[str, Any], team_id: str, *, retry_num: int | None = None) -> RecordOutcome:
        if self.workspace and event.get("user") == self.workspace.bot_user_id:
            outcome = RecordOutcome(False, Skipped("the bot's own message", event.get("type")), None, None, event.get("type"))
        else:
            parsed = parse_event(event, team_id, store_text=self.settings.slack_store_text, retry_num=retry_num)
            with connection(self.settings) as conn:
                cohort = self.workspace.cohort_id if self.workspace else self.settings.slack_cohort
                outcome = resolve_and_record(conn, self.client, parsed, cohort_id=cohort, load_id=self.load_id)
                if self.load_id:
                    touch_load_run(
                        conn, self.load_id, read=1,
                        written=1 if outcome.status == "written" else 0,
                        skipped=0 if outcome.status == "written" else 1,
                    )
            # Q&A runs AFTER the participation row is committed and on its own
            # connection: a failure here must never cost the observation. Only a
            # newly written act is considered, so a retried delivery cannot post
            # a second pointer.
            if self.qa is not None and outcome.status == "written":
                self._observe_qa(event, team_id)
        with self._lock:
            self.counts[outcome.status] += 1
            if retry_num:
                self.counts["retries_seen"] += 1
        return outcome

    def _observe_qa(self, event: dict[str, Any], team_id: str) -> None:
        assert self.qa is not None
        label: str | None
        try:
            with connection(self.settings) as conn:
                label = self.qa.observe(conn, event, team_id)
        except Exception as exc:  # noqa: BLE001 - never into the event path
            log.exception("qa observe failed: %s", exc)
            label = "error"
        if label:
            with self._lock:
                self.counts["qa_events"] += 1
                if label.endswith("+pointer"):
                    self.counts["qa_pointers"] += 1
                if label == "error":
                    self.counts["qa_errors"] += 1

    def handle_mention(self, event: dict[str, Any], team_id: str, *, retry_num: int | None = None) -> dict[str, Any]:
        """``@bot …``. A retried delivery is ignored: the first one is being
        handled (a summary can take longer than Slack's 3 s), and answering it
        twice would post the summary twice."""
        if self.qa is None:
            return {"intent": "ignored", "reason": "no Q&A channels configured"}
        if retry_num:
            return {"intent": "ignored", "reason": "retry"}
        try:
            with connection(self.settings) as conn:
                result = self.qa.handle_mention(conn, event)
        except Exception as exc:  # noqa: BLE001
            log.exception("mention handling failed: %s", exc)
            result = {"intent": "error", "error": str(exc)}
        with self._lock:
            self.counts["mentions"] += 1
        return result

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counts = dict(self.counts)
        return {
            "team_id": self.workspace.team_id if self.workspace else None,
            "team_name": self.workspace.team_name if self.workspace else None,
            "cohort_id": self.workspace.cohort_id if self.workspace else None,
            "load_id": self.load_id,
            "uptime_seconds": int(time.time() - self.started_at) if self.started_at else None,
            "store_text": self.settings.slack_store_text,
            "qa_channels": self.qa.channel_ids if self.qa is not None else [],
            "this_process": counts,
        }


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

#: What the manifest in docs/setup/slack-bot.md grants. `doctor` compares the
#: token's actual scopes against this and names what is missing.
REQUIRED_SCOPES = (
    "channels:history", "channels:read", "users:read", "users:read.email", "reactions:read",
)
OPTIONAL_SCOPES = ("groups:history", "groups:read", "chat:write", "app_mentions:read")
#: What the Q&A features need on top: posting a pointer or a summary is chat:write;
#: "@bot summary" needs the mention event, which is app_mentions:read.
QA_SCOPES = ("chat:write",)


def doctor(settings: Settings, *, out: Any = None) -> int:
    """Everything a first real run needs, checked in order, with the fix beside
    each failure. Exit 0 only when the bot would actually record something."""
    import sys

    from slack_sdk.errors import SlackApiError

    from ..db import ping

    out = out or sys.stdout
    failures: list[str] = []

    def line(ok: bool, label: str, detail: str = "", *, fix: str = "") -> None:
        mark = "ok  " if ok else "MISS"
        print(f"  {mark}  {label}" + (f"  — {detail}" if detail else ""), file=out)
        if not ok:
            failures.append(label)
            if fix:
                for row in fix.splitlines():
                    print(f"          {row}", file=out)

    print("Slack bot — preflight", file=out)
    print("=" * 62, file=out)

    # 1. configuration
    line(bool(settings.slack_bot_token), "SLACK_BOT_TOKEN set",
         fix="OAuth & Permissions → Bot User OAuth Token (xoxb-…) → .env")
    line(bool(settings.slack_app_token), "SLACK_APP_TOKEN set (Socket Mode)",
         "optional if you run HTTP mode" if settings.slack_signing_secret else "",
         fix="Basic Information → App-Level Tokens → Generate, scope connections:write → .env")
    line(bool(settings.slack_signing_secret), "SLACK_SIGNING_SECRET set (HTTP mode)",
         "optional if you run Socket Mode" if settings.slack_app_token else "",
         fix="Basic Information → Signing Secret → .env")
    if settings.slack_api_base_url:
        line(True, "SLACK_API_BASE_URL is set", f"talking to {settings.slack_api_base_url}, NOT slack.com")
    line(settings.slack_cohort != "demo" or bool(settings.slack_api_base_url),
         f"CUFA_SLACK_COHORT is {settings.slack_cohort!r}",
         "" if settings.slack_cohort != "demo" else "still the demo cohort",
         fix="set CUFA_SLACK_COHORT to the real cohort id (e.g. cu-2026) in .env")
    line(not settings.slack_store_text, "message text not stored",
         "" if not settings.slack_store_text else "CUFA_SLACK_STORE_TEXT=1 — text WILL be stored")
    if settings.slack_qa_channels:
        line(True, "Q&A channels configured",
             ", ".join("#" + c.lstrip("#") for c in settings.slack_qa_channels)
             + " — their text IS stored (ADR-032); the bot points repeats at earlier answers")
        line(True, "GEMINI_API_KEY",
             "set — paraphrases are matched by the model and summaries are written by it"
             if settings.gemini_api_key else
             "not set — matching is by word overlap only and the summary is the plain digest")

    # 2. database
    line(ping(settings), "database reachable", fix="make db-up   (Docker must be running)")

    if not settings.slack_bot_token:
        print("=" * 62, file=out)
        print(f"{len(failures)} problem(s). Nothing else can be checked without a token.", file=out)
        return 1

    # 3. the token
    client = make_web_client(settings)
    try:
        auth = client.auth_test()
    except SlackApiError as exc:
        error = (getattr(exc, "response", None) or {}).get("error", str(exc))
        hint = {
            "invalid_auth": "the token is wrong, revoked, or from a different app",
            "account_inactive": "the app was uninstalled from the workspace",
            "not_authed": "no token reached Slack — check .env is being read",
        }.get(error, "")
        line(False, "auth.test", f"{error}" + (f" — {hint}" if hint else ""),
             fix="Reinstall the app to the workspace and copy the new xoxb- token.")
        print("=" * 62, file=out)
        return 1
    except Exception as exc:  # noqa: BLE001 - network, DNS, proxy
        line(False, "auth.test", f"could not reach Slack: {exc}",
             fix="Check the network. If SLACK_API_BASE_URL is set, unset it for real Slack.")
        print("=" * 62, file=out)
        return 1
    team = auth.get("team") or auth.get("team_id")
    line(True, "token works", f"workspace {team} ({auth.get('team_id')}), bot user {auth.get('user_id')}")

    # 4. scopes — real Slack sends them in a response header
    headers = getattr(auth, "headers", None) or {}
    granted_raw = ""
    if headers:
        granted_raw = headers.get("x-oauth-scopes") or headers.get("X-OAuth-Scopes") or ""
        if isinstance(granted_raw, list):
            granted_raw = ",".join(granted_raw)
    if granted_raw:
        granted = {s.strip() for s in granted_raw.split(",") if s.strip()}
        missing = [s for s in REQUIRED_SCOPES if s not in granted]
        line(not missing, "required scopes granted",
             ", ".join(sorted(granted & set(REQUIRED_SCOPES))) if not missing else "missing: " + ", ".join(missing),
             fix="OAuth & Permissions → add the scopes → Reinstall to Workspace → copy the NEW token")
        extra = [s for s in OPTIONAL_SCOPES if s in granted]
        if extra:
            line(True, "optional scopes granted", ", ".join(extra))
        if settings.slack_qa_channels:
            qa_missing = [s for s in QA_SCOPES if s not in granted]
            line(not qa_missing, "Q&A scopes granted",
                 ", ".join(QA_SCOPES) if not qa_missing else "missing: " + ", ".join(qa_missing) + " — pointers and summaries cannot be posted",
                 fix="OAuth & Permissions → add chat:write → Reinstall to Workspace → copy the NEW token")
            line(True, "app_mentions:read",
                 "granted — `@bot summary` works in Slack" if "app_mentions:read" in granted
                 else "not granted — `@bot summary` will not work; `cufa slack qa summary` still does")
        for bad in ("im:history", "mpim:history", "im:read", "mpim:read"):
            if bad in granted:
                line(False, f"scope {bad} is granted", "the bot should never read direct messages",
                     fix="Remove it from OAuth & Permissions and reinstall.")
    else:
        line(True, "scopes", "not reported by this endpoint (fake server?) — checked functionally below")

    # 5. channels the bot is actually in
    member: list[str] = []
    visible: list[str] = []
    seen: list[dict[str, Any]] = []
    try:
        cursor: str | None = None
        while True:
            resp = client.conversations_list(types="public_channel,private_channel", cursor=cursor, limit=200)
            for ch in resp.get("channels") or []:
                seen.append(ch)
                visible.append(ch.get("name") or ch["id"])
                if ch.get("is_member"):
                    member.append(ch.get("name") or ch["id"])
            cursor = ((resp.get("response_metadata") or {}).get("next_cursor") or "").strip()
            if not cursor:
                break
        line(bool(member), "bot is a member of at least one channel",
             ", ".join("#" + c for c in member[:8]) + (" …" if len(member) > 8 else "") if member else f"can see {len(visible)} channel(s), member of none",
             fix="In each channel to count:  /invite @<bot name>\nA channel the bot is not in produces NOTHING, silently.")
        not_in = [c for c in visible if c not in member]
        if not_in:
            line(True, f"{len(not_in)} visible channel(s) the bot is not in",
                 ", ".join("#" + c for c in not_in[:8]) + (" …" if len(not_in) > 8 else ""))
        # 5b. each Q&A channel: it has to exist, and the bot has to be in it.
        for wanted in settings.slack_qa_channels:
            key = wanted.lstrip("#").strip().lower()
            found = next((ch for ch in seen if (ch.get("name") or "").lower() == key or ch["id"] == wanted), None)
            if found is None:
                line(False, f"Q&A channel #{key}", "not found in this workspace",
                     fix="Create it, or fix CUFA_SLACK_QA_CHANNELS in .env (names, comma-separated).")
            else:
                line(bool(found.get("is_member")), f"Q&A channel #{key}",
                     "bot is a member" if found.get("is_member") else "bot is NOT a member — nothing from it is recorded",
                     fix=f"In Slack:  /invite @<bot name>  in #{key}")
    except SlackApiError as exc:
        error = (getattr(exc, "response", None) or {}).get("error", str(exc))
        line(False, "conversations.list", error, fix="Grant channels:read (and groups:read) and reinstall.")

    # 6. can it turn a user id into an email? This is what joins to the roster.
    try:
        resp = client.users_list(limit=50)
        members = [m for m in (resp.get("members") or []) if not m.get("is_bot") and not m.get("deleted") and m.get("id") != "USLACKBOT"]
        with_email = [m for m in members if (m.get("profile") or {}).get("email")]
        if members:
            line(bool(with_email), "users carry an email on their profile",
                 f"{len(with_email)} of {len(members)} sampled" if with_email else f"none of {len(members)} — users:read.email missing, or a workspace that hides emails",
                 fix="Grant users:read.email and reinstall. Without it every event is unattributed.")
        else:
            line(True, "users", "no human members yet")
    except SlackApiError as exc:
        error = (getattr(exc, "response", None) or {}).get("error", str(exc))
        line(False, "users.list", error, fix="Grant users:read and users:read.email and reinstall.")

    print("=" * 62, file=out)
    if failures:
        print(f"{len(failures)} problem(s) above. Fix them, then re-run:  cufa slack doctor", file=out)
        return 1
    mode = "cufa slack socket" if settings.slack_app_token else "cufa slack serve"
    print("Ready. Next:", file=out)
    print(f"  {mode:<28} start the bot (Ctrl+C stops it)", file=out)
    print("  cufa slack backfill          read what is already in the channels", file=out)
    print("  cufa slack stats             confirm rows are arriving", file=out)
    return 0


# ---------------------------------------------------------------------------
# Bolt
# ---------------------------------------------------------------------------

def _retry_num(req: BoltRequest) -> int | None:
    raw = (req.headers or {}).get("x-slack-retry-num", [None])[0]
    try:
        return int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


class _DropSpuriousTokenWarning(logging.Filter):
    """Bolt warns "`token` will be unused" whenever it is given a `client` and can
    also see a token — and it reads SLACK_BOT_TOKEN from the environment on its
    own, so the warning fires even when no token is passed. A client is passed
    on purpose: it is the only way to set the Web API base URL, which is how the
    demo points the real WebClient at the fake server. The token *is* used, by
    that client. Dropped by message, not by silencing the logger, so every other
    Bolt warning still reaches the log.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return "`token` will be unused" not in record.getMessage()


def build_bolt_app(settings: Settings, client: WebClient, processor: EventProcessor) -> App:
    """The Bolt app. Signature verification stays ON — it is the only thing
    standing between the events table and anyone who can reach the URL."""
    if not settings.slack_signing_secret:
        raise ConfigError(
            "SLACK_SIGNING_SECRET is not set. It is on the app's Basic Information "
            "page at https://api.slack.com/apps. Without it the bot cannot tell a "
            "delivery from Slack apart from anything else that POSTs to the URL."
        )
    bolt_log = get_logger("cufa.slack.bolt")
    if not any(isinstance(f, _DropSpuriousTokenWarning) for f in bolt_log.filters):
        bolt_log.addFilter(_DropSpuriousTokenWarning())
    app = App(
        signing_secret=settings.slack_signing_secret,
        client=client,
        process_before_response=settings.slack_process_before_response,
        logger=bolt_log,
    )

    @app.event(EVENT_PATTERN)
    def on_event(event: dict[str, Any], context: Any, req: BoltRequest) -> None:
        team_id = getattr(context, "team_id", None) or (req.body or {}).get("team_id") or ""
        if event.get("type") == "app_mention":
            processor.handle_mention(event, team_id, retry_num=_retry_num(req))
            return
        processor.process(event, team_id, retry_num=_retry_num(req))

    return app


def build_http_app(
    settings: Settings | None = None,
    *,
    client: WebClient | None = None,
    processor: EventProcessor | None = None,
) -> FastAPI:
    """FastAPI wrapper: Bolt at ``/slack/events``, plus ``/health``, ``/stats`` and ``/``."""
    from slack_bolt.adapter.fastapi import SlackRequestHandler

    settings = settings or get_settings()
    client = client or make_web_client(settings)
    processor = processor or EventProcessor(settings, client)
    bolt = build_bolt_app(settings, client, processor)
    handler = SlackRequestHandler(bolt)

    api = FastAPI(title="cufa slack bot", docs_url=None, redoc_url=None)
    api.state.processor = processor

    @api.on_event("startup")
    def _startup() -> None:
        if processor.workspace is None:
            processor.start()

    @api.on_event("shutdown")
    def _shutdown() -> None:
        processor.stop()

    @api.post("/slack/events")
    async def slack_events(request: Request):
        return await handler.handle(request)

    @api.get("/health")
    def health() -> JSONResponse:
        return JSONResponse({"ok": True, "team_id": processor.workspace.team_id if processor.workspace else None})

    @api.get("/stats")
    def stats_json() -> JSONResponse:
        with connection(settings) as conn:
            db = stats(conn, processor.workspace.team_id if processor.workspace else None)
        return JSONResponse({"process": processor.snapshot(), "database": db})

    @api.get("/", response_class=HTMLResponse)
    def index() -> str:
        return STATUS_PAGE

    return api


def run_http(settings: Settings | None = None, *, host: str = "127.0.0.1", port: int | None = None) -> None:
    import uvicorn

    settings = settings or get_settings()
    configure_logging(settings.log_level)
    app = build_http_app(settings)
    uvicorn.run(app, host=host, port=port or settings.slack_port, log_level="warning")


def run_socket(settings: Settings | None = None) -> None:
    """Socket Mode: no public URL. Blocks until interrupted."""
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    settings = settings or get_settings()
    configure_logging(settings.log_level)
    if not settings.slack_app_token:
        raise ConfigError(
            "SLACK_APP_TOKEN is not set. Socket Mode needs an app-level token "
            "(xapp-…) with the connections:write scope, from the app's Basic "
            "Information → App-Level Tokens. See docs/setup/slack-bot.md."
        )
    client = make_web_client(settings)
    processor = EventProcessor(settings, client)
    # Socket Mode has no request to verify, so no signing secret is needed —
    # but Bolt's constructor wants one. Any value works; nothing is signed.
    bolt = build_bolt_app(
        settings if settings.slack_signing_secret else _with_placeholder_secret(settings),
        client,
        processor,
    )
    processor.start()
    handler = SocketModeHandler(bolt, settings.slack_app_token)
    try:
        log.info("socket mode: connected, waiting for events (Ctrl+C to stop)")
        handler.start()
    finally:
        processor.stop()


def _with_placeholder_secret(settings: Settings) -> Settings:
    from dataclasses import replace

    return replace(settings, slack_signing_secret="socket-mode-unused")


STATUS_PAGE = """<!doctype html>
<meta charset="utf-8">
<meta http-equiv="refresh" content="3">
<title>cufa slack bot</title>
<style>
  body{font:14px/1.45 system-ui,sans-serif;margin:2rem auto;max-width:56rem;padding:0 1rem;color:#1a1a1a;background:#fafaf9}
  h1{font-size:1.25rem;margin:0 0 .25rem} .sub{color:#666;margin:0 0 1.5rem}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(11rem,1fr));gap:.75rem;margin-bottom:1.5rem}
  .tile{background:#fff;border:1px solid #e5e5e3;border-radius:.5rem;padding:.75rem 1rem}
  .tile b{display:block;font-size:1.5rem;font-weight:600} .tile span{color:#666;font-size:.8rem}
  table{border-collapse:collapse;width:100%;background:#fff;border:1px solid #e5e5e3;border-radius:.5rem}
  th,td{text-align:left;padding:.4rem .75rem;border-top:1px solid #eee} th{border-top:0;color:#666;font-weight:500;font-size:.8rem}
  code{background:#f0efed;padding:.1rem .3rem;border-radius:.25rem}
</style>
<h1>cufa slack bot</h1>
<p class="sub" id="sub">loading…</p>
<div class="grid" id="tiles"></div>
<h3>By type</h3><table id="types"><tr><th>event</th><th>count</th></tr></table>
<h3 style="margin-top:1.25rem">By channel</h3><table id="channels"><tr><th>channel</th><th>count</th></tr></table>
<p class="sub" style="margin-top:1.5rem">Refreshes every 3 s. Raw numbers at <code>/stats</code>. No addresses are shown here or logged. Q&amp;A tiles count only the channels named in <code>CUFA_SLACK_QA_CHANNELS</code>.</p>
<script>
fetch('/stats').then(r=>r.json()).then(s=>{
  const p=s.process, d=s.database;
  document.getElementById('sub').textContent=
    `${p.team_name||'not connected'} · cohort ${p.cohort_id||'—'} · up ${p.uptime_seconds??0}s · text stored: ${p.store_text?'YES':'no'}`;
  const q=d.qa||{};
  const tiles=[['events recorded',d.events||0],['messages',d.messages||0],['reactions',d.reactions||0],
    ['distinct people',d.distinct_users||0],['on roster',d.users_on_roster||0],['unattributed',d.unattributed_users||0],
    ['written (this process)',(p.this_process||{}).written||0],['duplicates dropped',(p.this_process||{}).duplicate||0],
    ['retries seen',(p.this_process||{}).retries_seen||0],['awaiting review',d.identity_unresolved_open||0],
    ['Q&A questions',q.questions||0],['Q&A replies',q.answers||0],['"asked before" pointers',q.pointers_posted||0],['Q&A summaries',q.summaries||0]];
  document.getElementById('tiles').innerHTML=tiles.map(([k,v])=>`<div class="tile"><b>${v}</b><span>${k}</span></div>`).join('');
  const fill=(id,obj)=>{const t=document.getElementById(id);Object.entries(obj||{}).forEach(([k,v])=>{const r=t.insertRow();r.insertCell().textContent=k;r.insertCell().textContent=v;});};
  fill('types',d.by_type); fill('channels',d.by_channel);
});
</script>
"""
