# Every target here runs offline. `make verify` is the one the README promises a
# reviewer, so it is the one that must never be broken.
#
# The target names are the same in all seven repositories of this portfolio, so
# one CI workflow drives them all and the workflow never needs to know how any
# individual repository builds its own report.

PY ?= python3
PKG := srd

.PHONY: help install lint test cover receipts tables diagrams report proof check \
	preflight verify clean fixtures pdf

# Exported so every target works from a bare clone, before `make install` has
# been run. With the package installed this resolves to the same code, so the
# two ways of working cannot disagree.
export PYTHONPATH := src

help:
	@echo "install      editable install with the dev extra"
	@echo "lint         ruff over src, tests, tools and experiments"
	@echo "test         run the suite"
	@echo "cover        the suite with branch coverage, failing under 100 percent"
	@echo "receipts     re-measure every published figure into docs/metrics.json"
	@echo "tables       rewrite the generated blocks in the README"
	@echo "diagrams     re-render the committed SVGs from docs/metrics.json"
	@echo "report       build docs/report.html and screenshot it"
	@echo "proof        capture the command line into a committed transcript"
	@echo "check        fail if a document quotes a figure the code no longer produces"
	@echo "preflight    authorship, ascii, exit codes, determinism, offline, urls"
	@echo "verify       everything, in the order CI runs it"

install:
	$(PY) -m pip install -e ".[dev]"

lint:
	$(PY) -m ruff check .

test:
	$(PY) -m pytest -q

cover:
	$(PY) -m pytest -q --cov=$(PKG) --cov-branch --cov-report=term-missing \
		--cov-report=json:coverage.json --junitxml=junit.xml --cov-fail-under=100

# Depends on nothing and runs the suite itself, with --skip-tests left to the
# caller. A red suite surfacing as a CalledProcessError from inside a metrics
# script costs an hour to attribute, so CI runs the suite as its own step first
# and then calls this with the reports already on disk.
receipts:
	$(PY) tools/collect_metrics.py

tables:
	$(PY) tools/render_tables.py

diagrams:
	$(PY) tools/render_diagrams.py

report:
	$(PY) tools/build_report.py
	$(PY) tools/capture_screenshots.py
	$(PY) tools/verify_rendering.py

proof:
	$(PY) tools/proof_of_work.py

check:
	$(PY) tools/check_numbers.py

preflight:
	$(PY) tools/preflight.py

verify: lint cover receipts tables diagrams report proof check
	@echo "verified"

clean:
	rm -rf .pytest_cache .coverage htmlcov coverage.json junit.xml

fixtures:
	$(PY) -m srd.cli generate

pdf:
	$(PY) tools/build_pdf.py docs/runbook.md --out dist/runbook.pdf \
		--title "stream-replay-divergence" --subtitle "Runbook"
