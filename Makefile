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
