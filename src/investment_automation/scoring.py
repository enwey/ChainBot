from __future__ import annotations

import time
from typing import Any

from .settings import Settings


class SignalScorer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def launch_quality(self, scan: dict[str, Any]) -> tuple[float, list[str]]:
        score = 0.0
        signals: list[str] = []
        symbol = str(scan.get("symbol") or "")
        dev_buy = float(scan.get("dev_buy") or 0.0)
        liquidity = float(scan.get("liquidity") or 0.0)
        narrative_score = float(scan.get("narrative_score") or 0.0)

        if self.symbol_quality_ok(symbol):
            score += 15.0
            signals.append("clean_symbol")
        if self.settings.min_launch_dev_buy_sol <= dev_buy <= self.settings.max_dev_buy_sol:
            score += 25.0
            signals.append("dev_buy_ok")
        elif dev_buy > self.settings.max_dev_buy_sol:
            score += 8.0
            signals.append("large_dev_buy")
        if self.settings.min_watch_liquidity_usd <= liquidity <= 8_000:
            score += 20.0
            signals.append("early_liquidity_ok")
        elif liquidity > 8_000:
            score += 8.0
            signals.append("mature_liquidity")
        if narrative_score > 0:
            score += min(narrative_score, 25.0)
            signals.append("narrative")
        if self.has_socials(scan):
            score += 12.0
            signals.append("socials")
        if float(scan.get("score") or 0.0) >= self.settings.min_fast_track_score:
            score += 10.0
            signals.append("high_base_score")
        return min(score, 100.0), signals

    def fast_track_launch(self, scan: dict[str, Any], quality_score: float, signals: list[str]) -> bool:
        return (
            float(scan.get("narrative_score") or 0.0) >= 12.0
            or quality_score >= self.settings.min_fast_track_score
            or ("dev_buy_ok" in signals and "early_liquidity_ok" in signals and "clean_symbol" in signals)
        )

    def symbol_quality_ok(self, symbol: str) -> bool:
        normalized = symbol.strip().lower()
        if not normalized or normalized == "unknown":
            return False
        if normalized.replace(".", "").isdigit():
            return False
        if len(normalized) < 2 or len(normalized) > 24:
            return False
        blocked_words = (
            "test",
            "rug",
            "scam",
            "junk",
            "nigga",
            "nigger",
            "fuck",
            "shit",
        )
        return not any(word in normalized for word in blocked_words)

    def has_socials(self, scan: dict[str, Any]) -> bool:
        raw = scan.get("socials") or "{}"
        return any(key in raw and f'"{key}": null' not in raw for key in ("twitter", "telegram", "website"))

    def holder_ok(self, scan: dict[str, Any]) -> tuple[bool, str]:
        top10_owner_pct = float(scan.get("top10_owner_pct") or 0.0)
        top20_owner_pct = float(scan.get("top20_owner_pct") or 0.0)
        holder_count = int(float(scan.get("holder_count_estimate") or 0.0))
        if holder_count > 0 and holder_count < self.settings.min_holder_count:
            return False, f"holder 数过少 {holder_count}"
        if top10_owner_pct > self.settings.max_top10_owner_pct:
            return False, f"top10 持仓过高 {top10_owner_pct*100:.1f}%"
        if top20_owner_pct > self.settings.max_top20_owner_pct:
            return False, f"top20 持仓过高 {top20_owner_pct*100:.1f}%"
        return True, "ok"

    def observation_bonus(self, price_change_pct: float, liquidity_ratio: float, elapsed: int) -> float:
        bonus = 0.0
        bonus += min(max(price_change_pct, 0.0) * 120.0, 12.0)
        bonus += min(max(liquidity_ratio - 1.0, 0.0) * 20.0, 8.0)
        if elapsed >= self.settings.min_observation_seconds:
            bonus += 3.0
        return bonus

    def radar_momentum_bonus(self, price_change_pct: float, liquidity_ratio: float, elapsed: int) -> float:
        bonus = 0.0
        bonus += min(max(price_change_pct, 0.0) * 80.0, 10.0)
        bonus += min(max(liquidity_ratio - 1.0, 0.0) * 15.0, 6.0)
        if elapsed >= 10 and price_change_pct > 0:
            bonus += 2.0
        return bonus

    def market_quality_score(
        self,
        scan: dict[str, Any],
        price_change_pct: float = 0.0,
        liquidity_ratio: float = 1.0,
        elapsed: int = 0,
        *,
        now_ts: int | None = None,
    ) -> float:
        score = 0.0
        symbol = str(scan.get("symbol") or "")
        liquidity = float(scan.get("liquidity") or 0.0)
        dev_buy = float(scan.get("dev_buy") or 0.0)
        volume_5m = float(scan.get("realtime_volume_5m") or 0.0)
        holders = float(scan.get("holder_count_estimate") or 0.0)
        top10 = float(scan.get("top10_owner_pct") or 0.0)
        largest = float(scan.get("largest_owner_pct") or 0.0)
        created_ts = int(scan.get("created_ts") or 0)
        now = now_ts or int(time.time())
        age_seconds = max(now - created_ts, 0) if created_ts > 0 else 0

        if self.symbol_quality_ok(symbol):
            score += 8.0
        if age_seconds <= 45:
            score += 14.0
        elif age_seconds <= 180:
            score += 10.0
        elif age_seconds <= self.settings.max_token_age_seconds:
            score += 5.0

        if 2_200 <= liquidity <= 12_000:
            score += 18.0
        elif 1_500 <= liquidity < 2_200 or 12_000 < liquidity <= 35_000:
            score += 10.0
        elif liquidity > 35_000:
            score += 4.0

        if 0.75 <= dev_buy <= 4.0:
            score += 14.0
        elif 0.25 <= dev_buy < 0.75 or 4.0 < dev_buy <= 8.0:
            score += 7.0

        score += min(max(price_change_pct, 0.0) * 45.0, 14.0)
        if 0.9 <= liquidity_ratio <= 2.5:
            score += 8.0
        elif liquidity_ratio > 2.5:
            score += 4.0
        if elapsed >= 12 and price_change_pct > 0.02:
            score += 5.0
        if volume_5m > 0:
            score += min(volume_5m * 2.0, 8.0)
        if float(scan.get("narrative_score") or 0.0) > 0:
            score += min(float(scan.get("narrative_score") or 0.0), 10.0)

        if holders > 0:
            if holders >= self.settings.min_holder_count:
                score += 8.0
            elif holders >= 10:
                score += 3.0
            bundle_risk = self.bundle_risk_score(scan)
            if top10 <= 0.28 and largest <= 0.1:
                score += 8.0
            elif top10 <= 0.42 and largest <= 0.16:
                score += 4.0
            score -= self.bundle_penalty(bundle_risk)
            if bundle_risk >= 0.75:
                score = min(score, 45.0)
            elif bundle_risk >= 0.45:
                score = min(score, 62.0)
        else:
            score -= 4.0
            score = min(score, 52.0)

        return max(min(score, 100.0), 0.0)

    def bundle_risk_score(self, scan: dict[str, Any]) -> float:
        score = 0.0
        top10 = float(scan.get("top10_owner_pct") or 0.0)
        largest = float(scan.get("largest_owner_pct") or 0.0)
        holders = int(float(scan.get("holder_count_estimate") or 0.0))
        if top10 >= self.settings.bundle_risk_top10_owner_pct:
            score += min((top10 - self.settings.bundle_risk_top10_owner_pct) * 2.2, 0.45)
        if largest >= self.settings.bundle_risk_largest_owner_pct:
            score += min((largest - self.settings.bundle_risk_largest_owner_pct) * 4.0, 0.35)
        if holders > 0 and holders <= self.settings.bundle_risk_min_holders:
            score += min((self.settings.bundle_risk_min_holders - holders) / max(self.settings.bundle_risk_min_holders, 1), 0.25)
        return max(min(score, 1.0), 0.0)

    def bundle_penalty(self, bundle_risk_score: float) -> float:
        if bundle_risk_score >= 0.85:
            return 25.0
        if bundle_risk_score >= 0.55:
            return 12.0
        if bundle_risk_score >= 0.3:
            return 5.0
        return 0.0

    def bundle_risk_label(self, score: float) -> str:
        if score >= 0.75:
            return "高"
        if score >= 0.45:
            return "中"
        return "低"
