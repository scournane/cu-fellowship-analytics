"""Create (or refresh) the database the test suite runs against.

Deliberately a different database from the one the console uses. The `db`
fixture truncates every table before each test, `google_credential` included,
so aiming the suite at the working database destroys a connected Google
account and a loaded roster on every run. Separating them is the only reliable
fix; remembering not to is not one.

Idempotent — re-running re-applies the migrations over the same database.
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cufa.config import DEFAULT_DSN  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "supabase" / "migrations"


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--force"]
    # --force is for `make demo` pointed at a scratch database on purpose: there
    # the database being rebuilt IS the configured one, which is the case the
    # guard below otherwise exists to prevent.
    force = "--force" in sys.argv
    name = args[0] if args else "cufa_test"

    # Build the neighbouring DSNs off whatever the working one points at, so
    # this follows a moved port or a changed password without being told.
    import os

    dsn = os.environ.get("CUFA_DATABASE_URL") or DEFAULT_DSN
    prefix = dsn.rsplit("/", 1)[0]
    admin, target = f"{prefix}/postgres", f"{prefix}/{name}"

    if name == dsn.rsplit("/", 1)[-1] and not force:
        print(f"refusing: {name} is the database the console uses", file=sys.stderr)
        print("pass --force if you meant to rebuild it", file=sys.stderr)
        return 1

    # Dropped and rebuilt rather than migrated in place: the migrations are
    # plain CREATEs, so re-applying them over an existing schema fails on the
    # first table. Nothing in this database is ever worth keeping.
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'drop database if exists "{name}" with (force)')
        conn.execute(f'create database "{name}"')
        print(f"created database {name}")

    migrations = sorted(MIGRATIONS.glob("*.sql"))
    if not migrations:
        print(f"no migrations found under {MIGRATIONS}", file=sys.stderr)
        return 1

    with psycopg.connect(target, autocommit=True) as conn:
        for path in migrations:
            conn.execute(path.read_text(encoding="utf-8"))

    print(f"applied {len(migrations)} migrations")
    print(f"test database: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
