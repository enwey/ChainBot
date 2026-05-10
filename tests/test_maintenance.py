from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from investment_automation.app import Application
from investment_automation.db import Database
from investment_automation.maintenance import MaintenanceService
from investment_automation.settings import Settings


def timestamp_text(timestamp: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))


class MaintenanceTests(unittest.TestCase):
    def build_settings(self, base_dir: Path, **overrides: str) -> Settings:
        env = {
            "APP_BASE_DIR": str(base_dir),
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
        env.update(overrides)
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
            settings.validate()
        return settings

    def record_scan(self, database: Database, *, addr: str, symbol: str, seen_ts: int) -> None:
        database.record_scan(
            {
                "scan_time": timestamp_text(seen_ts),
                "platform": "pumpfun",
                "symbol": symbol,
                "age": "1m",
                "addr": addr,
                "price": 1.0,
                "liquidity": 3200.0,
                "dev_buy": 2.4,
                "progress": "50%",
                "ratio": "1.0",
                "score": 77.0,
                "first_seen_ts": seen_ts,
                "created_ts": seen_ts,
                "holder_count_estimate": 0.0,
                "top10_owner_pct": 0.0,
                "top20_owner_pct": 0.0,
                "largest_owner_pct": 0.0,
                "bundle_risk_score": 0.0,
                "socials": "",
                "dex_url": "",
            }
        )

    def record_opportunity(self, database: Database, *, addr: str, symbol: str, scan_ts: int) -> None:
        database.record_opportunity(
            {
                "type": "watch",
                "symbol": symbol,
                "score": 88.0,
                "reason": "test",
                "addr": addr,
                "liq": 4200.0,
                "vol": 180.0,
                "dex_url": "",
                "scan_time": timestamp_text(scan_ts),
                "socials": "",
                "progress": "80%",
            }
        )

    def record_news_event(
        self,
        database: Database,
        *,
        event_id: str,
        title: str,
        published_ts: int,
        active_until_ts: int,
    ) -> None:
        database.record_news_events(
            [
                SimpleNamespace(
                    event_id=event_id,
                    source="feed",
                    title=title,
                    url=f"https://example.com/{event_id}",
                    published_ts=published_ts,
                    fetched_ts=published_ts,
                    topics=["ai"],
                    entities=["solana"],
                    sentiment="bullish",
                    confidence=0.8,
                    source_weight=0.7,
                    active_until_ts=active_until_ts,
                    raw_text=title,
                )
            ]
        )

    def record_decision_audit(self, database: Database, *, addr: str, symbol: str, decision_ts: int) -> None:
        database.record_decision_audit(
            {
                "decision_ts": decision_ts,
                "decision_time": timestamp_text(decision_ts),
                "category": "entry",
                "action": "open_position",
                "outcome": "blocked",
                "symbol": symbol,
                "addr": addr,
                "reason": "test",
                "context": {"score": 80},
            }
        )

    def test_startup_cleanup_removes_expired_log_rows(self) -> None:
        now_ts = 1_750_000_000
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            (base_dir / "web").mkdir()
            settings = self.build_settings(base_dir)
            database = Database(settings)
            database.initialize()

            self.record_scan(database, addr="stale-scan", symbol="OLD", seen_ts=now_ts - (10 * 86400))
            self.record_scan(database, addr="fresh-scan", symbol="NEW", seen_ts=now_ts - (2 * 86400))
            self.record_opportunity(database, addr="stale-opp", symbol="OLD", scan_ts=now_ts - (20 * 86400))
            self.record_opportunity(database, addr="fresh-opp", symbol="NEW", scan_ts=now_ts - (2 * 86400))
            self.record_news_event(
                database,
                event_id="stale-news",
                title="Stale news",
                published_ts=now_ts - (7 * 86400),
                active_until_ts=now_ts - (4 * 86400),
            )
            self.record_news_event(
                database,
                event_id="fresh-news",
                title="Fresh news",
                published_ts=now_ts - (2 * 86400),
                active_until_ts=now_ts - 86400,
            )
            self.record_decision_audit(database, addr="stale-decision", symbol="OLD", decision_ts=now_ts - (35 * 86400))
            self.record_decision_audit(database, addr="fresh-decision", symbol="NEW", decision_ts=now_ts - (5 * 86400))

            result = MaintenanceService(settings, database, clock=lambda: now_ts).run_startup_tasks()
            self.assertEqual(result.scan_logs_deleted, 1)
            self.assertEqual(result.opportunities_deleted, 1)
            self.assertEqual(result.news_events_deleted, 1)
            self.assertEqual(result.decision_audits_deleted, 1)
            self.assertEqual(result.total_deleted, 4)
            self.assertEqual([row["addr"] for row in database.get_scan_logs(limit=10)], ["fresh-scan"])
            self.assertEqual([row["addr"] for row in database.get_opportunities(limit=10)], ["fresh-opp"])
            self.assertEqual([row["event_id"] for row in database.get_news_events(limit=10)], ["fresh-news"])
            self.assertEqual([row["addr"] for row in database.get_decision_audit(limit=10)], ["fresh-decision"])

    def test_bootstrap_logs_and_continues_when_maintenance_fails(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            (base_dir / "web").mkdir()
            settings = self.build_settings(base_dir)
            database = Mock()
            maintenance = Mock()
            maintenance.run_startup_tasks.side_effect = RuntimeError("boom")

            with patch("investment_automation.app.Database", return_value=database), patch(
                "investment_automation.app.MaintenanceService",
                return_value=maintenance,
            ), patch("investment_automation.app.MarketDataClient", return_value=Mock()), patch(
                "investment_automation.app.NewsClient",
                return_value=Mock(),
            ), patch("investment_automation.app.TradeExecutor", return_value=Mock()), patch(
                "investment_automation.app.TradingEngine",
                return_value=Mock(),
            ), patch("investment_automation.app.create_server", return_value=Mock()):
                app = Application(settings)
                with self.assertLogs("investment_automation.app", level="ERROR") as logs:
                    app.bootstrap()

        database.initialize.assert_called_once_with()
        maintenance.run_startup_tasks.assert_called_once_with()
        self.assertIn("Startup maintenance failed", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
