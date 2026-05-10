from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from investment_automation.db import Database
from investment_automation.settings import Settings


class ReplaySeedLoaderTests(unittest.TestCase):
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
            "PUMPPORTAL_WS_URL": "wss://pumpportal.fun/api/data",
        }
        with patch.dict(os.environ, env, clear=True):
            self.settings = Settings.from_env()
            self.settings.validate()
        self.database = Database(self.settings)
        self.database.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_loader_imports_fixture_into_sqlite(self) -> None:
        dataset = ROOT / "tests" / "fixtures" / "replay_cases" / "successful_entry.json"
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "replay" / "load_seed_dataset.py"),
                str(dataset),
                "--database",
                str(self.settings.database_path),
                "--reset-replay-tables",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn('"dataset_id": "successful-entry"', result.stdout)

        restored = Database(self.settings)
        decisions = restored.get_decision_audit_for_addr("seed-successful-entry", limit=5)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["outcome"], "executed")

        portfolio = restored.get_portfolio_position("seed-successful-entry")
        self.assertIsNotNone(portfolio)
        self.assertEqual(str(portfolio["symbol"]), "WIN")

        trades = restored.get_trade_history_for_addr("seed-successful-entry", limit=5)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["side"], "buy")


if __name__ == "__main__":
    unittest.main()
