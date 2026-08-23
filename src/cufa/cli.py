"""Command line entry point.

    python -m cufa ingest --roster fixtures/roster.csv --fixtures fixtures
    python -m cufa report --out out/report.html
    python -m cufa status
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from . import config, metrics, report as report_mod, roster as roster_mod
from .sources import resolve, sheets, slack, zoom
from .store import Store


def cmd_ingest(args) -> int:
    defs = config.load(args.config)
    store = Store(args.db)

    fellows, identities = roster_mod.read(args.roster)
    store.upsert_fellows(fellows)
    store.upsert_identities(identities)
    print(f"roster: {len(fellows)} fellows, {len(identities)} identity mappings")

    idmap = store.identity_map()
    root = Path(args.fixtures)
    # Everyone on the roster is expected at every live lesson from the week
    # they join, so a no-show becomes an explicit absence event rather than
    # missing data. Joining dates keep pre-enrolment lessons out of it.
    joined = {f.fellow_id: f.joined_on for f in fellows}
    expected = {i.source_user_id: joined.get(i.fellow_id)
                for i in identities if i.source == "zoom"}
    total_accepted = total_orphans = 0

    zoom_dir = root / "zoom"
    if zoom_dir.is_dir():
        for csv_path in sorted(zoom_dir.glob("*.csv")):
            raws = zoom.read(csv_path, expected=expected)
            events, orphans = resolve(raws, idmap)
            accepted = store.add_events(events)
            store.add_unresolved(orphans)
            store.log_ingest("zoom", csv_path.name, accepted, len(orphans))
            total_accepted += accepted; total_orphans += len(orphans)
            print(f"  zoom   {csv_path.name}: +{accepted} events, {len(orphans)} unresolved")

    slack_dir = root / "slack"
    if slack_dir.is_dir():
        raws = slack.read(slack_dir)
        events, orphans = resolve(raws, idmap)
        accepted = store.add_events(events)
        store.add_unresolved(orphans)
        store.log_ingest("slack", str(slack_dir), accepted, len(orphans))
        total_accepted += accepted; total_orphans += len(orphans)
        print(f"  slack  {slack_dir}: +{accepted} events, {len(orphans)} unresolved")

    sheet = root / "assignments.csv"
    if sheet.is_file():
        as_of = dt.datetime.fromisoformat(args.as_of) if args.as_of else None
        if as_of and as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=dt.timezone.utc)
        raws = sheets.read(sheet, as_of=as_of)
        events, orphans = resolve(raws, idmap)
        accepted = store.add_events(events)
        store.add_unresolved(orphans)
        store.log_ingest("sheets", sheet.name, accepted, len(orphans))
        total_accepted += accepted; total_orphans += len(orphans)
        print(f"  sheets {sheet.name}: +{accepted} events, {len(orphans)} unresolved")

    print(f"total: +{total_accepted} new events, {total_orphans} unresolved")
    store.close()
    return 0


def cmd_report(args) -> int:
    defs = config.load(args.config)
    store = Store(args.db)
    result = metrics.compute(store, defs)
    html = report_mod.render(result, defs)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} "
          f"({len(result.fellows)} fellows, {len(result.weeks)} weeks, "
          f"{len(result.withheld)} metrics withheld)")
    store.close()
    return 0


def cmd_status(args) -> int:
    """Prints the definition ledger. Answers 'what can this tool tell us yet?'"""
    defs = config.load(args.config)
    print(f"{defs.meta.get('program')} -- cohort {defs.meta.get('cohort_label')}\n")
    print("CONFIRMED")
    for t in defs.confirmed_terms():
        print(f"  {t.name:<24} {t.owner} ({t.decided_on})")
    print("\nUNDEFINED -- metrics withheld")
    for t in defs.undefined_terms():
        print(f"  {t.name:<24} needs: {t.needed_from}")
        print(f"  {'':<24} blocks: {', '.join(t.blocks) or '-'}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="cufa", description=__doc__)
    p.add_argument("--config", default=None, help="path to definitions.toml")
    p.add_argument("--db", default="out/cif.db")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("ingest", help="read source exports into the warehouse")
    i.add_argument("--roster", required=True)
    i.add_argument("--fixtures", required=True, help="directory holding zoom/, slack/, assignments.csv")
    i.add_argument("--as-of", default=None, help="ISO datetime; assignments due before this count as missed")
    i.set_defaults(func=cmd_ingest)

    r = sub.add_parser("report", help="render the HTML report")
    r.add_argument("--out", default="out/report.html")
    r.set_defaults(func=cmd_report)

    s = sub.add_parser("status", help="show which terms are defined")
    s.set_defaults(func=cmd_status)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
