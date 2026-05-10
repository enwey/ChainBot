PYTHON ?= python3

.PHONY: install-dev lint format format-check test smoke package check ci

install-dev:
	$(PYTHON) -m pip install -e ".[dev]"

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .

format-check:
	$(PYTHON) -m ruff format --check .

test:
	$(PYTHON) -m pytest -q

smoke:
	$(PYTHON) -m compileall -q src tests
	$(PYTHON) -c "import investment_automation"

package:
	rm -rf build dist *.egg-info
	$(PYTHON) -m build

check: lint format-check test smoke package

ci: check
