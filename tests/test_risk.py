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

from investment_automation.models import CandidateScan, StrategyState, TradeHistoryRow
from investment_automation.risk import RiskService
from investment_automation.settings import Settings


class RiskServiceTests(unittest.TestCase):
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
        self.service = RiskService(self.settings)

    def tearDown(self) -> None:
        self.env_patcher.stop()

    def test_evaluate_strategy_state_enters_cooldown_after_losses(self) -> None:
        sells = [
            {"pnl_pct": -0.25},
            {"pnl_pct": -0.12},
            {"pnl_pct": -0.08},
            {"pnl_pct": -0.02},
        ]

        state = self.service.evaluate_strategy_state(
            sells,
            StrategyState(max_open_positions=self.settings.max_open_positions, reason="seed"),
            now_ts=1000,
        )

        self.assertEqual(state.mode, "cooldown")
        self.assertFalse(state.instant_probe_enabled)
        self.assertGreater(state.cooldown_until_ts, 1000)

    def test_evaluate_strategy_state_accepts_trade_models(self) -> None:
        sells = [
            TradeHistoryRow.from_mapping({"pnl_pct": -0.25, "side": "sell"}),
            TradeHistoryRow.from_mapping({"pnl_pct": -0.12, "side": "sell"}),
            TradeHistoryRow.from_mapping({"pnl_pct": -0.08, "side": "sell"}),
            TradeHistoryRow.from_mapping({"pnl_pct": -0.02, "side": "sell"}),
        ]

        state = self.service.evaluate_strategy_state(
            sells,
            StrategyState(max_open_positions=self.settings.max_open_positions, reason="seed"),
            now_ts=1000,
        )

        self.assertEqual(state.mode, "cooldown")

    def test_should_open_position_accepts_valid_candidate(self) -> None:
        strategy_state = StrategyState(
            max_open_positions=self.settings.max_open_positions, reason="seed"
        )
        decision = self.service.should_open_position(
            scan=CandidateScan.from_mapping(
                {
                    "addr": "token-1",
                    "score": 92,
                    "dev_buy": 2.1,
                    "liquidity": 6000,
                    "created_ts": 995,
                }
            ),
            wallet={"current_balance": 100},
            strategy_state=strategy_state,
            has_position=False,
            open_position_count=0,
            last_buy_ts=0.0,
            now_ts=1000.0,
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "ok")

    def test_position_size_usd_applies_strategy_and_risk_multipliers(self) -> None:
        strategy_state = StrategyState(
            mode="aggressive",
            position_multiplier=1.15,
            max_open_positions=self.settings.max_open_positions,
            reason="seed",
        )

        size = self.service.position_size_usd(
            scan={
                "score": 95,
                "scalp_override": True,
                "bundle_risk_score": 0.2,
                "narrative_score": 10,
            },
            wallet={"current_balance": 500},
            strategy_state=strategy_state,
        )

        self.assertEqual(size, 11.64)


if __name__ == "__main__":
    unittest.main()
