from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from .db import Database
from .engine import TradingEngine
from .execution import TradeExecutor
from .settings import Settings

logger = logging.getLogger(__name__)


class ApiHandler(BaseHTTPRequestHandler):
    database: Database
    settings: Settings
    executor: TradeExecutor
    engine: Optional[TradingEngine]
    web_dir: Path

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            self._handle_api(path, parse_qs(parsed.query))
            return
        self._serve_static(path)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        logger.info("%s - %s", self.address_string(), format % args)

    def _handle_api(self, path: str, query: dict[str, list[str]]) -> None:
        try:
            if path == "/api/all":
                self._json(self.database.get_scan_logs(limit=self._limit(query, default=200, maximum=500)))
            elif path == "/api/opps":
                self._json(self.database.get_opportunities())
            elif path == "/api/wallet":
                self._json(self.database.wallet())
            elif path == "/api/portfolio":
                self._json(self.database.get_portfolio())
            elif path == "/api/history":
                self._json(self.database.get_trade_history())
            elif path == "/api/performance":
                self._json(self.database.performance_summary())
            elif path == "/api/strategy":
                self._json(self.database.get_strategy_state())
            elif path == "/api/watchlist":
                self._json(self.engine.get_watchlist_status() if self.engine else [])
            elif path == "/api/news":
                self._json(self.database.get_news_events(limit=20, active_only=True))
            elif path == "/api/health":
                self._json(
                    {
                        "status": "ok",
                        "mode": self.settings.execution_mode,
                        "live_enabled": self.settings.is_live_mode,
                    }
                )
            elif path == "/api/buy":
                self._manual_buy(query)
            elif path == "/api/sell":
                self._manual_sell(query)
            else:
                self._json({"error": "not found"}, status=404)
        except Exception as exc:
            logger.exception("API error")
            self._json({"error": str(exc)}, status=500)

    def _limit(self, query: dict[str, list[str]], default: int = 50, maximum: int = 500) -> int:
        try:
            value = int(query.get("limit", [str(default)])[0])
        except (TypeError, ValueError):
            value = default
        return max(1, min(value, maximum))

    def _manual_buy(self, query: dict[str, list[str]]) -> None:
        addr = query.get("addr", [""])[0]
        symbol = query.get("symbol", ["UNK"])[0]
        if not addr:
            self._json({"error": "addr is required"}, status=400)
            return
        if self.database.has_position(addr):
            self._json({"status": "exists"})
            return

        rows = self.database.get_scan_logs(limit=200)
        match = next((item for item in rows if item["addr"] == addr), None)
        price = float(match["price"]) if match else 0.0001
        entry_liquidity = float(match["liquidity"]) if match else 0.0
        result = self.executor.buy(addr, self.settings.position_size_usd)
        if not result.executed:
            self._json({"error": result.reason}, status=400)
            return
        self.database.open_position(
            addr,
            symbol,
            price,
            self.settings.position_size_usd,
            result.mode,
            result.tx_hash,
            entry_liquidity=entry_liquidity,
        )
        self.database.update_wallet_for_buy(self.settings.position_size_usd)
        self._json({"status": "ok", "mode": result.mode, "tx_hash": result.tx_hash})

    def _manual_sell(self, query: dict[str, list[str]]) -> None:
        addr = query.get("addr", [""])[0]
        if not addr:
            self._json({"error": "addr is required"}, status=400)
            return

        positions = self.database.get_portfolio()
        match = next((item for item in positions if item["addr"] == addr), None)
        if not match:
            self._json({"status": "missing"})
            return

        current_price = float(match["current_price"])
        amount_usd = float(match["amount_usd"])
        pnl_pct = (current_price - float(match["buy_price"])) / float(match["buy_price"])
        result = self.executor.sell(addr, amount_usd)
        if not result.executed:
            self._json({"error": result.reason}, status=400)
            return
        proceeds_usd = amount_usd * (1 + pnl_pct)
        self.database.update_wallet_for_sell(amount_usd, proceeds_usd)
        self.database.close_position(addr, match["symbol"], current_price, amount_usd, pnl_pct, result.tx_hash, {"manual": True})
        self._json({"status": "ok", "mode": result.mode, "tx_hash": result.tx_hash})

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"/", ""} else path.lstrip("/")
        candidate = (self.web_dir / relative).resolve()
        if not str(candidate).startswith(str(self.web_dir)) or not candidate.exists():
            self.send_error(404)
            return
        self.send_response(200)
        if candidate.suffix == ".html":
            self.send_header("Content-Type", "text/html; charset=utf-8")
        elif candidate.suffix == ".css":
            self.send_header("Content-Type", "text/css; charset=utf-8")
        elif candidate.suffix == ".js":
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self.end_headers()
        with candidate.open("rb") as handle:
            self.wfile.write(handle.read())

    def _json(self, payload, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def create_server(
    settings: Settings,
    database: Database,
    executor: TradeExecutor,
    engine: Optional[TradingEngine] = None,
) -> ThreadingHTTPServer:
    handler = type(
        "BoundApiHandler",
        (ApiHandler,),
        {
            "database": database,
            "settings": settings,
            "executor": executor,
            "engine": engine,
            "web_dir": settings.resolved_web_dir,
        },
    )
    return ThreadingHTTPServer((settings.host, settings.port), handler)
