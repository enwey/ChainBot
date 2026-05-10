# Schema Upgrade Notes

This release extends the SQLite schema with operational tables used by the productization work:

- `decision_audit.snapshot_json`
- `order_idempotency`
- `manual_trade_audit`
- `dependency_failures`

## Upgrade behavior

ChainBot still performs idempotent schema setup on startup. Existing local databases will be upgraded in place when the service boots.

## Operator guidance

1. Take a backup before first booting a new build:
   `python scripts/database/backup.py --database data/trading.db --output backups/pre-upgrade.db`
2. Start the service once and verify:
   - `/api/v1/health`
   - `/api/v1/dependencies`
   - `/api/v1/manual-trades`
   - `/api/v1/metrics`
3. If the upgrade is not acceptable, restore the pre-upgrade backup and restart the older build.

## Compatibility note

The shipped replay seed loader writes into the current SQLite schema and expects these productization tables to exist.
