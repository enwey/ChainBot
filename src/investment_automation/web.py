from __future__ import annotations

import json
import logging
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from . import __version__
from .db import Database
from .engine import TradingEngine
from .execution import TradeExecutor
from .market_data import MarketDataClient
from .settings import Settings

logger = logging.getLogger(__name__)

READ_API_PATHS = {
    "/api/all",
    "/api/opps",
    "/api/decisions",
    "/api/wallet",
    "/api/portfolio",
    "/api/history",
    "/api/performance",
    "/api/strategy",
    "/api/watchlist",
    "/api/news",
    "/api/status",
    "/api/health",
}


class ApiHandler(BaseHTTPRequestHandler):
    database: Database
    settings: Settings
    executor: TradeExecutor
    engine: Optional[TradingEngine]
    market_data: MarketDataClient
    web_dir: Path
    started_at: int

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            self._handle_api(path, parse_qs(parsed.query), method="GET")
            return
        self._serve_static(path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith("/api/"):
            self.send_error(405)
            return
        query = parse_qs(parsed.query)
        body_params = self._parse_body_params()
        merged = dict(query)
        merged.update(body_params)
        self._handle_api(path, merged, method="POST")

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        logger.info("%s - %s", self.address_string(), format % args)

    def _parse_body_params(self) -> dict[str, list[str]]:
        content_length = int(self.headers.get("Content-Length", "0") or 0)
        if content_length <= 0:
            return {}
        raw_body = self.rfile.read(content_length)
        content_type = (self.headers.get("Content-Type", "") or "").lower()
        if "application/json" in content_type:
            try:
                payload = json.loads(raw_body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                return {}
            if not isinstance(payload, dict):
                return {}
            result: dict[str, list[str]] = {}
            for key, value in payload.items():
                if isinstance(value, list):
                    result[str(key)] = [str(item) for item in value]
                else:
                    result[str(key)] = [str(value)]
            return result
        return parse_qs(raw_body.decode("utf-8"))

    def _handle_api(self, path: str, query: dict[str, list[str]], method: str) -> None:
        detail_id = self._decision_detail_id(path)
        if detail_id is not None:
            try:
                self._json(self._decision_detail_payload(detail_id))
            except LookupError:
                self._json({"error": "not found"}, status=404)
            except Exception as exc:
                logger.exception("API error")
                self._json({"error": str(exc)}, status=500)
            return

        path = self._canonical_api_path(path)
        try:
            if path == "/api/all":
                self._json(self.database.get_scan_logs(limit=self._limit(query, default=200, maximum=500)))
            elif path == "/api/opps":
                self._json(self.database.get_opportunities())
            elif path == "/api/decisions":
                self._json(self._decision_audit_payload(query))
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
            elif path == "/api/status":
                self._json(self._status_payload())
            elif path == "/api/health":
                health = self._health_payload()
                self._json(health, status=200 if health["ready"] else 503)
            elif path == "/api/buy":
                self._manual_buy(query, method=method)
            elif path == "/api/sell":
                self._manual_sell(query, method=method)
            else:
                self._json({"error": "not found"}, status=404)
        except Exception as exc:
            logger.exception("API error")
            self._json({"error": str(exc)}, status=500)

    def _canonical_api_path(self, path: str) -> str:
        if path in READ_API_PATHS:
            return path
        if not path.startswith("/api/v1/"):
            return path
        legacy_path = "/api/" + path.removeprefix("/api/v1/")
        if legacy_path in READ_API_PATHS:
            return legacy_path
        return path

    def _decision_detail_id(self, path: str) -> Optional[int]:
        for prefix in ("/api/decisions/", "/api/v1/decisions/"):
            if not path.startswith(prefix):
                continue
            raw_id = path[len(prefix):].strip("/")
            if raw_id.isdigit():
                return int(raw_id)
        return None

    def _limit(self, query: dict[str, list[str]], default: int = 50, maximum: int = 500) -> int:
        try:
            value = int(query.get("limit", [str(default)])[0])
        except (TypeError, ValueError):
            value = default
        return max(1, min(value, maximum))

    def _offset(self, query: dict[str, list[str]]) -> int:
        try:
            value = int(query.get("offset", ["0"])[0])
        except (TypeError, ValueError):
            value = 0
        return max(0, value)

    def _query_value(self, query: dict[str, list[str]], key: str) -> Optional[str]:
        value = str(query.get(key, [""])[0] or "").strip()
        return value or None

    def _decision_audit_payload(self, query: dict[str, list[str]]) -> dict[str, Any]:
        limit = self._limit(query, default=20, maximum=100)
        offset = self._offset(query)
        filters = {
            "category": self._query_value(query, "category"),
            "action": self._query_value(query, "action"),
            "outcome": self._query_value(query, "outcome"),
            "symbol": self._query_value(query, "symbol"),
            "addr": self._query_value(query, "addr"),
            "search": self._query_value(query, "search") or self._query_value(query, "q"),
        }
        items, total = self.database.query_decision_audit(
            limit=limit,
            offset=offset,
            category=filters["category"],
            action=filters["action"],
            outcome=filters["outcome"],
            symbol=filters["symbol"],
            addr=filters["addr"],
            search=filters["search"],
        )
        returned = len(items)
        next_offset = offset + returned
        has_more = next_offset < total
        return {
            "items": items,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "returned": returned,
                "total": total,
                "has_more": has_more,
                "next_offset": next_offset if has_more else None,
                "prev_offset": max(0, offset - limit) if offset > 0 else None,
            },
            "filters": filters,
        }

    def _decision_detail_payload(self, decision_id: int) -> dict[str, Any]:
        item = self.database.get_decision_audit_by_id(decision_id)
        if not item:
            raise LookupError(decision_id)
        payload = dict(item)
        payload["related"] = self._token_case_payload(
            addr=str(item.get("addr") or ""),
            focus_decision_id=int(item.get("id") or 0),
        )
        return payload

    def _token_case_payload(self, addr: str, focus_decision_id: int) -> dict[str, Any]:
        decisions = self.database.get_decision_audit_for_addr(addr, limit=20)
        trades = self.database.get_trade_history_for_addr(addr, limit=20)
        opportunities = self.database.get_opportunities_for_addr(addr, limit=12)
        current_position = self.database.get_portfolio_position(addr)
        return {
            "addr": addr,
            "current_position": current_position,
            "decisions": decisions,
            "trades": trades,
            "opportunities": opportunities,
            "timeline": self._token_case_timeline(
                decisions=decisions,
                trades=trades,
                opportunities=opportunities,
                focus_decision_id=focus_decision_id,
            ),
        }

    def _token_case_timeline(
        self,
        *,
        decisions: list[dict[str, Any]],
        trades: list[dict[str, Any]],
        opportunities: list[dict[str, Any]],
        focus_decision_id: int,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for item in decisions:
            events.append(
                {
                    "kind": "decision",
                    "event_id": f"decision:{item.get('id')}",
                    "order_ts": int(item.get("decision_ts") or 0),
                    "event_time": str(item.get("decision_time") or "-"),
                    "title": f"{str(item.get('category') or '').upper()} {str(item.get('outcome') or '').upper()}",
                    "summary": str(item.get("reason") or ""),
                    "meta": self._timeline_decision_meta(item),
                    "decision_id": int(item.get("id") or 0),
                    "highlight": int(item.get("id") or 0) == focus_decision_id,
                }
            )
        for item in trades:
            events.append(
                {
                    "kind": "trade",
                    "event_id": f"trade:{item.get('id')}",
                    "order_ts": self._time_string_to_ts(item.get("time")),
                    "event_time": str(item.get("time") or "-"),
                    "title": f"{str(item.get('side') or '').upper()} {str(item.get('symbol') or '')}".strip(),
                    "summary": self._timeline_trade_summary(item),
                    "meta": self._timeline_trade_meta(item),
                    "trade_id": int(item.get("id") or 0),
                    "highlight": False,
                }
            )
        for item in opportunities:
            events.append(
                {
                    "kind": "signal",
                    "event_id": f"signal:{item.get('addr')}:{item.get('scan_time')}",
                    "order_ts": self._time_string_to_ts(item.get("scan_time")),
                    "event_time": str(item.get("scan_time") or "-"),
                    "title": f"SIGNAL {str(item.get('type') or '').upper()}".strip(),
                    "summary": str(item.get("reason") or ""),
                    "meta": self._timeline_signal_meta(item),
                    "highlight": False,
                }
            )
        events.sort(
            key=lambda item: (
                1 if item.get("highlight") else 0,
                int(item.get("order_ts") or 0),
                str(item.get("event_id") or ""),
            ),
            reverse=True,
        )
        return events

    def _timeline_decision_meta(self, item: dict[str, Any]) -> str:
        context = item.get("context") or {}
        parts: list[str] = []
        if context.get("strategy_mode"):
            parts.append(f"mode {context['strategy_mode']}")
        if context.get("execution_mode"):
            parts.append(f"exec {context['execution_mode']}")
        if context.get("position_size_usd") is not None:
            parts.append(f"size ${float(context.get('position_size_usd') or 0):.2f}")
        if context.get("pnl_pct") is not None:
            parts.append(f"pnl {float(context.get('pnl_pct') or 0)*100:.1f}%")
        return " | ".join(parts)

    def _timeline_trade_summary(self, item: dict[str, Any]) -> str:
        metrics = item.get("metrics") or {}
        return str(metrics.get("entry_reason") or metrics.get("exit_reason") or "trade recorded")

    def _timeline_trade_meta(self, item: dict[str, Any]) -> str:
        parts = [
            f"amount ${float(item.get('amount_usd') or 0):.2f}",
            f"price ${float(item.get('price') or 0):.8f}",
        ]
        if item.get("pnl_pct") is not None:
            parts.append(f"pnl {float(item.get('pnl_pct') or 0)*100:.1f}%")
        return " | ".join(parts)

    def _timeline_signal_meta(self, item: dict[str, Any]) -> str:
        return (
            f"score {float(item.get('score') or 0):.0f} | "
            f"liq ${float(item.get('liq') or 0):.0f} | "
            f"vol ${float(item.get('vol') or 0):.0f}"
        )

    def _time_string_to_ts(self, value: Any) -> int:
        try:
            parsed = time.strptime(str(value), "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return 0
        return int(time.mktime(parsed))

    def _manual_buy(self, query: dict[str, list[str]], method: str) -> None:
        if method != "POST":
            self._json({"error": "method not allowed"}, status=405)
            return
        if not self._is_admin_authorized(query):
            self._json({"error": "unauthorized"}, status=401)
            return

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

    def _manual_sell(self, query: dict[str, list[str]], method: str) -> None:
        if method != "POST":
            self._json({"error": "method not allowed"}, status=405)
            return
        if not self._is_admin_authorized(query):
            self._json({"error": "unauthorized"}, status=401)
            return

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

    def _is_admin_authorized(self, query: dict[str, list[str]]) -> bool:
        expected = self.settings.admin_api_token
        if not expected:
            return True
        bearer = self.headers.get("Authorization", "")
        provided = ""
        if bearer.lower().startswith("bearer "):
            provided = bearer[7:].strip()
        if not provided:
            provided = self.headers.get("X-Admin-Token", "").strip()
        if not provided:
            provided = query.get("token", [""])[0].strip()
        return bool(provided) and provided == expected

    def _status_payload(self) -> dict[str, Any]:
        uptime_seconds = max(int(time.time()) - int(self.started_at or time.time()), 0)
        return {
            "service": self.settings.service_name,
            "version": __version__,
            "environment": self.settings.environment,
            "uptime_seconds": uptime_seconds,
            "mode": self.settings.execution_mode,
            "live_enabled": self.settings.is_live_mode,
            "binds_public_interface": self.settings.binds_public_interface,
            "market_data": self.market_data.runtime_status(),
            "executor": self.executor.runtime_status(),
            "engine": self.engine.runtime_status() if self.engine else None,
        }

    def _health_payload(self) -> dict[str, Any]:
        status_payload = self._status_payload()
        market_data_status = status_payload["market_data"]
        engine_status = status_payload["engine"] or {}
        ready = bool(market_data_status.get("stream_task_running")) and bool(engine_status.get("strategy_mode"))
        return {
            "status": "ok" if ready else "degraded",
            "ready": ready,
            "checked_at": int(time.time()),
            **status_payload,
        }

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

    def _json(self, payload: Any, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def create_server(
    settings: Settings,
    database: Database,
    executor: TradeExecutor,
    engine: Optional[TradingEngine] = None,
    market_data: Optional[MarketDataClient] = None,
    started_at: Optional[int] = None,
) -> ThreadingHTTPServer:
    handler = type(
        "BoundApiHandler",
        (ApiHandler,),
        {
            "database": database,
            "settings": settings,
            "executor": executor,
            "engine": engine,
            "market_data": market_data or MarketDataClient(settings),
            "web_dir": settings.resolved_web_dir,
            "started_at": started_at or int(time.time()),
        },
    )
    return ThreadingHTTPServer((settings.host, settings.port), handler)
