.PHONY: help test cover fixtures experiments tables report verify clean

PY ?= python3
SRC := src

help:
	@echo "test        run the suite"
	@echo "cover       run the suite with branch coverage, failing under 100 percent"
	@echo "fixtures    regenerate the log and the original run's output"
	@echo "experiments run every experiment, writing JSON under reports/"
	@echo "tables      regenerate the generated blocks in the README"
	@echo "report      build the HTML report and the diagrams"
	@echo "verify      everything, in the order CI runs it"

test:
	PYTHONPATH=$(SRC) $(PY) -m pytest tests

cover:
	PYTHONPATH=$(SRC) $(PY) -m pytest tests --cov=srd --cov-report=term-missing --cov-branch --cov-fail-under=100

clean:
	rm -rf .pytest_cache .coverage htmlcov

fixtures:
	PYTHONPATH=$(SRC) $(PY) -m srd.cli generate

experiments:
	@for script in experiments/exp*.py; do \
		echo "-- $$script"; \
		PYTHONPATH=$(SRC) $(PY) $$script > /dev/null || exit 1; \
	done
	PYTHONPATH=$(SRC) $(PY) tools/collect_metrics.py

tables:
	PYTHONPATH=$(SRC) $(PY) tools/render_tables.py

report:
	PYTHONPATH=$(SRC) $(PY) tools/render_diagrams.py
	PYTHONPATH=$(SRC) $(PY) tools/build_report.py

check:
	PYTHONPATH=$(SRC) $(PY) tools/check_numbers.py

verify: cover experiments tables check
	@echo "verified"
