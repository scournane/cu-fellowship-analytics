"""The cross-platform task runner.

The parts worth testing are the ones that differ between platforms and that
nobody on a Linux CI box would notice were wrong: where the venv puts its
executables, and whether every documented command actually exists.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_tasks():
    """Import tasks.py by path — it lives at the repo root, not in the package."""
    spec = importlib.util.spec_from_file_location("_tasks_under_test", ROOT / "tasks.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


tasks = _load_tasks()


def test_venv_paths_match_the_platform(monkeypatch):
    """Scripts\\name.exe on Windows, bin/name on POSIX.

    Getting this wrong is the single most common way a script that claims to be
    cross-platform turns out not to be, and it cannot be caught by running the
    suite on one OS unless the branch is exercised directly.
    """
    monkeypatch.setattr(tasks, "IS_WINDOWS", True)
    windows_path = tasks.venv_bin("python")
    assert windows_path.parent.name == "Scripts"
    assert windows_path.name == "python.exe"

    monkeypatch.setattr(tasks, "IS_WINDOWS", False)
    posix_path = tasks.venv_bin("python")
    assert posix_path.parent.name == "bin"
    assert posix_path.name == "python"


def test_every_task_in_help_is_runnable():
    """The help text and the dispatch table must not drift apart."""
    documented = {
        line.split()[2]
        for line in tasks.HELP.splitlines()
        if line.strip().startswith("python tasks.py ")
    }
    assert documented, "help text produced no commands — the parser above is wrong"
    missing = documented - set(tasks.TASKS) - {"help"}
    assert not missing, f"documented but not implemented: {sorted(missing)}"


def test_every_make_target_forwards_to_a_real_task():
    """`make demo` and `python tasks.py demo` must stay the same thing."""
    makefile = (ROOT / "Makefile").read_text()
    forwarded = {
        line.split(":")[0].strip()
        for line in makefile.splitlines()
        if "tasks.py" in line and ":" in line and not line.strip().startswith("#")
    }
    forwarded.discard("help")
    unknown = {t for t in forwarded if t and t not in tasks.TASKS}
    assert not unknown, f"Makefile forwards to tasks that do not exist: {sorted(unknown)}"

    # And the reverse: every task should be reachable from make on POSIX.
    # Join backslash continuations first — .PHONY spans several lines.
    joined = makefile.replace("\\\n", " ")
    declared = " ".join(ln for ln in joined.splitlines() if ln.startswith(".PHONY"))
    for task in tasks.TASKS:
        assert task in declared, f"{task} is not declared .PHONY in the Makefile"


def test_unknown_task_exits_nonzero_and_lists_the_real_ones(capsys):
    assert tasks.main(["not-a-real-task"]) == 2
    err = capsys.readouterr().err
    assert "unknown task" in err
    assert "python tasks.py demo" in err


def test_help_is_the_default(capsys):
    assert tasks.main([]) == 0
    assert "python tasks.py setup" in capsys.readouterr().out


def test_install_hints_cover_every_platform_we_claim_to_support():
    for tool, hints in tasks.INSTALL_HINTS.items():
        for system in ("Windows", "Darwin", "Linux"):
            assert system in hints, f"{tool} has no install hint for {system}"
            assert hints[system].strip(), f"{tool}/{system} hint is empty"


def test_demo_env_pins_the_fake_google_client():
    """A stray demo invocation must not be able to reach Google."""
    assert tasks.DEMO_ENV["CUFA_FAKE_GOOGLE"] == "1"
    assert "fake_google_state" in tasks.DEMO_ENV["CUFA_FAKE_GOOGLE_STATE"]


def test_run_forces_utf8_in_children(monkeypatch):
    """Child processes print em-dashes on a legacy Windows console."""
    captured = {}

    def fake_run(argv, **kwargs):
        captured["env"] = kwargs["env"]

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr(tasks.subprocess, "run", fake_run)
    tasks.run(["anything"])
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"
    assert captured["env"]["PYTHONUTF8"] == "1"
