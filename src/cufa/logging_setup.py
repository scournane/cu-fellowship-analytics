"""Logging with secrets and personal data filtered out by construction.

The rule is not "remember not to log an email". The rule is that a record
carrying an address cannot reach a handler at INFO or above even if someone
formats one into a message by accident, because the filter rewrites it.

  * Email addresses  — redacted at INFO and above, passed through at DEBUG.
  * API keys, OAuth tokens, encryption keys — redacted at every level,
    including DEBUG. There is no debugging situation that needs them.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Long opaque credentials. Ordered most specific first so a Google token is not
# partially matched by the generic rule.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bya29\.[A-Za-z0-9._\-]+"),            # Google OAuth access token
    re.compile(r"\b1//[A-Za-z0-9._\-]{10,}"),            # Google refresh token
    re.compile(r"\bAIza[A-Za-z0-9._\-]{10,}"),           # Google API key
    re.compile(r"\bgAAAAA[A-Za-z0-9._\-]{10,}"),         # Fernet ciphertext
    re.compile(
        r"(?i)\b(api[_-]?key|refresh[_-]?token|access[_-]?token|client[_-]?secret|"
        r"encryption[_-]?key|authorization|bearer)\b\s*[:=]?\s*[\"']?([A-Za-z0-9._\-/+]{8,})"
    ),
)

EMAIL_REDACTION = "<email redacted>"
SECRET_REDACTION = "<redacted>"


def redact_secrets(text: str) -> str:
    """Remove credential-shaped substrings at any log level."""
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda m: f"{m.group(1)}={SECRET_REDACTION}", text)
        else:
            text = pattern.sub(SECRET_REDACTION, text)
    return text


def redact_emails(text: str) -> str:
    """Replace every email address with a placeholder."""
    return _EMAIL_RE.sub(EMAIL_REDACTION, text)


class RedactionFilter(logging.Filter):
    """Rewrites the formatted message rather than trusting call sites.

    Emails survive at DEBUG because reconciling an unmatched address needs the
    address. Credentials never survive.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - malformed record
            return True

        cleaned = redact_secrets(message)
        if record.levelno >= logging.INFO:
            cleaned = redact_emails(cleaned)

        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        return True


def configure_logging(level: str | int | None = None) -> None:
    """Install the redaction filter on the root handler. Idempotent."""
    if level is None:
        level = os.environ.get("CUFA_LOG_LEVEL", "INFO")
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)-7s %(name)-22s %(message)s"))
        root.addHandler(handler)

    for handler in root.handlers:
        if not any(isinstance(f, RedactionFilter) for f in handler.filters):
            handler.addFilter(RedactionFilter())


def get_logger(name: str) -> logging.Logger:
    """Module logger. Configuration is the entry point's job, not the module's."""
    return logging.getLogger(name)


def mask_email(email: str) -> str:
    """Render an address safe to show at INFO: ``a***@example.invalid``.

    Used where an operator needs to distinguish two rows in a log line without
    the log becoming a roster dump.
    """
    if "@" not in email:
        return EMAIL_REDACTION
    local, _, domain = email.partition("@")
    head = local[:1] if local else ""
    return f"{head}***@{domain}"


def summarize(**counts: Any) -> str:
    """Format a counts-only summary line. Counts are always safe at INFO."""
    return " ".join(f"{key}={value}" for key, value in counts.items())
