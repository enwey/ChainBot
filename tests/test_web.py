from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from investment_automation.db import Database
from investment_automation.execution import TradeExecutor
from investment_automation.market_data import MarketDataClient
from investment_automation.settings import Settings
from investment_automation.web import create_server


class StubEngine:
    def get_watchlist_status(self) -> list[dict]:
        return []

    def runtime_status(self) -> dict:
        return {
            "strategy_mode": "balanced",
            "strategy_reason": "test",
            "watchlist_size": 0,
            "radar_pool_size": 0,
            "open_positions": 0,
            "workers": {},
        }


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class WebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        (self.base_dir / "web").mkdir()
        (self.base_dir / "web" / "index.html").write_text("<html><body>ok</body></html>", encoding="utf-8")

        self.port = find_free_port()
        env = {
            "APP_BASE_DIR": str(self.base_dir),
            "WEB_DIR": "web",
            "DATA_DIR": "data",
            "APP_HOST": "127.0.0.1",
            "APP_PORT": str(self.port),
            "EXECUTION_MODE": "paper",
            "ENABLE_LIVE_TRADING": "false",
            "ADMIN_API_TOKEN": "secret-token",
        }

        with patch.dict(os.environ, env, clear=True):
            self.settings = Settings.from_env()
            self.settings.validate()

        self.database = Database(self.settings)
        self.database.initialize()
        self.market_data = MarketDataClient(self.settings)
        self.market_data.runtime_status = lambda: {  # type: ignore[method-assign]
            "stream_connected": True,
            "stream_task_running": True,
            "desired_token_subscriptions": 0,
            "active_token_subscriptions": 0,
            "cached_trade_snapshots": 0,
            "cached_token_metadata": 0,
            "cached_holder_metrics": 0,
        }
        self.executor = TradeExecutor(self.settings, self.market_data)
        self.server = create_server(
            settings=self.settings,
            database=self.database,
            executor=self.executor,
            engine=StubEngine(),
            market_data=self.market_data,
            started_at=int(time.time()),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        asyncio.run(self.market_data.aclose())
        self.executor.close()
        self.temp_dir.cleanup()

    def api_url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def request_json(
        self,
        path: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: dict[str, object] | bytes | None = None,
    ) -> tuple[int, object]:
        request_headers = dict(headers or {})
        data: bytes | None = None
        if isinstance(body, dict):
            data = json.dumps(body).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        elif isinstance(body, bytes):
            data = body
        elif method == "POST":
            data = b""

        request = urllib.request.Request(self.api_url(path), data=data, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_dashboard_template_contains_decision_timeline_controls(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Decision Timeline", html)
        self.assertIn("Token Case Timeline", html)
        self.assertIn("/api/v1/decisions", html)
        self.assertIn("decision-category", html)
        self.assertIn("decision-next", html)
        self.assertIn("decision-detail", html)

    def seed_scan(self, addr: str = "test-token", symbol: str = "TST") -> None:
        now_ts = int(time.time())
        self.database.record_scan(
            {
                "scan_time": "2026-05-10 09:30:00",
                "platform": "pumpfun",
                "symbol": symbol,
                "age": "1m",
                "addr": addr,
                "price": 0.25,
                "liquidity": 1234.0,
                "dev_buy": 1.6,
                "progress": "55%",
                "ratio": "3.1",
                "score": 87.0,
                "first_seen_ts": now_ts,
                "created_ts": now_ts,
                "holder_count_estimate": 42.0,
                "top10_owner_pct": 18.0,
                "top20_owner_pct": 27.0,
                "largest_owner_pct": 5.0,
                "bundle_risk_score": 0.0,
                "socials": '{"twitter":"https://x.com/test"}',
                "dex_url": "https://dexscreener.com/solana/test-token",
            }
        )

    def seed_decision(self, addr: str = "test-token", symbol: str = "TST") -> None:
        now_ts = int(time.time())
        self.database.record_decision_audit(
            {
                "decision_ts": now_ts,
                "decision_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts)),
                "category": "entry",
                "action": "open_position",
                "outcome": "blocked",
                "symbol": symbol,
                "addr": addr,
                "reason": "score below threshold",
                "context": {"score": 84.0},
                "snapshot": {
                    "kind": "entry",
                    "candidate": {"symbol": symbol, "addr": addr, "score": 84.0},
                    "wallet": {"current_balance": 200.0},
                },
            }
        )

    def seed_custom_decision(
        self,
        *,
        addr: str,
        symbol: str,
        decision_ts: int,
        category: str,
        action: str,
        outcome: str,
        reason: str,
        snapshot: dict | None = None,
    ) -> None:
        self.database.record_decision_audit(
            {
                "decision_ts": decision_ts,
                "decision_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(decision_ts)),
                "category": category,
                "action": action,
                "outcome": outcome,
                "symbol": symbol,
                "addr": addr,
                "reason": reason,
                "context": {"score": 84.0, "execution_mode": "paper"},
                "snapshot": snapshot or {
                    "kind": category,
                    "candidate": {"symbol": symbol, "addr": addr},
                },
            }
        )

    def test_versioned_read_endpoints_match_existing_routes(self) -> None:
        self.seed_scan()
        self.seed_decision()
        self.database.open_position(
            "test-token",
            "TST",
            0.25,
            self.settings.position_size_usd,
            "paper",
            "tx-buy-1",
            entry_liquidity=1234.0,
        )

        stable_paths = [
            "/all?limit=1",
            "/opps",
            "/decisions",
            "/wallet",
            "/portfolio",
            "/history",
            "/performance",
            "/strategy",
            "/watchlist",
            "/news",
        ]
        for suffix in stable_paths:
            legacy_status, legacy_payload = self.request_json(f"/api{suffix}")
            versioned_status, versioned_payload = self.request_json(f"/api/v1{suffix}")
            self.assertEqual(versioned_status, legacy_status)
            self.assertEqual(versioned_payload, legacy_payload)

        decision_status, decision_payload = self.request_json("/api/v1/decisions")
        self.assertEqual(decision_status, 200)
        self.assertIn("items", decision_payload)
        self.assertIn("pagination", decision_payload)
        self.assertIn("filters", decision_payload)
        self.assertEqual(len(decision_payload["items"]), 1)
        self.assertEqual(decision_payload["items"][0]["addr"], "test-token")
        self.assertEqual(decision_payload["pagination"]["total"], 1)
        self.assertEqual(decision_payload["filters"]["category"], None)

        detail_status, detail_payload = self.request_json(f"/api/v1/decisions/{decision_payload['items'][0]['id']}")
        self.assertEqual(detail_status, 200)
        self.assertEqual(detail_payload["addr"], "test-token")
        self.assertEqual(detail_payload["snapshot"]["kind"], "entry")
        self.assertEqual(detail_payload["snapshot"]["candidate"]["symbol"], "TST")

        legacy_status, legacy_payload = self.request_json("/api/status")
        versioned_status, versioned_payload = self.request_json("/api/v1/status")
        self.assertEqual(versioned_status, legacy_status)
        self.assertEqual(versioned_payload["service"], legacy_payload["service"])
        self.assertEqual(versioned_payload["version"], legacy_payload["version"])
        self.assertEqual(versioned_payload["environment"], legacy_payload["environment"])
        self.assertEqual(versioned_payload["mode"], legacy_payload["mode"])
        self.assertEqual(versioned_payload["live_enabled"], legacy_payload["live_enabled"])
        self.assertEqual(versioned_payload["binds_public_interface"], legacy_payload["binds_public_interface"])
        self.assertEqual(versioned_payload["market_data"], legacy_payload["market_data"])
        self.assertEqual(versioned_payload["executor"], legacy_payload["executor"])
        self.assertEqual(versioned_payload["engine"], legacy_payload["engine"])
        self.assertIsInstance(versioned_payload["uptime_seconds"], int)

        legacy_status, legacy_payload = self.request_json("/api/health")
        versioned_status, versioned_payload = self.request_json("/api/v1/health")
        self.assertEqual(versioned_status, legacy_status)
        self.assertEqual(versioned_payload["status"], legacy_payload["status"])
        self.assertEqual(versioned_payload["ready"], legacy_payload["ready"])
        self.assertEqual(versioned_payload["service"], legacy_payload["service"])
        self.assertEqual(versioned_payload["version"], legacy_payload["version"])
        self.assertEqual(versioned_payload["environment"], legacy_payload["environment"])
        self.assertEqual(versioned_payload["mode"], legacy_payload["mode"])
        self.assertEqual(versioned_payload["live_enabled"], legacy_payload["live_enabled"])
        self.assertEqual(versioned_payload["binds_public_interface"], legacy_payload["binds_public_interface"])
        self.assertEqual(versioned_payload["market_data"], legacy_payload["market_data"])
        self.assertEqual(versioned_payload["executor"], legacy_payload["executor"])
        self.assertEqual(versioned_payload["engine"], legacy_payload["engine"])
        self.assertIsInstance(versioned_payload["uptime_seconds"], int)
        self.assertIsInstance(versioned_payload["checked_at"], int)

    def test_trade_control_endpoints_require_post_and_admin_auth(self) -> None:
        self.seed_scan()

        status, payload = self.request_json("/api/buy?addr=test-token")
        self.assertEqual(status, 405)
        self.assertEqual(payload, {"error": "method not allowed"})

        status, payload = self.request_json("/api/buy?addr=test-token", method="POST")
        self.assertEqual(status, 401)
        self.assertEqual(payload, {"error": "unauthorized"})

        status, payload = self.request_json("/api/buy?addr=test-token&symbol=TST&token=secret-token", method="POST")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["mode"], "paper")
        self.assertTrue(self.database.has_position("test-token"))

        status, payload = self.request_json("/api/sell?addr=test-token&token=secret-token")
        self.assertEqual(status, 405)
        self.assertEqual(payload, {"error": "method not allowed"})

        status, payload = self.request_json("/api/sell", method="POST", body={"addr": "test-token"})
        self.assertEqual(status, 401)
        self.assertEqual(payload, {"error": "unauthorized"})

        status, payload = self.request_json(
            "/api/sell",
            method="POST",
            headers={"Authorization": "Bearer secret-token"},
            body={"addr": "test-token"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["mode"], "paper")
        self.assertFalse(self.database.has_position("test-token"))

    def test_decisions_endpoint_supports_filters_and_pagination(self) -> None:
        now_ts = int(time.time())
        self.seed_custom_decision(
            addr="token-entry-blocked",
            symbol="AAA",
            decision_ts=now_ts - 3,
            category="entry",
            action="open_position",
            outcome="blocked",
            reason="score below threshold",
        )
        self.seed_custom_decision(
            addr="token-exit-executed",
            symbol="BBB",
            decision_ts=now_ts - 2,
            category="exit",
            action="close_position",
            outcome="executed",
            reason="profit_lock",
        )
        self.seed_custom_decision(
            addr="token-exit-skipped",
            symbol="AAA",
            decision_ts=now_ts - 1,
            category="exit",
            action="close_position",
            outcome="skipped",
            reason="venue unavailable",
        )

        status, payload = self.request_json("/api/v1/decisions?category=exit&symbol=AAA&limit=1")
        self.assertEqual(status, 200)
        self.assertEqual(payload["pagination"]["total"], 1)
        self.assertEqual(payload["pagination"]["returned"], 1)
        self.assertEqual(payload["pagination"]["has_more"], False)
        self.assertEqual(payload["filters"]["category"], "exit")
        self.assertEqual(payload["filters"]["symbol"], "AAA")
        self.assertEqual(payload["items"][0]["addr"], "token-exit-skipped")

        status, payload = self.request_json("/api/v1/decisions?limit=1&offset=1")
        self.assertEqual(status, 200)
        self.assertEqual(payload["pagination"]["total"], 3)
        self.assertEqual(payload["pagination"]["returned"], 1)
        self.assertEqual(payload["pagination"]["offset"], 1)
        self.assertEqual(payload["pagination"]["prev_offset"], 0)
        self.assertEqual(payload["pagination"]["next_offset"], 2)
        self.assertEqual(payload["items"][0]["addr"], "token-exit-executed")

        status, payload = self.request_json("/api/v1/decisions?search=venue")
        self.assertEqual(status, 200)
        self.assertEqual(payload["pagination"]["total"], 1)
        self.assertEqual(payload["items"][0]["reason"], "venue unavailable")

    def test_decision_detail_endpoint_returns_snapshot_replay(self) -> None:
        now_ts = int(time.time())
        self.seed_custom_decision(
            addr="token-detail",
            symbol="DET",
            decision_ts=now_ts,
            category="exit",
            action="close_position",
            outcome="executed",
            reason="profit_lock_moonbag",
            snapshot={
                "kind": "exit",
                "position": {"symbol": "DET", "amount_usd": 15.0, "buy_price": 0.25},
                "market_snapshot": {"price": 0.41, "liquidity_usd": 5800.0, "source": "pumpportal"},
                "decision": {"exit_reason": "profit_lock_moonbag", "sell_fraction": 0.75, "pnl_pct": 0.24},
            },
        )
        self.database.record_opportunity(
            {
                "type": "watch",
                "symbol": "DET",
                "score": 91.0,
                "reason": "watch candidate confirmed",
                "addr": "token-detail",
                "liq": 4200.0,
                "vol": 180.0,
                "dex_url": "",
                "scan_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts - 30)),
                "socials": "",
                "progress": "61%",
            }
        )
        self.database.open_position(
            "token-detail",
            "DET",
            0.25,
            15.0,
            "paper",
            "tx-buy-detail",
            entry_liquidity=4200.0,
            metrics={"entry_reason": "watch candidate confirmed"},
        )
        self.database.close_position(
            "token-detail",
            "DET",
            0.31,
            15.0,
            0.24,
            "tx-sell-detail",
            {"exit_reason": "profit_lock_moonbag"},
        )

        status, listing = self.request_json("/api/v1/decisions?search=token-detail")
        self.assertEqual(status, 200)
        decision_id = listing["items"][0]["id"]

        status, payload = self.request_json(f"/api/v1/decisions/{decision_id}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["id"], decision_id)
        self.assertEqual(payload["snapshot"]["kind"], "exit")
        self.assertEqual(payload["snapshot"]["position"]["symbol"], "DET")
        self.assertEqual(payload["snapshot"]["market_snapshot"]["source"], "pumpportal")
        self.assertEqual(payload["snapshot"]["decision"]["exit_reason"], "profit_lock_moonbag")
        self.assertEqual(payload["related"]["addr"], "token-detail")
        self.assertEqual(payload["related"]["trades"][0]["side"], "sell")
        self.assertEqual(payload["related"]["trades"][1]["side"], "buy")
        self.assertEqual(payload["related"]["opportunities"][0]["type"], "watch")
        self.assertEqual(payload["related"]["decisions"][0]["id"], decision_id)
        self.assertEqual(payload["related"]["timeline"][0]["kind"], "decision")
        self.assertEqual(payload["related"]["timeline"][0]["decision_id"], decision_id)


if __name__ == "__main__":
    unittest.main()
