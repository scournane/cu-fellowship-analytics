"""Sign an HTTP request the way Slack signs deliveries to an Events API URL.

Slack's scheme (v0): ``X-Slack-Signature = "v0=" + HMAC_SHA256(secret,
"v0:" + timestamp + ":" + body)``, with the timestamp also sent as
``X-Slack-Request-Timestamp``. Bolt verifies this on every inbound request and
rejects anything more than five minutes old.

This module exists so the demo server and the tests can produce requests that
pass Bolt's *real* verifier, rather than switching verification off and testing
a bot that does not check who is talking to it.
"""

from __future__ import annotations

import hashlib
import hmac
import time


def sign(secret: str, body: bytes | str, timestamp: int | None = None) -> dict[str, str]:
    """Headers for one request body. Timestamp defaults to now."""
    ts = int(timestamp if timestamp is not None else time.time())
    raw = body.encode("utf-8") if isinstance(body, str) else body
    base = b"v0:" + str(ts).encode("ascii") + b":" + raw
    digest = hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    return {
        "X-Slack-Request-Timestamp": str(ts),
        "X-Slack-Signature": f"v0={digest}",
        "Content-Type": "application/json",
    }
