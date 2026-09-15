"""Vercel entrypoint: the console and the Slack bot, one deployment.

Why both in one place. The console needs a public HTTPS origin so the
`/me/<token>` links the bot hands out actually open. The bot, once it is off
Socket Mode, needs a public HTTPS origin too, and it has to sign those same
links with the same `CUFA_CONSOLE_SECRET` and read the same database. Two
deployments would mean keeping two copies of both in step, which is the sort
of thing that works until the afternoon somebody rotates one of them.

So: the console is the root app and the bot is mounted under `/bot`.

    https://<host>/                 the console
    https://<host>/bot/slack/events Slack's request URL
    https://<host>/bot/cron/tick    the scheduler's minute hand

**The automation loop is off here, and must stay off.** `AutomationLoop` is a
thread inside a process that never exits, and on this platform the process is
the request. Set `CUFA_SLACK_AUTOMATIONS=0` and let something outside call
`/bot/cron/tick` once a minute (see deploy/vercel/README.md). With it left on,
each cold start would spawn a loop that dies moments later, having sent
whatever it managed to get through first.

Every setting below has to match wherever else this app runs, or the signed
links stop verifying:

    CUFA_CONSOLE_SECRET   signs both /me/<token> and the console session cookie
    CUFA_DATABASE_URL     the database Slack events are written to
"""

import atexit
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from cufa.config import get_settings  # noqa: E402
from cufa.console.app import app  # noqa: E402
from cufa.logging_setup import get_logger  # noqa: E402

log = get_logger(__name__)

_settings = get_settings()

if _settings.slack_bot_token and _settings.slack_signing_secret:
    from cufa.slack.bot import EventProcessor, build_http_app, make_web_client  # noqa: E402

    _client = make_web_client(_settings)
    _processor = EventProcessor(_settings, _client)
    app.mount("/bot", build_http_app(_settings, client=_client, processor=_processor))

    # A mounted sub-app never gets its own lifespan events, so the startup hook
    # inside build_http_app does not run and the workspace is resolved here
    # instead -- once per cold start, not once per request.
    #
    # This opens a `load_run` row that stays 'running' until stop(). atexit
    # closes it on a graceful shutdown; an instance the platform reclaims
    # without warning leaves the row open. That is worth knowing before reading
    # the health probe in RUNBOOK.md, where a stale 'running' row used to mean
    # exactly one thing: the bot crashed. Under serverless it usually means an
    # idle instance was collected, which is ordinary.
    try:
        _processor.start()
        atexit.register(_processor.stop)
    except Exception:  # noqa: BLE001 -- the console must still serve
        log.exception("could not resolve the Slack workspace at startup")
else:
    log.warning(
        "SLACK_BOT_TOKEN or SLACK_SIGNING_SECRET is unset, so /bot is not mounted. "
        "The console is unaffected; Slack has nowhere to deliver to."
    )

__all__ = ["app"]
