from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from investment_automation.db import Database
from investment_automation.models import CandidateScan, DependencyFailureRow, PortfolioPosition
from investment_automation.settings import Settings


class DatabasePersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        (self.base_dir / "web").mkdir()
        env = {
            "APP_BASE_DIR": str(self.base_dir),
            "WEB_DIR": "web",
            "DATA_DIR": "data",
            "APP_HOST": "127.0.0.1",
            "APP_PORT": "8000",
            "EXECUTION_MODE": "paper",
            "ENABLE_LIVE_TRADING": "false",
            "POSITION_SIZE_USD": "15",
            "INITIAL_BALANCE_USD": "200",
            "MAX_OPEN_POSITIONS": "2",
            "MAX_WATCHLIST_SIZE": "20",
            "MAX_POSITION_SIZE_USD": "25",
            "MAX_WALLET_EXPOSURE_PCT": "0.12",
            "MIN_SCORE_TO_BUY": "85",
            "MIN_WATCH_SCORE": "72",
            "MIN_DEV_BUY_SOL": "1.0",
            "MAX_DEV_BUY_SOL": "4.0",
            "MIN_LIQUIDITY_USD": "2500",
            "MAX_LIQUIDITY_USD": "50000",
            "MIN_OBSERVATION_SECONDS": "30",
            "MAX_OBSERVATION_SECONDS": "180",
            "SOLANA_RPC_URL": "https://api.mainnet-beta.solana.com",
            "PUMPPORTAL_WS_URL": "wss://pumpportal.fun/api/data",
        }
        with patch.dict(os.environ, env, clear=True):
            self.settings = Settings.from_env()
            self.settings.validate()
        self.database = Database(self.settings)
        self.database.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_order_idempotency_records_are_claimed_and_updated(self) -> None:
        record, created = self.database.claim_order_idempotency(
            "buy:abc:1",
            source="manual_api",
            side="buy",
            addr="token-1",
            symbol="TK1",
            amount_usd=12.5,
            request_fingerprint="fp-1",
            request={"amount_usd": 12.5},
            created_ts=1_750_000_000,
        )
        self.assertTrue(created)
        self.assertEqual(record["status"], "pending")
        self.assertEqual(record["request"]["amount_usd"], 12.5)

        duplicate, created_again = self.database.claim_order_idempotency(
            "buy:abc:1",
            source="manual_api",
            side="buy",
            addr="token-1",
            symbol="TK1",
            amount_usd=12.5,
        )
        self.assertFalse(created_again)
        self.assertEqual(duplicate["request_fingerprint"], "fp-1")

        updated = self.database.update_order_idempotency(
            "buy:abc:1",
            status="executed",
            result={"mode": "paper"},
            tx_hash="tx-1",
            trade_history_id=7,
            updated_ts=1_750_000_010,
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated["status"], "executed")
        self.assertEqual(updated["tx_hash"], "tx-1")
        self.assertEqual(updated["trade_history_id"], 7)
        self.assertEqual(updated["result"]["mode"], "paper")

    def test_manual_trade_audit_and_dependency_failure_round_trip(self) -> None:
        audit_id = self.database.record_manual_trade_audit(
            {
                "action_ts": 1_750_000_020,
                "idempotency_key": "sell:def:1",
                "operator_id": "tester",
                "operator_ip": "127.0.0.1",
                "mode": "paper",
                "side": "sell",
                "addr": "token-2",
                "symbol": "TK2",
                "amount_usd": 9.5,
                "outcome": "executed",
                "reason": "manual_exit",
                "tx_hash": "tx-2",
                "trade_history_id": 9,
                "request": {"amount_usd": 9.5},
                "response": {"ok": True},
            }
        )
        audits = self.database.get_manual_trade_audit(limit=5)
        self.assertEqual(audit_id, audits[0]["id"])
        self.assertEqual(audits[0]["response"]["ok"], True)

        failure_id = self.database.record_dependency_failure(
            {
                "dependency": "dexscreener",
                "operation": "fetch_quote",
                "failure_type": "timeout",
                "severity": "error",
                "message": "request timed out",
                "details": {"attempt": 3},
                "failure_ts": 1_750_000_030,
            }
        )
        failures = self.database.get_dependency_failures(
            limit=5, dependency="dexscreener", open_only=True
        )
        self.assertEqual(failure_id, failures[0]["id"])
        self.assertEqual(failures[0]["details"]["attempt"], 3)
        self.assertEqual(
            self.database.count_dependency_failures(
                "dexscreener", 1_750_000_000, operation="fetch_quote", open_only=True
            ),
            1,
        )
        self.assertEqual(
            self.database.resolve_dependency_failures(
                "dexscreener", operation="fetch_quote", resolved_ts=1_750_000_040
            ),
            1,
        )
        self.assertEqual(
            self.database.get_dependency_failures(
                limit=5, dependency="dexscreener", open_only=True
            ),
            [],
        )

    def test_typed_models_round_trip_from_database_edges(self) -> None:
        self.database.record_scan(
            CandidateScan.from_mapping(
                {
                    "scan_time": "2026-05-10 09:30:00",
                    "platform": "pumpfun",
                    "symbol": "EDGE",
                    "age": "10s",
                    "addr": "token-edge",
                    "price": 0.12,
                    "liquidity": 4200,
                    "dev_buy": 1.8,
                    "progress": "early",
                    "ratio": "1.2x",
                    "score": 91,
                    "created_ts": 1000,
                    "launch_quality_score": 77,
                }
            )
        )
        self.database.open_position(
            "token-edge",
            "EDGE",
            0.12,
            15.0,
            "paper",
            "tx-edge",
            entry_liquidity=4200.0,
        )
        self.database.record_dependency_failure(
            DependencyFailureRow.from_mapping(
                {
                    "dependency": "pumpportal",
                    "operation": "stream",
                    "failure_type": "timeout",
                    "severity": "warn",
                    "message": "slow stream",
                    "details": {"retry": 1},
                }
            )
        )

        scan = self.database.get_scan_log_models(limit=1)[0]
        position = self.database.open_position_models()[0]
        failure = self.database.get_dependency_failure_models(limit=1)[0]

        self.assertIsInstance(scan, CandidateScan)
        self.assertEqual(scan.symbol, "EDGE")
        self.assertEqual(scan.score, 91)
        self.assertIsInstance(position, PortfolioPosition)
        self.assertEqual(position.symbol, "EDGE")
        self.assertIsInstance(failure, DependencyFailureRow)
        self.assertEqual(failure.details["retry"], 1)

    def test_backup_and_restore_scripts_round_trip_database_state(self) -> None:
        self.database.record_manual_trade_audit(
            {
                "action_ts": 1_750_000_050,
                "idempotency_key": "buy:ghi:1",
                "side": "buy",
                "addr": "token-3",
                "symbol": "TK3",
                "amount_usd": 5.0,
                "outcome": "executed",
            }
        )
        backup_path = self.base_dir / "backup" / "trading-backup.db"
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "database" / "backup.py"),
                "--database",
                str(self.settings.database_path),
                "--output",
                str(backup_path),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        self.database.record_manual_trade_audit(
            {
                "action_ts": 1_750_000_060,
                "idempotency_key": "buy:jkl:1",
                "side": "buy",
                "addr": "token-4",
                "symbol": "TK4",
                "amount_usd": 6.0,
                "outcome": "blocked",
            }
        )

        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "database" / "restore.py"),
                "--database",
                str(self.settings.database_path),
                "--input",
                str(backup_path),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        restored_db = Database(self.settings)
        audits = restored_db.get_manual_trade_audit(limit=10)
        self.assertEqual([item["addr"] for item in audits], ["token-3"])


if __name__ == "__main__":
    unittest.main()
