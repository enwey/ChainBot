from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .settings import Settings


class RuntimeSafetyShutdown(RuntimeError):
    """Raised when runtime safety guardrails force a trading shutdown."""


@dataclass(frozen=True)
class DependencyFailureRecord:
    component: str
    dependency: str
    operation: str
    message: str
    occurred_ts: int
    failure_class: str = ""
    retryable: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def counter_key(self) -> tuple[str, str, str]:
        return (self.component, self.dependency, self.operation)

    def to_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "dependency": self.dependency,
            "operation": self.operation,
            "message": self.message,
            "occurred_ts": self.occurred_ts,
            "failure_class": self.failure_class,
            "retryable": self.retryable,
            "metadata": dict(self.metadata),
        }


class RuntimeSafetyManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.state_path = settings.resolved_data_dir / "runtime_safety.json"
        self._consecutive_failures: dict[tuple[str, str, str], int] = {}
        self._last_failure_ts: dict[tuple[str, str, str], int] = {}
        self._recent_dependency_failures: list[DependencyFailureRecord] = []
        self._claimed_order_keys: dict[str, dict[str, Any]] = {}
        self._shutdown: Optional[dict[str, Any]] = None
        self._abnormal_exit_cooldown_until_ts = 0
        self._abnormal_exit_count = 0
        self._last_heartbeat_persist_ts = 0

        self.settings.resolved_data_dir.mkdir(parents=True, exist_ok=True)
        self._boot()

    def _boot(self) -> None:
        now = int(time.time())
        previous = self._read_state()
        abnormal_exit_count = int(previous.get("abnormal_exit_count") or 0)
        if (
            previous.get("session_state") == "running"
            and self.settings.abnormal_exit_cooldown_seconds > 0
        ):
            abnormal_exit_count += 1
            if abnormal_exit_count >= self.settings.abnormal_exit_threshold:
                self._abnormal_exit_cooldown_until_ts = (
                    now + self.settings.abnormal_exit_cooldown_seconds
                )
        self._abnormal_exit_count = abnormal_exit_count
        self._write_state(
            {
                "session_state": "running",
                "clean_shutdown": False,
                "last_started_ts": now,
                "last_heartbeat_ts": now,
                "last_stopped_ts": int(previous.get("last_stopped_ts") or 0),
                "shutdown": previous.get("shutdown") or {},
                "abnormal_exit_count": abnormal_exit_count,
            }
        )
        self._last_heartbeat_persist_ts = now

    def heartbeat(self, *, now_ts: int | None = None) -> None:
        now = int(now_ts or time.time())
        if now - self._last_heartbeat_persist_ts < 5:
            return
        state = self._read_state()
        state["last_heartbeat_ts"] = now
        self._write_state(state)

    def record_exit_result(self, *, abnormal: bool, reason: str, now_ts: int | None = None) -> int:
        now = int(now_ts or time.time())
        if abnormal:
            self._abnormal_exit_count += 1
            if self._abnormal_exit_count >= self.settings.abnormal_exit_threshold:
                self._abnormal_exit_cooldown_until_ts = max(
                    self._abnormal_exit_cooldown_until_ts,
                    now + self.settings.abnormal_exit_cooldown_seconds,
                )
        else:
            self._abnormal_exit_count = 0
        state = self._read_state()
        state["abnormal_exit_count"] = self._abnormal_exit_count
        state["last_exit_reason"] = reason
        state["last_heartbeat_ts"] = now
        self._write_state(state)
        return self._abnormal_exit_count
        self._last_heartbeat_persist_ts = now

    def note_clean_shutdown(self, *, now_ts: int | None = None) -> None:
        now = int(now_ts or time.time())
        state = self._read_state()
        state.update(
            {
                "session_state": "stopped",
                "clean_shutdown": True,
                "last_stopped_ts": now,
                "last_heartbeat_ts": now,
                "abnormal_exit_count": 0,
            }
        )
        self._write_state(state)

    def claim_order_key(
        self,
        key: str,
        *,
        side: str,
        token_mint: str,
        amount_usd: float,
        now_ts: float | None = None,
    ) -> bool:
        now = float(now_ts or time.time())
        self._prune_order_keys(now)
        current = self._claimed_order_keys.get(key)
        if current and float(current.get("expires_at") or 0.0) > now:
            return False
        self._claimed_order_keys[key] = {
            "side": side,
            "token_mint": token_mint,
            "amount_usd": float(amount_usd),
            "expires_at": now + max(self.settings.order_idempotency_ttl_seconds, 1),
        }
        return True

    def record_dependency_failure(self, record: DependencyFailureRecord) -> int:
        key = record.counter_key()
        window_seconds = max(int(self.settings.dependency_failure_window_seconds), 1)
        previous_ts = int(self._last_failure_ts.get(key) or 0)
        if previous_ts and record.occurred_ts - previous_ts > window_seconds:
            count = 1
        else:
            count = self._consecutive_failures.get(key, 0) + 1
        self._consecutive_failures[key] = count
        self._last_failure_ts[key] = record.occurred_ts
        self._recent_dependency_failures.insert(0, record)
        del self._recent_dependency_failures[20:]

        threshold = self.settings.max_consecutive_dependency_failures
        if threshold > 0 and count >= threshold and self._shutdown is None:
            self.trigger_shutdown(
                source="dependency_failure",
                reason=(
                    f"{record.component}:{record.dependency}:{record.operation} "
                    f"failed {count} consecutive times"
                ),
                metadata={
                    "component": record.component,
                    "dependency": record.dependency,
                    "operation": record.operation,
                    "count": count,
                },
                now_ts=record.occurred_ts,
            )
        return count

    def record_dependency_success(self, component: str, dependency: str, operation: str) -> None:
        key = (component, dependency, operation)
        self._consecutive_failures.pop(key, None)
        self._last_failure_ts.pop(key, None)

    def daily_loss_status(
        self,
        sells: list[dict[str, Any]],
        *,
        now_ts: int | None = None,
    ) -> dict[str, Any]:
        now = int(now_ts or time.time())
        today = time.strftime("%Y-%m-%d", time.localtime(now))
        realized_pnl_usd = 0.0
        sell_count = 0
        for sell in sells:
            if not str(sell.get("time") or "").startswith(today):
                continue
            amount_usd = float(sell.get("amount_usd") or 0.0)
            pnl_pct = float(sell.get("pnl_pct") or 0.0)
            realized_pnl_usd += amount_usd * pnl_pct
            sell_count += 1
        max_daily_loss_usd = max(float(self.settings.max_daily_loss_usd), 0.0)
        return {
            "date": today,
            "sell_count": sell_count,
            "realized_pnl_usd": round(realized_pnl_usd, 4),
            "max_daily_loss_usd": max_daily_loss_usd,
            "breached": max_daily_loss_usd > 0 and realized_pnl_usd <= -max_daily_loss_usd,
        }

    def enforce_daily_loss_limit(
        self,
        sells: list[dict[str, Any]],
        *,
        now_ts: int | None = None,
    ) -> dict[str, Any]:
        status = self.daily_loss_status(sells, now_ts=now_ts)
        if status["breached"] and self._shutdown is None:
            self.trigger_shutdown(
                source="daily_loss",
                reason=(
                    f"daily realized pnl {status['realized_pnl_usd']:.2f} "
                    f"breached loss limit -{status['max_daily_loss_usd']:.2f}"
                ),
                metadata=status,
                now_ts=now_ts,
            )
        return status

    def can_open_new_positions(self, *, now_ts: int | None = None) -> tuple[bool, str]:
        now = int(now_ts or time.time())
        if self._shutdown is not None:
            return False, f"runtime safety shutdown active: {self._shutdown['reason']}"
        if now < self._abnormal_exit_cooldown_until_ts:
            remaining = self._abnormal_exit_cooldown_until_ts - now
            return False, f"startup cooldown active after abnormal exit ({remaining}s remaining)"
        return True, "ok"

    def shutdown_reason(self) -> str:
        return str((self._shutdown or {}).get("reason") or "")

    def ensure_runtime_active(self) -> None:
        if self._shutdown is None:
            return
        raise RuntimeSafetyShutdown(self.shutdown_reason())

    def trigger_shutdown(
        self,
        *,
        source: str,
        reason: str,
        metadata: Optional[dict[str, Any]] = None,
        now_ts: int | None = None,
    ) -> None:
        now = int(now_ts or time.time())
        self._shutdown = {
            "source": source,
            "reason": reason,
            "triggered_ts": now,
            "metadata": dict(metadata or {}),
        }
        state = self._read_state()
        state["shutdown"] = self._shutdown
        state["last_heartbeat_ts"] = now
        self._write_state(state)

    def runtime_status(self) -> dict[str, Any]:
        now = int(time.time())
        return {
            "shutdown": dict(self._shutdown or {}),
            "shutdown_active": self._shutdown is not None,
            "abnormal_exit_count": self._abnormal_exit_count,
            "abnormal_exit_cooldown_until_ts": self._abnormal_exit_cooldown_until_ts,
            "abnormal_exit_cooldown_remaining_seconds": max(
                self._abnormal_exit_cooldown_until_ts - now, 0
            ),
            "consecutive_failures": {
                f"{component}:{dependency}:{operation}": count
                for (component, dependency, operation), count in sorted(
                    self._consecutive_failures.items()
                )
            },
            "recent_dependency_failures": [
                record.to_dict() for record in self._recent_dependency_failures
            ],
            "claimed_order_keys": len(self._claimed_order_keys),
        }

    def _prune_order_keys(self, now_ts: float) -> None:
        expired = [
            key
            for key, payload in self._claimed_order_keys.items()
            if float(payload.get("expires_at") or 0.0) <= now_ts
        ]
        for key in expired:
            self._claimed_order_keys.pop(key, None)

    def _read_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_state(self, payload: dict[str, Any]) -> None:
        serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True)
        self.state_path.write_text(serialized, encoding="utf-8")
