from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Optional

from .settings import Settings


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = settings.database_path
        self._lock = threading.Lock()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scan_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_time TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    age TEXT,
                    addr TEXT NOT NULL UNIQUE,
                    price REAL NOT NULL,
                    liquidity REAL NOT NULL,
                    dev_buy REAL NOT NULL,
                    progress TEXT,
                    ratio TEXT,
                    score REAL NOT NULL,
                    first_seen_ts INTEGER NOT NULL DEFAULT 0,
                    created_ts INTEGER NOT NULL DEFAULT 0,
                    holder_count_estimate REAL NOT NULL DEFAULT 0,
                    top10_owner_pct REAL NOT NULL DEFAULT 0,
                    top20_owner_pct REAL NOT NULL DEFAULT 0,
                    largest_owner_pct REAL NOT NULL DEFAULT 0,
                    bundle_risk_score REAL NOT NULL DEFAULT 0,
                    socials TEXT,
                    dex_url TEXT
                )
                """
            )
            self._ensure_column(conn, "scan_logs", "first_seen_ts", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "scan_logs", "created_ts", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "scan_logs", "holder_count_estimate", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(conn, "scan_logs", "top10_owner_pct", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(conn, "scan_logs", "top20_owner_pct", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(conn, "scan_logs", "largest_owner_pct", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(conn, "scan_logs", "bundle_risk_score", "REAL NOT NULL DEFAULT 0")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS opportunities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    score REAL NOT NULL,
                    reason TEXT NOT NULL,
                    addr TEXT NOT NULL UNIQUE,
                    liq REAL NOT NULL,
                    vol REAL NOT NULL,
                    dex_url TEXT,
                    scan_time TEXT NOT NULL,
                    socials TEXT,
                    progress TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS news_events (
                    event_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT,
                    published_ts INTEGER NOT NULL,
                    fetched_ts INTEGER NOT NULL,
                    topics_json TEXT NOT NULL,
                    entities_json TEXT NOT NULL,
                    sentiment TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source_weight REAL NOT NULL,
                    active_until_ts INTEGER NOT NULL,
                    raw_text TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wallet (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    initial_bal REAL NOT NULL,
                    current_balance REAL NOT NULL,
                    buy_count INTEGER NOT NULL,
                    buy_total REAL NOT NULL,
                    sell_count INTEGER NOT NULL,
                    sell_total REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS portfolio (
                    addr TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    buy_price REAL NOT NULL,
                    max_price REAL NOT NULL,
                    amount_usd REAL NOT NULL,
                    entry_liquidity REAL NOT NULL DEFAULT 0,
                    profit_locked_usd REAL NOT NULL DEFAULT 0,
                    moonbag_active INTEGER NOT NULL DEFAULT 0,
                    buy_time TEXT NOT NULL,
                    current_price REAL NOT NULL,
                    price_source TEXT NOT NULL DEFAULT 'initial',
                    price_updated_ts INTEGER NOT NULL DEFAULT 0,
                    entry_tx TEXT,
                    mode TEXT NOT NULL DEFAULT 'paper'
                )
                """
            )
            self._ensure_column(conn, "portfolio", "entry_liquidity", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(conn, "portfolio", "profit_locked_usd", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(conn, "portfolio", "moonbag_active", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "portfolio", "price_source", "TEXT NOT NULL DEFAULT 'initial'")
            self._ensure_column(conn, "portfolio", "price_updated_ts", "INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    addr TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    price REAL NOT NULL,
                    amount_usd REAL NOT NULL,
                    pnl_pct REAL,
                    tx_hash TEXT,
                    metrics_json TEXT,
                    time TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    mode TEXT NOT NULL,
                    score_offset REAL NOT NULL DEFAULT 0,
                    position_multiplier REAL NOT NULL DEFAULT 1,
                    cooldown_multiplier REAL NOT NULL DEFAULT 1,
                    max_open_positions INTEGER NOT NULL DEFAULT 0,
                    instant_probe_enabled INTEGER NOT NULL DEFAULT 1,
                    reason TEXT NOT NULL DEFAULT '',
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    updated_ts INTEGER NOT NULL DEFAULT 0,
                    cooldown_until_ts INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            existing_strategy = conn.execute("SELECT COUNT(*) FROM strategy_state WHERE id = 1").fetchone()[0]
            if existing_strategy == 0:
                conn.execute(
                    """
                    INSERT INTO strategy_state
                    (id, mode, score_offset, position_multiplier, cooldown_multiplier, max_open_positions, instant_probe_enabled, reason, metrics_json, updated_ts, cooldown_until_ts)
                    VALUES (1, 'balanced', 0, 1, 1, 0, 1, '初始化策略状态', '{}', ?, 0)
                    """,
                    (int(time.time()),),
                )
            existing = conn.execute("SELECT COUNT(*) FROM wallet WHERE id = 1").fetchone()[0]
            if existing == 0:
                conn.execute(
                    """
                    INSERT INTO wallet (id, initial_bal, current_balance, buy_count, buy_total, sell_count, sell_total)
                    VALUES (1, ?, ?, 0, 0, 0, 0)
                    """,
                    (self.settings.initial_balance_usd, self.settings.initial_balance_usd),
                )

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @contextmanager
    def connection(self) -> sqlite3.Connection:
        with self._lock:
            conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            try:
                conn.row_factory = sqlite3.Row
                yield conn
                conn.commit()
            finally:
                conn.close()

    def record_scan(self, payload: dict[str, Any]) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO scan_logs
                (scan_time, platform, symbol, age, addr, price, liquidity, dev_buy, progress, ratio, score, first_seen_ts, created_ts, holder_count_estimate, top10_owner_pct, top20_owner_pct, largest_owner_pct, bundle_risk_score, socials, dex_url)
                VALUES (:scan_time, :platform, :symbol, :age, :addr, :price, :liquidity, :dev_buy, :progress, :ratio, :score, :first_seen_ts, :created_ts, :holder_count_estimate, :top10_owner_pct, :top20_owner_pct, :largest_owner_pct, :bundle_risk_score, :socials, :dex_url)
                ON CONFLICT(addr) DO UPDATE SET
                    scan_time=excluded.scan_time,
                    symbol=excluded.symbol,
                    age=excluded.age,
                    price=excluded.price,
                    liquidity=excluded.liquidity,
                    dev_buy=excluded.dev_buy,
                    progress=excluded.progress,
                    ratio=excluded.ratio,
                    score=excluded.score,
                    first_seen_ts=COALESCE(NULLIF(scan_logs.first_seen_ts, 0), excluded.first_seen_ts),
                    created_ts=COALESCE(NULLIF(scan_logs.created_ts, 0), excluded.created_ts),
                    holder_count_estimate=excluded.holder_count_estimate,
                    top10_owner_pct=excluded.top10_owner_pct,
                    top20_owner_pct=excluded.top20_owner_pct,
                    largest_owner_pct=excluded.largest_owner_pct,
                    bundle_risk_score=excluded.bundle_risk_score,
                    socials=excluded.socials,
                    dex_url=excluded.dex_url
                """,
                payload,
            )

    def pending_holder_scans(self, limit: int = 12, max_age_seconds: int = 420) -> list[dict[str, Any]]:
        min_seen_ts = int(time.time()) - max_age_seconds
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT scan_time, platform, symbol, age, addr, price, liquidity, dev_buy, progress, ratio, score, first_seen_ts, created_ts, holder_count_estimate, top10_owner_pct, top20_owner_pct, largest_owner_pct, bundle_risk_score, socials, dex_url
                FROM scan_logs
                WHERE holder_count_estimate <= 0
                  AND first_seen_ts >= ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (min_seen_ts, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def update_scan_holder_metrics(self, addr: str, metrics: dict[str, Any], score: float, bundle_risk_score: float) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE scan_logs
                SET holder_count_estimate = ?,
                    top10_owner_pct = ?,
                    top20_owner_pct = ?,
                    largest_owner_pct = ?,
                    bundle_risk_score = ?,
                    score = ?
                WHERE addr = ?
                """,
                (
                    float(metrics.get("holder_count_estimate") or 0.0),
                    float(metrics.get("top10_owner_pct") or 0.0),
                    float(metrics.get("top20_owner_pct") or 0.0),
                    float(metrics.get("largest_owner_pct") or 0.0),
                    float(bundle_risk_score or 0.0),
                    float(score or 0.0),
                    addr,
                ),
            )

    def record_opportunity(self, payload: dict[str, Any]) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO opportunities
                (type, symbol, score, reason, addr, liq, vol, dex_url, scan_time, socials, progress)
                VALUES (:type, :symbol, :score, :reason, :addr, :liq, :vol, :dex_url, :scan_time, :socials, :progress)
                ON CONFLICT(addr) DO UPDATE SET
                    type=excluded.type,
                    symbol=excluded.symbol,
                    score=excluded.score,
                    reason=excluded.reason,
                    liq=excluded.liq,
                    vol=excluded.vol,
                    dex_url=excluded.dex_url,
                    scan_time=excluded.scan_time,
                    socials=excluded.socials,
                    progress=excluded.progress
                """,
                payload,
            )

    def record_news_events(self, events: list[Any]) -> int:
        if not events:
            return 0
        with self.connection() as conn:
            before = conn.total_changes
            for event in events:
                conn.execute(
                    """
                    INSERT INTO news_events
                    (event_id, source, title, url, published_ts, fetched_ts, topics_json, entities_json, sentiment, confidence, source_weight, active_until_ts, raw_text)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id) DO UPDATE SET
                        fetched_ts=excluded.fetched_ts,
                        topics_json=excluded.topics_json,
                        entities_json=excluded.entities_json,
                        sentiment=excluded.sentiment,
                        confidence=excluded.confidence,
                        source_weight=excluded.source_weight,
                        active_until_ts=excluded.active_until_ts
                    """,
                    (
                        event.event_id,
                        event.source,
                        event.title,
                        event.url,
                        event.published_ts,
                        event.fetched_ts,
                        json.dumps(event.topics, ensure_ascii=False),
                        json.dumps(event.entities, ensure_ascii=False),
                        event.sentiment,
                        event.confidence,
                        event.source_weight,
                        event.active_until_ts,
                        event.raw_text,
                    ),
                )
            return conn.total_changes - before

    def get_news_events(self, limit: int = 30, active_only: bool = False) -> list[dict[str, Any]]:
        now = int(time.time())
        where = "WHERE active_until_ts >= ?" if active_only else ""
        params = (now, limit) if active_only else (limit,)
        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT event_id, source, title, url, published_ts, fetched_ts, topics_json, entities_json, sentiment, confidence, source_weight, active_until_ts, raw_text
                FROM news_events
                {where}
                ORDER BY published_ts DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            items = [dict(row) for row in rows]
            for item in items:
                item["topics"] = self._loads_json_list(item.pop("topics_json", "[]"))
                item["entities"] = self._loads_json_list(item.pop("entities_json", "[]"))
                item["age"] = self._format_duration(max(now - int(item.get("published_ts") or 0), 0))
                item["expires_in"] = self._format_duration(max(int(item.get("active_until_ts") or 0) - now, 0))
                item["published_time"] = self._format_clock(int(item.get("published_ts") or 0))
            return items

    def active_news_events(self) -> list[dict[str, Any]]:
        return self.get_news_events(limit=80, active_only=True)

    def _loads_json_list(self, value: Any) -> list[Any]:
        try:
            parsed = json.loads(value or "[]")
        except (TypeError, json.JSONDecodeError):
            return []
        return parsed if isinstance(parsed, list) else []

    def wallet(self) -> dict[str, Any]:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM wallet WHERE id = 1").fetchone()
            return dict(row) if row else {}

    def update_wallet_for_buy(self, amount_usd: float) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE wallet
                SET current_balance = current_balance - ?,
                    buy_count = buy_count + 1,
                    buy_total = buy_total + ?
                WHERE id = 1
                """,
                (amount_usd, amount_usd),
            )

    def update_wallet_for_sell(self, amount_usd: float, proceeds_usd: float) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE wallet
                SET current_balance = current_balance + ?,
                    sell_count = sell_count + 1,
                    sell_total = sell_total + ?
                WHERE id = 1
                """,
                (proceeds_usd, proceeds_usd),
            )

    def open_position(
        self,
        addr: str,
        symbol: str,
        price: float,
        amount_usd: float,
        mode: str,
        entry_tx: Any = None,
        entry_liquidity: float = 0.0,
        metrics: Optional[dict[str, Any]] = None,
    ) -> None:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        with self.connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO portfolio
                (addr, symbol, buy_price, max_price, amount_usd, entry_liquidity, buy_time, current_price, entry_tx, mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (addr, symbol, price, price, amount_usd, entry_liquidity, now, price, entry_tx, mode),
            )
            conn.execute(
                """
                INSERT INTO trade_history
                (addr, symbol, side, price, amount_usd, pnl_pct, tx_hash, metrics_json, time)
                VALUES (?, ?, 'buy', ?, ?, NULL, ?, ?, ?)
                """,
                (addr, symbol, price, amount_usd, entry_tx, json.dumps(metrics or {}), now),
            )

    def update_position_price(
        self,
        addr: str,
        current_price: float,
        max_price: float,
        source: str = "unknown",
        updated_ts: int = 0,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE portfolio SET current_price = ?, max_price = ?, price_source = ?, price_updated_ts = ? WHERE addr = ?",
                (current_price, max_price, source, updated_ts or int(time.time()), addr),
            )

    def close_position(
        self,
        addr: str,
        symbol: str,
        exit_price: float,
        amount_usd: float,
        pnl_pct: float,
        tx_hash: Any,
        metrics: dict[str, Any],
    ) -> None:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        with self.connection() as conn:
            conn.execute("DELETE FROM portfolio WHERE addr = ?", (addr,))
            conn.execute(
                """
                INSERT INTO trade_history
                (addr, symbol, side, price, amount_usd, pnl_pct, tx_hash, metrics_json, time)
                VALUES (?, ?, 'sell', ?, ?, ?, ?, ?, ?)
                """,
                (addr, symbol, exit_price, amount_usd, pnl_pct, tx_hash, json.dumps(metrics), now),
            )

    def latest_buy_metrics(self, addr: str) -> dict[str, Any]:
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT metrics_json
                FROM trade_history
                WHERE addr = ? AND side = 'buy'
                ORDER BY id DESC
                LIMIT 1
                """,
                (addr,),
            ).fetchone()
        if not row:
            return {}
        try:
            return json.loads(row["metrics_json"] or "{}")
        except json.JSONDecodeError:
            return {}

    def reduce_position(
        self,
        addr: str,
        symbol: str,
        exit_price: float,
        sold_amount_usd: float,
        remaining_amount_usd: float,
        pnl_pct: float,
        tx_hash: Any,
        metrics: dict[str, Any],
    ) -> None:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        realized_profit = sold_amount_usd * pnl_pct
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE portfolio
                SET amount_usd = ?,
                    profit_locked_usd = profit_locked_usd + ?,
                    moonbag_active = 1
                WHERE addr = ?
                """,
                (remaining_amount_usd, realized_profit, addr),
            )
            conn.execute(
                """
                INSERT INTO trade_history
                (addr, symbol, side, price, amount_usd, pnl_pct, tx_hash, metrics_json, time)
                VALUES (?, ?, 'sell', ?, ?, ?, ?, ?, ?)
                """,
                (addr, symbol, exit_price, sold_amount_usd, pnl_pct, tx_hash, json.dumps(metrics), now),
            )

    def get_scan_logs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT scan_time, platform, symbol, age, addr, price, liquidity, dev_buy, progress, ratio, score, first_seen_ts, created_ts, holder_count_estimate, top10_owner_pct, top20_owner_pct, largest_owner_pct, bundle_risk_score, socials, dex_url
                FROM scan_logs ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                first_seen_ts = int(item.get("first_seen_ts") or 0)
                created_ts = int(item.get("created_ts") or 0)
                item["volume_5m"] = item.pop("dev_buy")
                item["monitor_time"] = self._format_clock(first_seen_ts)
                item["token_age"] = self._format_duration(max(int(time.time()) - created_ts, 0)) if created_ts > 0 else "-"
                item["scan_time"] = item["monitor_time"]
                item["age"] = item["token_age"]
                holder_count = float(item.get("holder_count_estimate") or 0)
                item["bundle_risk_label"] = "待查" if holder_count <= 0 else self._bundle_risk_label(float(item.get("bundle_risk_score") or 0))
                result.append(item)
            return result

    def _bundle_risk_label(self, score: float) -> str:
        if score >= 0.75:
            return "高"
        if score >= 0.45:
            return "中"
        return "低"

    def _format_clock(self, timestamp: int) -> str:
        if timestamp <= 0:
            return "-"
        return time.strftime("%H:%M:%S", time.localtime(timestamp))

    def _format_duration(self, seconds: int) -> str:
        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            return f"{seconds // 60}m"
        if seconds < 86400:
            return f"{seconds // 3600}h"
        return f"{seconds // 86400}d"

    def get_opportunities(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT type, symbol, score, reason, addr, liq, vol, dex_url, scan_time, socials, progress
                FROM opportunities ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            keys = ["type", "symbol", "score", "reason", "addr", "liq", "vol", "dex_url", "scan_time", "socials", "progress"]
            return [dict(zip(keys, row)) for row in rows]

    def get_portfolio(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT addr, symbol, buy_price, current_price, max_price, amount_usd, entry_liquidity, profit_locked_usd, moonbag_active, buy_time, price_source, price_updated_ts, entry_tx, mode
                FROM portfolio
                ORDER BY buy_time DESC
                """
            ).fetchall()
            keys = ["addr", "symbol", "buy_price", "current_price", "max_price", "amount_usd", "entry_liquidity", "profit_locked_usd", "moonbag_active", "buy_time", "price_source", "price_updated_ts", "entry_tx", "mode"]
            items = [dict(zip(keys, row)) for row in rows]
            now = time.time()
            for item in items:
                buy_price = float(item.get("buy_price") or 0)
                current_price = float(item.get("current_price") or 0)
                amount_usd = float(item.get("amount_usd") or 0)
                quantity_estimate = (amount_usd / buy_price) if buy_price > 0 else 0.0
                current_value_usd = quantity_estimate * current_price
                unrealized_pnl_usd = current_value_usd - amount_usd
                unrealized_pnl_pct = (unrealized_pnl_usd / amount_usd) if amount_usd > 0 else 0.0
                item["quantity_estimate"] = quantity_estimate
                item["current_value_usd"] = current_value_usd
                item["unrealized_pnl_usd"] = unrealized_pnl_usd
                item["unrealized_pnl_pct"] = unrealized_pnl_pct
                item["buy_time_age"] = self._age_from_time_string(item.get("buy_time"), now)
                item["price_update_age"] = self._format_duration(max(int(now) - int(item.get("price_updated_ts") or 0), 0)) if int(item.get("price_updated_ts") or 0) > 0 else "-"
            return items

    def _age_from_time_string(self, value: Any, now_ts: float) -> str:
        try:
            parsed = time.strptime(str(value), "%Y-%m-%d %H:%M:%S")
            age_seconds = max(int(now_ts - time.mktime(parsed)), 0)
        except (TypeError, ValueError):
            return "-"
        return self._format_duration(age_seconds)

    def get_trade_history(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, addr, symbol, side, price, amount_usd, pnl_pct, tx_hash, metrics_json, time
                FROM trade_history ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            keys = ["id", "addr", "symbol", "side", "price", "amount_usd", "pnl_pct", "tx_hash", "metrics_json", "time"]
            items = [dict(zip(keys, row)) for row in rows]
            for item in items:
                raw_metrics = item.get("metrics_json") or "{}"
                try:
                    item["metrics"] = json.loads(raw_metrics)
                except json.JSONDecodeError:
                    item["metrics"] = {}
            return items

    def recent_sell_trades(self, limit: int = 40) -> list[dict[str, Any]]:
        return [row for row in self.get_trade_history(limit=limit * 3) if row.get("side") == "sell" and row.get("pnl_pct") is not None][:limit]

    def get_strategy_state(self) -> dict[str, Any]:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM strategy_state WHERE id = 1").fetchone()
        if not row:
            return {}
        item = dict(row)
        try:
            item["metrics"] = json.loads(item.pop("metrics_json", "{}") or "{}")
        except json.JSONDecodeError:
            item["metrics"] = {}
        item["instant_probe_enabled"] = bool(item.get("instant_probe_enabled"))
        return item

    def save_strategy_state(self, state: dict[str, Any]) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO strategy_state
                (id, mode, score_offset, position_multiplier, cooldown_multiplier, max_open_positions, instant_probe_enabled, reason, metrics_json, updated_ts, cooldown_until_ts)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    mode=excluded.mode,
                    score_offset=excluded.score_offset,
                    position_multiplier=excluded.position_multiplier,
                    cooldown_multiplier=excluded.cooldown_multiplier,
                    max_open_positions=excluded.max_open_positions,
                    instant_probe_enabled=excluded.instant_probe_enabled,
                    reason=excluded.reason,
                    metrics_json=excluded.metrics_json,
                    updated_ts=excluded.updated_ts,
                    cooldown_until_ts=excluded.cooldown_until_ts
                """,
                (
                    str(state.get("mode") or "balanced"),
                    float(state.get("score_offset") or 0.0),
                    float(state.get("position_multiplier") or 1.0),
                    float(state.get("cooldown_multiplier") or 1.0),
                    int(state.get("max_open_positions") or 0),
                    1 if state.get("instant_probe_enabled", True) else 0,
                    str(state.get("reason") or ""),
                    json.dumps(state.get("metrics") or {}, ensure_ascii=False),
                    int(state.get("updated_ts") or time.time()),
                    int(state.get("cooldown_until_ts") or 0),
                ),
            )

    def performance_summary(self) -> dict[str, Any]:
        wallet = self.wallet()
        initial_balance = float(wallet.get("initial_bal", self.settings.initial_balance_usd) or self.settings.initial_balance_usd)
        current_balance = float(wallet.get("current_balance", initial_balance) or initial_balance)
        portfolio = self.get_portfolio()
        unrealized_value_usd = sum(float(item.get("current_value_usd") or 0.0) for item in portfolio)
        unrealized_cost_usd = sum(float(item.get("amount_usd") or 0.0) for item in portfolio)
        unrealized_profit_usd = sum(float(item.get("unrealized_pnl_usd") or 0.0) for item in portfolio)
        unrealized_profit_pct = (unrealized_profit_usd / unrealized_cost_usd) if unrealized_cost_usd > 0 else 0.0
        total_equity_usd = current_balance + unrealized_value_usd
        total_profit_usd = total_equity_usd - initial_balance
        total_profit_pct = (total_profit_usd / initial_balance) if initial_balance else 0.0
        realized_rows = self.get_trade_history(limit=500)
        sells = [row for row in realized_rows if row["side"] == "sell" and row.get("pnl_pct") is not None]
        pnl_values = [float(row["pnl_pct"]) for row in sells]

        wins = [p for p in pnl_values if p > 0]
        losses = [p for p in pnl_values if p <= 0]
        total_realized_pct = sum(pnl_values)
        best_trade_pct = max(pnl_values) if pnl_values else 0.0
        worst_trade_pct = min(pnl_values) if pnl_values else 0.0
        avg_trade_pct = total_realized_pct / len(pnl_values) if pnl_values else 0.0

        equity = initial_balance
        peak = equity
        max_drawdown_pct = 0.0
        for row in reversed(sells):
            equity += float(row["amount_usd"]) * float(row["pnl_pct"])
            peak = max(peak, equity)
            if peak > 0:
                drawdown_pct = (equity - peak) / peak
                max_drawdown_pct = min(max_drawdown_pct, drawdown_pct)

        hold_seconds = []
        exit_reason_counts: dict[str, int] = {}
        for row in sells:
            metrics = row.get("metrics") or {}
            hold_seconds.append(float(metrics.get("hold_seconds", 0) or 0))
            reason = str(metrics.get("exit_reason") or "unknown")
            exit_reason_counts[reason] = exit_reason_counts.get(reason, 0) + 1

        avg_hold_seconds = sum(hold_seconds) / len(hold_seconds) if hold_seconds else 0.0
        best_exit_reason = max(exit_reason_counts, key=exit_reason_counts.get) if exit_reason_counts else "-"

        return {
            "initial_balance": initial_balance,
            "current_balance": current_balance,
            "cash_balance_usd": current_balance,
            "realized_profit_usd": current_balance - initial_balance,
            "realized_profit_pct": ((current_balance - initial_balance) / initial_balance) if initial_balance else 0.0,
            "unrealized_cost_usd": unrealized_cost_usd,
            "unrealized_value_usd": unrealized_value_usd,
            "unrealized_profit_usd": unrealized_profit_usd,
            "unrealized_profit_pct": unrealized_profit_pct,
            "total_equity_usd": total_equity_usd,
            "total_profit_usd": total_profit_usd,
            "total_profit_pct": total_profit_pct,
            "net_profit_usd": total_profit_usd,
            "net_profit_pct": total_profit_pct,
            "closed_trades": len(sells),
            "win_rate": (len(wins) / len(sells)) if sells else 0.0,
            "avg_trade_pct": avg_trade_pct,
            "best_trade_pct": best_trade_pct,
            "worst_trade_pct": worst_trade_pct,
            "profit_factor": (sum(wins) / abs(sum(losses))) if losses and abs(sum(losses)) > 0 else (999.0 if wins else 0.0),
            "max_drawdown_pct": max_drawdown_pct,
            "avg_hold_seconds": avg_hold_seconds,
            "best_exit_reason": best_exit_reason,
            "open_positions": self.open_position_count(),
        }

    def has_position(self, addr: str) -> bool:
        with self.connection() as conn:
            row = conn.execute("SELECT 1 FROM portfolio WHERE addr = ? LIMIT 1", (addr,)).fetchone()
            return row is not None

    def open_positions(self) -> list[dict[str, Any]]:
        return self.get_portfolio()

    def open_position_count(self) -> int:
        with self.connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM portfolio").fetchone()
            return int(row["count"]) if row else 0
