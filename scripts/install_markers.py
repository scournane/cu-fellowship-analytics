#!/usr/bin/env python3
"""Print what in this database says a REAL Google account has been set up here.

Used by `make demo` before it resets anything. Lives in its own file rather than
as a `python -c` string inside tasks.py for the ordinary reason: tasks.py runs on
a bare interpreter and cannot import `cufa`, so the probe has to be a subprocess
either way — and a subprocess that is a readable file beats one that is a wall of
escaped quotes.

Prints one human-readable reason per line, and nothing at all when the database
holds nothing worth protecting. Exit code is always 0: "I could not tell" and
"there is nothing here" both mean the demo should proceed, and refusing to run a
demo because a probe failed would be worse than the thing being guarded against.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def main() -> int:
    try:
        from cufa.db import connection, fetch_one
        from cufa.provenance import is_simulated_form_id
    except Exception:  # pragma: no cover - a broken install is not an install
        return 0

    markers: list[str] = []
    try:
        with connection() as conn:
            # A credential that is not the fake client's `.invalid` placeholder
            # means somebody completed a real OAuth round trip against this
            # database.
            row = fetch_one(
                conn,
                "select account_email from google_credential "
                " where revoked_at is null "
                "   and account_email not like %s "
                " limit 1",
                ("%example.invalid",),
            )
            if row:
                markers.append(f"a connected Google account ({row['account_email']})")

            # A template id Google actually issued. `fake-form-…` is not one.
            template = fetch_one(
                conn, "select form_id from form_template where is_active limit 1"
            )
            form_id = (template or {}).get("form_id")
            if form_id and not is_simulated_form_id(form_id):
                markers.append(f"a real template form ({form_id})")

            # Check-ins are context, never a trigger. The demo writes a hundred
            # of them, and refusing to re-run the demo on a demo database would
            # make this guard useless in exactly the case it must allow.
            if markers:
                a = (fetch_one(conn, "select count(*) as n from checkin") or {}).get("n", 0)
                b = (fetch_one(conn, "select count(*) as n from checkin_b") or {}).get("n", 0)
                if a or b:
                    markers.append(f"{a + b} recorded check-in(s)")
    except Exception:
        # Unreachable database, missing tables, anything. Say nothing.
        return 0

    for marker in markers:
        print(marker)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
