from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _safe_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _safe_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return []


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str


@dataclass(frozen=True)
class CandidateScan:
    scan_time: str
    platform: str
    symbol: str
    age: str
    addr: str
    price: float
    liquidity: float
    dev_buy: float
    progress: str
    ratio: str
    score: float
    first_seen_ts: int = 0
    created_ts: int = 0
    holder_count_estimate: float = 0.0
    top10_owner_pct: float = 0.0
    top20_owner_pct: float = 0.0
    largest_owner_pct: float = 0.0
    bundle_risk_score: float = 0.0
    socials: Any = None
    dex_url: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "CandidateScan":
        payload = dict(raw or {})
        known_keys = {
            "scan_time",
            "platform",
            "symbol",
            "age",
            "addr",
            "price",
            "liquidity",
            "dev_buy",
            "progress",
            "ratio",
            "score",
            "first_seen_ts",
            "created_ts",
            "holder_count_estimate",
            "top10_owner_pct",
            "top20_owner_pct",
            "largest_owner_pct",
            "bundle_risk_score",
            "socials",
            "dex_url",
        }
        return cls(
            scan_time=_safe_str(payload.get("scan_time")),
            platform=_safe_str(payload.get("platform")),
            symbol=_safe_str(payload.get("symbol"), "UNKNOWN"),
            age=_safe_str(payload.get("age")),
            addr=_safe_str(payload.get("addr")),
            price=_safe_float(payload.get("price"), 0.0),
            liquidity=_safe_float(payload.get("liquidity"), 0.0),
            dev_buy=_safe_float(payload.get("dev_buy"), 0.0),
            progress=_safe_str(payload.get("progress")),
            ratio=_safe_str(payload.get("ratio")),
            score=_safe_float(payload.get("score"), 0.0),
            first_seen_ts=_safe_int(payload.get("first_seen_ts"), 0),
            created_ts=_safe_int(payload.get("created_ts"), 0),
            holder_count_estimate=_safe_float(payload.get("holder_count_estimate"), 0.0),
            top10_owner_pct=_safe_float(payload.get("top10_owner_pct"), 0.0),
            top20_owner_pct=_safe_float(payload.get("top20_owner_pct"), 0.0),
            largest_owner_pct=_safe_float(payload.get("largest_owner_pct"), 0.0),
            bundle_risk_score=_safe_float(payload.get("bundle_risk_score"), 0.0),
            socials=payload.get("socials"),
            dex_url=_safe_str(payload.get("dex_url")),
            extras={key: value for key, value in payload.items() if key not in known_keys},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_time": self.scan_time,
            "platform": self.platform,
            "symbol": self.symbol,
            "age": self.age,
            "addr": self.addr,
            "price": self.price,
            "liquidity": self.liquidity,
            "dev_buy": self.dev_buy,
            "progress": self.progress,
            "ratio": self.ratio,
            "score": self.score,
            "first_seen_ts": self.first_seen_ts,
            "created_ts": self.created_ts,
            "holder_count_estimate": self.holder_count_estimate,
            "top10_owner_pct": self.top10_owner_pct,
            "top20_owner_pct": self.top20_owner_pct,
            "largest_owner_pct": self.largest_owner_pct,
            "bundle_risk_score": self.bundle_risk_score,
            "socials": self.socials,
            "dex_url": self.dex_url,
            **self.extras,
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)


@dataclass(frozen=True)
class PortfolioPosition:
    addr: str
    symbol: str
    buy_price: float
    current_price: float
    max_price: float
    amount_usd: float
    entry_liquidity: float = 0.0
    profit_locked_usd: float = 0.0
    moonbag_active: bool = False
    buy_time: str = ""
    price_source: str = "initial"
    price_updated_ts: int = 0
    entry_tx: str = ""
    mode: str = "paper"
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "PortfolioPosition":
        payload = dict(raw or {})
        known_keys = {
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
        }
        return cls(
            addr=_safe_str(payload.get("addr")),
            symbol=_safe_str(payload.get("symbol"), "UNKNOWN"),
            buy_price=_safe_float(payload.get("buy_price"), 0.0),
            current_price=_safe_float(payload.get("current_price"), 0.0),
            max_price=_safe_float(payload.get("max_price"), 0.0),
            amount_usd=_safe_float(payload.get("amount_usd"), 0.0),
            entry_liquidity=_safe_float(payload.get("entry_liquidity"), 0.0),
            profit_locked_usd=_safe_float(payload.get("profit_locked_usd"), 0.0),
            moonbag_active=bool(payload.get("moonbag_active")),
            buy_time=_safe_str(payload.get("buy_time")),
            price_source=_safe_str(payload.get("price_source"), "initial"),
            price_updated_ts=_safe_int(payload.get("price_updated_ts"), 0),
            entry_tx=_safe_str(payload.get("entry_tx")),
            mode=_safe_str(payload.get("mode"), "paper"),
            extras={key: value for key, value in payload.items() if key not in known_keys},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "addr": self.addr,
            "symbol": self.symbol,
            "buy_price": self.buy_price,
            "current_price": self.current_price,
            "max_price": self.max_price,
            "amount_usd": self.amount_usd,
            "entry_liquidity": self.entry_liquidity,
            "profit_locked_usd": self.profit_locked_usd,
            "moonbag_active": self.moonbag_active,
            "buy_time": self.buy_time,
            "price_source": self.price_source,
            "price_updated_ts": self.price_updated_ts,
            "entry_tx": self.entry_tx,
            "mode": self.mode,
            **self.extras,
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)


@dataclass(frozen=True)
class TradeHistoryRow:
    id: int
    addr: str
    symbol: str
    side: str
    price: float
    amount_usd: float
    pnl_pct: float | None
    tx_hash: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    time: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "TradeHistoryRow":
        payload = dict(raw or {})
        known_keys = {
            "id",
            "addr",
            "symbol",
            "side",
            "price",
            "amount_usd",
            "pnl_pct",
            "tx_hash",
            "metrics",
            "time",
        }
        pnl_value = payload.get("pnl_pct")
        return cls(
            id=_safe_int(payload.get("id"), 0),
            addr=_safe_str(payload.get("addr")),
            symbol=_safe_str(payload.get("symbol"), "UNKNOWN"),
            side=_safe_str(payload.get("side")),
            price=_safe_float(payload.get("price"), 0.0),
            amount_usd=_safe_float(payload.get("amount_usd"), 0.0),
            pnl_pct=None if pnl_value is None else _safe_float(pnl_value, 0.0),
            tx_hash=_safe_str(payload.get("tx_hash")),
            metrics=_safe_dict(payload.get("metrics")),
            time=_safe_str(payload.get("time")),
            extras={key: value for key, value in payload.items() if key not in known_keys},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "addr": self.addr,
            "symbol": self.symbol,
            "side": self.side,
            "price": self.price,
            "amount_usd": self.amount_usd,
            "pnl_pct": self.pnl_pct,
            "tx_hash": self.tx_hash,
            "metrics": dict(self.metrics),
            "time": self.time,
            **self.extras,
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)


@dataclass(frozen=True)
class DependencyFailureRow:
    id: int
    dependency: str
    operation: str
    failure_type: str
    severity: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    failure_ts: int = 0
    failure_time: str = ""
    status: str = "open"
    resolved_ts: int = 0
    resolved_time: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "DependencyFailureRow":
        payload = dict(raw or {})
        known_keys = {
            "id",
            "dependency",
            "operation",
            "failure_type",
            "severity",
            "message",
            "details",
            "failure_ts",
            "failure_time",
            "status",
            "resolved_ts",
            "resolved_time",
        }
        return cls(
            id=_safe_int(payload.get("id"), 0),
            dependency=_safe_str(payload.get("dependency")),
            operation=_safe_str(payload.get("operation")),
            failure_type=_safe_str(payload.get("failure_type")),
            severity=_safe_str(payload.get("severity"), "error"),
            message=_safe_str(payload.get("message")),
            details=_safe_dict(payload.get("details")),
            failure_ts=_safe_int(payload.get("failure_ts"), 0),
            failure_time=_safe_str(payload.get("failure_time")),
            status=_safe_str(payload.get("status"), "open"),
            resolved_ts=_safe_int(payload.get("resolved_ts"), 0),
            resolved_time=_safe_str(payload.get("resolved_time")),
            extras={key: value for key, value in payload.items() if key not in known_keys},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "dependency": self.dependency,
            "operation": self.operation,
            "failure_type": self.failure_type,
            "severity": self.severity,
            "message": self.message,
            "details": dict(self.details),
            "failure_ts": self.failure_ts,
            "failure_time": self.failure_time,
            "status": self.status,
            "resolved_ts": self.resolved_ts,
            "resolved_time": self.resolved_time,
            **self.extras,
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)


@dataclass(frozen=True)
class StrategyState:
    mode: str = "balanced"
    score_offset: float = 0.0
    position_multiplier: float = 1.0
    cooldown_multiplier: float = 1.0
    max_open_positions: int = 1
    instant_probe_enabled: bool = True
    reason: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    updated_ts: int = 0
    cooldown_until_ts: int = 0

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any] | None,
        *,
        default_max_open_positions: int,
        reason: str = "",
    ) -> "StrategyState":
        if not raw:
            return cls(
                max_open_positions=max(default_max_open_positions, 1),
                reason=reason,
            )
        metrics = raw.get("metrics") or {}
        if not isinstance(metrics, dict):
            metrics = {}
        return cls(
            mode=str(raw.get("mode") or "balanced"),
            score_offset=_safe_float(raw.get("score_offset"), 0.0),
            position_multiplier=max(_safe_float(raw.get("position_multiplier"), 1.0), 0.1),
            cooldown_multiplier=max(_safe_float(raw.get("cooldown_multiplier"), 1.0), 0.1),
            max_open_positions=max(
                _safe_int(raw.get("max_open_positions"), default_max_open_positions), 1
            ),
            instant_probe_enabled=bool(raw.get("instant_probe_enabled", True)),
            reason=str(raw.get("reason") or reason),
            metrics=dict(metrics),
            updated_ts=_safe_int(raw.get("updated_ts"), 0),
            cooldown_until_ts=_safe_int(raw.get("cooldown_until_ts"), 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "score_offset": self.score_offset,
            "position_multiplier": self.position_multiplier,
            "cooldown_multiplier": self.cooldown_multiplier,
            "max_open_positions": self.max_open_positions,
            "instant_probe_enabled": self.instant_probe_enabled,
            "reason": self.reason,
            "metrics": dict(self.metrics),
            "updated_ts": self.updated_ts,
            "cooldown_until_ts": self.cooldown_until_ts,
        }

    def is_cooldown_active(self, now_ts: int) -> bool:
        return self.cooldown_until_ts > now_ts
