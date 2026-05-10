from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping, Optional

from .models import CandidateScan, DependencyFailureRow, PortfolioPosition, TradeHistoryRow
from .settings import Settings


def _resolve_sqlite_path(value: Any) -> Path:
    return Path(value).expanduser().resolve()


def _remove_sqlite_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{path}{suffix}")
        if sidecar.exists():
            sidecar.unlink()


def backup_sqlite_database(source_path: Any, destination_path: Any) -> Path:
    source = _resolve_sqlite_path(source_path)
    destination = _resolve_sqlite_path(destination_path)
    if not source.exists():
        raise FileNotFoundError(f"SQLite database not found: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _remove_sqlite_sidecars(destination)
    with sqlite3.connect(source, timeout=30, check_same_thread=False) as source_conn:
        source_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        with sqlite3.connect(destination, timeout=30, check_same_thread=False) as destination_conn:
            source_conn.backup(destination_conn)
            destination_conn.commit()
    return destination


def restore_sqlite_database(source_path: Any, destination_path: Any) -> Path:
    source = _resolve_sqlite_path(source_path)
    destination = _resolve_sqlite_path(destination_path)
    if not source.exists():
        raise FileNotFoundError(f"SQLite backup not found: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _remove_sqlite_sidecars(destination)
    with sqlite3.connect(source, timeout=30, check_same_thread=False) as source_conn:
        with sqlite3.connect(destination, timeout=30, check_same_thread=False) as destination_conn:
            source_conn.backup(destination_conn)
            destination_conn.commit()
            destination_conn.execute("PRAGMA journal_mode=WAL")
    return destination


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
            self._ensure_column(
                conn, "scan_logs", "holder_count_estimate", "REAL NOT NULL DEFAULT 0"
            )
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
            self._ensure_column(
                conn, "portfolio", "price_source", "TEXT NOT NULL DEFAULT 'initial'"
            )
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
                CREATE TABLE IF NOT EXISTS order_idempotency (
                    idempotency_key TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    side TEXT NOT NULL,
                    addr TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    amount_usd REAL NOT NULL DEFAULT 0,
                    request_fingerprint TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_ts INTEGER NOT NULL DEFAULT 0,
                    updated_ts INTEGER NOT NULL DEFAULT 0,
                    tx_hash TEXT,
                    trade_history_id INTEGER,
                    request_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS manual_trade_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action_ts INTEGER NOT NULL DEFAULT 0,
                    action_time TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL DEFAULT '',
                    operator_id TEXT NOT NULL DEFAULT '',
                    operator_ip TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL DEFAULT 'paper',
                    side TEXT NOT NULL,
                    addr TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    amount_usd REAL NOT NULL DEFAULT 0,
                    outcome TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '',
                    tx_hash TEXT,
                    trade_history_id INTEGER,
                    request_json TEXT NOT NULL DEFAULT '{}',
                    response_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dependency_failures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dependency TEXT NOT NULL,
                    operation TEXT NOT NULL DEFAULT '',
                    failure_type TEXT NOT NULL DEFAULT '',
                    severity TEXT NOT NULL DEFAULT 'error',
                    message TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    failure_ts INTEGER NOT NULL DEFAULT 0,
                    failure_time TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    resolved_ts INTEGER NOT NULL DEFAULT 0,
                    resolved_time TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS decision_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_ts INTEGER NOT NULL DEFAULT 0,
                    decision_time TEXT NOT NULL,
                    category TEXT NOT NULL,
                    action TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    addr TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    context_json TEXT NOT NULL DEFAULT '{}',
                    snapshot_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            self._ensure_column(
                conn, "decision_audit", "snapshot_json", "TEXT NOT NULL DEFAULT '{}'"
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_manual_trade_audit_action_ts
                ON manual_trade_audit(action_ts DESC, id DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_dependency_failures_lookup
                ON dependency_failures(dependency, failure_ts DESC, status)
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
            existing_strategy = conn.execute(
                "SELECT COUNT(*) FROM strategy_state WHERE id = 1"
            ).fetchone()[0]
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

    def _ensure_column(
        self, conn: sqlite3.Connection, table: str, column: str, definition: str
    ) -> None:
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

    def _delete_rows(
        self, conn: sqlite3.Connection, statement: str, params: tuple[Any, ...]
    ) -> int:
        before = conn.total_changes
        conn.execute(statement, params)
        return conn.total_changes - before

    def delete_scan_logs_older_than(self, cutoff_ts: int, cutoff_time: str) -> int:
        with self.connection() as conn:
            return self._delete_rows(
                conn,
                """
                DELETE FROM scan_logs
                WHERE (
                    COALESCE(NULLIF(first_seen_ts, 0), NULLIF(created_ts, 0)) IS NOT NULL
                    AND COALESCE(NULLIF(first_seen_ts, 0), NULLIF(created_ts, 0)) < ?
                )
                OR (
                    COALESCE(NULLIF(first_seen_ts, 0), NULLIF(created_ts, 0)) IS NULL
                    AND scan_time < ?
                )
                """,
                (cutoff_ts, cutoff_time),
            )

    def delete_opportunities_older_than(self, cutoff_time: str) -> int:
        with self.connection() as conn:
            return self._delete_rows(
                conn,
                "DELETE FROM opportunities WHERE scan_time < ?",
                (cutoff_time,),
            )

    def delete_news_events_older_than(self, cutoff_ts: int) -> int:
        with self.connection() as conn:
            return self._delete_rows(
                conn,
                "DELETE FROM news_events WHERE active_until_ts < ?",
                (cutoff_ts,),
            )

    def delete_decision_audit_older_than(self, cutoff_ts: int, cutoff_time: str) -> int:
        with self.connection() as conn:
            return self._delete_rows(
                conn,
                """
                DELETE FROM decision_audit
                WHERE (
                    COALESCE(NULLIF(decision_ts, 0), 0) > 0
                    AND decision_ts < ?
                )
                OR (
                    COALESCE(NULLIF(decision_ts, 0), 0) = 0
                    AND decision_time < ?
                )
                """,
                (cutoff_ts, cutoff_time),
            )

    def claim_order_idempotency(
        self,
        idempotency_key: str,
        *,
        source: str,
        side: str,
        addr: str,
        symbol: str,
        amount_usd: float,
        request_fingerprint: str = "",
        request: Optional[dict[str, Any]] = None,
        created_ts: int = 0,
    ) -> tuple[dict[str, Any], bool]:
        now_ts = int(created_ts or time.time())
        with self.connection() as conn:
            before = conn.total_changes
            conn.execute(
                """
                INSERT OR IGNORE INTO order_idempotency
                (idempotency_key, source, side, addr, symbol, amount_usd, request_fingerprint, status, created_ts, updated_ts, request_json, result_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, '{}')
                """,
                (
                    idempotency_key,
                    source,
                    side,
                    addr,
                    symbol,
                    float(amount_usd or 0.0),
                    request_fingerprint,
                    now_ts,
                    now_ts,
                    json.dumps(request or {}, ensure_ascii=False),
                ),
            )
            created = conn.total_changes > before
            row = conn.execute(
                """
                SELECT idempotency_key, source, side, addr, symbol, amount_usd, request_fingerprint, status, created_ts, updated_ts, tx_hash, trade_history_id, request_json, result_json
                FROM order_idempotency
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
        return self._hydrate_order_idempotency_row(row), created

    def get_order_idempotency(self, idempotency_key: str) -> Optional[dict[str, Any]]:
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT idempotency_key, source, side, addr, symbol, amount_usd, request_fingerprint, status, created_ts, updated_ts, tx_hash, trade_history_id, request_json, result_json
                FROM order_idempotency
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
        return self._hydrate_order_idempotency_row(row) if row else None

    def update_order_idempotency(
        self,
        idempotency_key: str,
        *,
        status: str,
        result: Optional[dict[str, Any]] = None,
        tx_hash: Any = None,
        trade_history_id: Optional[int] = None,
        updated_ts: int = 0,
    ) -> Optional[dict[str, Any]]:
        now_ts = int(updated_ts or time.time())
        existing = self.get_order_idempotency(idempotency_key)
        if not existing:
            return None
        next_result = existing.get("result") or {}
        if result:
            next_result.update(result)
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE order_idempotency
                SET status = ?,
                    updated_ts = ?,
                    tx_hash = COALESCE(?, tx_hash),
                    trade_history_id = COALESCE(?, trade_history_id),
                    result_json = ?
                WHERE idempotency_key = ?
                """,
                (
                    status,
                    now_ts,
                    tx_hash,
                    trade_history_id,
                    json.dumps(next_result, ensure_ascii=False),
                    idempotency_key,
                ),
            )
            row = conn.execute(
                """
                SELECT idempotency_key, source, side, addr, symbol, amount_usd, request_fingerprint, status, created_ts, updated_ts, tx_hash, trade_history_id, request_json, result_json
                FROM order_idempotency
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
        return self._hydrate_order_idempotency_row(row) if row else None

    def record_manual_trade_audit(self, payload: dict[str, Any]) -> int:
        action_ts = int(payload.get("action_ts") or time.time())
        action_time = str(
            payload.get("action_time")
            or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(action_ts))
        )
        with self.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO manual_trade_audit
                (action_ts, action_time, idempotency_key, operator_id, operator_ip, mode, side, addr, symbol, amount_usd, outcome, reason, tx_hash, trade_history_id, request_json, response_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action_ts,
                    action_time,
                    str(payload.get("idempotency_key") or ""),
                    str(payload.get("operator_id") or ""),
                    str(payload.get("operator_ip") or ""),
                    str(payload.get("mode") or "paper"),
                    str(payload.get("side") or ""),
                    str(payload.get("addr") or ""),
                    str(payload.get("symbol") or ""),
                    float(payload.get("amount_usd") or 0.0),
                    str(payload.get("outcome") or ""),
                    str(payload.get("reason") or ""),
                    payload.get("tx_hash"),
                    payload.get("trade_history_id"),
                    json.dumps(payload.get("request") or {}, ensure_ascii=False),
                    json.dumps(payload.get("response") or {}, ensure_ascii=False),
                ),
            )
        return int(cursor.lastrowid)

    def get_manual_trade_audit(
        self,
        limit: int = 50,
        *,
        addr: Optional[str] = None,
        side: Optional[str] = None,
        outcome: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        filters: list[str] = []
        params: list[Any] = []
        if addr:
            filters.append("addr = ?")
            params.append(addr)
        if side:
            filters.append("side = ?")
            params.append(side)
        if outcome:
            filters.append("outcome = ?")
            params.append(outcome)
        where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT id, action_ts, action_time, idempotency_key, operator_id, operator_ip, mode, side, addr, symbol, amount_usd, outcome, reason, tx_hash, trade_history_id, request_json, response_json
                FROM manual_trade_audit
                {where_sql}
                ORDER BY id DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        return self._hydrate_manual_trade_audit_rows(rows)

    def record_dependency_failure(self, payload: Mapping[str, Any] | DependencyFailureRow) -> int:
        model = (
            payload
            if isinstance(payload, DependencyFailureRow)
            else DependencyFailureRow.from_mapping(payload)
        )
        failure_ts = int(model.failure_ts or time.time())
        failure_time = str(
            model.failure_time or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(failure_ts))
        )
        with self.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO dependency_failures
                (dependency, operation, failure_type, severity, message, details_json, failure_ts, failure_time, status, resolved_ts, resolved_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '')
                """,
                (
                    model.dependency,
                    model.operation,
                    model.failure_type,
                    model.severity,
                    model.message,
                    json.dumps(model.details, ensure_ascii=False),
                    failure_ts,
                    failure_time,
                    model.status,
                ),
            )
        return int(cursor.lastrowid)

    def resolve_dependency_failures(
        self,
        dependency: str,
        *,
        operation: Optional[str] = None,
        resolved_ts: int = 0,
    ) -> int:
        now_ts = int(resolved_ts or time.time())
        resolved_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts))
        with self.connection() as conn:
            before = conn.total_changes
            if operation:
                conn.execute(
                    """
                    UPDATE dependency_failures
                    SET status = 'resolved',
                        resolved_ts = ?,
                        resolved_time = ?
                    WHERE dependency = ? AND operation = ? AND status != 'resolved'
                    """,
                    (now_ts, resolved_time, dependency, operation),
                )
            else:
                conn.execute(
                    """
                    UPDATE dependency_failures
                    SET status = 'resolved',
                        resolved_ts = ?,
                        resolved_time = ?
                    WHERE dependency = ? AND status != 'resolved'
                    """,
                    (now_ts, resolved_time, dependency),
                )
            return conn.total_changes - before

    def count_dependency_failures(
        self,
        dependency: str,
        since_ts: int,
        *,
        operation: Optional[str] = None,
        open_only: bool = False,
    ) -> int:
        filters = ["dependency = ?", "failure_ts >= ?"]
        params: list[Any] = [dependency, since_ts]
        if operation:
            filters.append("operation = ?")
            params.append(operation)
        if open_only:
            filters.append("status = 'open'")
        where_sql = " AND ".join(filters)
        with self.connection() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS count FROM dependency_failures WHERE {where_sql}",
                params,
            ).fetchone()
        return int(row["count"]) if row else 0

    def get_dependency_failures(
        self,
        limit: int = 50,
        *,
        dependency: Optional[str] = None,
        open_only: bool = False,
    ) -> list[dict[str, Any]]:
        filters: list[str] = []
        params: list[Any] = []
        if dependency:
            filters.append("dependency = ?")
            params.append(dependency)
        if open_only:
            filters.append("status = 'open'")
        where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT id, dependency, operation, failure_type, severity, message, details_json, failure_ts, failure_time, status, resolved_ts, resolved_time
                FROM dependency_failures
                {where_sql}
                ORDER BY id DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        return [item.to_dict() for item in self._hydrate_dependency_failure_models(rows)]

    def get_dependency_failure_models(
        self,
        limit: int = 50,
        *,
        dependency: Optional[str] = None,
        open_only: bool = False,
    ) -> list[DependencyFailureRow]:
        return [
            DependencyFailureRow.from_mapping(item)
            for item in self.get_dependency_failures(
                limit=limit,
                dependency=dependency,
                open_only=open_only,
            )
        ]

    def record_scan(self, payload: Mapping[str, Any] | CandidateScan) -> None:
        model = (
            payload if isinstance(payload, CandidateScan) else CandidateScan.from_mapping(payload)
        )
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
                model.to_dict(),
            )

    def pending_holder_scan_models(
        self, limit: int = 12, max_age_seconds: int = 420
    ) -> list[CandidateScan]:
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
            return [CandidateScan.from_mapping(dict(row)) for row in rows]

    def pending_holder_scans(
        self, limit: int = 12, max_age_seconds: int = 420
    ) -> list[dict[str, Any]]:
        return [
            item.to_dict()
            for item in self.pending_holder_scan_models(
                limit=limit, max_age_seconds=max_age_seconds
            )
        ]

    def update_scan_holder_metrics(
        self, addr: str, metrics: dict[str, Any], score: float, bundle_risk_score: float
    ) -> None:
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

    def record_decision_audit(self, payload: dict[str, Any]) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO decision_audit
                (decision_ts, decision_time, category, action, outcome, symbol, addr, reason, context_json, snapshot_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(payload.get("decision_ts") or 0),
                    str(payload.get("decision_time") or ""),
                    str(payload.get("category") or ""),
                    str(payload.get("action") or ""),
                    str(payload.get("outcome") or ""),
                    str(payload.get("symbol") or ""),
                    str(payload.get("addr") or ""),
                    str(payload.get("reason") or ""),
                    json.dumps(payload.get("context") or {}),
                    json.dumps(payload.get("snapshot") or {}),
                ),
            )

    def query_news_events(
        self,
        *,
        limit: int = 30,
        offset: int = 0,
        active_only: bool = False,
        source: Optional[str] = None,
        sentiment: Optional[str] = None,
        search: Optional[str] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        now = int(time.time())
        where_parts: list[str] = []
        params: list[Any] = []
        if active_only:
            where_parts.append("active_until_ts >= ?")
            params.append(now)
        if source:
            where_parts.append("LOWER(source) = LOWER(?)")
            params.append(source)
        if sentiment:
            where_parts.append("LOWER(sentiment) = LOWER(?)")
            params.append(sentiment)
        if search:
            term = f"%{search.lower()}%"
            where_parts.append(
                "(LOWER(title) LIKE ? OR LOWER(COALESCE(raw_text, '')) LIKE ? OR LOWER(source) LIKE ?)"
            )
            params.extend([term, term, term])
        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        with self.connection() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS count FROM news_events {where_sql}",
                    tuple(params),
                ).fetchone()["count"]
            )
            rows = conn.execute(
                """
                SELECT event_id, source, title, url, published_ts, fetched_ts, topics_json, entities_json, sentiment, confidence, source_weight, active_until_ts, raw_text
                FROM news_events
                """
                + f" {where_sql} ORDER BY published_ts DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return self._hydrate_news_event_rows(rows, now=now), total

    def get_news_events(self, limit: int = 30, active_only: bool = False) -> list[dict[str, Any]]:
        items, _ = self.query_news_events(limit=limit, active_only=active_only)
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
                (
                    addr,
                    symbol,
                    price,
                    price,
                    amount_usd,
                    entry_liquidity,
                    now,
                    price,
                    entry_tx,
                    mode,
                ),
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
                (
                    addr,
                    symbol,
                    exit_price,
                    sold_amount_usd,
                    pnl_pct,
                    tx_hash,
                    json.dumps(metrics),
                    now,
                ),
            )

    def get_scan_logs(self, limit: int = 50) -> list[dict[str, Any]]:
        items, _ = self.query_scan_logs(limit=limit)
        return items

    def get_scan_log_models(self, limit: int = 50) -> list[CandidateScan]:
        items, _ = self.query_scan_logs(limit=limit)
        return [CandidateScan.from_mapping(item) for item in items]

    def query_scan_logs(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        platform: Optional[str] = None,
        symbol: Optional[str] = None,
        addr: Optional[str] = None,
        search: Optional[str] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        where_parts: list[str] = []
        params: list[Any] = []
        if platform:
            where_parts.append("LOWER(platform) = LOWER(?)")
            params.append(platform)
        if symbol:
            where_parts.append("LOWER(symbol) = LOWER(?)")
            params.append(symbol)
        if addr:
            where_parts.append("addr = ?")
            params.append(addr)
        if search:
            term = f"%{search.lower()}%"
            where_parts.append(
                "(LOWER(symbol) LIKE ? OR LOWER(addr) LIKE ? OR LOWER(COALESCE(platform, '')) LIKE ?)"
            )
            params.extend([term, term, term])
        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        with self.connection() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS count FROM scan_logs {where_sql}",
                    tuple(params),
                ).fetchone()["count"]
            )
            rows = conn.execute(
                """
                SELECT id, scan_time, platform, symbol, age, addr, price, liquidity, dev_buy, progress, ratio, score, first_seen_ts, created_ts, holder_count_estimate, top10_owner_pct, top20_owner_pct, largest_owner_pct, bundle_risk_score, socials, dex_url
                FROM scan_logs
                """
                + f" {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            return self._hydrate_scan_log_rows(rows), total

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
        items, _ = self.query_opportunities(limit=limit)
        return items

    def query_opportunities(
        self,
        *,
        limit: int = 30,
        offset: int = 0,
        opportunity_type: Optional[str] = None,
        symbol: Optional[str] = None,
        addr: Optional[str] = None,
        search: Optional[str] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        where_parts: list[str] = []
        params: list[Any] = []
        if opportunity_type:
            where_parts.append("LOWER(type) = LOWER(?)")
            params.append(opportunity_type)
        if symbol:
            where_parts.append("LOWER(symbol) = LOWER(?)")
            params.append(symbol)
        if addr:
            where_parts.append("addr = ?")
            params.append(addr)
        if search:
            term = f"%{search.lower()}%"
            where_parts.append(
                "(LOWER(symbol) LIKE ? OR LOWER(addr) LIKE ? OR LOWER(reason) LIKE ? OR LOWER(type) LIKE ?)"
            )
            params.extend([term, term, term, term])
        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        with self.connection() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS count FROM opportunities {where_sql}",
                    tuple(params),
                ).fetchone()["count"]
            )
            rows = conn.execute(
                """
                SELECT id, type, symbol, score, reason, addr, liq, vol, dex_url, scan_time, socials, progress
                FROM opportunities
                """
                + f" {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            return self._hydrate_opportunity_rows(rows), total

    def get_opportunities_for_addr(self, addr: str, limit: int = 12) -> list[dict[str, Any]]:
        items, _ = self.query_opportunities(limit=limit, addr=addr)
        return items

    def get_opportunity_by_id(self, opportunity_id: int) -> Optional[dict[str, Any]]:
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT id, type, symbol, score, reason, addr, liq, vol, dex_url, scan_time, socials, progress
                FROM opportunities
                WHERE id = ?
                """,
                (opportunity_id,),
            ).fetchone()
        if not row:
            return None
        return self._hydrate_opportunity_rows([row])[0]

    def get_portfolio(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.get_portfolio_models()]

    def get_portfolio_models(self) -> list[PortfolioPosition]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT addr, symbol, buy_price, current_price, max_price, amount_usd, entry_liquidity, profit_locked_usd, moonbag_active, buy_time, price_source, price_updated_ts, entry_tx, mode
                FROM portfolio
                ORDER BY buy_time DESC
                """
            ).fetchall()
            keys = [
                "addr",
                "symbol",
                "buy_price",
                "current_price",
                "max_price",
                "amount_usd",
                "entry_liquidity",
                "profit_locked_usd",
                "moonbag_active",
                "buy_time",
                "price_source",
                "price_updated_ts",
                "entry_tx",
                "mode",
            ]
            items = [dict(zip(keys, row)) for row in rows]
            now = time.time()
            result: list[PortfolioPosition] = []
            for item in items:
                buy_price = float(item.get("buy_price") or 0)
                current_price = float(item.get("current_price") or 0)
                amount_usd = float(item.get("amount_usd") or 0)
                quantity_estimate = (amount_usd / buy_price) if buy_price > 0 else 0.0
                current_value_usd = quantity_estimate * current_price
                unrealized_pnl_usd = current_value_usd - amount_usd
                unrealized_pnl_pct = (unrealized_pnl_usd / amount_usd) if amount_usd > 0 else 0.0
                result.append(
                    PortfolioPosition.from_mapping(
                        {
                            **item,
                            "quantity_estimate": quantity_estimate,
                            "current_value_usd": current_value_usd,
                            "unrealized_pnl_usd": unrealized_pnl_usd,
                            "unrealized_pnl_pct": unrealized_pnl_pct,
                            "buy_time_age": self._age_from_time_string(item.get("buy_time"), now),
                            "price_update_age": (
                                self._format_duration(
                                    max(int(now) - int(item.get("price_updated_ts") or 0), 0)
                                )
                                if int(item.get("price_updated_ts") or 0) > 0
                                else "-"
                            ),
                        }
                    )
                )
            return result

    def get_portfolio_position(self, addr: str) -> Optional[dict[str, Any]]:
        item = self.get_portfolio_position_model(addr)
        return item.to_dict() if item else None

    def get_portfolio_position_model(self, addr: str) -> Optional[PortfolioPosition]:
        return next((item for item in self.get_portfolio_models() if item.addr == addr), None)

    def _age_from_time_string(self, value: Any, now_ts: float) -> str:
        try:
            parsed = time.strptime(str(value), "%Y-%m-%d %H:%M:%S")
            age_seconds = max(int(now_ts - time.mktime(parsed)), 0)
        except (TypeError, ValueError):
            return "-"
        return self._format_duration(age_seconds)

    def get_trade_history(self, limit: int = 50) -> list[dict[str, Any]]:
        items, _ = self.query_trade_history(limit=limit)
        return items

    def get_trade_history_models(self, limit: int = 50) -> list[TradeHistoryRow]:
        items, _ = self.query_trade_history(limit=limit)
        return [TradeHistoryRow.from_mapping(item) for item in items]

    def query_trade_history(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        side: Optional[str] = None,
        symbol: Optional[str] = None,
        addr: Optional[str] = None,
        search: Optional[str] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        where_parts: list[str] = []
        params: list[Any] = []
        if side:
            where_parts.append("LOWER(side) = LOWER(?)")
            params.append(side)
        if symbol:
            where_parts.append("LOWER(symbol) = LOWER(?)")
            params.append(symbol)
        if addr:
            where_parts.append("addr = ?")
            params.append(addr)
        if search:
            term = f"%{search.lower()}%"
            where_parts.append(
                "(LOWER(symbol) LIKE ? OR LOWER(addr) LIKE ? OR LOWER(side) LIKE ? OR LOWER(COALESCE(tx_hash, '')) LIKE ? OR LOWER(COALESCE(metrics_json, '')) LIKE ?)"
            )
            params.extend([term, term, term, term, term])
        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        with self.connection() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS count FROM trade_history {where_sql}",
                    tuple(params),
                ).fetchone()["count"]
            )
            rows = conn.execute(
                """
                SELECT id, addr, symbol, side, price, amount_usd, pnl_pct, tx_hash, metrics_json, time
                FROM trade_history
                """
                + f" {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            return self._hydrate_trade_history_rows(rows), total

    def get_trade_history_for_addr(self, addr: str, limit: int = 20) -> list[dict[str, Any]]:
        items, _ = self.query_trade_history(limit=limit, addr=addr)
        return items

    def get_trade_history_models_for_addr(
        self, addr: str, limit: int = 20
    ) -> list[TradeHistoryRow]:
        items, _ = self.query_trade_history(limit=limit, addr=addr)
        return [TradeHistoryRow.from_mapping(item) for item in items]

    def get_trade_history_row(self, trade_history_id: int) -> Optional[dict[str, Any]]:
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT id, addr, symbol, side, price, amount_usd, pnl_pct, tx_hash, metrics_json, time
                FROM trade_history
                WHERE id = ?
                """,
                (trade_history_id,),
            ).fetchone()
        if not row:
            return None
        return self._hydrate_trade_history_rows([row])[0]

    def find_trade_history_id(
        self,
        *,
        addr: str,
        side: str,
        tx_hash: Any = None,
    ) -> Optional[int]:
        with self.connection() as conn:
            if tx_hash:
                row = conn.execute(
                    """
                    SELECT id
                    FROM trade_history
                    WHERE addr = ? AND side = ? AND tx_hash = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (addr, side, tx_hash),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT id
                    FROM trade_history
                    WHERE addr = ? AND side = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (addr, side),
                ).fetchone()
        return int(row["id"]) if row else None

    def get_decision_audit(
        self,
        limit: int = 100,
        *,
        offset: int = 0,
        category: Optional[str] = None,
        action: Optional[str] = None,
        outcome: Optional[str] = None,
        symbol: Optional[str] = None,
        addr: Optional[str] = None,
        search: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        rows, _ = self.query_decision_audit(
            limit=limit,
            offset=offset,
            category=category,
            action=action,
            outcome=outcome,
            symbol=symbol,
            addr=addr,
            search=search,
        )
        return rows

    def query_decision_audit(
        self,
        limit: int = 100,
        *,
        offset: int = 0,
        category: Optional[str] = None,
        action: Optional[str] = None,
        outcome: Optional[str] = None,
        symbol: Optional[str] = None,
        addr: Optional[str] = None,
        search: Optional[str] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        filters, params = self._decision_audit_filters(
            category=category,
            action=action,
            outcome=outcome,
            symbol=symbol,
            addr=addr,
            search=search,
        )
        where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
        with self.connection() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM decision_audit {where_sql}",
                    params,
                ).fetchone()[0]
            )
            rows = conn.execute(
                f"""
                SELECT id, decision_ts, decision_time, category, action, outcome, symbol, addr, reason, context_json, snapshot_json
                FROM decision_audit
                {where_sql}
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            ).fetchall()
        return self._hydrate_decision_audit_rows(rows), total

    def get_decision_audit_by_id(self, decision_id: int) -> Optional[dict[str, Any]]:
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT id, decision_ts, decision_time, category, action, outcome, symbol, addr, reason, context_json, snapshot_json
                FROM decision_audit
                WHERE id = ?
                """,
                (decision_id,),
            ).fetchone()
        if not row:
            return None
        items = self._hydrate_decision_audit_rows([row])
        return items[0] if items else None

    def get_decision_audit_for_addr(self, addr: str, limit: int = 20) -> list[dict[str, Any]]:
        rows, _ = self.query_decision_audit(limit=limit, addr=addr)
        return rows

    def _decision_audit_filters(
        self,
        *,
        category: Optional[str] = None,
        action: Optional[str] = None,
        outcome: Optional[str] = None,
        symbol: Optional[str] = None,
        addr: Optional[str] = None,
        search: Optional[str] = None,
    ) -> tuple[list[str], list[Any]]:
        filters: list[str] = []
        params: list[Any] = []
        if category:
            filters.append("category = ?")
            params.append(category)
        if action:
            filters.append("action = ?")
            params.append(action)
        if outcome:
            filters.append("outcome = ?")
            params.append(outcome)
        if symbol:
            filters.append("UPPER(symbol) = UPPER(?)")
            params.append(symbol)
        if addr:
            filters.append("addr = ?")
            params.append(addr)
        if search:
            like_value = f"%{search.lower()}%"
            filters.append("(LOWER(symbol) LIKE ? OR LOWER(addr) LIKE ? OR LOWER(reason) LIKE ?)")
            params.extend([like_value, like_value, like_value])
        return filters, params

    def _hydrate_decision_audit_rows(self, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            raw_context = item.pop("context_json", "{}") or "{}"
            try:
                item["context"] = json.loads(raw_context)
            except json.JSONDecodeError:
                item["context"] = {}
            raw_snapshot = item.pop("snapshot_json", "{}") or "{}"
            try:
                item["snapshot"] = json.loads(raw_snapshot)
            except json.JSONDecodeError:
                item["snapshot"] = {}
            items.append(item)
        return items

    def _hydrate_trade_history_rows(self, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._hydrate_trade_history_models(rows)]

    def _hydrate_trade_history_models(self, rows: list[sqlite3.Row]) -> list[TradeHistoryRow]:
        keys = [
            "id",
            "addr",
            "symbol",
            "side",
            "price",
            "amount_usd",
            "pnl_pct",
            "tx_hash",
            "metrics_json",
            "time",
        ]
        items: list[TradeHistoryRow] = []
        for row in rows:
            item = dict(zip(keys, row))
            raw_metrics = item.pop("metrics_json", "{}") or "{}"
            try:
                item["metrics"] = json.loads(raw_metrics)
            except json.JSONDecodeError:
                item["metrics"] = {}
            items.append(TradeHistoryRow.from_mapping(item))
        return items

    def _hydrate_news_event_rows(
        self, rows: list[sqlite3.Row], *, now: Optional[int] = None
    ) -> list[dict[str, Any]]:
        current_ts = int(now if now is not None else time.time())
        items = [dict(row) for row in rows]
        for item in items:
            item["topics"] = self._loads_json_list(item.pop("topics_json", "[]"))
            item["entities"] = self._loads_json_list(item.pop("entities_json", "[]"))
            item["age"] = self._format_duration(
                max(current_ts - int(item.get("published_ts") or 0), 0)
            )
            item["expires_in"] = self._format_duration(
                max(int(item.get("active_until_ts") or 0) - current_ts, 0)
            )
            item["published_time"] = self._format_clock(int(item.get("published_ts") or 0))
        return items

    def _hydrate_scan_log_rows(self, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        now_ts = int(time.time())
        for row in rows:
            item = dict(row)
            first_seen_ts = int(item.get("first_seen_ts") or 0)
            created_ts = int(item.get("created_ts") or 0)
            item["volume_5m"] = item.pop("dev_buy")
            item["monitor_time"] = self._format_clock(first_seen_ts)
            item["token_age"] = (
                self._format_duration(max(now_ts - created_ts, 0)) if created_ts > 0 else "-"
            )
            item["scan_time"] = item["monitor_time"]
            item["age"] = item["token_age"]
            holder_count = float(item.get("holder_count_estimate") or 0)
            item["bundle_risk_label"] = (
                "待查"
                if holder_count <= 0
                else self._bundle_risk_label(float(item.get("bundle_risk_score") or 0))
            )
            result.append(item)
        return result

    def _hydrate_opportunity_rows(self, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        return [dict(row) for row in rows]

    def _hydrate_order_idempotency_row(self, row: Optional[sqlite3.Row]) -> dict[str, Any]:
        if not row:
            return {}
        item = dict(row)
        try:
            item["request"] = json.loads(item.pop("request_json", "{}") or "{}")
        except json.JSONDecodeError:
            item["request"] = {}
        try:
            item["result"] = json.loads(item.pop("result_json", "{}") or "{}")
        except json.JSONDecodeError:
            item["result"] = {}
        return item

    def _hydrate_manual_trade_audit_rows(self, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["request"] = json.loads(item.pop("request_json", "{}") or "{}")
            except json.JSONDecodeError:
                item["request"] = {}
            try:
                item["response"] = json.loads(item.pop("response_json", "{}") or "{}")
            except json.JSONDecodeError:
                item["response"] = {}
            items.append(item)
        return items

    def _hydrate_dependency_failure_rows(self, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._hydrate_dependency_failure_models(rows)]

    def _hydrate_dependency_failure_models(
        self, rows: list[sqlite3.Row]
    ) -> list[DependencyFailureRow]:
        items: list[DependencyFailureRow] = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.pop("details_json", "{}") or "{}")
            except json.JSONDecodeError:
                item["details"] = {}
            items.append(DependencyFailureRow.from_mapping(item))
        return items

    def recent_sell_trades(self, limit: int = 40) -> list[dict[str, Any]]:
        return [row.to_dict() for row in self.recent_sell_trade_models(limit=limit)]

    def recent_sell_trade_models(self, limit: int = 40) -> list[TradeHistoryRow]:
        return [
            row
            for row in self.get_trade_history_models(limit=limit * 3)
            if row.side == "sell" and row.pnl_pct is not None
        ][:limit]

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

    def save_strategy_state(self, state: Any) -> None:
        if hasattr(state, "to_dict"):
            state = state.to_dict()
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
        initial_balance = float(
            wallet.get("initial_bal", self.settings.initial_balance_usd)
            or self.settings.initial_balance_usd
        )
        current_balance = float(wallet.get("current_balance", initial_balance) or initial_balance)
        portfolio = self.get_portfolio()
        unrealized_value_usd = sum(
            float(item.get("current_value_usd") or 0.0) for item in portfolio
        )
        unrealized_cost_usd = sum(float(item.get("amount_usd") or 0.0) for item in portfolio)
        unrealized_profit_usd = sum(
            float(item.get("unrealized_pnl_usd") or 0.0) for item in portfolio
        )
        unrealized_profit_pct = (
            (unrealized_profit_usd / unrealized_cost_usd) if unrealized_cost_usd > 0 else 0.0
        )
        total_equity_usd = current_balance + unrealized_value_usd
        total_profit_usd = total_equity_usd - initial_balance
        total_profit_pct = (total_profit_usd / initial_balance) if initial_balance else 0.0
        realized_rows = self.get_trade_history(limit=500)
        sells = [
            row for row in realized_rows if row["side"] == "sell" and row.get("pnl_pct") is not None
        ]
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
        best_exit_reason = (
            max(exit_reason_counts, key=exit_reason_counts.get) if exit_reason_counts else "-"
        )

        return {
            "initial_balance": initial_balance,
            "current_balance": current_balance,
            "cash_balance_usd": current_balance,
            "realized_profit_usd": current_balance - initial_balance,
            "realized_profit_pct": ((current_balance - initial_balance) / initial_balance)
            if initial_balance
            else 0.0,
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
            "profit_factor": (sum(wins) / abs(sum(losses)))
            if losses and abs(sum(losses)) > 0
            else (999.0 if wins else 0.0),
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

    def open_position_models(self) -> list[PortfolioPosition]:
        return self.get_portfolio_models()

    def open_position_count(self) -> int:
        with self.connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM portfolio").fetchone()
            return int(row["count"]) if row else 0

    def backup_to(self, destination_path: Any) -> Path:
        return backup_sqlite_database(self.path, destination_path)

    def restore_from(self, source_path: Any) -> Path:
        return restore_sqlite_database(source_path, self.path)
