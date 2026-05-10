from __future__ import annotations

import time
from typing import Any, Callable, Mapping, Optional, Protocol


class DecisionAuditStore(Protocol):
    def record_decision_audit(self, payload: dict[str, Any]) -> None:
        ...


class DecisionAuditService:
    def __init__(
        self,
        database: DecisionAuditStore,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.database = database
        self.clock = clock

    def record_entry_blocked(
        self,
        scan: Mapping[str, Any],
        reason: str,
        *,
        strategy_mode: str,
        wallet: Mapping[str, Any],
        position_size_usd: Optional[float] = None,
    ) -> None:
        self._record(
            category="entry",
            action="open_position",
            outcome="blocked",
            symbol=str(scan.get("symbol") or "UNK"),
            addr=str(scan.get("addr") or ""),
            reason=reason,
            context={
                "strategy_mode": strategy_mode,
                "score": float(scan.get("score") or 0.0),
                "liquidity_usd": float(scan.get("liquidity") or 0.0),
                "dev_buy_sol": float(scan.get("dev_buy") or 0.0),
                "wallet_balance_usd": float(wallet.get("current_balance") or 0.0),
                "position_size_usd": position_size_usd,
            },
            snapshot=self._entry_snapshot(scan, wallet),
        )

    def record_entry_execution_skipped(
        self,
        scan: Mapping[str, Any],
        reason: str,
        *,
        strategy_mode: str,
        position_size_usd: float,
        execution_mode: str,
        wallet: Mapping[str, Any],
    ) -> None:
        self._record(
            category="entry",
            action="open_position",
            outcome="skipped",
            symbol=str(scan.get("symbol") or "UNK"),
            addr=str(scan.get("addr") or ""),
            reason=reason,
            context={
                "strategy_mode": strategy_mode,
                "execution_mode": execution_mode,
                "position_size_usd": position_size_usd,
                "score": float(scan.get("score") or 0.0),
                "liquidity_usd": float(scan.get("liquidity") or 0.0),
                "dev_buy_sol": float(scan.get("dev_buy") or 0.0),
            },
            snapshot=self._entry_snapshot(scan, wallet),
        )

    def record_entry_opened(
        self,
        scan: Mapping[str, Any],
        *,
        strategy_mode: str,
        position_size_usd: float,
        execution_mode: str,
        tx_hash: Optional[str],
        effective_buy_price: float,
        entry_reason: str,
        wallet: Mapping[str, Any],
    ) -> None:
        self._record(
            category="entry",
            action="open_position",
            outcome="executed",
            symbol=str(scan.get("symbol") or "UNK"),
            addr=str(scan.get("addr") or ""),
            reason="position_opened",
            context={
                "strategy_mode": strategy_mode,
                "execution_mode": execution_mode,
                "position_size_usd": position_size_usd,
                "effective_buy_price": effective_buy_price,
                "market_price": float(scan.get("price") or 0.0),
                "tx_hash": tx_hash,
                "entry_reason": entry_reason,
                "score": float(scan.get("score") or 0.0),
                "liquidity_usd": float(scan.get("liquidity") or 0.0),
                "dev_buy_sol": float(scan.get("dev_buy") or 0.0),
                "scalp_override": bool(scan.get("scalp_override")),
                "scalp_reason": str(scan.get("scalp_reason") or ""),
                "narrative_label": str(scan.get("narrative_label") or "-"),
            },
            snapshot=self._entry_snapshot(scan, wallet),
        )

    def record_exit_execution_skipped(
        self,
        position: Mapping[str, Any],
        decision: Any,
        reason: str,
        *,
        execution_mode: str,
        market_snapshot: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self._record(
            category="exit",
            action="close_position",
            outcome="skipped",
            symbol=str(position.get("symbol") or "UNK"),
            addr=str(position.get("addr") or ""),
            reason=str(getattr(decision, "exit_reason", None) or reason),
            context={
                "execution_mode": execution_mode,
                "execution_reason": reason,
                **self._exit_context(decision),
            },
            snapshot=self._exit_snapshot(position, decision, market_snapshot),
        )

    def record_exit_executed(
        self,
        position: Mapping[str, Any],
        decision: Any,
        *,
        execution_mode: str,
        tx_hash: Optional[str],
        market_snapshot: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self._record(
            category="exit",
            action="close_position",
            outcome="executed",
            symbol=str(position.get("symbol") or "UNK"),
            addr=str(position.get("addr") or ""),
            reason=str(getattr(decision, "exit_reason", None) or "exit_executed"),
            context={
                "execution_mode": execution_mode,
                "tx_hash": tx_hash,
                **self._exit_context(decision),
            },
            snapshot=self._exit_snapshot(position, decision, market_snapshot),
        )

    def _exit_context(self, decision: Any) -> dict[str, Any]:
        exit_context = getattr(decision, "exit_context", {}) or {}
        if not isinstance(exit_context, dict):
            exit_context = {}
        return {
            "hold_seconds": int(getattr(decision, "hold_seconds", 0) or 0),
            "pnl_pct": float(getattr(decision, "pnl_pct", 0.0) or 0.0),
            "effective_exit_price": float(getattr(decision, "sell_price", 0.0) or 0.0),
            "market_price": float(getattr(decision, "current_price", 0.0) or 0.0),
            "sell_fraction": float(getattr(decision, "sell_fraction", 0.0) or 0.0),
            "sell_amount_usd": float(getattr(decision, "sell_amount_usd", 0.0) or 0.0),
            "remaining_amount_usd": float(getattr(decision, "remaining_amount_usd", 0.0) or 0.0),
            **exit_context,
        }

    def _record(
        self,
        *,
        category: str,
        action: str,
        outcome: str,
        symbol: str,
        addr: str,
        reason: str,
        context: dict[str, Any],
        snapshot: Optional[dict[str, Any]] = None,
    ) -> None:
        decision_ts = int(self.clock())
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
                "context": context,
                "snapshot": snapshot or {},
            }
        )

    def _entry_snapshot(
        self,
        scan: Mapping[str, Any],
        wallet: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "kind": "entry",
            "candidate": self._clean_value(scan),
            "wallet": self._clean_value(wallet),
        }

    def _exit_snapshot(
        self,
        position: Mapping[str, Any],
        decision: Any,
        market_snapshot: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        return {
            "kind": "exit",
            "position": self._clean_value(position),
            "market_snapshot": self._clean_value(market_snapshot or {}),
            "decision": self._clean_value(
                {
                    "current_price": getattr(decision, "current_price", 0.0),
                    "sell_price": getattr(decision, "sell_price", 0.0),
                    "max_price": getattr(decision, "max_price", 0.0),
                    "pnl_pct": getattr(decision, "pnl_pct", 0.0),
                    "hold_seconds": getattr(decision, "hold_seconds", 0),
                    "exit_reason": getattr(decision, "exit_reason", None),
                    "exit_context": getattr(decision, "exit_context", {}) or {},
                    "sell_fraction": getattr(decision, "sell_fraction", 0.0),
                    "sell_amount_usd": getattr(decision, "sell_amount_usd", 0.0),
                    "remaining_amount_usd": getattr(decision, "remaining_amount_usd", 0.0),
                }
            ),
        }

    def _clean_value(self, value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, Mapping):
            return {str(key): self._clean_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._clean_value(item) for item in value]
        return str(value)
