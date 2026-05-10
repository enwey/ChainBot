from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from investment_automation.runtime_safety import DependencyFailureRecord, RuntimeSafetyManager
from investment_automation.settings import Settings


class RuntimeSafetyManagerTests(unittest.TestCase):
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
            "MAX_DAILY_LOSS_USD": "12",
            "MAX_CONSECUTIVE_DEPENDENCY_FAILURES": "3",
            "DEPENDENCY_FAILURE_WINDOW_SECONDS": "300",
            "ABNORMAL_EXIT_COOLDOWN_SECONDS": "120",
            "ABNORMAL_EXIT_THRESHOLD": "1",
            "ORDER_IDEMPOTENCY_TTL_SECONDS": "90",
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
        self.env_patcher = patch.dict(os.environ, env, clear=True)
        self.env_patcher.start()
        self.settings = Settings.from_env()
        self.settings.validate()

    def tearDown(self) -> None:
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    def test_claim_order_key_blocks_duplicates_until_ttl_expires(self) -> None:
        manager = RuntimeSafetyManager(self.settings)

        self.assertTrue(
            manager.claim_order_key(
                "buy:token", side="buy", token_mint="token", amount_usd=10.0, now_ts=100.0
            )
        )
        self.assertFalse(
            manager.claim_order_key(
                "buy:token", side="buy", token_mint="token", amount_usd=10.0, now_ts=101.0
            )
        )
        self.assertTrue(
            manager.claim_order_key(
                "buy:token", side="buy", token_mint="token", amount_usd=10.0, now_ts=191.0
            )
        )

    def test_dependency_failures_trigger_shutdown_after_threshold(self) -> None:
        manager = RuntimeSafetyManager(self.settings)
        for offset in range(3):
            manager.record_dependency_failure(
                DependencyFailureRecord(
                    component="market_data",
                    dependency="dexscreener",
                    operation="fetch_snapshots",
                    message="boom",
                    occurred_ts=100 + offset,
                    failure_class="RuntimeError",
                )
            )

        status = manager.runtime_status()
        self.assertTrue(status["shutdown_active"])
        self.assertEqual(status["shutdown"]["source"], "dependency_failure")

    def test_daily_loss_guardrail_triggers_shutdown(self) -> None:
        manager = RuntimeSafetyManager(self.settings)
        today = time.strftime("%Y-%m-%d", time.localtime(1000))
        sells = [
            {"time": f"{today} 10:00:00", "amount_usd": 20.0, "pnl_pct": -0.4},
            {"time": f"{today} 11:00:00", "amount_usd": 15.0, "pnl_pct": -0.3},
        ]

        status = manager.enforce_daily_loss_limit(sells, now_ts=1000)

        self.assertTrue(status["breached"])
        self.assertTrue(manager.runtime_status()["shutdown_active"])

    def test_abnormal_restart_enters_startup_cooldown(self) -> None:
        first = RuntimeSafetyManager(self.settings)
        second = RuntimeSafetyManager(self.settings)

        allowed, reason = second.can_open_new_positions()

        self.assertFalse(allowed)
        self.assertIn("startup cooldown active", reason)
        first.note_clean_shutdown()


if __name__ == "__main__":
    unittest.main()
