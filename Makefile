.PHONY: help install benchmarks test lint results clean

PY ?= python3

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}'

install:  ## install package and dev dependencies
	$(PY) -m pip install --pre -e ".[dev]"  # PySAT ships pre-release-tagged builds only

benchmarks:  ## download the ISCAS-85 circuits
	$(PY) scripts/fetch_benchmarks.py

test:  ## run the test suite
	$(PY) -m pytest -q

lint:  ## static checks
	$(PY) -m ruff check src tests scripts

results:  ## regenerate the results table in docs/
	$(PY) scripts/run_all.py

clean:
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
