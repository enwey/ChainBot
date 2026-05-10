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


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str


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
            max_open_positions=max(_safe_int(raw.get("max_open_positions"), default_max_open_positions), 1),
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
