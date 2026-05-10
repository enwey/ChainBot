from __future__ import annotations

import hashlib
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
    "/api/manual-trades",
    "/api/metrics",
    "/api/wallet",
    "/api/portfolio",
    "/api/history",
    "/api/performance",
    "/api/strategy",
    "/api/watchlist",
    "/api/news",
    "/api/status",
    "/api/health",
    "/api/dependencies",
    "/api/worker-errors",
}

DETAIL_ROUTE_PREFIXES = {
    "decision": ("/api/decisions/", "/api/v1/decisions/"),
    "decision_export": ("/api/decisions/", "/api/v1/decisions/"),
    "token_case_export": ("/api/token-cases/", "/api/v1/token-cases/"),
    "opportunity": ("/api/opportunities/", "/api/v1/opportunities/"),
    "trade_history": ("/api/history/", "/api/v1/history/"),
    "open_position": ("/api/open-positions/", "/api/v1/open-positions/"),
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
        if path == "/metrics":
            self._serve_prometheus_metrics()
            return
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
        detail_export_id = self._decision_export_id(path)
        if detail_export_id is not None:
            try:
                self._json(self._decision_export_payload(detail_export_id))
            except LookupError:
                self._json({"error": "not found"}, status=404)
            except Exception as exc:
                logger.exception("API error")
                self._json({"error": str(exc)}, status=500)
            return

        token_case_export_addr = self._token_case_export_addr(path)
        if token_case_export_addr is not None:
            try:
                self._json(self._token_case_export_payload(token_case_export_addr))
            except LookupError:
                self._json({"error": "not found"}, status=404)
            except Exception as exc:
                logger.exception("API error")
                self._json({"error": str(exc)}, status=500)
            return

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

        opportunity_id = self._integer_detail_id(path, DETAIL_ROUTE_PREFIXES["opportunity"])
        if opportunity_id is not None:
            try:
                self._json(self._opportunity_detail_payload(opportunity_id))
            except LookupError:
                self._json({"error": "not found"}, status=404)
            except Exception as exc:
                logger.exception("API error")
                self._json({"error": str(exc)}, status=500)
            return

        trade_history_id = self._integer_detail_id(path, DETAIL_ROUTE_PREFIXES["trade_history"])
        if trade_history_id is not None:
            try:
                self._json(self._trade_history_detail_payload(trade_history_id))
            except LookupError:
                self._json({"error": "not found"}, status=404)
            except Exception as exc:
                logger.exception("API error")
                self._json({"error": str(exc)}, status=500)
            return

        position_addr = self._string_detail_key(path, DETAIL_ROUTE_PREFIXES["open_position"])
        if position_addr is not None:
            try:
                self._json(self._open_position_detail_payload(position_addr))
            except LookupError:
                self._json({"error": "not found"}, status=404)
            except Exception as exc:
                logger.exception("API error")
                self._json({"error": str(exc)}, status=500)
            return

        path = self._canonical_api_path(path)
        try:
            if path == "/api/all":
                self._json(self._scan_feed_payload(query))
            elif path == "/api/opps":
                self._json(self._opportunity_feed_payload(query))
            elif path == "/api/decisions":
                self._json(self._decision_audit_payload(query))
            elif path == "/api/manual-trades":
                self._json(self._manual_trade_audit_payload(query))
            elif path == "/api/metrics":
                self._json(self._metrics_payload())
            elif path == "/api/wallet":
                self._json(self.database.wallet())
            elif path == "/api/portfolio":
                self._json(self.database.get_portfolio())
            elif path == "/api/history":
                self._json(self._trade_history_payload(query))
            elif path == "/api/performance":
                self._json(self.database.performance_summary())
            elif path == "/api/strategy":
                self._json(self.database.get_strategy_state())
            elif path == "/api/watchlist":
                self._json(self.engine.get_watchlist_status() if self.engine else [])
            elif path == "/api/news":
                self._json(self._news_feed_payload(query))
            elif path == "/api/status":
                self._json(self._status_payload())
            elif path == "/api/health":
                health = self._health_payload()
                self._json(health, status=200 if health["ready"] else 503)
            elif path == "/api/dependencies":
                self._json(self._dependency_health_payload())
            elif path == "/api/worker-errors":
                self._json(self._worker_error_payload())
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
        return self._integer_detail_id(path, DETAIL_ROUTE_PREFIXES["decision"])

    def _decision_export_id(self, path: str) -> Optional[int]:
        for prefix in DETAIL_ROUTE_PREFIXES["decision_export"]:
            if not path.startswith(prefix):
                continue
            raw_suffix = path[len(prefix) :].strip("/")
            if not raw_suffix.endswith("/export"):
                continue
            raw_id = raw_suffix.removesuffix("/export").strip("/")
            if raw_id.isdigit():
                return int(raw_id)
        return None

    def _token_case_export_addr(self, path: str) -> Optional[str]:
        for prefix in DETAIL_ROUTE_PREFIXES["token_case_export"]:
            if not path.startswith(prefix):
                continue
            raw_suffix = path[len(prefix) :].strip("/")
            if not raw_suffix.endswith("/export"):
                continue
            raw_addr = raw_suffix.removesuffix("/export").strip("/")
            if raw_addr:
                return raw_addr
        return None

    def _integer_detail_id(self, path: str, prefixes: tuple[str, ...]) -> Optional[int]:
        raw_value = self._string_detail_key(path, prefixes)
        return int(raw_value) if raw_value and raw_value.isdigit() else None

    def _string_detail_key(self, path: str, prefixes: tuple[str, ...]) -> Optional[str]:
        for prefix in prefixes:
            if not path.startswith(prefix):
                continue
            raw_value = path[len(prefix) :].strip("/")
            if raw_value and "/" not in raw_value:
                return raw_value
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

    def _bool_query(self, query: dict[str, list[str]], key: str, *, default: bool) -> bool:
        raw_value = self._query_value(query, key)
        if raw_value is None:
            return default
        return raw_value.lower() not in {"0", "false", "no", "off"}

    def _paged_items_payload(
        self,
        *,
        items: list[dict[str, Any]],
        total: int,
        limit: int,
        offset: int,
        filters: dict[str, Any],
    ) -> dict[str, Any]:
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

    def _opportunity_detail_payload(self, opportunity_id: int) -> dict[str, Any]:
        item = self.database.get_opportunity_by_id(opportunity_id)
        if not item:
            raise LookupError(opportunity_id)
        payload = dict(item)
        payload["related"] = self._token_case_payload(
            addr=str(item.get("addr") or ""),
            focus_decision_id=0,
        )
        return payload

    def _trade_history_detail_payload(self, trade_history_id: int) -> dict[str, Any]:
        item = self.database.get_trade_history_row(trade_history_id)
        if not item:
            raise LookupError(trade_history_id)
        payload = dict(item)
        payload["related"] = self._token_case_payload(
            addr=str(item.get("addr") or ""),
            focus_decision_id=0,
        )
        return payload

    def _open_position_detail_payload(self, addr: str) -> dict[str, Any]:
        item = self.database.get_portfolio_position(addr)
        if not item:
            raise LookupError(addr)
        payload = dict(item)
        payload["related"] = self._token_case_payload(addr=addr, focus_decision_id=0)
        return payload

    def _decision_export_payload(self, decision_id: int) -> dict[str, Any]:
        item = self.database.get_decision_audit_by_id(decision_id)
        if not item:
            raise LookupError(decision_id)
        return self._build_token_case_export(
            addr=str(item.get("addr") or ""),
            focus_decision_id=int(item.get("id") or 0),
            focus_decision=item,
        )

    def _token_case_export_payload(self, addr: str) -> dict[str, Any]:
        decisions = self.database.get_decision_audit_for_addr(addr, limit=1)
        if not decisions and not self.database.get_trade_history_for_addr(addr, limit=1):
            if not self.database.get_opportunities_for_addr(
                addr, limit=1
            ) and not self.database.get_portfolio_position(addr):
                raise LookupError(addr)
        focus_decision = decisions[0] if decisions else None
        focus_decision_id = int(focus_decision.get("id") or 0) if focus_decision else 0
        return self._build_token_case_export(
            addr=addr,
            focus_decision_id=focus_decision_id,
            focus_decision=focus_decision,
        )

    def _build_token_case_export(
        self,
        *,
        addr: str,
        focus_decision_id: int,
        focus_decision: Optional[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "exported_at": int(time.time()),
            "service": self.settings.service_name,
            "version": __version__,
            "focus_decision_id": focus_decision_id or None,
            "decision": focus_decision,
            "token_case": self._token_case_payload(addr=addr, focus_decision_id=focus_decision_id),
        }

    def _scan_feed_payload(self, query: dict[str, list[str]]) -> dict[str, Any]:
        limit = self._limit(query, default=200, maximum=500)
        offset = self._offset(query)
        filters = {
            "platform": self._query_value(query, "platform"),
            "symbol": self._query_value(query, "symbol"),
            "addr": self._query_value(query, "addr"),
            "search": self._query_value(query, "search") or self._query_value(query, "q"),
        }
        items, total = self.database.query_scan_logs(
            limit=limit,
            offset=offset,
            platform=filters["platform"],
            symbol=filters["symbol"],
            addr=filters["addr"],
            search=filters["search"],
        )
        return self._paged_items_payload(
            items=items, total=total, limit=limit, offset=offset, filters=filters
        )

    def _opportunity_feed_payload(self, query: dict[str, list[str]]) -> dict[str, Any]:
        limit = self._limit(query, default=30, maximum=200)
        offset = self._offset(query)
        filters = {
            "type": self._query_value(query, "type"),
            "symbol": self._query_value(query, "symbol"),
            "addr": self._query_value(query, "addr"),
            "search": self._query_value(query, "search") or self._query_value(query, "q"),
        }
        items, total = self.database.query_opportunities(
            limit=limit,
            offset=offset,
            opportunity_type=filters["type"],
            symbol=filters["symbol"],
            addr=filters["addr"],
            search=filters["search"],
        )
        return self._paged_items_payload(
            items=items, total=total, limit=limit, offset=offset, filters=filters
        )

    def _trade_history_payload(self, query: dict[str, list[str]]) -> dict[str, Any]:
        limit = self._limit(query, default=50, maximum=200)
        offset = self._offset(query)
        filters = {
            "side": self._query_value(query, "side"),
            "symbol": self._query_value(query, "symbol"),
            "addr": self._query_value(query, "addr"),
            "search": self._query_value(query, "search") or self._query_value(query, "q"),
        }
        items, total = self.database.query_trade_history(
            limit=limit,
            offset=offset,
            side=filters["side"],
            symbol=filters["symbol"],
            addr=filters["addr"],
            search=filters["search"],
        )
        return self._paged_items_payload(
            items=items, total=total, limit=limit, offset=offset, filters=filters
        )

    def _news_feed_payload(self, query: dict[str, list[str]]) -> dict[str, Any]:
        limit = self._limit(query, default=20, maximum=100)
        offset = self._offset(query)
        active_only = self._bool_query(query, "active_only", default=True)
        filters = {
            "source": self._query_value(query, "source"),
            "sentiment": self._query_value(query, "sentiment"),
            "search": self._query_value(query, "search") or self._query_value(query, "q"),
            "active_only": active_only,
        }
        items, total = self.database.query_news_events(
            limit=limit,
            offset=offset,
            active_only=active_only,
            source=filters["source"],
            sentiment=filters["sentiment"],
            search=filters["search"],
        )
        return self._paged_items_payload(
            items=items, total=total, limit=limit, offset=offset, filters=filters
        )

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
            "comparison": self._token_case_comparison_payload(
                decisions=decisions,
                trades=trades,
                opportunities=opportunities,
                current_position=current_position,
            ),
            "timeline": self._token_case_timeline(
                decisions=decisions,
                trades=trades,
                opportunities=opportunities,
                focus_decision_id=focus_decision_id,
            ),
        }

    def _token_case_comparison_payload(
        self,
        *,
        decisions: list[dict[str, Any]],
        trades: list[dict[str, Any]],
        opportunities: list[dict[str, Any]],
        current_position: Optional[dict[str, Any]],
    ) -> dict[str, Any]:
        signal_scores = [float(item.get("score") or 0) for item in opportunities]
        signal_liquidity = [float(item.get("liq") or 0) for item in opportunities]
        sell_trades = [item for item in trades if str(item.get("side") or "").lower() == "sell"]
        trade_pnls = [
            float(item.get("pnl_pct") or 0)
            for item in sell_trades
            if item.get("pnl_pct") is not None
        ]
        executed_decisions = [
            item for item in decisions if str(item.get("outcome") or "").lower() == "executed"
        ]
        return {
            "decision_count": len(decisions),
            "executed_decision_count": len(executed_decisions),
            "trade_count": len(trades),
            "closed_trade_count": len(sell_trades),
            "signal_count": len(opportunities),
            "avg_signal_score": sum(signal_scores) / len(signal_scores) if signal_scores else 0.0,
            "max_signal_score": max(signal_scores) if signal_scores else 0.0,
            "avg_signal_liquidity_usd": (
                sum(signal_liquidity) / len(signal_liquidity) if signal_liquidity else 0.0
            ),
            "best_realized_pnl_pct": max(trade_pnls) if trade_pnls else 0.0,
            "worst_realized_pnl_pct": min(trade_pnls) if trade_pnls else 0.0,
            "avg_realized_pnl_pct": sum(trade_pnls) / len(trade_pnls) if trade_pnls else 0.0,
            "realized_win_rate": (
                sum(1 for value in trade_pnls if value > 0) / len(trade_pnls) if trade_pnls else 0.0
            ),
            "position_status": "open" if current_position else "closed",
            "current_unrealized_pnl_pct": (
                float(current_position.get("unrealized_pnl_pct") or 0) if current_position else 0.0
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
            parts.append(f"pnl {float(context.get('pnl_pct') or 0) * 100:.1f}%")
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
            parts.append(f"pnl {float(item.get('pnl_pct') or 0) * 100:.1f}%")
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
        amount_usd = float(
            query.get("amount_usd", [str(self.settings.position_size_usd)])[0]
            or self.settings.position_size_usd
        )
        idempotency_key = self._manual_trade_idempotency_key(
            query,
            side="buy",
            addr=addr,
            symbol=symbol,
            amount_usd=amount_usd,
        )
        idempotency_record, created = self.database.claim_order_idempotency(
            idempotency_key,
            source="manual_api",
            side="buy",
            addr=addr,
            symbol=symbol,
            amount_usd=amount_usd,
            request_fingerprint=self._request_fingerprint(query, method=method),
            request=self._manual_trade_request_payload(
                query, side="buy", addr=addr, symbol=symbol, amount_usd=amount_usd
            ),
        )
        if not created and str(idempotency_record.get("status") or "") == "executed":
            cached = dict(idempotency_record.get("result") or {})
            cached["idempotency_key"] = idempotency_key
            self._json(cached)
            return
        if self.database.has_position(addr):
            payload = {"status": "exists", "idempotency_key": idempotency_key}
            self.database.record_manual_trade_audit(
                self._manual_trade_audit_record(
                    side="buy",
                    addr=addr,
                    symbol=symbol,
                    amount_usd=amount_usd,
                    outcome="blocked",
                    reason="position already exists",
                    idempotency_key=idempotency_key,
                    request=self._manual_trade_request_payload(
                        query, side="buy", addr=addr, symbol=symbol, amount_usd=amount_usd
                    ),
                    response=payload,
                )
            )
            self.database.update_order_idempotency(
                idempotency_key, status="blocked", result=payload
            )
            self._json(payload)
            return

        rows = self.database.get_scan_logs(limit=200)
        match = next((item for item in rows if item["addr"] == addr), None)
        price = float(match["price"]) if match else 0.0001
        entry_liquidity = float(match["liquidity"]) if match else 0.0
        result = self.executor.buy(addr, amount_usd)
        if not result.executed:
            payload = {"error": result.reason, "idempotency_key": idempotency_key}
            self.database.record_manual_trade_audit(
                self._manual_trade_audit_record(
                    side="buy",
                    addr=addr,
                    symbol=symbol,
                    amount_usd=amount_usd,
                    outcome="failed",
                    reason=result.reason or "execution failed",
                    idempotency_key=idempotency_key,
                    tx_hash=result.tx_hash,
                    mode=result.mode,
                    request=self._manual_trade_request_payload(
                        query, side="buy", addr=addr, symbol=symbol, amount_usd=amount_usd
                    ),
                    response=payload,
                )
            )
            self.database.update_order_idempotency(
                idempotency_key, status="failed", result=payload, tx_hash=result.tx_hash
            )
            self._json(payload, status=400)
            return
        self.database.open_position(
            addr,
            symbol,
            price,
            amount_usd,
            result.mode,
            result.tx_hash,
            entry_liquidity=entry_liquidity,
            metrics=self._manual_trade_metrics(
                side="buy",
                addr=addr,
                symbol=symbol,
                mode=result.mode,
                tx_hash=result.tx_hash,
                idempotency_key=idempotency_key,
            ),
        )
        self.database.update_wallet_for_buy(amount_usd)
        trade_history_id = self.database.find_trade_history_id(
            addr=addr, side="buy", tx_hash=result.tx_hash
        )
        payload = {
            "status": "ok",
            "mode": result.mode,
            "tx_hash": result.tx_hash,
            "idempotency_key": idempotency_key,
        }
        self.database.record_manual_trade_audit(
            self._manual_trade_audit_record(
                side="buy",
                addr=addr,
                symbol=symbol,
                amount_usd=amount_usd,
                outcome="executed",
                reason="manual buy executed",
                idempotency_key=idempotency_key,
                tx_hash=result.tx_hash,
                mode=result.mode,
                trade_history_id=trade_history_id,
                request=self._manual_trade_request_payload(
                    query, side="buy", addr=addr, symbol=symbol, amount_usd=amount_usd
                ),
                response=payload,
            )
        )
        self.database.update_order_idempotency(
            idempotency_key,
            status="executed",
            result=payload,
            tx_hash=result.tx_hash,
            trade_history_id=trade_history_id,
        )
        self._json(payload)

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
            payload = {"status": "missing"}
            self._json(payload)
            return

        current_price = float(match["current_price"])
        amount_usd = float(match["amount_usd"])
        symbol = str(match.get("symbol") or "")
        idempotency_key = self._manual_trade_idempotency_key(
            query,
            side="sell",
            addr=addr,
            symbol=symbol,
            amount_usd=amount_usd,
        )
        idempotency_record, created = self.database.claim_order_idempotency(
            idempotency_key,
            source="manual_api",
            side="sell",
            addr=addr,
            symbol=symbol,
            amount_usd=amount_usd,
            request_fingerprint=self._request_fingerprint(query, method=method),
            request=self._manual_trade_request_payload(
                query, side="sell", addr=addr, symbol=symbol, amount_usd=amount_usd
            ),
        )
        if not created and str(idempotency_record.get("status") or "") == "executed":
            cached = dict(idempotency_record.get("result") or {})
            cached["idempotency_key"] = idempotency_key
            self._json(cached)
            return
        pnl_pct = (current_price - float(match["buy_price"])) / float(match["buy_price"])
        result = self.executor.sell(addr, amount_usd)
        if not result.executed:
            payload = {"error": result.reason, "idempotency_key": idempotency_key}
            self.database.record_manual_trade_audit(
                self._manual_trade_audit_record(
                    side="sell",
                    addr=addr,
                    symbol=symbol,
                    amount_usd=amount_usd,
                    outcome="failed",
                    reason=result.reason or "execution failed",
                    idempotency_key=idempotency_key,
                    tx_hash=result.tx_hash,
                    mode=result.mode,
                    request=self._manual_trade_request_payload(
                        query, side="sell", addr=addr, symbol=symbol, amount_usd=amount_usd
                    ),
                    response=payload,
                )
            )
            self.database.update_order_idempotency(
                idempotency_key, status="failed", result=payload, tx_hash=result.tx_hash
            )
            self._json(payload, status=400)
            return
        proceeds_usd = amount_usd * (1 + pnl_pct)
        self.database.update_wallet_for_sell(amount_usd, proceeds_usd)
        self.database.close_position(
            addr,
            symbol,
            current_price,
            amount_usd,
            pnl_pct,
            result.tx_hash,
            self._manual_trade_metrics(
                side="sell",
                addr=addr,
                symbol=symbol,
                mode=result.mode,
                tx_hash=result.tx_hash,
                idempotency_key=idempotency_key,
            ),
        )
        trade_history_id = self.database.find_trade_history_id(
            addr=addr, side="sell", tx_hash=result.tx_hash
        )
        payload = {
            "status": "ok",
            "mode": result.mode,
            "tx_hash": result.tx_hash,
            "idempotency_key": idempotency_key,
        }
        self.database.record_manual_trade_audit(
            self._manual_trade_audit_record(
                side="sell",
                addr=addr,
                symbol=symbol,
                amount_usd=amount_usd,
                outcome="executed",
                reason="manual sell executed",
                idempotency_key=idempotency_key,
                tx_hash=result.tx_hash,
                mode=result.mode,
                trade_history_id=trade_history_id,
                request=self._manual_trade_request_payload(
                    query, side="sell", addr=addr, symbol=symbol, amount_usd=amount_usd
                ),
                response=payload,
            )
        )
        self.database.update_order_idempotency(
            idempotency_key,
            status="executed",
            result=payload,
            tx_hash=result.tx_hash,
            trade_history_id=trade_history_id,
        )
        self._json(payload)

    def _manual_trade_metrics(
        self,
        *,
        side: str,
        addr: str,
        symbol: str,
        mode: str,
        tx_hash: Optional[str],
        idempotency_key: str,
    ) -> dict[str, Any]:
        return {
            "manual": True,
            "source": "admin_api",
            "idempotency_key": idempotency_key,
            "request": {
                "endpoint": f"/api/{side}",
                "side": side,
                "addr": addr,
                "symbol": symbol,
            },
            "execution_mode": mode,
            "tx_hash": tx_hash,
        }

    def _manual_trade_audit_payload(self, query: dict[str, list[str]]) -> dict[str, Any]:
        limit = self._limit(query, default=20, maximum=100)
        offset = self._offset(query)
        addr = self._query_value(query, "addr")
        side = self._query_value(query, "side")
        outcome = self._query_value(query, "outcome")
        items = self.database.get_manual_trade_audit(
            limit=limit + offset, addr=addr, side=side, outcome=outcome
        )
        sliced = items[offset : offset + limit]
        total = len(items)
        return {
            "items": sliced,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "returned": len(sliced),
                "total": total,
                "has_more": offset + len(sliced) < total,
                "next_offset": offset + len(sliced) if offset + len(sliced) < total else None,
                "prev_offset": max(0, offset - limit) if offset > 0 else None,
            },
            "filters": {
                "addr": addr,
                "side": side,
                "outcome": outcome,
            },
            "persisted": True,
            "source": "manual_trade_audit",
        }

    def _manual_trade_idempotency_key(
        self,
        query: dict[str, list[str]],
        *,
        side: str,
        addr: str,
        symbol: str,
        amount_usd: float,
    ) -> str:
        explicit = self._query_value(query, "idempotency_key")
        if explicit:
            return explicit
        header_value = self.headers.get("Idempotency-Key", "").strip()
        if header_value:
            return header_value
        fingerprint = self._request_fingerprint(query, method="POST")
        return (
            f"manual:{side}:{addr}:{hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()[:16]}"
        )

    def _manual_trade_request_payload(
        self,
        query: dict[str, list[str]],
        *,
        side: str,
        addr: str,
        symbol: str,
        amount_usd: float,
    ) -> dict[str, Any]:
        return {
            "side": side,
            "addr": addr,
            "symbol": symbol,
            "amount_usd": amount_usd,
            "token": bool(self._query_value(query, "token")),
        }

    def _manual_trade_audit_record(
        self,
        *,
        side: str,
        addr: str,
        symbol: str,
        amount_usd: float,
        outcome: str,
        reason: str,
        idempotency_key: str,
        request: dict[str, Any],
        response: dict[str, Any],
        tx_hash: Optional[str] = None,
        mode: str = "paper",
        trade_history_id: Optional[int] = None,
    ) -> dict[str, Any]:
        return {
            "idempotency_key": idempotency_key,
            "operator_id": self._request_operator_id(),
            "operator_ip": self._request_operator_ip(),
            "mode": mode,
            "side": side,
            "addr": addr,
            "symbol": symbol,
            "amount_usd": amount_usd,
            "outcome": outcome,
            "reason": reason,
            "tx_hash": tx_hash,
            "trade_history_id": trade_history_id,
            "request": request,
            "response": response,
        }

    def _request_operator_id(self) -> str:
        bearer = self.headers.get("Authorization", "")
        if bearer.lower().startswith("bearer "):
            return "bearer"
        if self.headers.get("X-Admin-Token", "").strip():
            return "x-admin-token"
        return (
            "query-token"
            if self._query_value(parse_qs(urlparse(self.path).query), "token")
            else "anonymous"
        )

    def _request_operator_ip(self) -> str:
        return str(self.client_address[0] if self.client_address else "")

    def _request_fingerprint(self, query: dict[str, list[str]], *, method: str) -> str:
        payload = {
            "method": method,
            "path": urlparse(self.path).path,
            "query": {key: list(value) for key, value in sorted(query.items()) if key != "token"},
        }
        serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

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

    def _dependency_health_payload(self) -> dict[str, Any]:
        market_status = self.market_data.runtime_status()
        executor_status = self.executor.runtime_status()
        recent_failures = self.database.get_dependency_failures(limit=10, open_only=True)
        items = [
            {
                "name": "pumpportal_stream",
                "kind": "market_data",
                "status": "ok"
                if bool(market_status.get("stream_connected"))
                and bool(market_status.get("stream_task_running"))
                else "degraded",
                "details": {
                    "stream_connected": bool(market_status.get("stream_connected")),
                    "stream_task_running": bool(market_status.get("stream_task_running")),
                    "desired_token_subscriptions": int(
                        market_status.get("desired_token_subscriptions") or 0
                    ),
                    "active_token_subscriptions": int(
                        market_status.get("active_token_subscriptions") or 0
                    ),
                },
            },
            {
                "name": "market_data_cache",
                "kind": "market_data",
                "status": "ok",
                "details": {
                    "cached_trade_snapshots": int(market_status.get("cached_trade_snapshots") or 0),
                    "cached_token_metadata": int(market_status.get("cached_token_metadata") or 0),
                    "cached_holder_metrics": int(market_status.get("cached_holder_metrics") or 0),
                },
            },
            {
                "name": "trade_executor",
                "kind": "execution",
                "status": "paper" if not bool(executor_status.get("live_enabled")) else "armed",
                "details": dict(executor_status),
            },
        ]
        degraded = sum(1 for item in items if item["status"] == "degraded")
        return {
            "items": items,
            "recent_failures": recent_failures,
            "summary": {
                "total": len(items),
                "degraded": degraded,
                "healthy": len(items) - degraded,
                "open_failures": len(recent_failures),
            },
            "persisted": True,
            "source": "runtime_status+dependency_failures",
            "checked_at": int(time.time()),
        }

    def _worker_error_payload(self) -> dict[str, Any]:
        engine_status = self.engine.runtime_status() if self.engine else {}
        workers = engine_status.get("workers") or {}
        items = []
        for name, worker in workers.items():
            error_count = int(worker.get("error_count") or 0)
            last_error = str(worker.get("last_error") or "")
            if error_count <= 0 and not last_error:
                continue
            items.append(
                {
                    "worker": name,
                    "state": str(worker.get("state") or "unknown"),
                    "error_count": error_count,
                    "last_error": last_error,
                    "last_error_ts": int(worker.get("last_error_ts") or 0),
                    "last_seen_ts": int(worker.get("last_seen_ts") or 0),
                    "last_success_ts": int(worker.get("last_success_ts") or 0),
                    "last_seen_age": str(worker.get("last_seen_age") or "-"),
                    "last_success_age": str(worker.get("last_success_age") or "-"),
                }
            )
        items.sort(
            key=lambda item: (item["last_error_ts"], item["error_count"], item["worker"]),
            reverse=True,
        )
        return {
            "items": items,
            "summary": {
                "workers_with_errors": len(items),
                "total_errors": sum(int(item["error_count"]) for item in items),
            },
            "persisted": False,
            "source": "engine.runtime_status",
            "checked_at": int(time.time()),
        }

    def _health_payload(self) -> dict[str, Any]:
        status_payload = self._status_payload()
        market_data_status = status_payload["market_data"]
        engine_status = status_payload["engine"] or {}
        ready = bool(market_data_status.get("stream_task_running")) and bool(
            engine_status.get("strategy_mode")
        )
        return {
            "status": "ok" if ready else "degraded",
            "ready": ready,
            "checked_at": int(time.time()),
            **status_payload,
        }

    def _metrics_payload(self) -> dict[str, Any]:
        status = self._status_payload()
        health = self._health_payload()
        dependency_payload = self._dependency_health_payload()
        worker_payload = self._worker_error_payload()
        performance = self.database.performance_summary()
        runtime_safety = (status.get("engine") or {}).get("runtime_safety") or {}
        alerts: list[dict[str, Any]] = []

        if not bool(health.get("ready")):
            alerts.append(
                {
                    "severity": "warning",
                    "code": "service_not_ready",
                    "message": "Service health is degraded or startup is incomplete.",
                }
            )
        if int(dependency_payload["summary"].get("open_failures") or 0) > 0:
            alerts.append(
                {
                    "severity": "warning",
                    "code": "dependency_failures_open",
                    "message": "One or more dependency failures are still open.",
                }
            )
        if int(worker_payload["summary"].get("workers_with_errors") or 0) > 0:
            alerts.append(
                {
                    "severity": "warning",
                    "code": "worker_errors_present",
                    "message": "At least one worker reported recent runtime errors.",
                }
            )
        if bool(runtime_safety.get("shutdown_active")):
            alerts.append(
                {
                    "severity": "critical",
                    "code": "runtime_shutdown_active",
                    "message": str(runtime_safety.get("shutdown_reason") or "Runtime safety stop"),
                }
            )

        gauges = {
            "uptime_seconds": float(status.get("uptime_seconds") or 0),
            "ready": 1.0 if bool(health.get("ready")) else 0.0,
            "live_enabled": 1.0 if bool(status.get("live_enabled")) else 0.0,
            "open_positions": float(performance.get("open_positions") or 0),
            "cash_balance_usd": float(performance.get("cash_balance_usd") or 0.0),
            "total_equity_usd": float(performance.get("total_equity_usd") or 0.0),
            "net_profit_usd": float(performance.get("net_profit_usd") or 0.0),
            "max_drawdown_pct": float(performance.get("max_drawdown_pct") or 0.0),
            "dependency_open_failures": float(
                dependency_payload["summary"].get("open_failures") or 0
            ),
            "degraded_dependencies": float(dependency_payload["summary"].get("degraded") or 0),
            "workers_with_errors": float(worker_payload["summary"].get("workers_with_errors") or 0),
            "worker_total_errors": float(worker_payload["summary"].get("total_errors") or 0),
            "market_stream_connected": 1.0
            if bool((status.get("market_data") or {}).get("stream_connected"))
            else 0.0,
            "market_stream_task_running": 1.0
            if bool((status.get("market_data") or {}).get("stream_task_running"))
            else 0.0,
            "runtime_shutdown_active": 1.0 if bool(runtime_safety.get("shutdown_active")) else 0.0,
        }
        counters = {
            "closed_trades": float(performance.get("closed_trades") or 0),
            "worker_total_errors": float(worker_payload["summary"].get("total_errors") or 0),
            "open_dependency_failures": float(
                dependency_payload["summary"].get("open_failures") or 0
            ),
        }

        return {
            "service": self.settings.service_name,
            "version": __version__,
            "environment": self.settings.environment,
            "generated_at": int(time.time()),
            "mode": self.settings.execution_mode,
            "summary": {
                "ready": bool(health.get("ready")),
                "open_positions": int(performance.get("open_positions") or 0),
                "open_dependency_failures": int(
                    dependency_payload["summary"].get("open_failures") or 0
                ),
                "workers_with_errors": int(
                    worker_payload["summary"].get("workers_with_errors") or 0
                ),
                "current_balance_usd": float(performance.get("cash_balance_usd") or 0.0),
                "total_equity_usd": float(performance.get("total_equity_usd") or 0.0),
                "net_profit_usd": float(performance.get("net_profit_usd") or 0.0),
            },
            "gauges": gauges,
            "counters": counters,
            "alerts": alerts,
            "sources": {
                "status": "/api/v1/status",
                "health": "/api/v1/health",
                "dependencies": "/api/v1/dependencies",
                "worker_errors": "/api/v1/worker-errors",
                "performance": "/api/v1/performance",
            },
        }

    def _serve_prometheus_metrics(self) -> None:
        payload = self._metrics_payload()
        labels = (
            f'service="{self.settings.service_name}",'
            f'environment="{self.settings.environment}",'
            f'mode="{self.settings.execution_mode}"'
        )
        lines = [
            "# HELP chainbot_ready Service readiness derived from /api/health.",
            "# TYPE chainbot_ready gauge",
            f"chainbot_ready{{{labels}}} {payload['gauges']['ready']:.0f}",
            "# HELP chainbot_live_enabled Whether real trading is enabled.",
            "# TYPE chainbot_live_enabled gauge",
            f"chainbot_live_enabled{{{labels}}} {payload['gauges']['live_enabled']:.0f}",
            "# HELP chainbot_uptime_seconds Service uptime in seconds.",
            "# TYPE chainbot_uptime_seconds gauge",
            f"chainbot_uptime_seconds{{{labels}}} {payload['gauges']['uptime_seconds']:.0f}",
            "# HELP chainbot_open_positions Number of open positions.",
            "# TYPE chainbot_open_positions gauge",
            f"chainbot_open_positions{{{labels}}} {payload['gauges']['open_positions']:.0f}",
            "# HELP chainbot_cash_balance_usd Current cash balance in USD.",
            "# TYPE chainbot_cash_balance_usd gauge",
            f"chainbot_cash_balance_usd{{{labels}}} {payload['gauges']['cash_balance_usd']:.8f}",
            "# HELP chainbot_total_equity_usd Total equity including open positions.",
            "# TYPE chainbot_total_equity_usd gauge",
            f"chainbot_total_equity_usd{{{labels}}} {payload['gauges']['total_equity_usd']:.8f}",
            "# HELP chainbot_net_profit_usd Net profit in USD.",
            "# TYPE chainbot_net_profit_usd gauge",
            f"chainbot_net_profit_usd{{{labels}}} {payload['gauges']['net_profit_usd']:.8f}",
            "# HELP chainbot_max_drawdown_pct Maximum realized drawdown percentage.",
            "# TYPE chainbot_max_drawdown_pct gauge",
            f"chainbot_max_drawdown_pct{{{labels}}} {payload['gauges']['max_drawdown_pct']:.8f}",
            "# HELP chainbot_dependency_open_failures Number of unresolved dependency failures.",
            "# TYPE chainbot_dependency_open_failures gauge",
            f"chainbot_dependency_open_failures{{{labels}}} {payload['gauges']['dependency_open_failures']:.0f}",
            "# HELP chainbot_degraded_dependencies Number of runtime dependencies in degraded state.",
            "# TYPE chainbot_degraded_dependencies gauge",
            f"chainbot_degraded_dependencies{{{labels}}} {payload['gauges']['degraded_dependencies']:.0f}",
            "# HELP chainbot_workers_with_errors Number of workers reporting errors.",
            "# TYPE chainbot_workers_with_errors gauge",
            f"chainbot_workers_with_errors{{{labels}}} {payload['gauges']['workers_with_errors']:.0f}",
            "# HELP chainbot_worker_total_errors Total worker error count across all workers.",
            "# TYPE chainbot_worker_total_errors gauge",
            f"chainbot_worker_total_errors{{{labels}}} {payload['gauges']['worker_total_errors']:.0f}",
            "# HELP chainbot_market_stream_connected Whether the market stream is connected.",
            "# TYPE chainbot_market_stream_connected gauge",
            f"chainbot_market_stream_connected{{{labels}}} {payload['gauges']['market_stream_connected']:.0f}",
            "# HELP chainbot_market_stream_task_running Whether the market stream task is running.",
            "# TYPE chainbot_market_stream_task_running gauge",
            f"chainbot_market_stream_task_running{{{labels}}} {payload['gauges']['market_stream_task_running']:.0f}",
            "# HELP chainbot_runtime_shutdown_active Whether runtime safety has stopped automated trading.",
            "# TYPE chainbot_runtime_shutdown_active gauge",
            f"chainbot_runtime_shutdown_active{{{labels}}} {payload['gauges']['runtime_shutdown_active']:.0f}",
        ]
        body = "\n".join(lines) + "\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

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
