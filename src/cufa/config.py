"""Runtime configuration, read once from the environment.

Everything is overridable so tests never depend on a developer's shell, and so
`make demo` can force the fake Google client without editing a file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

from .errors import ConfigError

DEFAULT_DSN = "postgresql://postgres:postgres@localhost:64322/postgres"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Resolved configuration for one process."""

    database_url: str = DEFAULT_DSN
    encryption_key: str | None = None

    console_allowlist: tuple[str, ...] = ()
    #: Who may open the help-requests screen. A SUBSET of console_allowlist, and
    #: deliberately a separate list: the general console allowlist is "CU staff
    #: who run lessons", and a record that a young person asked to be contacted
    #: is not routine operational data. When this is empty the recipients named
    #: in config/help_routing.json are used instead — the people already being
    #: emailed the requests are the obvious people allowed to read them, and
    #: invariant 2 guarantees that list is non-empty whenever the field exists
    #: on a form at all.
    help_allowlist: tuple[str, ...] = ()
    console_secret: str = "dev-insecure-secret"
    console_host: str = "127.0.0.1"
    console_port: int = 8000

    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str = "http://127.0.0.1:8000/google/callback"
    fake_google: bool = False
    # Where the fake client persists its forms, so a multi-command demo and
    # the console see the same state across processes.
    fake_google_state: str = "fixtures/fake_google_state.json"

    gemini_api_key: str | None = None
    ai_model: str = "gemini-2.5-flash"
    ai_max_calls_per_run: int = 250

    max_edit_distance: int = 1
    log_level: str = "INFO"

    # --- Slack -----------------------------------------------------------
    #: xoxb- bot token. Required for both HTTP and Socket Mode.
    slack_bot_token: str | None = None
    #: xapp- app-level token. Socket Mode only.
    slack_app_token: str | None = None
    #: Verifies every inbound HTTP delivery. Required for `cufa slack serve`.
    slack_signing_secret: str | None = None
    #: Override the Web API base. The demo points this at the fake server;
    #: blank means slack.com. Must end in "/api/".
    slack_api_base_url: str | None = None
    #: Which cohort this workspace's members belong to.
    slack_cohort: str = "demo"
    #: Whether message TEXT is stored. Default off — the participation
    #: definition counts acts; it does not read them. See the migration.
    slack_store_text: bool = False
    slack_port: int = 3000
    #: How long a users.info answer is trusted before it is refreshed.
    slack_user_cache_hours: int = 24
    #: HTTP mode: run the listener before answering Slack (deterministic for
    #: the demo and tests) rather than acking first and writing in a thread.
    slack_process_before_response: bool = True
    #: Channels (names or ids) treated as Q&A: their message text IS stored,
    #: the bot points a repeated question at the earlier answer, and a
    #: per-session summary can be generated. Empty means none of that runs.
    #: See ADR-032.
    slack_qa_channels: tuple[str, ...] = ()
    #: Where `cufa slack qa summary --post` goes when no --channel is given.
    #: Blank means the first Q&A channel.
    slack_qa_summary_channel: str | None = None

    fixtures_dir: Path = field(default_factory=lambda: _repo_root() / "fixtures")

    def require_encryption_key(self) -> str:
        """Return the Fernet key, refusing to continue without one.

        Storing a refresh token unencrypted would satisfy every test in this
        repo and quietly break the one security property that matters, so the
        absence of a key is an error rather than a fallback.
        """
        if not self.encryption_key:
            raise ConfigError(
                "CUFA_ENCRYPTION_KEY is not set, so a Google refresh token cannot be "
                "encrypted at rest. Generate one with:\n"
                "    python -m cufa.crypto keygen\n"
                "then add it to .env as CUFA_ENCRYPTION_KEY."
            )
        return self.encryption_key


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Build settings from the environment, loading .env first if present."""
    if env is None:
        load_dotenv(_repo_root() / ".env", override=False)
        env = dict(os.environ)

    def _addresses(name: str) -> tuple[str, ...]:
        return tuple(
            item.strip().lower()
            for item in (env.get(name) or "").split(",")
            if item.strip()
        )

    allowlist = _addresses("CUFA_CONSOLE_ALLOWLIST")
    help_allowlist = _addresses("CUFA_HELP_ALLOWLIST")

    def _int(name: str, default: int) -> int:
        raw = (env.get(name) or "").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError as exc:
            raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc

    return Settings(
        database_url=env.get("CUFA_DATABASE_URL") or DEFAULT_DSN,
        encryption_key=(env.get("CUFA_ENCRYPTION_KEY") or "").strip() or None,
        console_allowlist=allowlist,
        help_allowlist=help_allowlist,
        console_secret=env.get("CUFA_CONSOLE_SECRET") or "dev-insecure-secret",
        console_host=env.get("CUFA_CONSOLE_HOST") or "127.0.0.1",
        console_port=_int("CUFA_CONSOLE_PORT", 8000),
        google_client_id=(env.get("GOOGLE_CLIENT_ID") or "").strip() or None,
        google_client_secret=(env.get("GOOGLE_CLIENT_SECRET") or "").strip() or None,
        google_redirect_uri=env.get("GOOGLE_OAUTH_REDIRECT_URI")
        or "http://127.0.0.1:8000/google/callback",
        fake_google=_truthy(env.get("CUFA_FAKE_GOOGLE")),
        fake_google_state=env.get("CUFA_FAKE_GOOGLE_STATE")
        or "fixtures/fake_google_state.json",
        gemini_api_key=(env.get("GEMINI_API_KEY") or "").strip() or None,
        ai_model=env.get("CUFA_AI_MODEL") or "gemini-2.5-flash",
        ai_max_calls_per_run=_int("CUFA_AI_MAX_CALLS_PER_RUN", 250),
        max_edit_distance=_int("CUFA_MAX_EDIT_DISTANCE", 1),
        log_level=(env.get("CUFA_LOG_LEVEL") or "INFO").upper(),
        slack_bot_token=(env.get("SLACK_BOT_TOKEN") or "").strip() or None,
        slack_app_token=(env.get("SLACK_APP_TOKEN") or "").strip() or None,
        slack_signing_secret=(env.get("SLACK_SIGNING_SECRET") or "").strip() or None,
        slack_api_base_url=(env.get("SLACK_API_BASE_URL") or "").strip() or None,
        slack_cohort=(env.get("CUFA_SLACK_COHORT") or "demo").strip(),
        slack_store_text=_truthy(env.get("CUFA_SLACK_STORE_TEXT")),
        slack_port=_int("CUFA_SLACK_PORT", 3000),
        slack_user_cache_hours=_int("CUFA_SLACK_USER_CACHE_HOURS", 24),
        slack_process_before_response=not _truthy(env.get("CUFA_SLACK_ACK_FIRST")),
        slack_qa_channels=tuple(
            item.strip() for item in (env.get("CUFA_SLACK_QA_CHANNELS") or "").split(",") if item.strip()
        ),
        slack_qa_summary_channel=(env.get("CUFA_SLACK_QA_SUMMARY_CHANNEL") or "").strip().lstrip("#") or None,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings. Cached so .env is read once."""
    return load_settings()


def reset_settings_cache() -> None:
    """Drop the cached settings. Used by tests that manipulate the environment."""
    get_settings.cache_clear()
