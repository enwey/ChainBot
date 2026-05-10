# Operations Guide

## Runtime Profiles

Complete runtime profiles live in `deploy/profiles/`:

- `local-research.env`
- `paper-staging.env`
- `guarded-live.env`

Lightweight local override snippets also live in `config/profiles/` with the same names.
Use `deploy/profiles/` when you need a complete env file.
Use `config/profiles/` only when layering a few values onto `.env.example`.

Load one profile into `.env` before starting the service, then override secrets locally as needed.

## Backup and Restore

Create a backup:

```bash
mkdir -p backups
python scripts/database/backup.py --output ./backups/trading.db
```

Restore a backup:

```bash
python scripts/database/restore.py --input ./backups/trading.db
```

The backup/restore flow is covered by `tests/test_db_persistence.py`.

## Metrics

ChainBot exposes:

- JSON metrics: `/api/v1/metrics`
- Prometheus-style text metrics: `/metrics`

These surfaces are intended for lightweight local alerting or scraping.

## Container Runtime

For a local containerized run:

```bash
cp .env.example .env
docker compose up --build
```

The SQLite database is persisted through the `./data` volume mount.

## Replay Seeds

Representative replay fixtures live in `tests/fixtures/replay_cases/`.

Load one with:

```bash
python scripts/replay/load_seed_dataset.py tests/fixtures/replay_cases/successful_entry.json --reset-replay-tables
```

The replay loader defaults to `data/trading.db` unless `--database` is provided.
