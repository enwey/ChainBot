from __future__ import annotations

import asyncio
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from investment_automation.position import PositionService
from investment_automation.scoring import SignalScorer
from investment_automation.settings import Settings
from investment_automation.watchlist import WatchlistService


class StubDatabase:
    def __init__(self, latest_metrics: dict | None = None) -> None:
        self.latest_metrics = latest_metrics or {}

    def latest_buy_metrics(self, addr: str) -> dict:
        return dict(self.latest_metrics)


class StubMarketData:
    def __init__(self, holder_metrics: dict | None = None) -> None:
        self.holder_metrics = holder_metrics or {}

    def fetch_holder_metrics(self, addr: str) -> dict:
        return dict(self.holder_metrics)


class PositionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        env = {
            "APP_BASE_DIR": str(ROOT),
            "WEB_DIR": "web",
            "DATA_DIR": "data",
            "APP_HOST": "127.0.0.1",
            "APP_PORT": "8000",
            "EXECUTION_MODE": "paper",
            "ENABLE_LIVE_TRADING": "false",
            "POSITION_SIZE_USD": "15",
            "INITIAL_BALANCE_USD": "200",
            "MAX_OPEN_POSITIONS": "3",
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
        self.env_patcher = patch.dict(os.environ, env, clear=True)
        self.env_patcher.start()
        self.settings = Settings.from_env()
        self.scorer = SignalScorer(self.settings)
        self.watchlist_service = WatchlistService(self.settings, self.scorer)

    def tearDown(self) -> None:
        self.env_patcher.stop()

    def make_service(self, *, latest_metrics: dict | None = None, holder_metrics: dict | None = None) -> PositionService:
        return PositionService(
            self.settings,
            self.scorer,
            self.watchlist_service,
            StubDatabase(latest_metrics),
            StubMarketData(holder_metrics),
        )

    def _buy_time(self, ts: int) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))

    def test_evaluate_position_returns_emergency_stop_for_deep_loss(self) -> None:
        service = self.make_service(holder_metrics={"holder_count_estimate": 0})

        decision = asyncio.run(
            service.evaluate_position(
                {
                    "addr": "token-1",
                    "symbol": "AIBOT",
                    "buy_price": 1.0,
                    "max_price": 1.0,
                    "amount_usd": 10.0,
                    "entry_liquidity": 5000.0,
                    "buy_time": self._buy_time(990),
                    "mode": "paper",
                    "moonbag_active": 0,
                    "price_source": "pumpportal",
                },
                {
                    "price": 0.70,
                    "source": "pumpportal",
                    "timestamp": 1000,
                    "liquidity_usd": 4500.0,
                    "volume_5m": 50.0,
                },
                now_ts=1000,
            )
        )

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.exit_reason, "emergency_stop")
        self.assertEqual(decision.sell_fraction, 1.0)
        self.assertEqual(decision.sell_amount_usd, 10.0)

    def test_evaluate_position_returns_holder_risk_cut_when_distribution_is_extreme(self) -> None:
        service = self.make_service(
            holder_metrics={
                "holder_count_estimate": 5,
                "top10_owner_pct": 0.90,
                "top20_owner_pct": 0.95,
                "largest_owner_pct": 0.50,
            }
        )

        decision = asyncio.run(
            service.evaluate_position(
                {
                    "addr": "token-2",
                    "symbol": "GROK",
                    "buy_price": 1.0,
                    "max_price": 1.05,
                    "amount_usd": 12.0,
                    "entry_liquidity": 5000.0,
                    "buy_time": self._buy_time(990),
                    "mode": "paper",
                    "moonbag_active": 0,
                    "price_source": "pumpportal",
                },
                {
                    "price": 1.10,
                    "source": "pumpportal",
                    "timestamp": 1000,
                    "liquidity_usd": 5300.0,
                    "volume_5m": 100.0,
                },
                now_ts=1000,
            )
        )

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.exit_reason, "holder_risk_cut")
        self.assertIn("top10=90.0%", decision.exit_context["holder_risk_reason"])

    def test_evaluate_position_returns_zombie_exit_for_stale_non_live_position(self) -> None:
        service = self.make_service(latest_metrics={}, holder_metrics={"holder_count_estimate": 0})

        decision = asyncio.run(
            service.evaluate_position(
                {
                    "addr": "token-3",
                    "symbol": "SOLAI",
                    "buy_price": 1.0,
                    "max_price": 1.15,
                    "amount_usd": 15.0,
                    "entry_liquidity": 5000.0,
                    "buy_time": self._buy_time(0),
                    "mode": "paper",
                    "moonbag_active": 0,
                    "price_source": "dexscreener",
                    "price_updated_ts": 500,
                },
                {
                    "price": 1.10,
                    "source": "dexscreener",
                    "timestamp": 500,
                    "liquidity_usd": 5000.0,
                    "volume_5m": 100.0,
                },
                now_ts=1000,
            )
        )

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.exit_reason, "zombie_position_exit")
        self.assertIn("zombie_reason", decision.exit_context)


if __name__ == "__main__":
    unittest.main()
