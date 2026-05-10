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
from investment_automation.watchlist import WatchlistService


class WatchlistServiceTests(unittest.TestCase):
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
            "MAX_WATCHLIST_SIZE": "2",
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
        self.service = WatchlistService(self.settings, self.scorer)

    def tearDown(self) -> None:
        self.env_patcher.stop()

    def test_should_watch_accepts_strong_candidate_and_enriches_scan(self) -> None:
        scan = {
            "symbol": "AIBOT",
            "score": 91,
            "dev_buy": 2.2,
            "liquidity": 4500,
            "narrative_score": 8,
            "socials": '{"twitter":"https://x.com/aibot"}',
            "created_ts": 995,
        }

        allowed, reason = self.service.should_watch(scan, now_ts=1000)

        self.assertTrue(allowed)
        self.assertEqual(reason, "ok")
        self.assertGreater(float(scan["launch_quality_score"]), 0)
        self.assertIn("dev_buy_ok", scan["launch_signals"])

    def test_trim_to_limit_removes_lowest_quality_candidates(self) -> None:
        watchlist = {
            "good-1": self.service.build_watch_candidate(
                {
                    "addr": "good-1",
                    "symbol": "AIBOT",
                    "score": 92,
                    "dev_buy": 2.0,
                    "liquidity": 5000,
                    "price": 0.01,
                    "socials": '{"twitter":"x"}',
                },
                now_ts=1000,
            ),
            "weak": self.service.build_watch_candidate(
                {
                    "addr": "weak",
                    "symbol": "TEST",
                    "score": 72,
                    "dev_buy": 0.2,
                    "liquidity": 1800,
                    "price": 0.01,
                    "socials": "{}",
                },
                now_ts=1000,
            ),
            "good-2": self.service.build_watch_candidate(
                {
                    "addr": "good-2",
                    "symbol": "GROK",
                    "score": 90,
                    "dev_buy": 1.8,
                    "liquidity": 4700,
                    "price": 0.01,
                    "socials": '{"website":"x"}',
                },
                now_ts=1000,
            ),
        }

        removals = self.service.trim_to_limit(watchlist)

        self.assertEqual(len(removals), 1)
        self.assertEqual(removals[0][0], "weak")
        self.assertEqual(set(watchlist.keys()), {"good-1", "good-2"})

    def test_get_watchlist_status_reports_pending_snapshot(self) -> None:
        candidate = self.service.build_watch_candidate(
            {
                "addr": "token-1",
                "symbol": "AIBOT",
                "score": 90,
                "dev_buy": 2.1,
                "liquidity": 5000,
                "price": 0.01,
                "progress": "10%",
                "socials": '{"twitter":"x"}',
                "created_ts": 990,
                "holder_count_estimate": 0,
                "top10_owner_pct": 0.0,
                "top20_owner_pct": 0.0,
                "largest_owner_pct": 0.0,
                "bundle_risk_score": 0.0,
                "launch_quality_score": 60,
                "launch_signals": ["dev_buy_ok"],
                "dex_url": "https://pump.fun/token-1",
            },
            now_ts=1000,
        )
        candidate["snapshot_miss_count"] = 2
        watchlist = {"token-1": candidate}

        status = self.service.get_watchlist_status(watchlist, now_ts=1010)

        self.assertEqual(len(status), 1)
        self.assertEqual(status[0]["bundle_risk_label"], "待查")
        self.assertIn("第 2 次重试", status[0]["status_reason"])


if __name__ == "__main__":
    unittest.main()
