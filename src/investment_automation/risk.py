from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from .models import CandidateScan, RiskDecision, StrategyState, TradeHistoryRow
from .settings import Settings


class RiskService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _coerce_scan(self, scan: CandidateScan | Mapping[str, Any]) -> CandidateScan:
        return scan if isinstance(scan, CandidateScan) else CandidateScan.from_mapping(scan)

    def _coerce_trade_rows(
        self, sells: Sequence[TradeHistoryRow | Mapping[str, Any]]
    ) -> list[TradeHistoryRow]:
        return [
            row if isinstance(row, TradeHistoryRow) else TradeHistoryRow.from_mapping(row)
            for row in sells
        ]

    def default_strategy_state(self, reason: str, *, now_ts: int | None = None) -> StrategyState:
        return StrategyState(
            mode="balanced",
            score_offset=0.0,
            position_multiplier=1.0,
            cooldown_multiplier=1.0,
            max_open_positions=self.settings.max_open_positions,
            instant_probe_enabled=True,
            reason=reason,
            metrics={},
            updated_ts=now_ts or int(time.time()),
            cooldown_until_ts=0,
        )

    def evaluate_strategy_state(
        self,
        sells: Sequence[TradeHistoryRow | Mapping[str, Any]],
        previous_state: StrategyState,
        *,
        now_ts: int | None = None,
    ) -> StrategyState:
        now = now_ts or int(time.time())
        trade_rows = self._coerce_trade_rows(sells)
        if len(trade_rows) < 4:
            return self.default_strategy_state(
                "insufficient sell sample, keep balanced mode",
                now_ts=now,
            )

        recent = trade_rows[:12]
        pnl_values = [float(row.pnl_pct or 0.0) for row in recent]
        wins = [pnl for pnl in pnl_values if pnl > 0]
        losses = [pnl for pnl in pnl_values if pnl <= 0]
        average_pnl = sum(pnl_values) / len(pnl_values)
        win_rate = len(wins) / len(pnl_values)
        worst_trade = min(pnl_values)
        best_trade = max(pnl_values)
        loss_streak = 0
        for pnl in pnl_values:
            if pnl <= 0:
                loss_streak += 1
            else:
                break

        metrics = {
            "sample_size": len(pnl_values),
            "win_rate": win_rate,
            "avg_trade_pct": average_pnl,
            "worst_trade_pct": worst_trade,
            "best_trade_pct": best_trade,
            "loss_streak": loss_streak,
            "profit_factor": (sum(wins) / abs(sum(losses)))
            if losses and abs(sum(losses)) > 0
            else (999.0 if wins else 0.0),
        }

        mode = "balanced"
        score_offset = 0.0
        position_multiplier = 1.0
        cooldown_multiplier = 1.0
        max_open_positions = self.settings.max_open_positions
        instant_probe_enabled = True
        cooldown_until_ts = previous_state.cooldown_until_ts
        reason = "recent performance stable, keep balanced mode"

        if loss_streak >= 3 or average_pnl <= -0.16 or worst_trade <= -0.55:
            mode = "cooldown"
            score_offset = 12.0
            position_multiplier = 0.35
            cooldown_multiplier = 3.0
            max_open_positions = 1
            instant_probe_enabled = False
            cooldown_until_ts = max(cooldown_until_ts, now + 180)
            reason = "recent drawdown and loss streak triggered cooldown protection"
        elif win_rate < 0.28 or average_pnl <= -0.07:
            mode = "defensive"
            score_offset = 8.0
            position_multiplier = 0.55
            cooldown_multiplier = 2.0
            max_open_positions = max(1, min(self.settings.max_open_positions, 2))
            instant_probe_enabled = False
            reason = "recent win rate softened, switching to defensive mode"
        elif win_rate >= 0.45 and average_pnl > 0.04 and worst_trade > -0.25:
            mode = "aggressive"
            score_offset = -3.0
            position_multiplier = 1.15
            cooldown_multiplier = 0.6
            max_open_positions = self.settings.max_open_positions
            instant_probe_enabled = True
            cooldown_until_ts = 0
            reason = "recent win rate improved, allowing controlled aggression"
        elif now < cooldown_until_ts:
            mode = "cooldown"
            score_offset = 12.0
            position_multiplier = 0.35
            cooldown_multiplier = 3.0
            max_open_positions = 1
            instant_probe_enabled = False
            reason = "cooldown window still active"

        return StrategyState(
            mode=mode,
            score_offset=score_offset,
            position_multiplier=position_multiplier,
            cooldown_multiplier=cooldown_multiplier,
            max_open_positions=max_open_positions,
            instant_probe_enabled=instant_probe_enabled,
            reason=reason,
            metrics=metrics,
            updated_ts=now,
            cooldown_until_ts=cooldown_until_ts if mode == "cooldown" else 0,
        )

    def adaptive_min_score_to_buy(self, strategy_state: StrategyState) -> float:
        return max(0.0, self.settings.min_score_to_buy + strategy_state.score_offset)

    def adaptive_position_multiplier(self, strategy_state: StrategyState) -> float:
        return max(strategy_state.position_multiplier, 0.1)

    def adaptive_cooldown_seconds(self, strategy_state: StrategyState) -> float:
        return max(self.settings.min_buy_interval_seconds * strategy_state.cooldown_multiplier, 1.0)

    def adaptive_max_open_positions(self, strategy_state: StrategyState) -> int:
        return max(1, min(strategy_state.max_open_positions, self.settings.max_open_positions))

    def instant_probe_allowed(self, strategy_state: StrategyState) -> bool:
        return strategy_state.instant_probe_enabled

    def instant_probe_ok(
        self,
        scan: CandidateScan | Mapping[str, Any],
        strategy_state: StrategyState,
        *,
        has_position: bool,
    ) -> bool:
        scan_row = self._coerce_scan(scan)
        if not self.instant_probe_allowed(strategy_state):
            return False
        if has_position:
            return False
        quality_score = float(scan_row.get("launch_quality_score") or 0.0)
        if strategy_state.mode == "defensive" and quality_score < 70:
            return False
        if quality_score < 60:
            return False
        if scan_row.score < self.adaptive_min_score_to_buy(strategy_state):
            return False
        dev_buy = scan_row.dev_buy
        if not (self.settings.min_launch_dev_buy_sol <= dev_buy <= self.settings.max_dev_buy_sol):
            return False
        liquidity = scan_row.liquidity
        if (
            liquidity < self.settings.min_liquidity_usd
            or liquidity > self.settings.max_liquidity_usd
        ):
            return False
        if scan_row.bundle_risk_score >= 0.85:
            return False
        return True

    def should_open_position(
        self,
        scan: CandidateScan | Mapping[str, Any],
        wallet: Mapping[str, Any],
        strategy_state: StrategyState,
        *,
        has_position: bool,
        open_position_count: int,
        last_buy_ts: float,
        now_ts: float | None = None,
    ) -> RiskDecision:
        now = now_ts or time.time()
        scan_row = self._coerce_scan(scan)
        if strategy_state.is_cooldown_active(int(now)):
            return RiskDecision(False, "adaptive cooldown active")
        min_score = self.adaptive_min_score_to_buy(strategy_state)
        if scan_row.score < min_score:
            return RiskDecision(False, f"score below adaptive threshold {min_score:.0f}")
        dev_buy = scan_row.dev_buy
        if not (self.settings.min_dev_buy_sol <= dev_buy <= self.settings.max_dev_buy_sol):
            return RiskDecision(False, "developer buy outside target band")
        if has_position:
            return RiskDecision(False, "position already open")
        if open_position_count >= self.adaptive_max_open_positions(strategy_state):
            return RiskDecision(False, "portfolio already full")
        liquidity = scan_row.liquidity
        if liquidity < self.settings.min_liquidity_usd:
            return RiskDecision(False, "liquidity too low")
        if liquidity > self.settings.max_liquidity_usd:
            return RiskDecision(False, "liquidity too mature")
        created_ts = scan_row.created_ts
        if created_ts > 0:
            age_seconds = max(int(now) - created_ts, 0)
            if age_seconds > self.settings.max_token_age_seconds:
                return RiskDecision(False, "token too old for entry window")
        if now - last_buy_ts < self.adaptive_cooldown_seconds(strategy_state):
            return RiskDecision(False, "global buy cooldown active")
        if float(wallet.get("current_balance", 0.0) or 0.0) < self.settings.position_size_usd:
            return RiskDecision(False, "insufficient balance")
        return RiskDecision(True, "ok")

    def position_size_usd(
        self,
        scan: CandidateScan | Mapping[str, Any],
        wallet: Mapping[str, Any],
        strategy_state: StrategyState,
    ) -> float:
        scan_row = self._coerce_scan(scan)
        balance = float(wallet.get("current_balance", 0.0) or 0.0)
        base_size = self.settings.position_size_usd
        score = scan_row.score
        if score >= 95:
            multiplier = 1.25
        elif score >= 88:
            multiplier = 1.0
        else:
            multiplier = 0.75
        if scan_row.get("scalp_override") or scan_row.bundle_risk_score >= 0.75:
            multiplier *= self.settings.scalp_risk_position_multiplier
        score_size = base_size * multiplier
        score_size *= self.adaptive_position_multiplier(strategy_state)
        if float(scan_row.get("narrative_score") or 0.0) > 0:
            score_size *= self.settings.narrative_position_multiplier
        wallet_cap = balance * self.settings.max_wallet_exposure_pct
        return round(
            max(min(score_size, self.settings.max_position_size_usd, wallet_cap, balance), 0.0), 2
        )
