# Backup And Restore Notes

ChainBot keeps state in a local SQLite database at `data/trading.db` by default.

## Create a backup

```bash
mkdir -p backups
python scripts/database/backup.py --database data/trading.db --output backups/trading-$(date +%Y%m%d-%H%M%S).db
```

This performs a SQLite online backup and checkpoints WAL state first.

## Restore a backup

```bash
python scripts/database/restore.py --database data/trading.db --input backups/trading-20260510-120000.db
```

Restoring replaces the target database contents with the selected backup.

## Recommended operator workflow

1. Stop the service if you are restoring into the active database.
2. Keep timestamped backups outside the repo working tree when possible.
3. Take a fresh backup before importing replay seed datasets or schema-changing builds.
4. After restore, open `/api/v1/health` and `/api/v1/metrics` to confirm the service is healthy.
5. If you rely on the default script behavior, confirm `.env` still points at the expected database path before running restore.
