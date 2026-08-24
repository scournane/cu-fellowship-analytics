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
FRONTEND = ROOT / "frontend"
# The console refuses to render without this file. Nothing else in the repo
# depends on a build step, so it is easy to forget that this one does.
BUNDLE = ROOT / "src" / "cufa" / "console" / "static" / "app" / "console.js"

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
    cwd: Path | None = None,
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

    cmd = [str(a) for a in argv]
    try:
        result = subprocess.run(
            cmd,
            env=merged,
            cwd=str(cwd or ROOT),
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE if capture or quiet else None,
            stderr=subprocess.PIPE if capture else None,
        )
    except FileNotFoundError as exc:
        # Typical on Windows when PATH has an npm .cmd shim but CreateProcess
        # was asked to launch a bare name, which only finds .exe.
        if check:
            raise TaskError(f"command not found: {cmd[0]}") from exc
        return subprocess.CompletedProcess(cmd, 127, stdout="", stderr=str(exc))
    if check and result.returncode != 0:
        if capture and result.stderr:
            sys.stderr.write(result.stderr)
        raise TaskError(f"command failed ({result.returncode}): {' '.join(cmd)}")
    return result


def tool_argv(name: str, *args: str) -> list[str] | None:
    """Argv that CreateProcess can actually launch for a PATH command.

    `shutil.which("supabase")` on Windows often returns an npm `supabase.cmd`
    shim. `subprocess.run(["supabase"])` then looks for `supabase.exe` and
    raises FileNotFoundError. A .cmd/.bat still has to go through cmd.exe.
    """
    path = shutil.which(name)
    if not path:
        return None
    extra = [str(a) for a in args]
    if IS_WINDOWS and path.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", path, *extra]
    return [path, *extra]


def run_tool(name: str, *args: str, **kwargs) -> subprocess.CompletedProcess:
    argv = tool_argv(name, *args)
    if argv is None:
        if kwargs.get("check", True):
            raise TaskError(f"{name} is not on PATH")
        return subprocess.CompletedProcess([name, *args], 127, stdout="", stderr="")
    return run(argv, **kwargs)


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
    "node": {
        "Windows": "Install Node.js 20 or newer: https://nodejs.org/en/download\n"
                   "  or: winget install OpenJS.NodeJS.LTS",
        "Darwin":  "brew install node",
        "Linux":   "https://nodejs.org/en/download  or your package manager's nodejs package",
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
    if tool_argv("docker") is None:
        return False, "not installed"
    probe = run_tool("docker", "info", check=False, capture=True)
    if probe.returncode != 0:
        return False, "installed but not running"
    return True, "running"


def check_supabase() -> tuple[bool, str]:
    # run_tool answers 127 for a tool that is not on PATH, so the probe covers
    # the missing case too.
    probe = run_tool("supabase", "--version", check=False, capture=True)
    if probe.returncode != 0:
        return False, "not installed"
    return True, (probe.stdout or "").strip() or "installed"


def check_node() -> tuple[bool, str]:
    probe = run_tool("npm", "--version", check=False, capture=True)
    if probe.returncode != 0:
        return False, "not installed"
    return True, f"npm {(probe.stdout or '').strip()}"


def check_bundle() -> tuple[bool, str]:
    if BUNDLE.exists():
        return True, f"built at {BUNDLE.parent}"
    return False, "not built"


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
    ok, detail = {"docker": check_docker, "supabase": check_supabase, "node": check_node}[tool]()
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
        ("Node / npm", check_node),
        ("Console bundle", check_bundle),
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
    if "Node / npm" in missing:
        print(f"\n  Node — the console bundle is built with it:\n  {hint('node')}")
    if "Console bundle" in missing:
        print("\n  python tasks.py frontend")
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
        run_tool("supabase", "init")
    print("supabase project: initialised")

    task_frontend()

    activate = (
        r".venv\Scripts\Activate.ps1" if IS_WINDOWS else "source .venv/bin/activate"
    )
    print(
        f"\nSetup complete.\n"
        f"  Next:            python tasks.py demo\n"
        f"  To use `cufa` directly, activate the venv:  {activate}"
    )
    return 0


def task_frontend() -> int:
    """Build the console bundle.

    The console is React served from static/app; `cufa serve` raises rather
    than rendering when it is missing. Nothing else in this repo needs a
    toolchain, which is exactly why this has to be a task rather than a line in
    a setup doc that someone reads once.
    """
    require("node")
    if not (FRONTEND / "node_modules").is_dir():
        print("installing front-end dependencies (first run only)")
        run_tool("npm", "ci", cwd=FRONTEND)
    print("building the console bundle")
    run_tool("npm", "run", "build", cwd=FRONTEND)
    print(f"bundle written to {BUNDLE.parent}")
    return 0


def ensure_frontend() -> None:
    """Build the bundle if it is not there. Cheap to check, fatal to skip."""
    if not BUNDLE.exists():
        task_frontend()


def task_db_up() -> int:
    require("docker")
    require("supabase")
    status = run_tool("supabase", "status", check=False, capture=True)
    if status.returncode != 0:
        print("starting the local Supabase stack (the first run pulls images)")
        run_tool("supabase", "start")
    print("postgres: postgresql://postgres:postgres@localhost:64322/postgres")
    print("studio:   http://localhost:64323")
    return 0


def configured_database_name() -> str:
    """The database name in CUFA_DATABASE_URL, or "" when it is unset."""
    url = os.environ.get("CUFA_DATABASE_URL", "")
    if not url:
        return ""
    return url.rsplit("/", 1)[-1].split("?")[0]


def task_db_reset() -> int:
    """Rebuild whichever database this run is actually pointed at.

    `supabase db reset` always resets the linked project's `postgres` database,
    whatever CUFA_DATABASE_URL says. So telling someone to run the demo against
    a scratch database only works if the reset follows them there — otherwise
    the "safe" workaround wipes the real install anyway, which is worse than no
    workaround at all.
    """
    task_db_up()
    name = configured_database_name()
    if name and name != "postgres":
        run([venv_python(), ROOT / "scripts" / "make_test_db.py", name, "--force"])
        return 0
    run_tool("supabase", "db", "reset")
    return 0


def task_db_down() -> int:
    require("supabase")
    run_tool("supabase", "stop", check=False)
    return 0


def task_studio() -> int:
    print("Supabase Studio (visual table browser): http://localhost:64323")
    return 0


def task_fixtures() -> int:
    script("generate_fixtures.py", "--out", str(FIXTURES))
    return 0


def real_install_markers() -> list[str]:
    """What in the working database says a REAL account has been set up here.

    `make demo` starts with a database reset, which drops everything. That is
    right for a demo and catastrophic for an install: it takes out the connected
    Google credential, the roster, the sessions somebody typed in, and the
    template rows that point at real forms in their Drive — leaving those forms
    orphaned and the database full of simulated ones. The next real provisioning
    run then fails with a 404 that explains none of it. Not hypothetical: it is
    what the first real install did.

    Only two things trigger it, and both mean a real Google account has been
    used against this database — a template id Google actually issued, or a
    stored credential that is not the fake client's placeholder. Recorded
    check-ins are deliberately **not** a trigger: the demo writes a hundred of
    them, and refusing to re-run the demo on a demo database would make the
    guard useless in exactly the case it has to allow.

    The probe lives in ``scripts/install_markers.py`` because tasks.py runs on a
    bare interpreter and cannot import ``cufa``. Best-effort throughout: a probe
    that cannot run reports nothing, and the demo proceeds.
    """
    if not have_venv():
        return []
    result = run(
        [venv_python(), ROOT / "scripts" / "install_markers.py"],
        check=False,
        capture=True,
        quiet=True,
    )
    if result.returncode != 0:
        return []
    return [line for line in (result.stdout or "").splitlines() if line.strip()]


def task_demo() -> int:
    markers = real_install_markers()
    if markers and not os.environ.get("CUFA_DEMO_FORCE"):
        raise TaskError(
            "Refusing to run the demo: this database looks like a real install.\n"
            "\n"
            "`make demo` begins with `supabase db reset`, which drops every table. "
            "Found here:\n"
            + "".join(f"  * {marker}\n" for marker in markers)
            + "\n"
            "Resetting would delete the roster, the sessions and the connected "
            "account, and leave the real forms stranded in Drive with nothing "
            "pointing at them.\n"
            "\n"
            "  * To explore the demo safely, point it at another database:\n"
            "        CUFA_DATABASE_URL=postgresql://postgres:postgres@localhost:64322/cufa_demo make demo\n"
            "    (create it first with:  python scripts/make_test_db.py cufa_demo)\n"
            "\n"
            "  * To wipe this one anyway, say so explicitly:\n"
            "        CUFA_DEMO_FORCE=1 make demo\n"
        )

    task_db_reset()
    task_fixtures()

    state = Path(DEMO_ENV["CUFA_FAKE_GOOGLE_STATE"])
    state.unlink(missing_ok=True)

    banner("1. roster and sessions")
    cufa("load-roster", "--csv", str(FIXTURES / "roster.csv"), "--cohort", COHORT)
    cufa("load-sessions", "--csv", str(FIXTURES / "sessions.csv"))

    banner("2. one-time Google setup — once per PART, not once overall")
    for part in ("a", "b"):
        cufa("template", "create", "--part", part)

        print(f"\n-- part {part}: provisioning is blocked until it verifies (trap 2) ----")
        blocked = cufa("template", "verify", "--part", part, check=False, quiet=True)
        if blocked.returncode == 0:
            raise TaskError(
                f"UNEXPECTED: the part-{part} template verified before the manual step"
            )
        print("blocked, as designed: emailCollectionType is not VERIFIED yet")

        script("seed_fake_google.py", "--set-verified", "--part", part)
        cufa("template", "verify", "--part", part)

    banner("3. the rotating question, and what each week will ask")
    cufa("rotation", "--cohort", COHORT, "--weeks", "11")

    print("\n-- a teacher-question week with no question BLOCKS provisioning --------")
    week10 = _session_for_week(10)
    blocked = cufa("provision", "--session", week10, "--part", "b",
                   check=False, quiet=True)
    if blocked.returncode == 0:
        raise TaskError(
            "UNEXPECTED: week 10 provisioned with no teacher question set. "
            "Substituting a generic question would destroy the one unfakeable signal."
        )
    print("blocked, as designed: no generic question was substituted")
    cufa("session", "edit", "--session", week10,
         "--teacher-question", "What would you tell someone who missed all ten weeks?")

    banner("4. provision both forms for every session")
    cufa("provision", "--cohort", COHORT, "--part", "a")
    cufa("provision", "--cohort", COHORT, "--part", "b")

    banner("5. the lesson happens")
    script("seed_fake_google.py", "--seed-responses", "--fixtures", str(FIXTURES))
    script("seed_fake_google.py", "--announce", "--fixtures", str(FIXTURES))

    banner("6. pull Part A responses (Forms API path)")
    cufa("pull", "--cohort", COHORT, "--part", "a")

    banner("7. import a manually created form (CSV fallback path)")
    cufa("ingest", "part-a", "--csv", str(FIXTURES / "manual_form_export.csv"),
         "--cohort", COHORT, "--sheet-timezone", SHEET_TZ)

    banner("8. the lesson ends — Part B goes out")
    script("seed_fake_google.py", "--seed-responses-b", "--fixtures", str(FIXTURES))

    print("\n-- a form with an incomplete question map REFUSES to ingest -------------")
    sabotaged = "Session 5 — Building a coalition"
    script("seed_fake_google.py", "--break-question-map", sabotaged)
    session_id = _session_for_title(sabotaged)
    refused = cufa("pull", "--session", session_id, "--part", "b",
                   check=False, quiet=True)
    if refused.returncode == 0:
        raise TaskError(
            "UNEXPECTED: a form with an incomplete question map ingested anyway. "
            "Guessing which answer is which corrupts every downstream number in a "
            "way that looks plausible."
        )
    print("refused, as designed: no rows were written from a form it cannot key")
    print("restoring the map by re-provisioning (which reads the form back)…")
    cufa("provision", "--session", session_id, "--part", "b")

    banner("9. pull Part B responses")
    cufa("pull", "--cohort", COHORT, "--part", "b")

    banner("10. adjudicate Part A (tier 1 only; tier 2 skipped)")
    cufa("adjudicate", "--cohort", COHORT, "--no-ai")

    banner("11. muddiest-point themes (degrades cleanly with no GEMINI_API_KEY)")
    for week in (2, 5, 8):
        cufa("themes", "--session", _session_for_week(week))

    banner("12. shoutouts awaiting a human")
    cufa("shoutouts", "review", "--cohort", COHORT)

    banner("13. help requests (this list is exported nowhere)")
    cufa("help-requests", "list")

    banner("14. report")
    cufa("report", "--cohort", COHORT)
    cufa("report", "--cohort", COHORT, "--confidence")

    banner("15. acceptance checks")
    script("verify_demo.py", "--cohort", COHORT, "--fixtures", str(FIXTURES))

    print("\nInspect the data in Supabase Studio: http://localhost:64323")
    print("Re-run the demo — it is idempotent and will report the same numbers.")
    return 0


def _query_one(sql: str, *params: str) -> str:
    """Run a one-value query through the venv's interpreter.

    tasks.py cannot import cufa: it has to run on a bare interpreter before the
    package is installed. So the query goes through a subprocess that can.
    """
    code = (
        "import sys;"
        "from cufa.db import connection, fetch_one;"
        "params=tuple(sys.argv[2:]);"
        "row=None\n"
        "with connection() as conn:\n"
        "    row = fetch_one(conn, sys.argv[1], params)\n"
        "print('' if row is None else str(list(row.values())[0]))"
    )
    result = run(
        [venv_python(), "-c", code, sql, *params], capture=True, quiet=True
    )
    value = (result.stdout or "").strip()
    if not value:
        raise TaskError(f"no row for query: {sql}")
    return value


def _session_for_week(week: int) -> str:
    return _query_one(
        'select session_id from "session" where cohort_id = %s and week_index = %s',
        COHORT,
        str(week),
    )


def _session_for_title(title: str) -> str:
    return _query_one(
        'select session_id from "session" where cohort_id = %s and title = %s',
        COHORT,
        title,
    )


def task_demo_again() -> int:
    """Re-run the pipeline over the SAME database, to show idempotency."""
    cufa("pull", "--cohort", COHORT, "--part", "a")
    cufa("pull", "--cohort", COHORT, "--part", "b")
    cufa("ingest", "part-a", "--csv", str(FIXTURES / "manual_form_export.csv"),
         "--cohort", COHORT, "--sheet-timezone", SHEET_TZ)
    cufa("adjudicate", "--cohort", COHORT, "--no-ai")
    cufa("report", "--cohort", COHORT)
    script("verify_demo.py", "--cohort", COHORT, "--fixtures", str(FIXTURES))
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
    banner("Part B: muddiest-point clustering, live")
    for week in (2, 5, 8):
        cufa("themes", "--session", _session_for_week(week), "--regenerate")
    return 0


def task_demo_console() -> int:
    ensure_frontend()
    task_demo()
    print(f"\nConsole at http://127.0.0.1:{PORT} — fake Google client, zero Google calls.")
    print("Press Ctrl+C to stop.\n")
    cufa("serve", "--port", PORT)
    return 0


def task_test() -> int:
    # The console tests render real screens, which needs the real bundle.
    ensure_frontend()
    # And a database of their own — see task_db_test for why.
    task_db_test()
    run([venv_python(), "-m", "pytest"])
    return 0


def task_clean() -> int:
    run_tool("supabase", "stop", "--no-backup", check=False, capture=True)
    if FIXTURES.exists():
        shutil.rmtree(FIXTURES, ignore_errors=True)
    for pattern in ("**/__pycache__", ".pytest_cache"):
        for path in ROOT.glob(pattern):
            shutil.rmtree(path, ignore_errors=True)
    print("stopped Supabase and removed generated fixtures")
    return 0


TEST_DB = "cufa_test"


def task_db_test() -> int:
    """Create the database the test suite runs against.

    Deliberately not the database the console uses. The suite truncates every
    table before each test, ``google_credential`` included, so pointing it at
    the working database destroys a connected Google account and a loaded
    roster on every run. Keeping them apart is the only reliable fix; asking
    people to remember is not.

    Idempotent: re-running re-applies the migrations over the same database.
    """
    task_db_up()
    # Run under the venv: psycopg lives there, not necessarily in whatever
    # interpreter is running this file.
    run([venv_python(), ROOT / "scripts" / "make_test_db.py", TEST_DB])
    return 0


TASKS = {
    "doctor": task_doctor,
    "setup": task_setup,
    "demo": task_demo,
    "demo-again": task_demo_again,
    "demo-ai": task_demo_ai,
    "demo-console": task_demo_console,
    "frontend": task_frontend,
    "test": task_test,
    "clean": task_clean,
    "db-up": task_db_up,
    "db-reset": task_db_reset,
    "db-test": task_db_test,
    "db-down": task_db_down,
    "studio": task_studio,
    "fixtures": task_fixtures,
}

HELP = """Civic Innovators check-in — Parts A and B

  python tasks.py doctor        what is installed, what is missing, how to fix it
  python tasks.py setup         install dependencies, init Supabase, check Docker
  python tasks.py demo          both parts end to end on synthetic data, no Google, no Gemini
  python tasks.py demo-again    re-run over the same database, to show idempotency
  python tasks.py demo-ai       same as demo, with tier 2 live (needs GEMINI_API_KEY)
  python tasks.py demo-console  demo data plus the web console
  python tasks.py frontend      build the console bundle (npm ci + vite build)
  python tasks.py test          pytest, no network
  python tasks.py clean         stop Supabase, remove generated fixtures

  python tasks.py db-up         start the local Supabase stack
  python tasks.py db-reset      re-apply migrations and seed
  python tasks.py db-test       create the separate database the tests use
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
