from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from investment_automation.scoring import SignalScorer
from investment_automation.settings import Settings


class SignalScorerTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.env_patcher.stop()

    def test_launch_quality_accumulates_expected_signals(self) -> None:
        score, signals = self.scorer.launch_quality(
            {
                "symbol": "AIBOT",
                "dev_buy": 2.0,
                "liquidity": 5000,
                "narrative_score": 18,
                "socials": '{"twitter":"https://x.com/aibot","telegram":null,"website":null}',
                "score": 95,
            }
        )

        self.assertEqual(score, 100.0)
        self.assertEqual(
            signals,
            ["clean_symbol", "dev_buy_ok", "early_liquidity_ok", "narrative", "socials", "high_base_score"],
        )

    def test_holder_ok_rejects_overconcentrated_supply(self) -> None:
        allowed, reason = self.scorer.holder_ok(
            {
                "holder_count_estimate": 64,
                "top10_owner_pct": 0.52,
                "top20_owner_pct": 0.61,
            }
        )

        self.assertFalse(allowed)
        self.assertIn("top10", reason)

    def test_market_quality_score_caps_when_bundle_risk_is_high(self) -> None:
        score = self.scorer.market_quality_score(
            {
                "symbol": "AIBOT",
                "liquidity": 5000,
                "dev_buy": 2.2,
                "realtime_volume_5m": 4.0,
                "holder_count_estimate": 5,
                "top10_owner_pct": 0.90,
                "largest_owner_pct": 0.50,
                "created_ts": 995,
                "narrative_score": 10,
            },
            price_change_pct=0.10,
            liquidity_ratio=1.2,
            elapsed=20,
            now_ts=1000,
        )

        self.assertEqual(score, 45.0)


if __name__ == "__main__":
    unittest.main()
