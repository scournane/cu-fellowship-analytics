"""Postgres access: psycopg 3 and plain SQL.

No ORM on purpose. This is batch ingest plus a small server; the queries are
the interesting part and hiding them behind a mapper makes the handoff longer,
not shorter. CU has no data manager — the next person to read this should be
able to paste any query here straight into Studio.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Sequence
from typing import Any

import psycopg
from psycopg import sql as _sql  # noqa: F401  (re-exported for callers building DDL)
from psycopg.rows import dict_row

from .config import Settings, get_settings
from .errors import DatabaseUnreachable
from .logging_setup import get_logger

log = get_logger(__name__)

UNREACHABLE_HINT = (
    "Cannot reach Postgres at {dsn}.\n"
    "\n"
    "The local database is the Supabase stack, which runs in Docker:\n"
    "  1. Start Docker Desktop (or your Docker daemon) and confirm `docker ps` works.\n"
    "  2. Run `make db-up` (or `supabase start`) in the repo root.\n"
    "  3. Re-run this command.\n"
    "\n"
    "If the stack is running on a different port, set CUFA_DATABASE_URL in .env."
)


def _safe_dsn(dsn: str) -> str:
    """Strip the password before a DSN is shown to a human or a log."""
    try:
        info = psycopg.conninfo.conninfo_to_dict(dsn)
    except Exception:
        return "<unparseable dsn>"
    user = info.get("user", "postgres")
    host = info.get("host", "localhost")
    port = info.get("port", "5432")
    dbname = info.get("dbname", "postgres")
    return f"postgresql://{user}@{host}:{port}/{dbname}"


def connect(settings: Settings | None = None, *, autocommit: bool = False) -> psycopg.Connection:
    """Open a connection, translating "not running" into an actionable message."""
    settings = settings or get_settings()
    try:
        conn = psycopg.connect(settings.database_url, row_factory=dict_row, connect_timeout=10)
    except psycopg.OperationalError as exc:
        raise DatabaseUnreachable(
            UNREACHABLE_HINT.format(dsn=_safe_dsn(settings.database_url))
        ) from exc
    conn.autocommit = autocommit
    return conn


@contextlib.contextmanager
def connection(settings: Settings | None = None, *, autocommit: bool = False) -> Iterator[psycopg.Connection]:
    """Connection context manager that always closes, and commits on clean exit."""
    conn = connect(settings, autocommit=autocommit)
    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            with contextlib.suppress(Exception):
                conn.rollback()
        raise
    finally:
        conn.close()


def fetch_all(conn: psycopg.Connection, query: str, params: Sequence[Any] | dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(query, params)
        return list(cur.fetchall())


def fetch_one(conn: psycopg.Connection, query: str, params: Sequence[Any] | dict[str, Any] | None = None) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchone()


def execute(conn: psycopg.Connection, query: str, params: Sequence[Any] | dict[str, Any] | None = None) -> int:
    """Run a statement and return the affected row count."""
    with conn.cursor() as cur:
        cur.execute(query, params)
        return cur.rowcount


def ping(settings: Settings | None = None) -> bool:
    """True when the database answers. Used by the console's health banner."""
    try:
        with connection(settings, autocommit=True) as conn:
            fetch_one(conn, "select 1 as ok")
        return True
    except DatabaseUnreachable:
        return False
