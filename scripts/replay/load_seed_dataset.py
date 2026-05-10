from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from investment_automation.db import Database
from investment_automation.settings import Settings

REPLAY_TABLES = (
    "trade_history",
    "portfolio",
    "decision_audit",
    "opportunities",
    "scan_logs",
    "dependency_failures",
    "manual_trade_audit",
)


def load_dataset(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_settings(base_dir: Path, database_path: Path) -> Settings:
    data_dir = database_path.parent
    env = {
        "APP_BASE_DIR": str(base_dir),
        "WEB_DIR": "web",
        "DATA_DIR": os.path.relpath(data_dir, base_dir),
        "APP_HOST": "127.0.0.1",
        "APP_PORT": "8000",
        "EXECUTION_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "PUMPPORTAL_WS_URL": "wss://pumpportal.fun/api/data",
    }
    old_env = dict(os.environ)
    os.environ.update(env)
    try:
        settings = Settings.from_env()
        settings.validate()
        return settings
    finally:
        os.environ.clear()
        os.environ.update(old_env)


def reset_replay_tables(database: Database) -> None:
    with database.connection() as conn:
        for table in REPLAY_TABLES:
            conn.execute(f"DELETE FROM {table}")


def seed_wallet(database: Database, wallet: dict[str, Any]) -> None:
    with database.connection() as conn:
        conn.execute(
            """
            INSERT INTO wallet (id, initial_bal, current_balance, buy_count, buy_total, sell_count, sell_total)
            VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                initial_bal=excluded.initial_bal,
                current_balance=excluded.current_balance,
                buy_count=excluded.buy_count,
                buy_total=excluded.buy_total,
                sell_count=excluded.sell_count,
                sell_total=excluded.sell_total
            """,
            (
                float(wallet.get("initial_bal") or 0.0),
                float(wallet.get("current_balance") or 0.0),
                int(wallet.get("buy_count") or 0),
                float(wallet.get("buy_total") or 0.0),
                int(wallet.get("sell_count") or 0),
                float(wallet.get("sell_total") or 0.0),
            ),
        )


def seed_portfolio(database: Database, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with database.connection() as conn:
        for row in rows:
            conn.execute(
                """
                INSERT OR REPLACE INTO portfolio
                (addr, symbol, buy_price, max_price, amount_usd, entry_liquidity, profit_locked_usd, moonbag_active, buy_time, current_price, price_source, price_updated_ts, entry_tx, mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(row.get("addr") or ""),
                    str(row.get("symbol") or ""),
                    float(row.get("buy_price") or 0.0),
                    float(row.get("max_price") or 0.0),
                    float(row.get("amount_usd") or 0.0),
                    float(row.get("entry_liquidity") or 0.0),
                    float(row.get("profit_locked_usd") or 0.0),
                    int(row.get("moonbag_active") or 0),
                    str(row.get("buy_time") or ""),
                    float(row.get("current_price") or 0.0),
                    str(row.get("price_source") or "seed"),
                    int(row.get("price_updated_ts") or 0),
                    row.get("entry_tx"),
                    str(row.get("mode") or "paper"),
                ),
            )


def seed_trade_history(database: Database, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with database.connection() as conn:
        for row in rows:
            conn.execute(
                """
                INSERT INTO trade_history
                (addr, symbol, side, price, amount_usd, pnl_pct, tx_hash, metrics_json, time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(row.get("addr") or ""),
                    str(row.get("symbol") or ""),
                    str(row.get("side") or ""),
                    float(row.get("price") or 0.0),
                    float(row.get("amount_usd") or 0.0),
                    row.get("pnl_pct"),
                    row.get("tx_hash"),
                    json.dumps(row.get("metrics") or {}, ensure_ascii=False),
                    str(row.get("time") or ""),
                ),
            )


def import_dataset(database: Database, payload: dict[str, Any]) -> dict[str, int]:
    if payload.get("wallet"):
        seed_wallet(database, dict(payload["wallet"]))
    for row in payload.get("scan_logs") or []:
        database.record_scan(dict(row))
    for row in payload.get("opportunities") or []:
        database.record_opportunity(dict(row))
    for row in payload.get("decision_audit") or []:
        database.record_decision_audit(dict(row))
    for row in payload.get("dependency_failures") or []:
        database.record_dependency_failure(dict(row))
    for row in payload.get("manual_trade_audit") or []:
        database.record_manual_trade_audit(dict(row))
    seed_portfolio(database, list(payload.get("portfolio") or []))
    seed_trade_history(database, list(payload.get("trade_history") or []))

    return {
        "scan_logs": len(payload.get("scan_logs") or []),
        "opportunities": len(payload.get("opportunities") or []),
        "decision_audit": len(payload.get("decision_audit") or []),
        "portfolio": len(payload.get("portfolio") or []),
        "trade_history": len(payload.get("trade_history") or []),
        "dependency_failures": len(payload.get("dependency_failures") or []),
        "manual_trade_audit": len(payload.get("manual_trade_audit") or []),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Load a replay seed dataset into ChainBot SQLite.")
    parser.add_argument("dataset", type=Path, help="Path to a replay seed dataset JSON file.")
    parser.add_argument(
        "--database",
        type=Path,
        default=ROOT / "data" / "trading.db",
        help="SQLite database path. Defaults to ./data/trading.db",
    )
    parser.add_argument(
        "--reset-replay-tables",
        action="store_true",
        help="Delete replay-related tables before importing the dataset.",
    )
    args = parser.parse_args()

    dataset_path = args.dataset.expanduser().resolve()
    database_path = args.database.expanduser().resolve()
    base_dir = ROOT
    settings = build_settings(base_dir, database_path)
    database = Database(settings)
    database.initialize()

    payload = load_dataset(dataset_path)
    if args.reset_replay_tables:
        reset_replay_tables(database)
    counts = import_dataset(database, payload)

    summary = {
        "dataset_id": payload.get("dataset_id"),
        "title": payload.get("title"),
        "database": str(database_path),
        "counts": counts,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
