PY := python3
export PYTHONPATH := src

.PHONY: help test status ingest report all fixtures clean

help:
	@echo "make test      run the test suite"
	@echo "make status    show which terms are defined and what that gates"
	@echo "make all       fixtures -> ingest -> report"
	@echo "make report    render out/report.html from the current warehouse"
	@echo "make fixtures  regenerate synthetic sample data"
	@echo "make clean     remove the warehouse and generated report"

test:
	$(PY) -m unittest discover -s tests -v

status:
	$(PY) -m cufa status

fixtures:
	$(PY) tools/make_fixtures.py

ingest:
	$(PY) -m cufa ingest --roster fixtures/roster.csv --fixtures fixtures

report:
	$(PY) -m cufa report --out out/report.html

all: clean ingest report
	@echo "-> out/report.html"

clean:
	rm -f out/cif.db out/report.html
