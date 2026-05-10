# Release Notes 0.2.0

## Highlights

This release moves ChainBot materially closer to an operable product:

- live-safety guardrails for daily loss, dependency failure shutdown, and abnormal-exit cooldown
- idempotent manual and automated trade submission paths
- persisted manual trade audit and dependency failure records
- decision replay export endpoints for single decisions and full token cases
- local SQLite backup and restore tooling
- development workflow assets: `Makefile`, CI, packaging checks
- operations assets: runtime profiles, Docker/Compose, Prometheus-ready metrics, replay seed fixtures

## New operator surfaces

- `/api/v1/manual-trades`
- `/api/v1/dependencies`
- `/api/v1/worker-errors`
- `/api/v1/metrics`
- `/metrics`

## New assets

- [deploy/profiles](/Users/apple/Documents/ChainBot/deploy/profiles)
- [config/profiles](/Users/apple/Documents/ChainBot/config/profiles)
- [deploy/monitoring](/Users/apple/Documents/ChainBot/deploy/monitoring)
- [scripts/database](/Users/apple/Documents/ChainBot/scripts/database)
- [scripts/replay/load_seed_dataset.py](/Users/apple/Documents/ChainBot/scripts/replay/load_seed_dataset.py)
- [tests/fixtures/replay_cases](/Users/apple/Documents/ChainBot/tests/fixtures/replay_cases)

## Upgrade reminder

Take a database backup before first booting the release, then verify health, dependencies, and metrics endpoints after startup.
