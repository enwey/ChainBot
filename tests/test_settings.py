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

from investment_automation.settings import Settings, SettingsValidationError


class SettingsTests(unittest.TestCase):
    def base_env(self) -> dict[str, str]:
        return {
            "APP_BASE_DIR": str(ROOT),
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

    def test_from_env_reads_current_environment(self) -> None:
        env = self.base_env()
        env["POSITION_SIZE_USD"] = "42.5"

        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()

        self.assertEqual(settings.position_size_usd, 42.5)

    def test_from_env_applies_maintenance_defaults(self) -> None:
        with patch.dict(os.environ, self.base_env(), clear=True):
            settings = Settings.from_env()

        self.assertTrue(settings.maintenance_enabled)
        self.assertEqual(settings.scan_log_retention_days, 7)
        self.assertEqual(settings.opportunity_retention_days, 14)
        self.assertEqual(settings.news_event_retention_days, 3)
        self.assertEqual(settings.decision_audit_retention_days, 30)

    def test_validate_rejects_live_mode_without_private_key(self) -> None:
        env = self.base_env()
        env["EXECUTION_MODE"] = "live"
        env["ENABLE_LIVE_TRADING"] = "true"

        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
            with self.assertRaises(SettingsValidationError):
                settings.validate()

    def test_validate_rejects_negative_retention_days(self) -> None:
        env = self.base_env()
        env["DECISION_AUDIT_RETENTION_DAYS"] = "-1"

        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
            with self.assertRaises(SettingsValidationError):
                settings.validate()

    def test_validate_rejects_public_bind_without_admin_token(self) -> None:
        env = self.base_env()
        env["APP_HOST"] = "0.0.0.0"

        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
            with self.assertRaises(SettingsValidationError):
                settings.validate()


if __name__ == "__main__":
    unittest.main()
