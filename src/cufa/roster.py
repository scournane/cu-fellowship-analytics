"""Roster loading.

The roster CSV is the one place a human declares who is in the cohort and
which per-system ids belong to them. It is the input the pipeline cannot
derive: Slack ids and Zoom emails cannot be matched to each other without
somebody saying so.

Columns: fellow_id, display_name, cohort, joined_on, status,
         zoom_email, slack_user_id, sheets_email
"""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from .model import Fellow, Identity

# roster column -> source system whose namespace that column belongs to
ID_COLUMNS = {"zoom_email": "zoom", "slack_user_id": "slack", "sheets_email": "sheets"}


def read(path: Path | str) -> tuple[list[Fellow], list[Identity]]:
    fellows: list[Fellow] = []
    identities: list[Identity] = []
    with open(Path(path), newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            fid = row["fellow_id"].strip()
            if not fid:
                continue
            fellows.append(Fellow(
                fellow_id=fid,
                display_name=row["display_name"].strip(),
                cohort=row["cohort"].strip(),
                joined_on=dt.date.fromisoformat(row["joined_on"].strip()),
                status=(row.get("status") or "active").strip(),
            ))
            for column, source in ID_COLUMNS.items():
                value = (row.get(column) or "").strip()
                if value:
                    # Emails are matched case-insensitively; Slack ids are not
                    # emails but are already uppercase and stable.
                    key = value.lower() if "email" in column else value
                    identities.append(Identity(source, key, fid))
    return fellows, identities
