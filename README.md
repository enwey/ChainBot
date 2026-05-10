# ChainBot

A standard Python service for monitoring new Solana opportunities, storing market signals, running paper trading by default, and optionally executing live trades when explicitly enabled.

## Features

- Centralized configuration via environment variables
- Persistent SQLite storage with idempotent schema setup
- Clear separation between market data, strategy engine, execution, and web API
- Safe default `paper` mode with explicit `live` enablement
- Local dashboard and JSON API for wallet, portfolio, and signals

## Project layout

```text
src/investment_automation/
  app.py          # Main process bootstrap
  settings.py     # Environment-driven configuration
  db.py           # SQLite schema and repositories
  market_data.py  # PumpPortal + DexScreener integration
  execution.py    # Paper/live trade execution adapters
  engine.py       # Monitoring and position management
  web.py          # HTTP API and static file server
web/
  index.html      # Local dashboard
main.py           # Legacy-compatible entrypoint
```

## Quick start

1. Create a virtual environment and install dependencies.
2. Copy `.env.example` to `.env`.
3. Start the service:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python main.py
```

The web UI will be available at `http://127.0.0.1:8000`.

## Developer workflow

Install the local development toolchain once:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Common commands:

```bash
make lint         # Ruff lint checks
make format       # Apply Ruff formatting
make test         # Pytest unit tests
make smoke        # Import + bytecode compilation smoke checks
make package      # Build sdist and wheel
make ci           # Full local CI pass
```

The repository also ships with a GitHub Actions workflow at `.github/workflows/ci.yml` that runs lint, format check, tests, smoke checks, and packaging validation on pushes and pull requests.

## Operations

- Canonical runtime profiles live in `deploy/profiles/`
- Lightweight local override snippets live in `config/profiles/`
- Monitoring samples live in `deploy/monitoring/`
- Replay seed fixtures live in `tests/fixtures/replay_cases/`
- Backup and restore scripts live in `scripts/database/`
- JSON metrics are available at `/api/v1/metrics`
- Prometheus-style metrics are available at `/metrics`
- Container assets are provided via `Dockerfile` and `docker-compose.yml`

Use `deploy/profiles/*.env` as complete env files for Compose or direct deployment.
Use `config/profiles/*.env` only as smaller local overrides on top of `.env.example`.

Load one replay seed into a local SQLite database with:

```bash
python scripts/replay/load_seed_dataset.py tests/fixtures/replay_cases/successful_entry.json --reset-replay-tables
```

## Modes

- `EXECUTION_MODE=paper`: default and recommended for testing
- `EXECUTION_MODE=live`: enables real order submission only when `ENABLE_LIVE_TRADING=true` and a valid `SOLANA_PRIVATE_KEY` is present

## Important note

Live trading always carries financial risk. This project now defaults to paper execution and requires explicit opt-in before it will submit a real swap.

## Productization roadmap

For a more mature service architecture and delivery plan, see [docs/productization-roadmap.md](docs/productization-roadmap.md).

For delivery workflow details, see [docs/development-workflow.md](docs/development-workflow.md).
For runtime operations, see [docs/operations.md](docs/operations.md) and [docs/operations-runbook.md](docs/operations-runbook.md).
For backup and restore, see [docs/backup-restore.md](docs/backup-restore.md).
For schema changes, see [docs/schema-upgrade-notes.md](docs/schema-upgrade-notes.md).
For this acceptance pass, see [docs/release-notes.md](docs/release-notes.md) and [docs/release-notes-0.2.0.md](docs/release-notes-0.2.0.md).
