from __future__ import annotations

import asyncio
import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from investment_automation.db import Database
from investment_automation.engine import TradingEngine
from investment_automation.execution import ExecutionResult
from investment_automation.models import CandidateScan, PortfolioPosition
from investment_automation.position import PositionExitDecision
from investment_automation.settings import Settings


class StubMarketData:
    def get_token_metadata(self, addr: str) -> dict:
        return {}

    def fetch_holder_metrics(self, addr: str) -> dict:
        return {}


class StubExecutor:
    def __init__(self) -> None:
        self.buy_result = ExecutionResult(executed=True, mode="paper", tx_hash="tx-buy")
        self.sell_result = ExecutionResult(executed=True, mode="paper", tx_hash="tx-sell")

    def buy(
        self, token_mint: str, amount_usd: float, *, idempotency_key: str | None = None
    ) -> ExecutionResult:
        return self.buy_result

    def sell(
        self, token_mint: str, amount_usd: float, *, idempotency_key: str | None = None
    ) -> ExecutionResult:
        return self.sell_result


class DecisionAuditTests(unittest.TestCase):
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
        self.market_data = StubMarketData()
        self.executor = StubExecutor()
        self.engine = TradingEngine(self.settings, self.database, self.market_data, self.executor)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_records_blocked_and_opened_entry_decisions(self) -> None:
        blocked_scan = CandidateScan.from_mapping(self._scan("token-blocked", "BLK"))
        self.engine._should_open_position = lambda scan, wallet: (
            False,
            "score below adaptive threshold 85",
        )  # type: ignore[method-assign]

        asyncio.run(self.engine._maybe_open_position(blocked_scan))

        opened_scan = CandidateScan.from_mapping(self._scan("token-opened", "OPN"))
        self.engine._should_open_position = lambda scan, wallet: (True, "ok")  # type: ignore[method-assign]
        self.engine._position_size_usd = lambda scan, wallet: 12.5  # type: ignore[method-assign]

        asyncio.run(self.engine._maybe_open_position(opened_scan))

        audits = self.database.get_decision_audit(limit=10)
        self.assertEqual(audits[0]["category"], "entry")
        self.assertEqual(audits[0]["outcome"], "executed")
        self.assertEqual(audits[0]["addr"], "token-opened")
        self.assertEqual(audits[0]["reason"], "position_opened")
        self.assertEqual(audits[0]["context"]["position_size_usd"], 12.5)
        self.assertEqual(audits[0]["snapshot"]["kind"], "entry")
        self.assertEqual(audits[0]["snapshot"]["candidate"]["symbol"], "OPN")
        self.assertEqual(audits[0]["snapshot"]["wallet"]["current_balance"], 200.0)
        self.assertEqual(audits[1]["category"], "entry")
        self.assertEqual(audits[1]["outcome"], "blocked")
        self.assertEqual(audits[1]["addr"], "token-blocked")
        self.assertIn("adaptive threshold", audits[1]["reason"])
        self.assertEqual(audits[1]["snapshot"]["kind"], "entry")
        self.assertEqual(audits[1]["snapshot"]["candidate"]["addr"], "token-blocked")

    def test_records_exit_executed_and_execution_skipped_decisions(self) -> None:
        self.database.open_position(
            "token-exit",
            "EXT",
            1.0,
            15.0,
            "paper",
            "tx-buy-1",
            entry_liquidity=4200.0,
        )
        position = PortfolioPosition.from_mapping(self.database.open_positions()[0])
        decision = PositionExitDecision(
            current_price=1.2,
            sell_price=1.18,
            max_price=1.25,
            pnl_pct=0.18,
            hold_seconds=45,
            exit_reason="profit_lock_moonbag",
            exit_context={"peak_gain_pct": 0.25},
            sell_fraction=1.0,
            sell_amount_usd=15.0,
            remaining_amount_usd=0.0,
        )
        self.engine.position_service.evaluate_position = AsyncMock(return_value=decision)  # type: ignore[method-assign]

        asyncio.run(
            self.engine._process_position(
                position,
                {
                    "token-exit": {
                        "price": 1.2,
                        "timestamp": int(time.time()),
                        "source": "pumpportal",
                    }
                },
            )
        )

        self.database.open_position(
            "token-skip",
            "SKP",
            1.0,
            10.0,
            "paper",
            "tx-buy-2",
            entry_liquidity=4200.0,
        )
        skipped_position = next(
            item for item in self.database.open_positions() if item["addr"] == "token-skip"
        )
        skipped_decision = PositionExitDecision(
            current_price=1.1,
            sell_price=1.08,
            max_price=1.2,
            pnl_pct=0.08,
            hold_seconds=30,
            exit_reason="profit_lock_weak_follow",
            exit_context={"volume_to_position_ratio": 0.8},
            sell_fraction=0.75,
            sell_amount_usd=7.5,
            remaining_amount_usd=2.5,
        )
        self.engine.position_service.evaluate_position = AsyncMock(return_value=skipped_decision)  # type: ignore[method-assign]
        self.executor.sell_result = ExecutionResult(
            executed=False, mode="paper", reason="venue unavailable"
        )

        asyncio.run(
            self.engine._process_position(
                skipped_position,
                {
                    "token-skip": {
                        "price": 1.1,
                        "timestamp": int(time.time()),
                        "source": "pumpportal",
                    }
                },
            )
        )

        audits = self.database.get_decision_audit(limit=10)
        self.assertEqual(audits[0]["category"], "exit")
        self.assertEqual(audits[0]["outcome"], "skipped")
        self.assertEqual(audits[0]["addr"], "token-skip")
        self.assertEqual(audits[0]["reason"], "profit_lock_weak_follow")
        self.assertEqual(audits[0]["context"]["execution_reason"], "venue unavailable")
        self.assertEqual(audits[0]["snapshot"]["kind"], "exit")
        self.assertEqual(audits[0]["snapshot"]["market_snapshot"]["source"], "pumpportal")
        self.assertEqual(
            audits[0]["snapshot"]["decision"]["exit_reason"], "profit_lock_weak_follow"
        )
        self.assertEqual(audits[1]["category"], "exit")
        self.assertEqual(audits[1]["outcome"], "executed")
        self.assertEqual(audits[1]["addr"], "token-exit")
        self.assertEqual(audits[1]["reason"], "profit_lock_moonbag")
        self.assertEqual(audits[1]["context"]["tx_hash"], "tx-sell")
        self.assertEqual(audits[1]["snapshot"]["position"]["symbol"], "EXT")
        self.assertEqual(audits[1]["snapshot"]["decision"]["sell_amount_usd"], 15.0)

    def test_blocks_duplicate_automated_entry_by_idempotency_key(self) -> None:
        scan = self._scan("token-dup-buy", "DBUY")
        self.engine._should_open_position = lambda scan, wallet: (True, "ok")  # type: ignore[method-assign]
        self.engine._position_size_usd = lambda scan, wallet: 12.5  # type: ignore[method-assign]
        self.engine._entry_idempotency_key = lambda scan, size: "dup-buy-key"  # type: ignore[method-assign]
        self.engine.runtime_safety.claim_order_key(
            "dup-buy-key",
            side="buy",
            token_mint=scan["addr"],
            amount_usd=12.5,
        )

        asyncio.run(self.engine._maybe_open_position(scan))

        audits = self.database.get_decision_audit(limit=5)
        self.assertEqual(audits[0]["category"], "entry")
        self.assertEqual(audits[0]["outcome"], "skipped")
        self.assertEqual(
            audits[0]["reason"], "duplicate automated buy order blocked by idempotency guard"
        )

    def test_blocks_duplicate_automated_exit_by_idempotency_key(self) -> None:
        self.database.open_position(
            "token-dup-sell",
            "DSEL",
            1.0,
            15.0,
            "paper",
            "tx-buy-dup",
            entry_liquidity=4200.0,
        )
        position = self.database.open_positions()[0]
        decision = PositionExitDecision(
            current_price=1.1,
            sell_price=1.08,
            max_price=1.15,
            pnl_pct=0.08,
            hold_seconds=30,
            exit_reason="profit_lock_weak_follow",
            exit_context={"volume_to_position_ratio": 0.8},
            sell_fraction=0.75,
            sell_amount_usd=7.5,
            remaining_amount_usd=2.5,
        )
        self.engine.position_service.evaluate_position = AsyncMock(return_value=decision)  # type: ignore[method-assign]
        self.engine._exit_idempotency_key = lambda position, decision, snapshot: "dup-sell-key"  # type: ignore[method-assign]
        self.engine.runtime_safety.claim_order_key(
            "dup-sell-key",
            side="sell",
            token_mint=position["addr"],
            amount_usd=7.5,
        )

        asyncio.run(
            self.engine._process_position(
                position,
                {
                    "token-dup-sell": {
                        "price": 1.1,
                        "timestamp": int(time.time()),
                        "source": "pumpportal",
                    }
                },
            )
        )

        audits = self.database.get_decision_audit(limit=5)
        self.assertEqual(audits[0]["category"], "exit")
        self.assertEqual(audits[0]["outcome"], "skipped")
        self.assertEqual(
            audits[0]["context"]["execution_reason"],
            "duplicate automated sell order blocked by idempotency guard",
        )

    def _scan(self, addr: str, symbol: str) -> dict:
        now_ts = int(time.time())
        return {
            "addr": addr,
            "symbol": symbol,
            "score": 92.0,
            "price": 0.25,
            "liquidity": 4200.0,
            "dev_buy": 1.6,
            "progress": "55%",
            "dex_url": "https://dexscreener.com/solana/test-token",
            "scan_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts)),
            "socials": '{"twitter":"https://x.com/test"}',
            "narrative_label": "ai",
            "narrative_score": 12.0,
            "narrative_tags": ["ai"],
            "bundle_risk_score": 0.1,
            "created_ts": now_ts,
        }


if __name__ == "__main__":
    unittest.main()
