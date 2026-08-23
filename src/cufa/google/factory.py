"""Choose between the real client and the fake.

One switch, read from configuration, so that `make demo`, `make demo-console`
and the test suite all reach the fake by the same route the real thing uses —
no import-time monkeypatching and no separate code path to rot.
"""

from __future__ import annotations

from typing import Any

import psycopg

from ..config import Settings, get_settings
from ..logging_setup import get_logger
from .base import FormsClient

log = get_logger(__name__)

# The demo and the console share one fake so state (the template, provisioned
# forms, seeded responses) survives across requests within a process.
_FAKE_SINGLETON: Any | None = None


def set_fake_client(client: Any | None) -> None:
    """Install a process-wide fake. Used by the demo, the console and tests."""
    global _FAKE_SINGLETON
    _FAKE_SINGLETON = client


def get_fake_client() -> Any | None:
    return _FAKE_SINGLETON


def get_client(conn: psycopg.Connection, settings: Settings | None = None) -> FormsClient:
    """Return whichever client this process should use.

    ``CUFA_FAKE_GOOGLE=1`` yields the fake; anything else builds the real client
    from the stored credential, which raises ``GoogleNotConnected`` if there is
    none.
    """
    settings = settings or get_settings()
    if settings.fake_google:
        global _FAKE_SINGLETON
        if _FAKE_SINGLETON is None:
            from .fake import FakeGoogleClient

            _FAKE_SINGLETON = FakeGoogleClient()
            log.info("using FakeGoogleClient (CUFA_FAKE_GOOGLE=1); no Google calls will be made")
        return _FAKE_SINGLETON

    from .oauth import load_credentials
    from .real import RealGoogleClient

    return RealGoogleClient(load_credentials(conn, settings))
