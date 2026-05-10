# ChainBot Operations Runbook

## Runtime Profiles

The repository now ships with three profile assets under [deploy/profiles](/Users/apple/Documents/ChainBot/deploy/profiles):

- `local-research.env`: safe local development and replay work.
- `paper-staging.env`: longer-running paper trading with stronger auth and tighter retention.
- `guarded-live.env`: minimal guarded live configuration with very conservative exposure and shutdown limits.

Copy one profile to `.env` or reference it from `docker-compose.yml`.
If you only need a small local override set, matching lightweight snippets are available under [config/profiles](/Users/apple/Documents/ChainBot/config/profiles), but those are not full deployment env files.

## Local Container Run

Build and start the service:

```bash
docker compose up --build
```

The container publishes the dashboard on `http://127.0.0.1:8000` and persists SQLite state in `./data`.

## Metrics and Alerts

ChainBot now exposes:

- `/api/v1/metrics`: structured JSON metrics payload
- `/metrics`: Prometheus-compatible text metrics

Sample monitoring assets live under [deploy/monitoring](/Users/apple/Documents/ChainBot/deploy/monitoring):

- `prometheus.yml`
- `alert-rules.yml`

The alert rules intentionally stay small:

- service readiness degraded
- unresolved dependency failures
- runtime safety shutdown active

## Replay Seed Datasets

Representative replay fixtures live under [tests/fixtures/replay_cases](/Users/apple/Documents/ChainBot/tests/fixtures/replay_cases):

- blocked entry
- successful entry
- partial moonbag exit
- emergency stop
- zombie exit

Load a fixture into a local SQLite database with:

```bash
python scripts/replay/load_seed_dataset.py tests/fixtures/replay_cases/successful_entry.json --reset-replay-tables
```

Use this on a disposable local database or after taking a backup.
