# Development workflow

This repository keeps the delivery toolchain intentionally lightweight:

- `ruff` for linting and formatting
- `pytest` for unit tests
- `build` for source and wheel packaging validation

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Commands

```bash
make lint
make format
make test
make smoke
make package
make ci
```

`make ci` is the expected pre-push check. It runs lint, format verification, unit tests, import/compile smoke checks, and package builds.

## Continuous integration

GitHub Actions runs the same workflow for pushes and pull requests:

- `ruff check .`
- `ruff format --check .`
- `pytest -q`
- `python -m compileall -q src tests`
- `python -c "import investment_automation"`
- `python -m build`
- `python -m pip install --force-reinstall dist/*.whl`
