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
from .events import POLL_ACTION_ID, Skipped, parse_event, parse_interaction
from .store import RecordOutcome, WorkspaceInfo, ensure_workspace, resolve_and_record, stats, touch_load_run

log = get_logger(__name__)

#: One listener, matched on event type alone. Bolt's string form is
#: subtype-agnostic too, but a pattern makes the set explicit in one place.
EVENT_PATTERN = re.compile(
    r"^(message|reaction_added|reaction_removed|member_joined_channel|member_left_channel"
    r"|user_huddle_changed|file_created|file_change|file_shared|file_comment_added)$"
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
    return WebClient(**kwargs)


class EventProcessor:
    """Everything that happens to an event after Bolt has verified it.

    One instance per bot process. Opens a ``load_run`` at start so every row
    it writes carries provenance, bumps that run's counters per event, and
    closes it on a clean stop. A run left in ``running`` after a crash is
    itself the record that the process died — which is the failure mode of
    a collector that matters most, and the one nobody notices otherwise.
    """

    def __init__(self, settings: Settings, client: Any) -> None:
        self.settings = settings
        self.client = client
        self.workspace: WorkspaceInfo | None = None
        self.load_id: str | None = None
        self.started_at: float | None = None
        self.counts: Counter[str] = Counter()
        self._lock = threading.Lock()

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
        self.started_at = time.time()
        log.info(
            "slack bot connected team=%s (%s) cohort=%s store_text=%s load=%s",
            self.workspace.team_id, self.workspace.team_name,
            self.workspace.cohort_id, self.settings.slack_store_text, self.load_id,
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
        actor = event.get("user")
        if isinstance(actor, dict):  # user_huddle_changed carries the whole user
            actor = actor.get("id")
        if self.workspace and actor == self.workspace.bot_user_id:
            outcome = RecordOutcome(False, Skipped("the bot's own message", event.get("type")), None, None, event.get("type"))
        else:
            parsed = parse_event(event, team_id, store_text=self.settings.slack_store_text, retry_num=retry_num)
            outcome = self._record(parsed)
        with self._lock:
            self.counts[outcome.status] += 1
            if retry_num:
                self.counts["retries_seen"] += 1
        return outcome

    def process_interaction(self, body: dict[str, Any], team_id: str) -> RecordOutcome:
        """A ``block_actions`` payload — for this bot, only ever a poll vote."""
        outcome = self._record(parse_interaction(body, team_id))
        with self._lock:
            self.counts[outcome.status] += 1
            if outcome.status == "written":
                self.counts["poll_votes"] += 1
        return outcome

    def _record(self, parsed: Any) -> RecordOutcome:
        with connection(self.settings) as conn:
            cohort = self.workspace.cohort_id if self.workspace else self.settings.slack_cohort
            outcome = resolve_and_record(conn, self.client, parsed, cohort_id=cohort, load_id=self.load_id)
            if self.load_id:
                touch_load_run(
                    conn, self.load_id, read=1,
                    written=1 if outcome.status == "written" else 0,
                    skipped=0 if outcome.status == "written" else 1,
                )
        return outcome

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
            "this_process": counts,
        }


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
        processor.process(event, team_id, retry_num=_retry_num(req))

    @app.action(POLL_ACTION_ID)
    def on_poll_vote(ack: Any, body: dict[str, Any], context: Any) -> None:
        # Slack wants the press acknowledged within three seconds; the row is
        # written after that. The message is left as posted — results are
        # never edited into it, so a channel cannot watch a tally move.
        ack()
        team_id = getattr(context, "team_id", None) or (body.get("team") or {}).get("id") or ""
        processor.process_interaction(body, team_id)

    @app.command("/cufa-reminders")
    def reminder_preferences(ack: Any, command: dict[str, Any], respond: Any) -> None:
        # Slack requires the command to be acknowledged within three seconds.
        # The database lookup and users.info refresh happen after that ack.
        ack()
        from .reminders import preference_command

        try:
            with connection(settings) as conn:
                message = preference_command(
                    conn,
                    client,
                    team_id=command.get("team_id") or (
                        processor.workspace.team_id if processor.workspace else ""
                    ),
                    cohort_id=(
                        processor.workspace.cohort_id or settings.slack_cohort
                        if processor.workspace
                        else settings.slack_cohort
                    ),
                    user_id=command.get("user_id") or "",
                    text=command.get("text") or "",
                    default_timezone=settings.slack_default_fellow_timezone,
                    default_quiet_start=settings.slack_quiet_start,
                    default_quiet_end=settings.slack_quiet_end,
                )
        except Exception as exc:
            log.warning("reminder preference command failed: %s", type(exc).__name__)
            message = (
                "I couldn't save that preference just now. Nothing changed; "
                "please try again or ask staff to check the bot status."
            )
        respond(text=message, response_type="ephemeral")

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
    api.state.automation = None

    @api.on_event("startup")
    def _startup() -> None:
        if processor.workspace is None:
            workspace = processor.start()
        else:
            workspace = processor.workspace
        if settings.slack_automations_enabled and api.state.automation is None:
            from .reminders import AutomationLoop

            api.state.automation = AutomationLoop(
                settings,
                client,
                team_id=workspace.team_id,
                cohort_id=workspace.cohort_id or settings.slack_cohort,
            )
            api.state.automation.start()

    @api.on_event("shutdown")
    def _shutdown() -> None:
        if api.state.automation is not None:
            api.state.automation.stop()
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
        automation = (
            api.state.automation.snapshot()
            if api.state.automation is not None
            else {"enabled": settings.slack_automations_enabled, "last_tick_at": None, "counts": {}}
        )
        return JSONResponse(
            {"process": processor.snapshot(), "automation": automation, "database": db}
        )

    @api.get("/insights")
    def insights_json(days: int | None = 28) -> JSONResponse:
        """The read-time signals. Fellow names appear (this is staff-facing,
        same as ``cufa slack report``); addresses do not."""
        from .insights import all_insights

        team_id = processor.workspace.team_id if processor.workspace else None
        cohort = (processor.workspace.cohort_id if processor.workspace else None) or settings.slack_cohort
        with connection(settings) as conn:
            data = all_insights(
                conn, cohort_id=cohort, team_id=team_id, days=days,
                quiet_days=settings.slack_quiet_channel_days,
                default_zone=settings.slack_default_fellow_timezone,
            )
        return JSONResponse(_without_addresses(data), headers={"Access-Control-Allow-Origin": "*"})

    @api.get("/", response_class=HTMLResponse)
    def index() -> str:
        return STATUS_PAGE

    return api


def _without_addresses(value: Any) -> Any:
    """Strip ``*_email`` keys and JSON-encode dates, for the HTTP endpoints."""
    if isinstance(value, dict):
        return {k: _without_addresses(v) for k, v in value.items() if not str(k).endswith("email")}
    if isinstance(value, list):
        return [_without_addresses(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


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
    automation = None
    if settings.slack_automations_enabled and processor.workspace is not None:
        from .reminders import AutomationLoop

        automation = AutomationLoop(
            settings,
            client,
            team_id=processor.workspace.team_id,
            cohort_id=processor.workspace.cohort_id or settings.slack_cohort,
        )
        automation.start()
    handler = SocketModeHandler(bolt, settings.slack_app_token)
    try:
        log.info("socket mode: connected, waiting for events (Ctrl+C to stop)")
        handler.start()
    finally:
        if automation is not None:
            automation.stop()
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
<p class="sub" style="margin-top:1.5rem">Refreshes every 3 s. Raw numbers at <code>/stats</code>. No addresses are shown here or logged.</p>
<script>
fetch('/stats').then(r=>r.json()).then(s=>{
  const p=s.process, d=s.database, a=s.automation||{};
  document.getElementById('sub').textContent=
    `${p.team_name||'not connected'} · cohort ${p.cohort_id||'—'} · up ${p.uptime_seconds??0}s · text stored: ${p.store_text?'YES':'no'} · automations: ${a.enabled?'on':'off'}`;
  const tiles=[['events recorded',d.events||0],['messages',d.messages||0],['reactions',d.reactions||0],
    ['distinct people',d.distinct_users||0],['on roster',d.users_on_roster||0],['unattributed',d.unattributed_users||0],
    ['written (this process)',(p.this_process||{}).written||0],['duplicates dropped',(p.this_process||{}).duplicate||0],
    ['retries seen',(p.this_process||{}).retries_seen||0],['reminders sent',(a.counts||{}).sent||0],
    ['reminder failures',(a.counts||{}).failed||0],['awaiting review',d.identity_unresolved_open||0]];
  document.getElementById('tiles').innerHTML=tiles.map(([k,v])=>`<div class="tile"><b>${v}</b><span>${k}</span></div>`).join('');
  const fill=(id,obj)=>{const t=document.getElementById(id);Object.entries(obj||{}).forEach(([k,v])=>{const r=t.insertRow();r.insertCell().textContent=k;r.insertCell().textContent=v;});};
  fill('types',d.by_type); fill('channels',d.by_channel);
});
</script>
"""
