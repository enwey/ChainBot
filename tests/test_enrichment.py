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

from investment_automation.enrichment import EnrichmentService
from investment_automation.scoring import SignalScorer
from investment_automation.settings import Settings


class StubMarketData:
    def __init__(self) -> None:
        self.metadata = {
            "meta-1": {
                "symbol": "META",
                "twitter": "https://x.com/meta",
                "telegram": "https://t.me/meta",
            }
        }
        self.fetch_holder_metrics_result = {
            "holder_count_estimate": 32,
            "top10_owner_pct": 0.22,
            "top20_owner_pct": 0.34,
            "largest_owner_pct": 0.07,
        }
        self.fetch_token_metadata_calls: list[list[str]] = []

    def get_token_metadata(self, addr: str) -> dict:
        return dict(self.metadata.get(addr, {}))

    def fetch_holder_metrics(self, addr: str) -> dict:
        return dict(self.fetch_holder_metrics_result)

    def fetch_token_metadata(self, addresses: list[str]) -> dict:
        self.fetch_token_metadata_calls.append(list(addresses))
        return {address: self.get_token_metadata(address) for address in addresses}


class EnrichmentServiceTests(unittest.TestCase):
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
        self.market_data = StubMarketData()
        self.scorer = SignalScorer(self.settings)
        self.service = EnrichmentService(self.settings, self.market_data, self.scorer)

    def tearDown(self) -> None:
        self.env_patcher.stop()

    def test_enrich_scan_metadata_fills_symbol_and_missing_socials(self) -> None:
        scan = {
            "addr": "meta-1",
            "symbol": "UNKNOWN",
            "socials": '{"twitter": null, "telegram": null, "website": null}',
        }
        source = {"website": "https://meta.example", "telegram": ""}

        self.service.enrich_scan_metadata(scan, source)

        self.assertEqual(scan["symbol"], "META")
        self.assertIn("https://x.com/meta", scan["socials"])
        self.assertIn("https://meta.example", scan["socials"])

    def test_refresh_candidate_holder_metrics_updates_scan_and_timestamp(self) -> None:
        candidate = {
            "scan": self._scan("holder-1"),
            "holder_refreshed_ts": 0,
        }

        asyncio.run(self.service.refresh_candidate_holder_metrics("holder-1", candidate, now_ts=100))

        self.assertEqual(candidate["holder_refreshed_ts"], 100)
        self.assertEqual(candidate["scan"]["holder_count_estimate"], 32)
        self.assertIn("bundle_risk_score", candidate["scan"])

    def test_holder_enriched_scan_returns_scored_snapshot(self) -> None:
        result = asyncio.run(self.service.holder_enriched_scan(self._scan("holder-scan"), now_ts=1000))

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["holder_count_estimate"], 32)
        self.assertIn("bundle_risk_score", result)
        self.assertIn("score", result)
        self.assertEqual(result["scan_time"], time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(1000)))

    def test_refresh_unknown_metadata_fetches_only_unknown_symbols_after_interval(self) -> None:
        radar_pool = {
            "meta-1": {"scan": {"symbol": "UNKNOWN"}},
            "known-1": {"scan": {"symbol": "KNOWN"}},
        }
        watchlist = {
            "meta-2": {"scan": {"symbol": ""}},
        }

        asyncio.run(self.service.refresh_unknown_metadata((radar_pool, watchlist), now_ts=100.0))
        asyncio.run(self.service.refresh_unknown_metadata((radar_pool, watchlist), now_ts=110.0))
        asyncio.run(self.service.refresh_unknown_metadata((radar_pool, watchlist), now_ts=116.0))

        self.assertEqual(self.market_data.fetch_token_metadata_calls[0], ["meta-1", "meta-2"])
        self.assertEqual(len(self.market_data.fetch_token_metadata_calls), 2)

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
            "created_ts": 990,
            "narrative_score": 0.0,
        }


if __name__ == "__main__":
    unittest.main()
