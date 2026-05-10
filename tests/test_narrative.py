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

from investment_automation.narrative import NarrativeService
from investment_automation.settings import Settings


class StubDatabase:
    def __init__(self, events: list[dict]) -> None:
        self._events = events

    def active_news_events(self) -> list[dict]:
        return list(self._events)


class NarrativeServiceTests(unittest.TestCase):
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
            "NARRATIVE_SCORE_BONUS": "10",
            "TRUSTED_KOL_SCORE_BONUS": "12",
            "NARRATIVE_MAX_BONUS": "22",
            "NEWS_MATCH_BONUS": "12",
            "NEWS_MAX_BONUS": "24",
            "NEWS_MIN_CONFIDENCE": "0.35",
        }
        self.env_patcher = patch.dict(os.environ, env, clear=True)
        self.env_patcher.start()
        self.settings = Settings.from_env()

    def tearDown(self) -> None:
        self.env_patcher.stop()

    def test_apply_overlay_adds_keyword_and_news_bonus(self) -> None:
        service = NarrativeService(
            self.settings,
            StubDatabase(
                [
                    {
                        "title": "Solana AI momentum",
                        "source": "coindesk",
                        "sentiment": "bullish",
                        "confidence": 0.85,
                        "source_weight": 0.9,
                        "entities": ["openai", "solana"],
                        "topics": ["ai"],
                    }
                ]
            ),
        )
        scan = {"score": 62.0}
        payload = {
            "symbol": "AIBOT",
            "name": "OpenAI Solana Bot",
            "description": "Elon and Solana communities are watching this AI launch.",
            "twitter": "https://x.com/aibot",
            "telegram": "",
            "website": "",
        }

        result = service.apply_overlay(scan, payload)

        self.assertEqual(result["score"], 96.0)
        self.assertEqual(result["narrative_score"], 34.0)
        self.assertEqual(result["narrative_label"], "ai,elon,openai,solana")
        self.assertEqual(result["narrative_tags"], ["ai", "elon", "openai", "solana"])
        self.assertEqual(len(result["news_matches"]), 1)

    def test_apply_overlay_resets_fields_when_no_matches_exist(self) -> None:
        service = NarrativeService(self.settings, StubDatabase([]))
        scan = {"score": 41.0}
        payload = {
            "symbol": "ZZZ",
            "name": "Quiet token",
            "description": "No narrative here.",
            "twitter": "",
            "telegram": "",
            "website": "",
        }

        result = service.apply_overlay(scan, payload)

        self.assertEqual(result["score"], 41.0)
        self.assertEqual(result["narrative_score"], 0.0)
        self.assertEqual(result["narrative_tags"], [])
        self.assertEqual(result["narrative_label"], "-")
        self.assertEqual(result["news_matches"], [])

    def test_match_news_events_filters_low_confidence_records(self) -> None:
        service = NarrativeService(
            self.settings,
            StubDatabase(
                [
                    {
                        "title": "Low confidence rumor",
                        "source": "unknown",
                        "sentiment": "bullish",
                        "confidence": 0.2,
                        "source_weight": 0.4,
                        "entities": ["solana"],
                        "topics": ["ai"],
                    },
                    {
                        "title": "Confirmed Solana AI story",
                        "source": "coindesk",
                        "sentiment": "bullish",
                        "confidence": 0.8,
                        "source_weight": 0.8,
                        "entities": ["solana"],
                        "topics": ["ai"],
                    },
                ]
            ),
        )

        matches = service.match_news_events("solana ai token launch")

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["title"], "Confirmed Solana AI story")


if __name__ == "__main__":
    unittest.main()
