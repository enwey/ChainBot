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
            "workers": {
                "news": {
                    "state": "error",
                    "iterations": 3,
                    "error_count": 2,
                    "last_seen_ts": 1715337600,
                    "last_success_ts": 1715337540,
                    "last_error_ts": 1715337580,
                    "last_error": "news feed timeout",
                    "last_seen_age": "5s",
                    "last_success_age": "1m",
                },
                "strategy": {
                    "state": "running",
                    "iterations": 12,
                    "error_count": 0,
                    "last_seen_ts": 1715337600,
                    "last_success_ts": 1715337590,
                    "last_error_ts": 0,
                    "last_error": "",
                    "last_seen_age": "5s",
                    "last_success_age": "10s",
                },
            },
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
        (self.base_dir / "web" / "index.html").write_text(
            "<html><body>ok</body></html>", encoding="utf-8"
        )

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

        request = urllib.request.Request(
            self.api_url(path), data=data, headers=request_headers, method=method
        )
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def request_text(self, path: str) -> tuple[int, str, str]:
        request = urllib.request.Request(self.api_url(path), method="GET")
        with urllib.request.urlopen(request) as response:
            return (
                response.status,
                response.headers.get("Content-Type", ""),
                response.read().decode("utf-8"),
            )

    def test_dashboard_template_contains_decision_timeline_controls(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Decision Timeline", html)
        self.assertIn("Token Case Timeline", html)
        self.assertIn("Operator Console", html)
        self.assertIn("/api/v1/decisions", html)
        self.assertIn("decision-category", html)
        self.assertIn("decision-next", html)
        self.assertIn("decision-detail", html)
        self.assertIn("dependency-list", html)
        self.assertIn("manual-trade-list", html)

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

    def seed_news_event(
        self,
        *,
        event_id: str,
        source: str,
        title: str,
        sentiment: str,
        published_ts: int,
        active_until_ts: int,
    ) -> None:
        event = type(
            "NewsEvent",
            (),
            {
                "event_id": event_id,
                "source": source,
                "title": title,
                "url": f"https://example.com/{event_id}",
                "published_ts": published_ts,
                "fetched_ts": published_ts + 5,
                "topics": ["memecoin"],
                "entities": ["SOL"],
                "sentiment": sentiment,
                "confidence": 0.82,
                "source_weight": 1.2,
                "active_until_ts": active_until_ts,
                "raw_text": title,
            },
        )
        self.database.record_news_events([event])

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
                "snapshot": snapshot
                or {
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
            if suffix == "/portfolio":
                self.assertEqual(len(versioned_payload), len(legacy_payload))
                self.assertEqual(versioned_payload[0]["addr"], legacy_payload[0]["addr"])
                self.assertEqual(versioned_payload[0]["symbol"], legacy_payload[0]["symbol"])
                self.assertEqual(
                    versioned_payload[0]["amount_usd"], legacy_payload[0]["amount_usd"]
                )
                self.assertEqual(
                    versioned_payload[0]["current_value_usd"],
                    legacy_payload[0]["current_value_usd"],
                )
            else:
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

        detail_status, detail_payload = self.request_json(
            f"/api/v1/decisions/{decision_payload['items'][0]['id']}"
        )
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
        self.assertEqual(
            versioned_payload["binds_public_interface"], legacy_payload["binds_public_interface"]
        )
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
        self.assertEqual(
            versioned_payload["binds_public_interface"], legacy_payload["binds_public_interface"]
        )
        self.assertEqual(versioned_payload["market_data"], legacy_payload["market_data"])
        self.assertEqual(versioned_payload["executor"], legacy_payload["executor"])
        self.assertEqual(versioned_payload["engine"], legacy_payload["engine"])
        self.assertIsInstance(versioned_payload["uptime_seconds"], int)
        self.assertIsInstance(versioned_payload["checked_at"], int)

        dependency_status, dependency_payload = self.request_json("/api/dependencies")
        dependency_v1_status, dependency_v1_payload = self.request_json("/api/v1/dependencies")
        self.assertEqual(dependency_v1_status, dependency_status)
        self.assertEqual(dependency_v1_payload, dependency_payload)

        worker_status, worker_payload = self.request_json("/api/worker-errors")
        worker_v1_status, worker_v1_payload = self.request_json("/api/v1/worker-errors")
        self.assertEqual(worker_v1_status, worker_status)
        self.assertEqual(worker_v1_payload, worker_payload)

        metrics_status, metrics_payload = self.request_json("/api/metrics")
        metrics_v1_status, metrics_v1_payload = self.request_json("/api/v1/metrics")
        self.assertEqual(metrics_v1_status, metrics_status)
        self.assertEqual(metrics_v1_payload["service"], metrics_payload["service"])
        self.assertEqual(metrics_v1_payload["gauges"], metrics_payload["gauges"])
        self.assertEqual(metrics_v1_payload.get("counters"), metrics_payload.get("counters"))

    def test_metrics_endpoints_expose_json_and_prometheus_surfaces(self) -> None:
        self.seed_scan(addr="metrics-token", symbol="MET")
        self.database.open_position(
            "metrics-token",
            "MET",
            0.42,
            15.0,
            "paper",
            "tx-metrics-buy",
            entry_liquidity=3200.0,
        )
        self.database.record_dependency_failure(
            {
                "dependency": "dexscreener",
                "operation": "fetch_quote",
                "failure_type": "timeout",
                "severity": "error",
                "message": "request timed out",
                "details": {"attempt": 2},
                "failure_ts": int(time.time()),
            }
        )

        status, payload = self.request_json("/api/v1/metrics")
        self.assertEqual(status, 200)
        self.assertEqual(payload["service"], self.settings.service_name)
        self.assertIn("gauges", payload)
        self.assertIn("alerts", payload)
        self.assertEqual(payload["summary"]["open_positions"], 1)
        self.assertGreaterEqual(payload["summary"]["open_dependency_failures"], 1)
        self.assertIn("performance", payload["sources"])
        self.assertIn("dependency_failures_open", {item["code"] for item in payload["alerts"]})

        status, content_type, body = self.request_text("/metrics")
        self.assertEqual(status, 200)
        self.assertIn("text/plain", content_type)
        self.assertIn("chainbot_ready", body)
        self.assertIn('service="ChainBot"', body)
        self.assertIn("chainbot_dependency_open_failures", body)

    def test_trade_control_endpoints_require_post_and_admin_auth(self) -> None:
        self.seed_scan()

        status, payload = self.request_json("/api/buy?addr=test-token")
        self.assertEqual(status, 405)
        self.assertEqual(payload, {"error": "method not allowed"})

        status, payload = self.request_json("/api/buy?addr=test-token", method="POST")
        self.assertEqual(status, 401)
        self.assertEqual(payload, {"error": "unauthorized"})

        status, payload = self.request_json(
            "/api/buy?addr=test-token&symbol=TST&token=secret-token", method="POST"
        )
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

    def test_manual_trade_audit_endpoint_returns_manual_api_history(self) -> None:
        self.seed_scan(addr="manual-token", symbol="MNL")

        status, payload = self.request_json(
            "/api/buy?addr=manual-token&symbol=MNL&token=secret-token", method="POST"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")

        status, payload = self.request_json(
            "/api/sell",
            method="POST",
            headers={"Authorization": "Bearer secret-token"},
            body={"addr": "manual-token"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")

        status, payload = self.request_json("/api/v1/manual-trades?addr=manual-token")
        self.assertEqual(status, 200)
        self.assertEqual(payload["persisted"], True)
        self.assertEqual(payload["source"], "manual_trade_audit")
        self.assertEqual(payload["pagination"]["total"], 2)
        self.assertEqual([item["side"] for item in payload["items"]], ["sell", "buy"])
        self.assertTrue(all(item["idempotency_key"] for item in payload["items"]))
        self.assertEqual(payload["items"][0]["request"]["side"], "sell")
        self.assertEqual(payload["items"][1]["request"]["side"], "buy")

        legacy_status, legacy_payload = self.request_json("/api/manual-trades?side=buy")
        self.assertEqual(legacy_status, 200)
        self.assertEqual(legacy_payload["pagination"]["total"], 1)
        self.assertEqual(legacy_payload["items"][0]["side"], "buy")

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

    def test_feed_endpoints_support_filters_pagination_and_detail_routes(self) -> None:
        now_ts = int(time.time())
        self.seed_scan(addr="scan-alpha", symbol="ALP")
        self.seed_scan(addr="scan-beta", symbol="BET")
        self.database.record_opportunity(
            {
                "type": "watch",
                "symbol": "ALP",
                "score": 88.0,
                "reason": "watch candidate",
                "addr": "scan-alpha",
                "liq": 4100.0,
                "vol": 210.0,
                "dex_url": "",
                "scan_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts - 30)),
                "socials": "",
                "progress": "58%",
            }
        )
        self.database.record_opportunity(
            {
                "type": "confirmed",
                "symbol": "BET",
                "score": 91.0,
                "reason": "volume breakout",
                "addr": "scan-beta",
                "liq": 6100.0,
                "vol": 410.0,
                "dex_url": "",
                "scan_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts - 10)),
                "socials": "",
                "progress": "74%",
            }
        )
        self.database.open_position(
            "position-open",
            "OPN",
            0.19,
            15.0,
            "paper",
            "tx-open-1",
            entry_liquidity=2200.0,
            metrics={"entry_reason": "manual watch promotion"},
        )
        self.database.open_position(
            "position-closed",
            "CLS",
            0.22,
            20.0,
            "paper",
            "tx-close-buy",
            entry_liquidity=4300.0,
            metrics={"entry_reason": "breakout retest"},
        )
        self.database.close_position(
            "position-closed",
            "CLS",
            0.31,
            20.0,
            0.41,
            "tx-close-sell",
            {"exit_reason": "profit_lock"},
        )
        self.seed_news_event(
            event_id="news-positive",
            source="KOL",
            title="ALP momentum building",
            sentiment="positive",
            published_ts=now_ts - 60,
            active_until_ts=now_ts + 3600,
        )
        self.seed_news_event(
            event_id="news-negative",
            source="wire",
            title="BET liquidity concerns",
            sentiment="negative",
            published_ts=now_ts - 120,
            active_until_ts=now_ts + 1200,
        )
        self.seed_custom_decision(
            addr="position-open",
            symbol="OPN",
            decision_ts=now_ts - 5,
            category="entry",
            action="open_position",
            outcome="executed",
            reason="watch confirmation",
        )

        status, payload = self.request_json("/api/v1/all?search=alp&limit=1")
        self.assertEqual(status, 200)
        self.assertEqual(payload["pagination"]["total"], 1)
        self.assertEqual(payload["items"][0]["symbol"], "ALP")

        status, payload = self.request_json("/api/v1/opps?type=confirmed&limit=1")
        self.assertEqual(status, 200)
        self.assertEqual(payload["filters"]["type"], "confirmed")
        self.assertEqual(payload["items"][0]["symbol"], "BET")
        opportunity_id = payload["items"][0]["id"]

        status, detail = self.request_json(f"/api/v1/opportunities/{opportunity_id}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["addr"], "scan-beta")
        self.assertEqual(detail["related"]["comparison"]["signal_count"], 1)

        status, payload = self.request_json("/api/v1/history?side=sell&search=profit_lock")
        self.assertEqual(status, 200)
        self.assertEqual(payload["pagination"]["total"], 1)
        self.assertEqual(payload["items"][0]["symbol"], "CLS")
        trade_id = payload["items"][0]["id"]

        status, detail = self.request_json(f"/api/v1/history/{trade_id}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["metrics"]["exit_reason"], "profit_lock")
        self.assertEqual(detail["related"]["addr"], "position-closed")

        status, detail = self.request_json("/api/v1/open-positions/position-open")
        self.assertEqual(status, 200)
        self.assertEqual(detail["symbol"], "OPN")
        self.assertEqual(detail["related"]["comparison"]["position_status"], "open")

        status, payload = self.request_json("/api/v1/news?source=kol&sentiment=positive")
        self.assertEqual(status, 200)
        self.assertEqual(payload["pagination"]["total"], 1)
        self.assertEqual(payload["items"][0]["event_id"], "news-positive")

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
                "decision": {
                    "exit_reason": "profit_lock_moonbag",
                    "sell_fraction": 0.75,
                    "pnl_pct": 0.24,
                },
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
        self.assertEqual(payload["related"]["comparison"]["closed_trade_count"], 1)
        self.assertEqual(payload["related"]["timeline"][0]["kind"], "decision")
        self.assertEqual(payload["related"]["timeline"][0]["decision_id"], decision_id)

    def test_decision_export_endpoint_returns_full_token_case_json(self) -> None:
        now_ts = int(time.time())
        self.seed_custom_decision(
            addr="token-export",
            symbol="EXP",
            decision_ts=now_ts,
            category="entry",
            action="open_position",
            outcome="executed",
            reason="score passed threshold",
            snapshot={
                "kind": "entry",
                "candidate": {"symbol": "EXP", "addr": "token-export", "score": 92.0},
                "wallet": {"current_balance": 180.0},
            },
        )
        self.database.record_opportunity(
            {
                "type": "watch",
                "symbol": "EXP",
                "score": 92.0,
                "reason": "watch candidate promoted",
                "addr": "token-export",
                "liq": 5100.0,
                "vol": 260.0,
                "dex_url": "",
                "scan_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts - 15)),
                "socials": "",
                "progress": "70%",
            }
        )
        self.database.open_position(
            "token-export",
            "EXP",
            0.18,
            15.0,
            "paper",
            "tx-export-buy",
            entry_liquidity=5100.0,
            metrics={"entry_reason": "watch candidate promoted"},
        )

        status, listing = self.request_json("/api/v1/decisions?addr=token-export")
        self.assertEqual(status, 200)
        decision_id = listing["items"][0]["id"]

        status, payload = self.request_json(f"/api/v1/decisions/{decision_id}/export")
        self.assertEqual(status, 200)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["focus_decision_id"], decision_id)
        self.assertEqual(payload["decision"]["addr"], "token-export")
        self.assertEqual(payload["token_case"]["addr"], "token-export")
        self.assertEqual(payload["token_case"]["decisions"][0]["id"], decision_id)
        self.assertEqual(payload["token_case"]["opportunities"][0]["type"], "watch")
        self.assertEqual(payload["token_case"]["comparison"]["signal_count"], 1)
        self.assertEqual(payload["token_case"]["current_position"]["symbol"], "EXP")
        self.assertTrue(any(item["kind"] == "signal" for item in payload["token_case"]["timeline"]))
        self.assertIsInstance(payload["exported_at"], int)

        status, token_case_payload = self.request_json("/api/v1/token-cases/token-export/export")
        self.assertEqual(status, 200)
        self.assertEqual(token_case_payload["token_case"]["addr"], "token-export")
        self.assertEqual(token_case_payload["focus_decision_id"], decision_id)

    def test_dependency_and_worker_error_endpoints_surface_runtime_state(self) -> None:
        status, payload = self.request_json("/api/v1/dependencies")
        self.assertEqual(status, 200)
        self.assertEqual(payload["persisted"], True)
        self.assertEqual(payload["source"], "runtime_status+dependency_failures")
        self.assertEqual(payload["summary"]["total"], 3)
        self.assertEqual(payload["summary"]["open_failures"], 0)
        self.assertTrue(any(item["name"] == "pumpportal_stream" for item in payload["items"]))
        self.assertTrue(any(item["name"] == "trade_executor" for item in payload["items"]))
        self.assertIsInstance(payload["checked_at"], int)

        status, payload = self.request_json("/api/v1/worker-errors")
        self.assertEqual(status, 200)
        self.assertEqual(payload["persisted"], False)
        self.assertEqual(payload["source"], "engine.runtime_status")
        self.assertEqual(payload["summary"]["workers_with_errors"], 1)
        self.assertEqual(payload["summary"]["total_errors"], 2)
        self.assertEqual(payload["items"][0]["worker"], "news")
        self.assertEqual(payload["items"][0]["last_error"], "news feed timeout")


if __name__ == "__main__":
    unittest.main()
