"""Who gets woken up when a fellow ticks the help box, and what they are told.

Design invariant 2, in code rather than in a comment: **the help checkbox
cannot be provisioned without a named recipient.** If ``config/help_routing.json``
names nobody, the field is left off the form entirely and provisioning records
why. A system that invites a young person to ask for help and then routes that
request nowhere is worse than one that never asks — it collects the signal, does
nothing with it, and leaves everyone believing it was handled.

Two other rules live here:

* **Minimum necessary.** The notification carries the fellow's name and the
  session. Not the takeaway, not the confidence score, not the shoutout, not the
  free text of anything. Whoever responds needs to know who to contact and when
  it happened; the rest is the fellow's to tell them, or not.
* **Nothing about a help request is ever logged**, at any level, DEBUG
  included. The rest of this codebase redacts addresses above DEBUG; this module
  emits no content at all, only counts and outcomes.
"""

from __future__ import annotations

import json
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Protocol

from .errors import CufaError
from .logging_setup import get_logger

log = get_logger(__name__)

DEFAULT_CONFIG_PATH = "config/help_routing.json"

NO_RECIPIENT_REASON = (
    "config/help_routing.json names no recipient, so the “I'd like someone to "
    "check in with me” checkbox was left off this form. Asking a fellow to raise "
    "their hand and then routing it nowhere is worse than not asking: the request "
    "would be recorded, nobody would be told, and everyone would assume it had "
    "been handled. Name a recipient in config/help_routing.json and provision "
    "again to include the field."
)


class HelpRoutingConfigError(CufaError):
    """``config/help_routing.json`` exists but cannot be read."""


@dataclass(frozen=True)
class Recipient:
    name: str
    email: str

    def header(self) -> str:
        return f"{self.name} <{self.email}>" if self.name else self.email


@dataclass(frozen=True)
class HelpRouting:
    """The configured destination for help requests, possibly empty."""

    recipients: tuple[Recipient, ...] = ()
    from_address: str = "no-reply@civicsunplugged.org"
    subject_prefix: str = "[Civic Innovators] Check-in request"
    status: str = "(no status)"
    source: str = DEFAULT_CONFIG_PATH

    @property
    def has_recipient(self) -> bool:
        """Whether the help checkbox may be put on a form at all."""
        return bool(self.recipients)

    @property
    def reason_omitted(self) -> str | None:
        return None if self.has_recipient else NO_RECIPIENT_REASON

    def to_dict(self) -> dict[str, Any]:
        """Safe to hand to the console. Contains configuration, never a request."""
        return {
            "has_recipient": self.has_recipient,
            "recipients": [{"name": r.name, "email": r.email} for r in self.recipients],
            "status": self.status,
            "source": self.source,
            "reason_omitted": self.reason_omitted,
        }


def parse_help_routing(
    payload: dict[str, Any], *, source: str = DEFAULT_CONFIG_PATH
) -> HelpRouting:
    """Read the config. An empty or absent recipient list is legal, not an error.

    Legal because it is a real state CU can be in — nobody has been named yet —
    and the correct response to it is to omit the field, not to crash. What is
    *not* legal is provisioning the field anyway; that is enforced where the form
    is built.
    """
    if not isinstance(payload, dict):
        raise HelpRoutingConfigError(f"{source} must contain a JSON object.")

    raw = payload.get("recipients") or []
    if not isinstance(raw, list):
        raise HelpRoutingConfigError(f'{source} "recipients" must be a list.')

    recipients: list[Recipient] = []
    for entry in raw:
        if isinstance(entry, str):
            email = entry.strip()
            name = ""
        elif isinstance(entry, dict):
            email = str(entry.get("email") or "").strip()
            name = str(entry.get("name") or "").strip()
        else:
            raise HelpRoutingConfigError(
                f"{source} recipients entries must be an address or "
                f'{{"name": ..., "email": ...}}, got {type(entry).__name__}.'
            )
        # A blank entry is treated as no recipient rather than as a recipient
        # with an empty address: the second would pass has_recipient and then
        # send nothing, which is the exact failure this module exists to stop.
        if email:
            recipients.append(Recipient(name=name, email=email))

    return HelpRouting(
        recipients=tuple(recipients),
        from_address=str(payload.get("from_address") or "no-reply@civicsunplugged.org"),
        subject_prefix=str(payload.get("subject_prefix") or "[Civic Innovators] Check-in request"),
        status=str(payload.get("status") or "(no status)"),
        source=source,
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_help_routing(path: str | Path | None = None) -> HelpRouting:
    """Read the routing config. A missing file means "nobody is named"."""
    resolved = Path(path) if path else _repo_root() / DEFAULT_CONFIG_PATH
    if not resolved.exists():
        log.warning(
            "no help routing config at %s; the help checkbox will be omitted "
            "from Part B forms",
            resolved,
        )
        return HelpRouting(source=str(resolved))
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise HelpRoutingConfigError(f"{resolved} is not valid JSON: {exc}") from exc
    return parse_help_routing(payload, source=str(resolved))


# Cached by modification time rather than forever.
#
# `functools.lru_cache` was the obvious choice and the wrong one: the console is
# a long-running process, and someone naming a recipient in
# config/help_routing.json would go on being told nobody is configured until the
# server was restarted — with no indication that a restart was what was needed.
# A stat() per call is free next to the database round trip that follows it.
_CACHE: dict[str, tuple[int, HelpRouting]] = {}


def _config_path(path: str | Path | None) -> Path:
    return Path(path) if path else _repo_root() / DEFAULT_CONFIG_PATH


def _mtime(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        # Missing is a state, not an error — the field is simply omitted. -1 is
        # cached like any other, so the file appearing later is picked up.
        return -1


def get_help_routing(path: str | Path | None = None) -> HelpRouting:
    """The routing config, re-read whenever the file on disk has changed."""
    resolved = _config_path(path)
    key = str(resolved)
    stamp = _mtime(resolved)
    hit = _CACHE.get(key)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    value = load_help_routing(resolved)
    _CACHE[key] = (stamp, value)
    return value


def reset_help_routing_cache() -> None:
    """Drop the cache. Used by tests that write their own config."""
    _CACHE.clear()


# ---------------------------------------------------------------------------
# notification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HelpNotification:
    """Exactly what leaves this process when a fellow asks to be contacted.

    Deliberately a value object rather than a formatted blob: a test can assert
    on its fields, and the assertion that the takeaway text is absent is then a
    property of the type rather than of a regex over an email body.
    """

    to: tuple[str, ...]
    subject: str
    body: str
    fellow_name: str
    session_title: str
    submitted_at_utc: str


#: The fields of a Part B submission a notification may mention. Anything not
#: named here — takeaway, confidence, rotating answer, shoutout — is withheld.
NOTIFIABLE_FIELDS: tuple[str, ...] = ("fellow_name", "session_title", "submitted_at_utc")


def build_notification(
    routing: HelpRouting,
    *,
    fellow_name: str,
    session_title: str,
    submitted_at_utc: str,
) -> HelpNotification:
    """Compose the message. Minimum necessary, by construction.

    The signature is the enforcement: there is no parameter through which a
    takeaway, a confidence score or a shoutout could be passed, so no future
    edit adds one by reflex while adding "a bit more context".
    """
    name = (fellow_name or "").strip() or "A fellow whose address is not on the roster"
    session = (session_title or "").strip() or "a session we could not identify"

    subject = f"{routing.subject_prefix}: {name}"
    body = (
        f"{name} ticked “I'd like someone to check in with me” on the "
        f"end-of-session form.\n"
        "\n"
        f"Session:   {session}\n"
        f"Submitted: {submitted_at_utc} UTC\n"
        "\n"
        "That is everything this message carries, on purpose. Nothing else the "
        "fellow wrote on the form is included — not their takeaway, not their "
        "confidence rating, not who they thanked. If they want to tell you any "
        "of it, that is theirs to say.\n"
        "\n"
        "Open requests are listed in the console under “Help requests”, where "
        "you can acknowledge and close this one.\n"
    )
    return HelpNotification(
        to=tuple(r.header() for r in routing.recipients),
        subject=subject,
        body=body,
        fellow_name=name,
        session_title=session,
        submitted_at_utc=submitted_at_utc,
    )


class Notifier(Protocol):
    """How a notification leaves the process. Injectable so tests never send."""

    def send(self, notification: HelpNotification) -> None:
        ...


@dataclass
class RecordingNotifier:
    """Records instead of sending. The default outside a configured SMTP host.

    Used by the demo and the test suite. It exists as a real class rather than a
    test double because "no SMTP configured" is a state a CU install can be in,
    and in that state the request must still be recorded and still be visible in
    the console — the console screen, not the email, is the durable channel.
    """

    sent: list[HelpNotification] = field(default_factory=list)

    def send(self, notification: HelpNotification) -> None:
        self.sent.append(notification)
        # Counts only. The body names a fellow who asked for help, and that
        # belongs in the database and the console, never in a log file.
        log.info("help request notification prepared recipients=%d", len(notification.to))


@dataclass
class SmtpNotifier:
    """Sends through a plain SMTP host.

    No SMTP settings are shipped: CU has not chosen a mail path, and inventing
    one would put a fellow's name through a service nobody agreed to.
    """

    host: str
    port: int = 25
    from_address: str = "no-reply@civicsunplugged.org"
    timeout: int = 20

    def send(self, notification: HelpNotification) -> None:
        message = EmailMessage()
        message["From"] = self.from_address
        message["To"] = ", ".join(notification.to)
        message["Subject"] = notification.subject
        message.set_content(notification.body)
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
            smtp.send_message(message)
        log.info("help request notification sent recipients=%d", len(notification.to))


__all__ = [
    "NOTIFIABLE_FIELDS",
    "NO_RECIPIENT_REASON",
    "HelpNotification",
    "HelpRouting",
    "HelpRoutingConfigError",
    "Notifier",
    "Recipient",
    "RecordingNotifier",
    "SmtpNotifier",
    "build_notification",
    "get_help_routing",
    "load_help_routing",
    "parse_help_routing",
    "reset_help_routing_cache",
]
