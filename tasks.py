#!/usr/bin/env python3
"""Cross-platform task runner — the Windows-and-everything-else equivalent of `make`.

`make` is not present on a stock Windows install, and the Makefile's recipes are
bash. Rather than ask Windows users to install a POSIX toolchain to run a
Python project, every target is reimplemented here in the standard library.

    python tasks.py doctor    # what is installed, what is missing, how to fix it
    python tasks.py setup
    python tasks.py demo

The Makefile is kept as a thin wrapper that forwards to this file, so `make
demo` and `python tasks.py demo` run exactly the same code and cannot drift.

Run this with any Python 3.11+, before a virtualenv exists — `setup` creates
one. Everything afterwards uses the interpreter inside `.venv`.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
FIXTURES = ROOT / "fixtures"

COHORT = os.environ.get("COHORT", "demo")
SHEET_TZ = os.environ.get("SHEET_TZ", "America/New_York")
PORT = os.environ.get("PORT", "8000")

IS_WINDOWS = os.name == "nt"

# Everything below runs against the fake Google client. Set here rather than per
# command so that a stray invocation cannot reach Google by forgetting a flag.
DEMO_ENV = {
    "CUFA_FAKE_GOOGLE": "1",
    "CUFA_FAKE_GOOGLE_STATE": str(FIXTURES / "fake_google_state.json"),
}


# ---------------------------------------------------------------------------
# platform-aware paths
# ---------------------------------------------------------------------------

def venv_bin(name: str) -> Path:
    """Path to an executable inside .venv, on either layout.

    Windows puts scripts in Scripts\\ with an .exe suffix; POSIX uses bin/.
    Getting this wrong is the single most common way a "cross-platform" script
    turns out not to be.
    """
    if IS_WINDOWS:
        return VENV / "Scripts" / f"{name}.exe"
    return VENV / "bin" / name


def venv_python() -> Path:
    return venv_bin("python")


def have_venv() -> bool:
    return venv_python().exists()


# ---------------------------------------------------------------------------
# running things
# ---------------------------------------------------------------------------

class TaskError(RuntimeError):
    """A task failed in a way the user needs to read, not a traceback."""


def run(
    argv: list[str | Path],
    *,
    env: dict[str, str] | None = None,
    check: bool = True,
    capture: bool = False,
    quiet: bool = False,
) -> subprocess.CompletedProcess:
    """Run a command with no shell. No shell means no quoting differences."""
    merged = os.environ.copy()
    merged.update(DEMO_ENV)
    if env:
        merged.update(env)
    # Force UTF-8 in the child. The report and the fixtures contain em-dashes
    # and curly quotes, and a legacy Windows console encoding turns those into
    # a UnicodeEncodeError that looks like a crash in the pipeline.
    merged.setdefault("PYTHONIOENCODING", "utf-8")
    merged.setdefault("PYTHONUTF8", "1")

    result = subprocess.run(
        [str(a) for a in argv],
        env=merged,
        cwd=ROOT,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE if capture or quiet else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if check and result.returncode != 0:
        if capture and result.stderr:
            sys.stderr.write(result.stderr)
        raise TaskError(f"command failed ({result.returncode}): {' '.join(str(a) for a in argv)}")
    return result


def cufa(*args: str, check: bool = True, quiet: bool = False) -> subprocess.CompletedProcess:
    """Invoke the CLI through the venv's interpreter.

    `python -m cufa` rather than the `cufa` script: on Windows the console
    script can lag behind an editable reinstall, and `-m` always resolves to the
    package actually importable in that environment.
    """
    return run([venv_python(), "-m", "cufa", *args], check=check, quiet=quiet)


def script(name: str, *args: str, quiet: bool = False) -> subprocess.CompletedProcess:
    return run([venv_python(), ROOT / "scripts" / name, *args], quiet=quiet)


def banner(text: str) -> None:
    print(f"\n== {text} " + "=" * max(0, 70 - len(text)))


# ---------------------------------------------------------------------------
# environment checks
# ---------------------------------------------------------------------------

INSTALL_HINTS = {
    "docker": {
        "Windows": "Install Docker Desktop: https://docs.docker.com/desktop/install/windows-install/\n"
                   "  Then start it and wait for the whale icon to stop animating.",
        "Darwin": "brew install --cask docker   (then launch Docker Desktop)",
        "Linux":  "https://docs.docker.com/engine/install/  then: sudo systemctl start docker",
    },
    "supabase": {
        "Windows": "Scoop:  scoop bucket add supabase https://github.com/supabase/scoop-bucket.git\n"
                   "          scoop install supabase\n"
                   "  or npm: npm install -g supabase\n"
                   "  or download supabase_windows_amd64.zip from\n"
                   "          https://github.com/supabase/cli/releases and put supabase.exe on PATH",
        "Darwin":  "brew install supabase/tap/supabase",
        "Linux":   "npm install -g supabase\n"
                   "  or a binary from https://github.com/supabase/cli/releases",
    },
}


def hint(tool: str) -> str:
    return INSTALL_HINTS[tool].get(platform.system(), INSTALL_HINTS[tool]["Linux"])


def check_python() -> tuple[bool, str]:
    ok = sys.version_info >= (3, 11)
    return ok, f"Python {platform.python_version()}" + ("" if ok else "  (3.11+ required)")


def check_docker() -> tuple[bool, str]:
    if not shutil.which("docker"):
        return False, "not installed"
    probe = run(["docker", "info"], check=False, capture=True)
    if probe.returncode != 0:
        return False, "installed but not running"
    return True, "running"


def check_supabase() -> tuple[bool, str]:
    if not shutil.which("supabase"):
        return False, "not installed"
    probe = run(["supabase", "--version"], check=False, capture=True)
    return True, (probe.stdout or "").strip() or "installed"


def check_deps() -> tuple[bool, str]:
    if not have_venv():
        return False, ".venv does not exist yet"
    probe = run(
        [venv_python(), "-c", "import cufa, fastapi, psycopg; print(cufa.__version__)"],
        check=False,
        capture=True,
    )
    if probe.returncode != 0:
        return False, "dependencies not installed into .venv"
    return True, f"cufa {(probe.stdout or '').strip()} installed in .venv"


def require(tool: str) -> None:
    ok, detail = {"docker": check_docker, "supabase": check_supabase}[tool]()
    if not ok:
        raise TaskError(f"{tool}: {detail}\n\n{hint(tool)}\n")


# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------

def task_doctor() -> int:
    """Report exactly what is present and what is missing."""
    print("Civic Innovators check-in — environment check")
    print("=" * 62)
    checks = [
        ("Python 3.11+", check_python),
        ("Dependencies", check_deps),
        ("Docker", check_docker),
        ("Supabase CLI", check_supabase),
    ]
    missing: list[str] = []
    for label, fn in checks:
        ok, detail = fn()
        print(f"  {'ok  ' if ok else 'MISS'}  {label:<16} {detail}")
        if not ok:
            missing.append(label)

    print("=" * 62)
    if not missing:
        print("Everything is present. Next:  python tasks.py demo")
        return 0

    print("\nTo fix:")
    if "Python 3.11+" in missing:
        print("\n  Install Python 3.11 or newer from https://www.python.org/downloads/")
    if "Dependencies" in missing:
        print("\n  python tasks.py setup")
    if "Docker" in missing:
        print(f"\n  Docker — the local database runs in it:\n  {hint('docker')}")
    if "Supabase CLI" in missing:
        print(f"\n  Supabase CLI:\n  {hint('supabase')}")
    print()
    return 1


def task_setup() -> int:
    ok, detail = check_python()
    if not ok:
        raise TaskError(f"{detail}\nInstall Python 3.11+ from https://www.python.org/downloads/")

    if not have_venv():
        print(f"creating virtualenv at {VENV}")
        run([sys.executable, "-m", "venv", str(VENV)])

    print("installing dependencies (this takes a minute the first time)")
    run([venv_python(), "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    # An editable install of this project. There is no requirements.txt: the
    # dependency list lives in pyproject.toml, which is also what pip reads.
    run([venv_python(), "-m", "pip", "install", "--quiet", "-e", ".[dev]"])
    print(f"dependencies installed into {VENV}")

    require("docker")
    print("docker: running")
    require("supabase")
    print(f"supabase: {check_supabase()[1]}")

    if not (ROOT / "supabase" / "config.toml").exists():
        run(["supabase", "init"])
    print("supabase project: initialised")

    activate = (
        r".venv\Scripts\Activate.ps1" if IS_WINDOWS else "source .venv/bin/activate"
    )
    print(
        f"\nSetup complete.\n"
        f"  Next:            python tasks.py demo\n"
        f"  To use `cufa` directly, activate the venv:  {activate}"
    )
    return 0


def task_db_up() -> int:
    require("docker")
    require("supabase")
    status = run(["supabase", "status"], check=False, capture=True)
    if status.returncode != 0:
        print("starting the local Supabase stack (the first run pulls images)")
        run(["supabase", "start"])
    print("postgres: postgresql://postgres:postgres@localhost:54322/postgres")
    print("studio:   http://localhost:54323")
    return 0


def task_db_reset() -> int:
    task_db_up()
    run(["supabase", "db", "reset"])
    return 0


def task_db_down() -> int:
    require("supabase")
    run(["supabase", "stop"], check=False)
    return 0


def task_studio() -> int:
    print("Supabase Studio (visual table browser): http://localhost:54323")
    return 0


def task_fixtures() -> int:
    script("generate_fixtures.py", "--out", str(FIXTURES))
    return 0


def task_demo() -> int:
    task_db_reset()
    task_fixtures()

    state = Path(DEMO_ENV["CUFA_FAKE_GOOGLE_STATE"])
    state.unlink(missing_ok=True)

    banner("1. roster and sessions")
    cufa("load-roster", "--csv", str(FIXTURES / "roster.csv"), "--cohort", COHORT)
    cufa("load-sessions", "--csv", str(FIXTURES / "sessions.csv"))

    banner("2. one-time Google setup")
    cufa("template", "create")

    print("\n-- provisioning is blocked until the template verifies (trap 2) --------")
    blocked = cufa("template", "verify", check=False, quiet=True)
    if blocked.returncode == 0:
        raise TaskError("UNEXPECTED: the template verified before the manual step")
    print("blocked, as designed: emailCollectionType is not VERIFIED yet")

    script("seed_fake_google.py", "--set-verified")
    cufa("template", "verify")

    banner("3. provision one form per session")
    cufa("provision", "--cohort", COHORT)

    banner("4. the lesson happens")
    script("seed_fake_google.py", "--seed-responses", "--fixtures", str(FIXTURES))
    script("seed_fake_google.py", "--announce", "--fixtures", str(FIXTURES))

    banner("5. pull responses (Forms API path)")
    cufa("pull", "--cohort", COHORT)

    banner("6. import a manually created form (CSV fallback path)")
    cufa("ingest", "part-a", "--csv", str(FIXTURES / "manual_form_export.csv"),
         "--cohort", COHORT, "--sheet-timezone", SHEET_TZ)

    banner("7. adjudicate (tier 1 only; tier 2 skipped)")
    cufa("adjudicate", "--cohort", COHORT, "--no-ai")

    banner("8. report")
    cufa("report", "--cohort", COHORT)

    banner("9. acceptance checks")
    script("verify_demo.py", "--cohort", COHORT, "--fixtures", str(FIXTURES))

    print("\nInspect the data in Supabase Studio: http://localhost:54323")
    print("Re-run the demo — it is idempotent and will report the same numbers.")
    return 0


def task_demo_again() -> int:
    """Re-run the pipeline over the SAME database, to show idempotency."""
    cufa("pull", "--cohort", COHORT)
    cufa("ingest", "part-a", "--csv", str(FIXTURES / "manual_form_export.csv"),
         "--cohort", COHORT, "--sheet-timezone", SHEET_TZ)
    cufa("adjudicate", "--cohort", COHORT, "--no-ai")
    cufa("report", "--cohort", COHORT)
    return 0


def task_demo_ai() -> int:
    if not os.environ.get("GEMINI_API_KEY"):
        print(
            "\nSkipping: GEMINI_API_KEY is not set, so tier 2 cannot run.\n"
            "\n"
            "Set it in .env (see .env.example) and re-run.\n"
            "Nothing else depends on it — the plain demo is the offline path, and\n"
            "there mismatch cases land in needs_review with\n"
            "rule_name='ai_unavailable' rather than being guessed at.\n"
        )
        return 0

    task_demo()
    banner("tier 2 live (only mismatch-in-window cases reach Gemini)")
    cufa("adjudicate", "--cohort", COHORT)
    banner("second pass: every pair is cached, so zero API calls")
    cufa("adjudicate", "--cohort", COHORT)
    cufa("review", "--status", "ai", "--cohort", COHORT)
    return 0


def task_demo_console() -> int:
    task_demo()
    print(f"\nConsole at http://127.0.0.1:{PORT} — fake Google client, zero Google calls.")
    print("Press Ctrl+C to stop.\n")
    cufa("serve", "--port", PORT)
    return 0


def task_test() -> int:
    run([venv_python(), "-m", "pytest"])
    return 0


def task_clean() -> int:
    if shutil.which("supabase"):
        run(["supabase", "stop", "--no-backup"], check=False, capture=True)
    if FIXTURES.exists():
        shutil.rmtree(FIXTURES, ignore_errors=True)
    for pattern in ("**/__pycache__", ".pytest_cache"):
        for path in ROOT.glob(pattern):
            shutil.rmtree(path, ignore_errors=True)
    print("stopped Supabase and removed generated fixtures")
    return 0


TASKS = {
    "doctor": task_doctor,
    "setup": task_setup,
    "demo": task_demo,
    "demo-again": task_demo_again,
    "demo-ai": task_demo_ai,
    "demo-console": task_demo_console,
    "test": task_test,
    "clean": task_clean,
    "db-up": task_db_up,
    "db-reset": task_db_reset,
    "db-down": task_db_down,
    "studio": task_studio,
    "fixtures": task_fixtures,
}

HELP = """Civic Innovators check-in — Part A

  python tasks.py doctor        what is installed, what is missing, how to fix it
  python tasks.py setup         install dependencies, init Supabase, check Docker
  python tasks.py demo          full pipeline on synthetic data, no Google, no Gemini
  python tasks.py demo-again    re-run over the same database, to show idempotency
  python tasks.py demo-ai       same as demo, with tier 2 live (needs GEMINI_API_KEY)
  python tasks.py demo-console  demo data plus the web console
  python tasks.py test          pytest, no network
  python tasks.py clean         stop Supabase, remove generated fixtures

  python tasks.py db-up         start the local Supabase stack
  python tasks.py db-reset      re-apply migrations and seed
  python tasks.py db-down       stop the stack
  python tasks.py studio        print the Studio URL
  python tasks.py fixtures      regenerate synthetic fixtures

On macOS and Linux, `make <target>` forwards to exactly these.
"""


def main(argv: list[str] | None = None) -> int:
    # The report and the fixtures contain em-dashes and curly quotes. A legacy
    # Windows console encoding turns printing those into a UnicodeEncodeError,
    # which looks like the pipeline crashed rather than like a console setting.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("task", nargs="?", default="help")
    args, _extra = parser.parse_known_args(argv)

    if args.task in ("help", "-h", "--help"):
        print(HELP)
        return 0

    if args.task not in TASKS:
        print(f"unknown task {args.task!r}\n", file=sys.stderr)
        print(HELP, file=sys.stderr)
        return 2

    try:
        return TASKS[args.task]() or 0
    except TaskError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
