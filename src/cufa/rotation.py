"""The rotating slot: which question field 4 asks in a given week.

Field 4 changes weekly and the other five do not. That is the survey-fatigue
mitigation, and it is load-bearing rather than decorative: going from three
questions to four drops completion by 18%, response rates fall roughly 60% past
eight minutes, and fatigued respondents straight-line about 30% more. Rotating
one slot collects all three dimensions across ten weeks while no single fellow
ever faces more than four questions in one sitting.

Two things this module refuses to do, both for the same reason — a silent
substitution destroys the signal it substitutes for:

* **It never derives the week from a calendar date.** The week comes from an
  explicit ``session.week_index`` typed into the console. Sessions get
  rescheduled, skipped and doubled up; a calendar-derived week desynchronises
  the whole rotation the first time that happens, and nothing announces it.
* **It never falls back to a generic question** when a teacher-question week has
  no teacher question set. The teacher's own question is the only genuinely
  unfakeable item on the form — it depends on content only someone present could
  know — so a generic stand-in would look like the same data and be worth
  nothing. Provisioning is blocked instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import CufaError

#: The three dimensions the rotating slot cycles through.
TEACHER_QUESTION = "teacher_question"
MUDDIEST_POINT = "muddiest_point"
APPLICATION = "application"

ROTATING_KINDS: tuple[str, ...] = (TEACHER_QUESTION, MUDDIEST_POINT, APPLICATION)

DEFAULT_CONFIG_PATH = "config/rotation.json"


class RotationConfigError(CufaError):
    """The rotation schedule is malformed.

    Raised at load time rather than at provisioning time. A gap or an overlap in
    the schedule is a configuration mistake that would otherwise surface halfway
    through a batch of forms, with some of them already created.
    """


class TeacherQuestionMissing(CufaError):
    """A teacher-question week has no question written for it.

    Blocks provisioning by design. See the module docstring.
    """


@dataclass(frozen=True)
class ResolvedSlot:
    """What field 4 asks for one session, and where the wording came from."""

    kind: str
    text: str
    week_index: int
    #: The week the schedule was actually consulted for. Differs from
    #: ``week_index`` only when the schedule wrapped.
    schedule_week: int

    @property
    def wrapped(self) -> bool:
        return self.schedule_week != self.week_index


@dataclass(frozen=True)
class RotationConfig:
    """A validated schedule, owned and edited by the Director of Programs."""

    version: str
    owner: str
    status: str
    #: week number -> rotating kind, densely covering 1..weeks
    by_week: dict[int, str]
    fixed_text: dict[str, str]
    wrap: bool
    source: str = DEFAULT_CONFIG_PATH

    @property
    def weeks(self) -> int:
        """How many weeks the schedule covers before it wraps."""
        return len(self.by_week)

    def schedule_week_for(self, week_index: int) -> int:
        """Map an arbitrary week onto a week the schedule actually covers.

        With ``wrap`` true the cycle simply repeats: week 11 asks what week 1
        asked, week 12 what week 2 asked. With ``wrap`` false a week past the
        end of the schedule is an error rather than a guess.
        """
        if week_index < 1:
            raise RotationConfigError(
                f"week_index must be 1 or greater, got {week_index}. Week numbering "
                f"starts at 1 because that is how the schedule in {self.source} "
                "is written."
            )
        if week_index in self.by_week:
            return week_index
        if not self.wrap:
            raise RotationConfigError(
                f"week {week_index} is past the end of the {self.weeks}-week schedule "
                f'in {self.source}, and "wrap" is false. Either extend the schedule '
                'or set "wrap": true.'
            )
        # 1-based modular arithmetic: week N maps onto ((N-1) mod weeks) + 1.
        return ((week_index - 1) % self.weeks) + 1

    def kind_for(self, week_index: int) -> str:
        """The rotating kind for a week, without resolving its text."""
        return self.by_week[self.schedule_week_for(week_index)]

    def resolve(
        self,
        week_index: int,
        *,
        teacher_question: str | None = None,
        session_label: str = "this session",
    ) -> ResolvedSlot:
        """The exact question text field 4 shows in ``week_index``.

        ``teacher_question`` is consulted only on a teacher-question week, and
        its absence there is fatal rather than substituted.
        """
        schedule_week = self.schedule_week_for(week_index)
        kind = self.by_week[schedule_week]

        if kind == TEACHER_QUESTION:
            text = (teacher_question or "").strip()
            if not text:
                raise TeacherQuestionMissing(
                    f"Week {week_index} of the rotation asks the teacher's own "
                    f"question, and {session_label} has none set.\n"
                    "\n"
                    "Provisioning is blocked rather than substituting a generic "
                    "question. The teacher's question is the only item on this form "
                    "that depends on content someone had to be present to know — a "
                    "stand-in would produce data that looks the same and means "
                    "nothing.\n"
                    "\n"
                    "Set “Teacher's question for this week” on the session "
                    "and provision again."
                )
            return ResolvedSlot(kind, text, week_index, schedule_week)

        text = (self.fixed_text.get(kind) or "").strip()
        if not text:
            raise RotationConfigError(
                f"{self.source} has no fixed_text entry for {kind!r}, but week "
                f"{schedule_week} of the schedule calls for it."
            )
        return ResolvedSlot(kind, text, week_index, schedule_week)

    def preview(
        self,
        first_week: int,
        count: int,
        *,
        teacher_questions: dict[int, str] | None = None,
    ) -> list[dict[str, Any]]:
        """What the next ``count`` weeks will ask, for the console's preview.

        Never raises on a missing teacher question: the entire point of the
        preview is to show which weeks still need one written.
        """
        supplied_by_week = teacher_questions or {}
        rows: list[dict[str, Any]] = []
        for offset in range(max(0, count)):
            week = first_week + offset
            try:
                schedule_week = self.schedule_week_for(week)
            except RotationConfigError as exc:
                rows.append(
                    {
                        "week_index": week,
                        "kind": None,
                        "text": None,
                        "needs_teacher_question": False,
                        "wrapped": False,
                        "schedule_week": None,
                        "error": str(exc),
                    }
                )
                continue

            kind = self.by_week[schedule_week]
            supplied = (supplied_by_week.get(week) or "").strip()
            text = supplied if kind == TEACHER_QUESTION else self.fixed_text.get(kind)
            rows.append(
                {
                    "week_index": week,
                    "kind": kind,
                    "text": text or None,
                    "needs_teacher_question": kind == TEACHER_QUESTION and not supplied,
                    "wrapped": schedule_week != week,
                    "schedule_week": schedule_week,
                    "error": None,
                }
            )
        return rows

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "owner": self.owner,
            "status": self.status,
            "weeks": self.weeks,
            "wrap": self.wrap,
            "source": self.source,
            "by_week": {str(week): kind for week, kind in sorted(self.by_week.items())},
            "fixed_text": dict(self.fixed_text),
        }


def parse_rotation(
    payload: dict[str, Any], *, source: str = DEFAULT_CONFIG_PATH
) -> RotationConfig:
    """Validate a schedule and refuse a malformed one.

    Every week from 1 to N must appear exactly once across the three lists. A
    gap means some week has no question at all; an overlap means two kinds claim
    the same week and whichever is read last silently wins. Both are caught
    here, at load, rather than mid-provisioning with forms already made.
    """
    if not isinstance(payload, dict):
        raise RotationConfigError(f"{source} must contain a JSON object.")

    schedule = payload.get("schedule")
    if not isinstance(schedule, dict) or not schedule:
        raise RotationConfigError(
            f'{source} has no "schedule" object. It maps each rotating kind '
            f"({', '.join(ROTATING_KINDS)}) to the week numbers it covers."
        )

    unknown = sorted(set(schedule) - set(ROTATING_KINDS))
    if unknown:
        raise RotationConfigError(
            f"{source} schedule names unknown kind(s) {unknown}. "
            f"Valid kinds are: {', '.join(ROTATING_KINDS)}."
        )

    by_week: dict[int, str] = {}
    duplicates: list[str] = []
    for kind, weeks in schedule.items():
        if not isinstance(weeks, list):
            raise RotationConfigError(
                f"{source} schedule.{kind} must be a list of week numbers."
            )
        for raw in weeks:
            if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
                raise RotationConfigError(
                    f"{source} schedule.{kind} contains {raw!r}; week numbers are "
                    "whole numbers of 1 or more."
                )
            if raw in by_week:
                duplicates.append(
                    f"week {raw} is claimed by both {by_week[raw]} and {kind}"
                )
            by_week[raw] = kind

    if duplicates:
        raise RotationConfigError(
            f"{source} schedule overlaps: "
            + "; ".join(sorted(duplicates))
            + ". Each week must be claimed exactly once, or which question gets "
            "asked depends on dictionary ordering."
        )

    covered = sorted(by_week)
    expected = list(range(1, len(covered) + 1))
    if covered != expected:
        gaps = sorted(set(expected) - set(covered))
        raise RotationConfigError(
            f"{source} schedule covers weeks {covered}, which is not a complete run "
            f"from 1 to {len(covered)}. Missing: {gaps}. A gap means some week has "
            "no rotating question at all, and provisioning would fail for that week "
            "only, weeks later."
        )

    fixed_text = payload.get("fixed_text") or {}
    if not isinstance(fixed_text, dict):
        raise RotationConfigError(f'{source} "fixed_text" must be an object.')

    scheduled_kinds = set(by_week.values())
    for kind in (MUDDIEST_POINT, APPLICATION):
        if kind in scheduled_kinds and not str(fixed_text.get(kind, "")).strip():
            raise RotationConfigError(
                f"{source} schedules {kind} but gives it no fixed_text. That is the "
                "exact wording fellows see, so it cannot be defaulted here."
            )

    return RotationConfig(
        version=str(payload.get("version") or "0"),
        owner=str(payload.get("owner") or "(unowned)"),
        status=str(payload.get("status") or "(no status)"),
        by_week=by_week,
        fixed_text={str(k): str(v) for k, v in fixed_text.items()},
        wrap=bool(payload.get("wrap", True)),
        source=source,
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_rotation(path: str | Path | None = None) -> RotationConfig:
    """Read and validate the schedule from disk."""
    resolved = Path(path) if path else _repo_root() / DEFAULT_CONFIG_PATH
    if not resolved.exists():
        raise RotationConfigError(
            f"No rotation schedule at {resolved}.\n"
            "\n"
            "It decides which question the rotating slot asks each week and is "
            "owned by the Director of Programs, not by this code. Copy the one in "
            "the repo's config/ directory, or see docs/setup/part-b-form.md."
        )
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RotationConfigError(f"{resolved} is not valid JSON: {exc}") from exc
    return parse_rotation(payload, source=str(resolved))


# Cached by modification time, not forever. The Director of Programs owns this
# file and edits it directly; a schedule change that needed the console
# restarted before it took effect would be indistinguishable from a schedule
# change that silently did not save.
_CACHE: dict[str, tuple[int, RotationConfig]] = {}


def _mtime(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return -1


def get_rotation(path: str | Path | None = None) -> RotationConfig:
    """The rotation schedule, re-read whenever the file on disk has changed."""
    resolved = Path(path) if path else _repo_root() / DEFAULT_CONFIG_PATH
    key = str(resolved)
    stamp = _mtime(resolved)
    hit = _CACHE.get(key)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    value = load_rotation(resolved)
    _CACHE[key] = (stamp, value)
    return value


def reset_rotation_cache() -> None:
    """Drop the cache. Used by tests that write their own schedule."""
    _CACHE.clear()


__all__ = [
    "APPLICATION",
    "MUDDIEST_POINT",
    "ROTATING_KINDS",
    "TEACHER_QUESTION",
    "ResolvedSlot",
    "RotationConfig",
    "RotationConfigError",
    "TeacherQuestionMissing",
    "get_rotation",
    "load_rotation",
    "parse_rotation",
    "reset_rotation_cache",
]
