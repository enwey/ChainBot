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

from investment_automation.observation import ObservationService
from investment_automation.scoring import SignalScorer
from investment_automation.settings import Settings
from investment_automation.watchlist import WatchlistService


class ObservationServiceTests(unittest.TestCase):
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
            "MAX_RADAR_TRACKED_TOKENS": "2",
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
        self.service = ObservationService(self.settings, self.scorer)

    def tearDown(self) -> None:
        self.env_patcher.stop()

    def test_track_and_prune_radar_pool_respects_capacity_and_active_sets(self) -> None:
        radar_pool: dict[str, dict] = {}

        self.service.track_radar_candidate(radar_pool, self._scan("a1"), now_ts=100)
        self.service.track_radar_candidate(radar_pool, self._scan("a2"), now_ts=101)
        self.service.track_radar_candidate(radar_pool, self._scan("a3"), now_ts=102)

        self.assertNotIn("a1", radar_pool)
        self.assertEqual(set(radar_pool.keys()), {"a2", "a3"})

        self.service.prune_radar_pool(radar_pool, ["a2"], ["a3"], now_ts=103)

        self.assertEqual(radar_pool, {})

    def test_process_radar_candidate_rejects_abnormal_drawn_line(self) -> None:
        radar_pool: dict[str, dict] = {}
        self.service.track_radar_candidate(radar_pool, self._scan("radar-1"), now_ts=100)
        candidate = radar_pool["radar-1"]
        candidate["samples"] = [
            {"ts": 100, "price": 1.00, "volume_5m": 12.0},
            {"ts": 104, "price": 1.08, "volume_5m": 14.0},
            {"ts": 108, "price": 1.16, "volume_5m": 16.0},
            {"ts": 112, "price": 1.24, "volume_5m": 18.0},
            {"ts": 116, "price": 1.32, "volume_5m": 20.0},
        ]
        candidate["highest_price"] = 1.32

        decision = self.service.process_radar_candidate(
            candidate,
            {"price": 1.40, "liquidity_usd": 4300, "volume_5m": 22.0, "timestamp": 120},
            now_ts=120,
        )

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertFalse(decision.record_scan)
        self.assertIsNotNone(decision.rejected_reason)

    def test_process_watch_candidate_returns_pending_reason_before_window(self) -> None:
        candidate = self.watchlist_service.build_watch_candidate(
            self._scan("watch-pending"), now_ts=100
        )

        decision = self.service.process_watch_candidate(
            candidate,
            {
                "price": 1.02,
                "liquidity_usd": 4200,
                "volume_5m": 1.5,
                "timestamp": 110,
                "source": "pumpportal",
            },
            self._holder_metrics(holders=28, top10=0.24, top20=0.34, largest=0.08),
            "normal",
            now_ts=110,
        )

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertTrue(decision.record_scan)
        self.assertIsNone(decision.rejected_reason)
        self.assertIsNotNone(decision.watch_reason)
        self.assertIsNone(decision.open_reason)

    def test_process_watch_candidate_returns_confirmed_open_reason(self) -> None:
        candidate = self.watchlist_service.build_watch_candidate(
            self._scan("watch-open"), now_ts=100
        )

        decision = self.service.process_watch_candidate(
            candidate,
            {
                "price": 1.08,
                "liquidity_usd": 4600,
                "volume_5m": 2.0,
                "timestamp": 140,
                "source": "pumpportal",
            },
            self._holder_metrics(holders=36, top10=0.22, top20=0.33, largest=0.07),
            "normal",
            now_ts=140,
        )

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertTrue(decision.record_scan)
        self.assertIsNone(decision.rejected_reason)
        self.assertIsNone(decision.watch_reason)
        self.assertIsNotNone(decision.open_reason)

    def test_process_watch_candidate_allows_scalp_override_for_high_risk_distribution(self) -> None:
        candidate = self.watchlist_service.build_watch_candidate(
            self._scan("watch-scalp"), now_ts=100
        )

        decision = self.service.process_watch_candidate(
            candidate,
            {
                "price": 1.20,
                "liquidity_usd": 4200,
                "volume_5m": 3.0,
                "timestamp": 140,
                "source": "pumpportal",
            },
            self._holder_metrics(holders=4, top10=0.85, top20=0.92, largest=0.20),
            "normal",
            now_ts=140,
        )

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertIsNone(decision.rejected_reason)
        self.assertIsNone(decision.watch_reason)
        self.assertIsNotNone(decision.open_reason)
        self.assertTrue(decision.scan["scalp_override"])
        self.assertIn("scalp override", decision.scan["scalp_reason"])
        self.assertGreaterEqual(float(decision.scan["score"]), self.settings.min_score_to_buy)

    def _scan(self, addr: str) -> dict:
        return {
            "addr": addr,
            "symbol": "AIBOT",
            "score": 78.0,
            "price": 1.0,
            "liquidity": 4000.0,
            "dev_buy": 1.6,
            "progress": "0%",
            "dex_url": "https://dex.example/token",
            "scan_time": "",
            "socials": '{"twitter":"https://x.com/aibot","telegram":null,"website":null}',
            "created_ts": 95,
            "narrative_score": 0.0,
        }

    def _holder_metrics(self, *, holders: int, top10: float, top20: float, largest: float) -> dict:
        return {
            "holder_count_estimate": holders,
            "top10_owner_pct": top10,
            "top20_owner_pct": top20,
            "largest_owner_pct": largest,
        }


if __name__ == "__main__":
    unittest.main()
