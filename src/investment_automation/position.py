from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Optional

from .db import Database
from .market_data import MarketDataClient
from .scoring import SignalScorer
from .settings import Settings
from .watchlist import WatchlistService


@dataclass(frozen=True)
class PositionExitDecision:
    current_price: float
    sell_price: float
    max_price: float
    pnl_pct: float
    hold_seconds: int
    exit_reason: Optional[str]
    exit_context: dict[str, Any]
    sell_fraction: float
    sell_amount_usd: float
    remaining_amount_usd: float


class PositionService:
    def __init__(
        self,
        settings: Settings,
        scorer: SignalScorer,
        watchlist_service: WatchlistService,
        database: Database,
        market_data: MarketDataClient,
    ) -> None:
        self.settings = settings
        self.scorer = scorer
        self.watchlist_service = watchlist_service
        self.database = database
        self.market_data = market_data

    def paper_adjusted_buy_price(self, market_price: float, mode: str) -> float:
        if mode != "paper":
            return market_price
        total_bps = self.settings.paper_buy_slippage_bps + self.settings.paper_fee_bps
        return market_price * (1 + total_bps / 10_000)

    def paper_adjusted_sell_price(self, market_price: float, mode: str) -> float:
        if mode != "paper":
            return market_price
        total_bps = self.settings.paper_sell_slippage_bps + self.settings.paper_fee_bps
        return market_price * max(1 - total_bps / 10_000, 0.01)

    def sale_proceeds(self, amount_usd: float, pnl_pct: float, mode: str) -> float:
        proceeds = amount_usd * (1 + pnl_pct)
        return max(proceeds, 0.0)

    def entry_reason(self, scan: dict[str, Any], position_size_usd: float) -> str:
        return (
            f"score={scan['score']:.0f}, age={self.watchlist_service.token_age_text(scan)}, "
            f"dev={scan['dev_buy']:.2f} SOL, liq=${scan['liquidity']:.0f}, "
            f"narrative={scan.get('narrative_label', '-')}, "
            f"top10={float(scan.get('top10_owner_pct') or 0) * 100:.1f}%, holders={int(float(scan.get('holder_count_estimate') or 0))}, "
            f"bundle={self.scorer.bundle_risk_label(float(scan.get('bundle_risk_score') or 0))}, "
            f"mode={'scalp' if scan.get('scalp_override') else 'normal'}, "
            f"size=${position_size_usd:.2f}"
        )

    def hold_seconds(self, buy_time: str, *, now_ts: Optional[float] = None) -> int:
        try:
            buy_ts = time.mktime(time.strptime(buy_time, "%Y-%m-%d %H:%M:%S"))
        except (TypeError, ValueError):
            return 0
        reference_ts = now_ts if now_ts is not None else time.time()
        return max(int(reference_ts - buy_ts), 0)

    async def holder_risk_exit_reason(
        self,
        position: dict[str, Any],
        pnl_pct: float,
        hold_seconds: int,
    ) -> Optional[str]:
        if hold_seconds < 3 or pnl_pct >= 0.08:
            return None
        holder_metrics = await asyncio.to_thread(
            self.market_data.fetch_holder_metrics, position["addr"]
        )
        holders = int(float(holder_metrics.get("holder_count_estimate") or 0))
        if holders <= 0:
            return None
        scan = {
            "holder_count_estimate": holders,
            "top10_owner_pct": float(holder_metrics.get("top10_owner_pct") or 0.0),
            "top20_owner_pct": float(holder_metrics.get("top20_owner_pct") or 0.0),
            "largest_owner_pct": float(holder_metrics.get("largest_owner_pct") or 0.0),
        }
        bundle_risk = self.scorer.bundle_risk_score(scan)
        if bundle_risk >= 0.85:
            return (
                f"holders={holders}, top10={scan['top10_owner_pct'] * 100:.1f}%, "
                f"largest={scan['largest_owner_pct'] * 100:.1f}%, risk={self.scorer.bundle_risk_label(bundle_risk)}"
            )
        return None

    def zombie_exit_reason(
        self,
        position: dict[str, Any],
        snapshot: dict[str, Any],
        pnl_pct: float,
        hold_seconds: int,
        volume_5m: float,
        *,
        now_ts: Optional[int] = None,
    ) -> Optional[str]:
        if int(position.get("moonbag_active") or 0):
            return None
        entry_metrics = self.database.latest_buy_metrics(position["addr"])
        has_narrative = float(entry_metrics.get("narrative_score") or 0.0) > 0 or bool(
            entry_metrics.get("narrative_tags")
        )
        if has_narrative:
            return None
        if pnl_pct >= self.settings.zombie_min_profit_keep_pct:
            return None
        source = str(snapshot.get("source") or position.get("price_source") or "").lower()
        updated_ts = int(float(snapshot.get("timestamp") or position.get("price_updated_ts") or 0))
        now = now_ts if now_ts is not None else int(time.time())
        non_live_age = max(now - updated_ts, 0) if source != "pumpportal" and updated_ts > 0 else 0
        if (
            hold_seconds >= self.settings.zombie_position_seconds
            and volume_5m <= self.settings.zombie_min_volume_5m_usd
        ):
            return f"持仓 {hold_seconds}s 且 5m 成交仅 ${volume_5m:.0f}"
        if hold_seconds >= self.settings.zombie_position_seconds and source != "pumpportal":
            return f"持仓 {hold_seconds}s 且无实时成交流 source={source or '-'}"
        if (
            non_live_age >= self.settings.zombie_non_live_seconds
            and volume_5m <= self.settings.zombie_min_volume_5m_usd
        ):
            return f"非实时行情 {non_live_age}s 且 5m 成交 ${volume_5m:.0f}"
        return None

    def dynamic_exit_signal(
        self,
        position: dict[str, Any],
        snapshot: dict[str, Any],
        sell_price: float,
        max_price: float,
        pnl_pct: float,
        hold_seconds: int,
        *,
        now_ts: Optional[int] = None,
    ) -> tuple[Optional[str], dict[str, Any], float]:
        buy_price = max(float(position.get("buy_price") or 0), 0.000000001)
        volume_5m = float(snapshot.get("volume_5m") or 0.0)
        entry_liquidity = float(position.get("entry_liquidity") or 0.0)
        raw_liquidity = float(snapshot.get("liquidity_usd") or 0.0)
        liquidity_missing = raw_liquidity <= 0 and volume_5m > 0
        current_liquidity = entry_liquidity if liquidity_missing else raw_liquidity
        liquidity_ratio = (current_liquidity / entry_liquidity) if entry_liquidity > 0 else 1.0
        peak_gain_pct = (max_price - buy_price) / buy_price
        drawdown_from_peak_pct = (max_price - sell_price) / max(max_price, 0.0000001)
        volume_to_position_ratio = volume_5m / max(float(position.get("amount_usd") or 0.0), 1.0)

        context = {
            "current_liquidity": current_liquidity,
            "entry_liquidity": entry_liquidity,
            "liquidity_ratio": liquidity_ratio,
            "liquidity_missing": liquidity_missing,
            "volume_5m": volume_5m,
            "volume_to_position_ratio": volume_to_position_ratio,
            "peak_gain_pct": peak_gain_pct,
            "drawdown_from_peak_pct": drawdown_from_peak_pct,
            "moonbag_active": bool(position.get("moonbag_active")),
            "price_source": position.get("price_source"),
            "position_age_seconds": hold_seconds,
        }

        zombie_reason = self.zombie_exit_reason(
            position, snapshot, pnl_pct, hold_seconds, volume_5m, now_ts=now_ts
        )
        if zombie_reason:
            context["zombie_reason"] = zombie_reason
            return "zombie_position_exit", context, 1.0

        if pnl_pct <= self.settings.emergency_stop_loss_pct:
            return "emergency_stop", context, 1.0

        if (
            hold_seconds >= 8
            and not liquidity_missing
            and liquidity_ratio <= self.settings.emergency_liquidity_ratio
        ):
            return "liquidity_break", context, 1.0

        if (
            peak_gain_pct >= self.settings.fast_exit_peak_pct
            and drawdown_from_peak_pct >= self.settings.fast_exit_drawdown_pct
        ):
            if int(position.get("moonbag_active") or 0):
                return "moonbag_parabolic_exit", context, 1.0
            return "profit_lock_moonbag", context, self.settings.profit_lock_sell_pct

        if pnl_pct >= self.settings.profit_lock_min_pct:
            if (
                drawdown_from_peak_pct >= self.settings.profit_lock_drawdown_pct
                and liquidity_ratio <= self.settings.profit_lock_liquidity_ratio
            ):
                if int(position.get("moonbag_active") or 0):
                    return "moonbag_momentum_exit", context, 1.0
                return "profit_lock_moonbag", context, self.settings.profit_lock_sell_pct
            if (
                hold_seconds >= self.settings.profit_lock_min_hold_seconds
                and volume_to_position_ratio < self.settings.min_volume_to_position_ratio
                and liquidity_ratio < 1.0
            ):
                if int(position.get("moonbag_active") or 0):
                    return None, context, 0.0
                return "profit_lock_weak_follow", context, self.settings.profit_lock_sell_pct

        if (
            hold_seconds >= self.settings.stale_position_seconds
            and pnl_pct > 0
            and drawdown_from_peak_pct >= 0.05
            and volume_to_position_ratio < self.settings.min_volume_to_position_ratio
        ):
            if int(position.get("moonbag_active") or 0):
                return None, context, 0.0
            return "stale_profit_lock", context, self.settings.profit_lock_sell_pct

        return None, context, 0.0

    async def evaluate_position(
        self,
        position: dict[str, Any],
        snapshot: dict[str, Any],
        *,
        now_ts: Optional[int] = None,
    ) -> Optional[PositionExitDecision]:
        current_price = float(snapshot.get("price") or 0.0)
        if current_price <= 0:
            return None

        sell_price = self.paper_adjusted_sell_price(current_price, str(position["mode"]))
        buy_price = float(position["buy_price"])
        max_price = max(float(position["max_price"]), sell_price)
        pnl_pct = (sell_price - buy_price) / buy_price
        hold_seconds = self.hold_seconds(
            str(position["buy_time"]), now_ts=float(now_ts) if now_ts is not None else None
        )

        holder_exit_reason = await self.holder_risk_exit_reason(position, pnl_pct, hold_seconds)
        if holder_exit_reason:
            exit_reason = "holder_risk_cut"
            exit_context = {
                "holder_risk_reason": holder_exit_reason,
                "position_age_seconds": hold_seconds,
            }
            sell_fraction = 1.0
        else:
            exit_reason, exit_context, sell_fraction = self.dynamic_exit_signal(
                position,
                snapshot,
                sell_price,
                max_price,
                pnl_pct,
                hold_seconds,
                now_ts=now_ts,
            )

        amount_usd = float(position.get("amount_usd") or 0.0)
        sell_amount_usd = 0.0
        remaining_amount_usd = amount_usd
        normalized_sell_fraction = sell_fraction
        if exit_reason:
            sell_amount_usd = round(amount_usd * sell_fraction, 2)
            if amount_usd - sell_amount_usd <= self.settings.moonbag_min_usd:
                sell_amount_usd = amount_usd
                normalized_sell_fraction = 1.0
            remaining_amount_usd = max(amount_usd - sell_amount_usd, 0.0)

        return PositionExitDecision(
            current_price=current_price,
            sell_price=sell_price,
            max_price=max_price,
            pnl_pct=pnl_pct,
            hold_seconds=hold_seconds,
            exit_reason=exit_reason,
            exit_context=exit_context,
            sell_fraction=normalized_sell_fraction,
            sell_amount_usd=sell_amount_usd,
            remaining_amount_usd=remaining_amount_usd,
        )
