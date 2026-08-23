# Civic Innovators Fellowship — mid-session passphrase check-in (Part A)
#
# One command to a working local stack:  make setup && make demo
#
# This file is a thin forwarder. Every target runs `python3 tasks.py <target>`,
# which is where the actual logic lives. Two reasons:
#
#   * `make` is not present on a stock Windows install, and these recipes were
#     bash. Windows users run `python tasks.py demo` and get identical
#     behaviour rather than being asked to install a POSIX toolchain to run a
#     Python project.
#   * One implementation cannot drift from the other.
#
# `make demo` needs no Google account and no GEMINI_API_KEY. It runs the whole
# pipeline against FakeGoogleClient, which reproduces each documented Google
# trap, so the demo exercises the trap handling rather than routing around it.

PY ?= python3

.DEFAULT_GOAL := help

.PHONY: help doctor setup demo demo-again demo-ai demo-console test clean \
        db-up db-reset db-down studio fixtures

help:
	@$(PY) tasks.py help

doctor:        ; @$(PY) tasks.py doctor
setup:         ; @$(PY) tasks.py setup
demo:          ; @$(PY) tasks.py demo
demo-again:    ; @$(PY) tasks.py demo-again
demo-ai:       ; @$(PY) tasks.py demo-ai
demo-console:  ; @$(PY) tasks.py demo-console
test:          ; @$(PY) tasks.py test
clean:         ; @$(PY) tasks.py clean
db-up:         ; @$(PY) tasks.py db-up
db-reset:      ; @$(PY) tasks.py db-reset
db-down:       ; @$(PY) tasks.py db-down
studio:        ; @$(PY) tasks.py studio
fixtures:      ; @$(PY) tasks.py fixtures
