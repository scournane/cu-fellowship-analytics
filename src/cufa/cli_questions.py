"""``cufa questions …`` — Part A's exit-ticket questions from the terminal.

Kept in its own module so ``cli.py`` stays about the forms pipeline. Every
command is a thin call into ``question_sets``, the same functions the console
uses, so the terminal is a complete alternative to the web editor and nothing
here makes a decision of its own:

    cufa questions show         --cohort C | --session S [--json]
    cufa questions seed-default --cohort C [--file PATH]
    cufa questions import-form  --cohort C --form ID|URL [--dry-run] [--write PATH]
    cufa questions set          --cohort C | --session S  --file PATH
    cufa questions revert       --session S
    cufa questions export       --cohort C | --session S [--out PATH]

``export`` writes the same shape ``set`` reads, so "export, edit in a text
editor, set" is a supported way to change a long form.

Database work commits before anything is printed, for the reason ``cli.py``
gives: printing inside the transaction means a closed stdout (``| head``)
aborts between the write and the commit.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from .db import connection
from .errors import CufaError


def _actor() -> str:
    """Who ran the command, for ``created_by``. Provenance, not authentication."""
    try:
        return f"cli:{getpass.getuser()}"
    except Exception:  # noqa: BLE001 - a missing login name is not worth failing over
        return "cli"


def _session_id(value: str) -> str:
    try:
        return str(uuid.UUID(str(value).strip()))
    except (ValueError, AttributeError, TypeError):
        raise CufaError(
            f"{value!r} is not a session id. Ids are UUIDs — run `cufa session list` "
            "to see them."
        ) from None


def _read_json(path: str) -> Any:
    file_path = Path(path)
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CufaError(f"No such file: {file_path}") from None
    except json.JSONDecodeError as exc:
        raise CufaError(f"{file_path} is not valid JSON: {exc}") from None
    if isinstance(data, dict):
        # The config file's human-only fields.
        data = {k: v for k, v in data.items() if k not in ("status", "_comment")}
    return data


def _exportable(content: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": content.get("schema_version", 1),
        "title": content.get("title", ""),
        "description": content.get("description", ""),
        "questions": content.get("questions") or [],
    }


def _write_json(content: dict[str, Any], path: str | None) -> None:
    text = json.dumps(_exportable(content), indent=2, ensure_ascii=False) + "\n"
    if path:
        Path(path).write_text(text, encoding="utf-8")
        print(f"wrote {path}")
    else:
        sys.stdout.write(text)


def _describe_question(index: int, question: dict[str, Any]) -> list[str]:
    qtype = question.get("type")
    required = " *" if question.get("required") else ""
    lines = [f"  {index:>2}. [{qtype}] {question.get('title') or '(untitled)'}{required}"
             f"   ({question.get('key')})"]
    if question.get("description"):
        lines.append(f"      {question['description']}")
    if question.get("options"):
        options = list(question["options"])
        if question.get("allow_other"):
            options.append("Other…")
        lines.append(f"      options: {' | '.join(options)}")
    if question.get("scale"):
        scale = question["scale"]
        labels = ""
        if scale.get("low_label") or scale.get("high_label"):
            labels = f"  ({scale.get('low_label', '')} … {scale.get('high_label', '')})"
        lines.append(f"      scale: {scale.get('low')}–{scale.get('high')}{labels}")
    return lines


def _print_set(qset: Any, heading: str) -> None:
    print(heading)
    print(f"  version   {qset.label}  (id {qset.question_set_id})")
    print(f"  source    {qset.source}{' — ' + qset.source_ref if qset.source_ref else ''}")
    print(f"  saved     {qset.created_at} by {qset.created_by or '(unknown)'}")
    if qset.based_on_id:
        print(f"  based on  {qset.based_on_id}")
    print(f"  title     {qset.content.get('title')}")
    print(f"  questions {qset.question_count} to answer (* = required)")
    for index, question in enumerate(qset.questions, start=1):
        for line in _describe_question(index, question):
            print(line)


def _print_warnings(warnings: list[str]) -> None:
    for warning in warnings:
        print(f"  note: {warning}")


def cmd_questions(args: argparse.Namespace) -> int:
    try:
        return _run(args)
    except LookupError as exc:
        # An unknown session id. Said plainly rather than as a traceback, which
        # would read as the tool being broken rather than the argument wrong.
        raise CufaError(str(exc).strip("'\"")) from None


def _run(args: argparse.Namespace) -> int:
    from . import part_a_questions, question_sets

    action = args.questions_action
    cohort = getattr(args, "cohort", None)
    session = _session_id(args.session) if getattr(args, "session", None) else None

    if action == "show":
        with connection() as conn:
            if session:
                locked, reason = question_sets.is_locked(conn, session)
                provisioned = question_sets.provisioned_for_session(conn, session)
                try:
                    scope, qset = question_sets.resolve_for_session(conn, session)
                except CufaError as exc:
                    scope, qset, missing = None, None, str(exc)
                else:
                    missing = None
            else:
                qset = question_sets.current_default(conn, cohort)
                scope, locked, reason, provisioned, missing = "default", False, None, None, None
                if qset is None:
                    missing = question_sets.missing_message(cohort)
        if args.json:
            if qset is None:
                raise CufaError(missing or "No question set.")
            _write_json(qset.content, None)
            return 0
        if qset is None:
            print(missing, file=sys.stderr)
            return 1
        where = "this session's own override" if scope == "session" else "the cohort default"
        _print_set(qset, f"Part A exit ticket — {where}")
        if session:
            print()
            print("  editing   " + ("LOCKED — " + (reason or "") if locked else "open until the form is published"))
            if provisioned is not None and provisioned.question_set_id != qset.question_set_id:
                print(
                    f"  NOTE      the published form was built from {provisioned.label}; "
                    f"{qset.label} applies only to forms not yet published."
                )
        return 0

    if action == "seed-default":
        with connection() as conn:
            before = question_sets.current_default(conn, cohort)
            qset = question_sets.seed_default_from_file(
                conn, cohort, args.file, created_by=_actor()
            )
        unchanged = before is not None and before.question_set_id == qset.question_set_id
        verb = "unchanged — already" if unchanged else "seeded as"
        print(f"cohort {cohort}: Part A default {verb} {qset.label} "
              f"({qset.question_count} questions, from {qset.source_ref})")
        _print_warnings(part_a_questions.validate(qset.content).warnings)
        return 0

    if action == "import-form":
        from .google.factory import get_client

        with connection() as conn:
            before = question_sets.current_default(conn, cohort)
            content, warnings = question_sets.import_from_form(
                get_client(conn), conn, cohort, args.form,
                created_by=_actor(), dry_run=args.dry_run,
            )
            after = None if args.dry_run else question_sets.current_default(conn, cohort)
        if args.write:
            _write_json(content, args.write)
        count = len(part_a_questions.answerable(content))
        if args.dry_run:
            print(f"dry run — nothing saved. The form would become the default with "
                  f"{count} question(s):")
            for index, question in enumerate(content.get("questions") or [], start=1):
                for line in _describe_question(index, question):
                    print(line)
        elif after is not None:
            unchanged = before is not None and before.question_set_id == after.question_set_id
            print(f"cohort {cohort}: Part A default "
                  f"{'unchanged — already' if unchanged else 'imported as'} {after.label} "
                  f"({count} questions)")
        _print_warnings(warnings)
        return 0

    if action == "set":
        content = _read_json(args.file)
        warnings = part_a_questions.validate(content).warnings
        with connection() as conn:
            if session:
                before = question_sets.current_override(conn, session)
                qset = question_sets.save_override(
                    conn, session, content, created_by=_actor(), source="cli"
                )
            else:
                before = question_sets.current_default(conn, cohort)
                qset = question_sets.save_default(
                    conn, cohort, content, created_by=_actor(), source="cli",
                    source_ref=str(args.file),
                )
        unchanged = before is not None and before.question_set_id == qset.question_set_id
        target = f"session {session}" if session else f"cohort {cohort} default"
        print(f"{target}: {'unchanged — already' if unchanged else 'saved as'} {qset.label} "
              f"({qset.question_count} questions)")
        _print_warnings(warnings)
        return 0

    if action == "revert":
        with connection() as conn:
            before = question_sets.current_override(conn, session)
            question_sets.revert_override(conn, session, by=_actor())
        if before is None:
            print(f"session {session} already uses the cohort default; nothing to revert.")
        else:
            print(f"session {session}: {before.label} retired; the session uses the cohort "
                  "default again. The override stays in the history.")
        return 0

    if action == "export":
        with connection() as conn:
            if session:
                _scope, qset = question_sets.resolve_for_session(conn, session)
            else:
                qset = question_sets.current_default(conn, cohort)
                if qset is None:
                    raise CufaError(question_sets.missing_message(cohort))
        _write_json(qset.content, args.out)
        return 0

    raise CufaError(f"unknown questions action {action!r}")


def add_parsers(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register ``cufa questions`` on cli.py's top-level subparsers."""
    p = sub.add_parser(
        "questions",
        help="Part A exit-ticket questions: show, seed, import, set, revert, export",
        description=(
            "Part A is the exit ticket: questions CU staff write. Each cohort has a "
            "default; any session may carry its own override until its form is "
            "published. Placeholders {lesson} and {session_title} are filled per session."
        ),
    )
    qp = p.add_subparsers(dest="questions_action", required=True)

    def scope(parser: argparse.ArgumentParser, *, session_only: bool = False) -> None:
        if session_only:
            parser.add_argument("--session", required=True, help="session id")
            return
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--cohort", help="the cohort default")
        group.add_argument("--session", help="one session (its override, else the default)")

    q = qp.add_parser("show", help="print the questions a cohort or session uses")
    scope(q)
    q.add_argument("--json", action="store_true", help="print the set as JSON")

    q = qp.add_parser("seed-default", help="load the cohort default from a JSON file")
    q.add_argument("--cohort", required=True)
    q.add_argument("--file", default=None,
                   help="default: config/part_a_default_questions.json (the week-1 exit ticket)")

    q = qp.add_parser("import-form", help="make an existing Google Form the cohort default")
    q.add_argument("--cohort", required=True)
    q.add_argument("--form", required=True, help="form id or editor URL")
    q.add_argument("--dry-run", action="store_true", help="read and show; save nothing")
    q.add_argument("--write", default=None, help="also write the imported set to this JSON file")

    q = qp.add_parser("set", help="save a JSON file as the cohort default or a session override")
    scope(q)
    q.add_argument("--file", required=True)

    q = qp.add_parser("revert", help="send a session back to the cohort default")
    scope(q, session_only=True)

    q = qp.add_parser("export", help="write the current set as JSON (the shape `set` reads)")
    scope(q)
    q.add_argument("--out", default=None, help="file to write (default: stdout)")

    p.set_defaults(func=cmd_questions)


__all__ = ["add_parsers", "cmd_questions"]
