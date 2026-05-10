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

## Modes

- `EXECUTION_MODE=paper`: default and recommended for testing
- `EXECUTION_MODE=live`: enables real order submission only when `ENABLE_LIVE_TRADING=true` and a valid `SOLANA_PRIVATE_KEY` is present

## Important note

Live trading always carries financial risk. This project now defaults to paper execution and requires explicit opt-in before it will submit a real swap.

## Productization roadmap

For a more mature service architecture and delivery plan, see [docs/productization-roadmap.md](docs/productization-roadmap.md).
