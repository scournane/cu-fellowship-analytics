# Civic Innovators Fellowship — mid-session passphrase check-in (Part A)
#
# One command to a working local stack:  make setup && make demo
#
# `make demo` needs no Google account and no GEMINI_API_KEY. It runs the whole
# pipeline against FakeGoogleClient, which reproduces each documented Google
# trap, so the demo exercises the trap handling rather than routing around it.

SHELL := /bin/bash
.DEFAULT_GOAL := help

VENV      := .venv
PY        := $(VENV)/bin/python
PIP       := $(VENV)/bin/pip
CUFA      := $(VENV)/bin/cufa
COHORT    ?= demo
FIXTURES  ?= fixtures
SHEET_TZ  ?= America/New_York
PORT      ?= 8000

# Everything below runs against the fake client. Exported for every recipe so a
# stray command cannot reach Google by forgetting a flag.
export CUFA_FAKE_GOOGLE       ?= 1
export CUFA_FAKE_GOOGLE_STATE ?= $(FIXTURES)/fake_google_state.json
export CUFA_DATABASE_URL      ?= postgresql://postgres:postgres@localhost:54322/postgres

.PHONY: help
help:
	@echo "Civic Innovators check-in — Part A"
	@echo
	@echo "  make setup         install dependencies, init Supabase, check Docker"
	@echo "  make demo          full pipeline on synthetic data, no Google, no Gemini"
	@echo "  make demo-ai       same, with tier 2 live (needs GEMINI_API_KEY)"
	@echo "  make demo-console  demo data + the web console at http://127.0.0.1:$(PORT)"
	@echo "  make test          pytest, no network"
	@echo "  make clean         stop Supabase, remove generated fixtures"
	@echo
	@echo "  make db-up         start the local Supabase stack"
	@echo "  make db-reset      re-apply migrations and seed"
	@echo "  make db-down       stop the stack"
	@echo "  make studio        print the Studio URL"
	@echo "  make fixtures      regenerate synthetic fixtures"

# --------------------------------------------------------------------------
# setup
# --------------------------------------------------------------------------

.PHONY: setup
setup: $(VENV)/.installed check-docker check-supabase supabase-init
	@echo
	@echo "Setup complete. Next:  make demo"

$(VENV)/.installed: pyproject.toml
	@command -v python3 >/dev/null || { echo "error: python3 not found (3.11+ required)"; exit 1; }
	@python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' \
		|| { echo "error: Python 3.11+ required, found $$(python3 -V)"; exit 1; }
	@test -d $(VENV) || python3 -m venv $(VENV)
	@$(PIP) install --quiet --upgrade pip
	@$(PIP) install --quiet -e '.[dev]'
	@touch $@
	@echo "dependencies installed into $(VENV)"

.PHONY: check-docker
check-docker:
	@command -v docker >/dev/null 2>&1 || { \
		echo ""; \
		echo "error: Docker is not installed."; \
		echo "The local Supabase stack runs in Docker. Install Docker Desktop"; \
		echo "(macOS/Windows) or the docker engine (Linux), then re-run 'make setup'."; \
		echo ""; exit 1; }
	@docker info >/dev/null 2>&1 || { \
		echo ""; \
		echo "error: Docker is installed but not running."; \
		echo "  1. Start Docker Desktop, or: sudo systemctl start docker"; \
		echo "  2. Confirm with: docker ps"; \
		echo "  3. Re-run 'make setup'"; \
		echo ""; exit 1; }
	@echo "docker: running"

.PHONY: check-supabase
check-supabase:
	@command -v supabase >/dev/null 2>&1 || { \
		echo ""; \
		echo "error: the Supabase CLI is not on PATH."; \
		echo "  brew install supabase/tap/supabase"; \
		echo "  # or: npm install -g supabase"; \
		echo "  # or: https://github.com/supabase/cli/releases"; \
		echo ""; exit 1; }
	@echo "supabase: $$(supabase --version)"

.PHONY: supabase-init
supabase-init:
	@test -f supabase/config.toml || supabase init
	@echo "supabase project: initialised"

# --------------------------------------------------------------------------
# database
# --------------------------------------------------------------------------

.PHONY: db-up
db-up: check-docker check-supabase
	@supabase status >/dev/null 2>&1 || supabase start
	@echo "postgres: $(CUFA_DATABASE_URL)"
	@echo "studio:   http://localhost:54323"

.PHONY: db-reset
db-reset: db-up
	@supabase db reset

.PHONY: db-down
db-down:
	@supabase stop || true

.PHONY: studio
studio:
	@echo "Supabase Studio (visual table browser): http://localhost:54323"

# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

.PHONY: fixtures
fixtures:
	@$(PY) scripts/generate_fixtures.py --out $(FIXTURES)

# --------------------------------------------------------------------------
# demo
# --------------------------------------------------------------------------

.PHONY: demo
demo: db-reset fixtures
	@rm -f "$(CUFA_FAKE_GOOGLE_STATE)"
	@echo ""
	@echo "== 1. roster and sessions ==============================================="
	@$(CUFA) load-roster   --csv $(FIXTURES)/roster.csv --cohort $(COHORT)
	@$(CUFA) load-sessions --csv $(FIXTURES)/sessions.csv
	@echo ""
	@echo "== 2. one-time Google setup ============================================="
	@$(CUFA) template create
	@echo ""
	@echo "-- provisioning is blocked until the template verifies (trap 2) --------"
	@if $(CUFA) template verify >/dev/null 2>&1; then \
		echo "UNEXPECTED: template verified before the manual step"; exit 1; \
	else \
		echo "blocked, as designed: emailCollectionType is not VERIFIED yet"; \
	fi
	@$(PY) scripts/seed_fake_google.py --set-verified
	@$(CUFA) template verify
	@echo ""
	@echo "== 3. provision one form per session ===================================="
	@$(CUFA) provision --cohort $(COHORT)
	@echo ""
	@echo "== 4. the lesson happens ================================================"
	@$(PY) scripts/seed_fake_google.py --seed-responses --fixtures $(FIXTURES)
	@$(PY) scripts/seed_fake_google.py --announce      --fixtures $(FIXTURES)
	@echo ""
	@echo "== 5. pull responses (Forms API path) ==================================="
	@$(CUFA) pull --cohort $(COHORT)
	@echo ""
	@echo "== 6. import a manually created form (CSV fallback path) ================"
	@$(CUFA) ingest part-a --csv $(FIXTURES)/manual_form_export.csv \
		--cohort $(COHORT) --sheet-timezone $(SHEET_TZ)
	@echo ""
	@echo "== 7. adjudicate (tier 1 only; tier 2 skipped) =========================="
	@$(CUFA) adjudicate --cohort $(COHORT) --no-ai
	@echo ""
	@echo "== 8. report ============================================================"
	@$(CUFA) report --cohort $(COHORT)
	@echo ""
	@echo "== 9. acceptance checks ================================================="
	@$(PY) scripts/verify_demo.py --cohort $(COHORT) --fixtures $(FIXTURES)
	@echo ""
	@echo "Inspect the data in Supabase Studio: http://localhost:54323"
	@echo "Re-run 'make demo' — it is idempotent and will report the same numbers."

# Re-run the pipeline over the SAME database, to show idempotency without a reset.
.PHONY: demo-again
demo-again:
	@$(CUFA) pull --cohort $(COHORT)
	@$(CUFA) ingest part-a --csv $(FIXTURES)/manual_form_export.csv \
		--cohort $(COHORT) --sheet-timezone $(SHEET_TZ)
	@$(CUFA) adjudicate --cohort $(COHORT) --no-ai
	@$(CUFA) report --cohort $(COHORT)

# Every line of a Make recipe is its own shell, so a bare `exit 0` in the guard
# would end only that line and the rest of the target would run anyway. The
# whole target is therefore one shell command.
.PHONY: demo-ai
demo-ai:
	@set -e; \
	if [ -z "$$GEMINI_API_KEY" ]; then \
		echo ""; \
		echo "Skipping: GEMINI_API_KEY is not set, so tier 2 cannot run."; \
		echo ""; \
		echo "Set it in .env (see .env.example) and re-run 'make demo-ai'."; \
		echo "Nothing else depends on it — 'make demo' is the offline path, and"; \
		echo "there mismatch cases land in needs_review with"; \
		echo "rule_name='ai_unavailable' rather than being guessed at."; \
		echo ""; \
		exit 0; \
	fi; \
	$(MAKE) demo; \
	echo ""; \
	echo "== tier 2 live (only mismatch-in-window cases reach Gemini) ============="; \
	$(CUFA) adjudicate --cohort $(COHORT); \
	echo ""; \
	echo "== second pass: every pair is cached, so zero API calls ================="; \
	$(CUFA) adjudicate --cohort $(COHORT); \
	echo ""; \
	$(CUFA) review --status ai --cohort $(COHORT)

.PHONY: demo-console
demo-console: demo
	@echo ""
	@echo "Console at http://127.0.0.1:$(PORT) — fake Google client, zero Google calls."
	@$(CUFA) serve --port $(PORT)

# --------------------------------------------------------------------------
# tests and cleanup
# --------------------------------------------------------------------------

.PHONY: test
test:
	@$(VENV)/bin/python -m pytest

.PHONY: clean
clean:
	@supabase stop --no-backup >/dev/null 2>&1 || true
	@rm -rf $(FIXTURES)
	@find . -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@rm -rf .pytest_cache
	@echo "stopped Supabase and removed generated fixtures"
