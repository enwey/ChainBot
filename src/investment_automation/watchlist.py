from __future__ import annotations

import time
from copy import deepcopy
from typing import Any, Optional

from .scoring import SignalScorer
from .settings import Settings


class WatchlistService:
    def __init__(self, settings: Settings, scorer: SignalScorer) -> None:
        self.settings = settings
        self.scorer = scorer

    def get_watchlist_status(
        self, watchlist: dict[str, dict[str, Any]], *, now_ts: Optional[int] = None
    ) -> list[dict[str, Any]]:
        now = now_ts or int(time.time())
        items: list[dict[str, Any]] = []
        for addr, candidate in list(watchlist.items()):
            scan = candidate["scan"]
            initial_price = max(float(candidate["initial_price"]), 0.000000001)
            highest_price = max(float(candidate["highest_price"]), 0.000000001)
            latest_price = float(candidate["latest_price"])
            initial_liquidity = max(float(candidate["initial_liquidity"]), 0.000000001)
            latest_liquidity = float(candidate["latest_liquidity"])
            elapsed = max(now - int(candidate["watch_started_ts"]), 0)
            price_change_pct = (latest_price - initial_price) / initial_price
            drawdown_pct = (highest_price - latest_price) / highest_price
            liquidity_ratio = latest_liquidity / initial_liquidity
            items.append(
                {
                    "addr": addr,
                    "symbol": scan["symbol"],
                    "score": float(scan["score"]),
                    "watch_seconds": elapsed,
                    "watch_age": self.format_duration(elapsed),
                    "token_age": self.token_age_text(scan, now_ts=now),
                    "initial_price": initial_price,
                    "latest_price": latest_price,
                    "price_change_pct": price_change_pct,
                    "drawdown_pct": drawdown_pct,
                    "initial_liquidity": initial_liquidity,
                    "latest_liquidity": latest_liquidity,
                    "liquidity_ratio": liquidity_ratio,
                    "progress": scan["progress"],
                    "dev_buy": scan["dev_buy"],
                    "launch_quality_score": float(scan.get("launch_quality_score") or 0.0),
                    "launch_signals": scan.get("launch_signals") or [],
                    "holder_count_estimate": float(scan.get("holder_count_estimate") or 0.0),
                    "top10_owner_pct": float(scan.get("top10_owner_pct") or 0.0),
                    "top20_owner_pct": float(scan.get("top20_owner_pct") or 0.0),
                    "largest_owner_pct": float(scan.get("largest_owner_pct") or 0.0),
                    "bundle_risk_score": float(scan.get("bundle_risk_score") or 0.0),
                    "bundle_risk_label": "待查"
                    if float(scan.get("holder_count_estimate") or 0.0) <= 0
                    else self.scorer.bundle_risk_label(float(scan.get("bundle_risk_score") or 0.0)),
                    "last_update_ts": int(
                        candidate.get("last_update_ts", candidate["watch_started_ts"])
                    ),
                    "last_update_age": self.format_duration(
                        max(
                            now
                            - int(candidate.get("last_update_ts", candidate["watch_started_ts"])),
                            0,
                        )
                    ),
                    "last_snapshot_age": self.format_duration(
                        max(now - int(candidate.get("last_snapshot_ts", 0)), 0)
                    )
                    if int(candidate.get("last_snapshot_ts", 0)) > 0
                    else "-",
                    "last_snapshot_source": candidate.get("last_snapshot_source", "-"),
                    "snapshot_miss_count": int(candidate.get("snapshot_miss_count", 0)),
                    "status_reason": self.watch_status_reason_with_snapshot(
                        candidate,
                        elapsed,
                        price_change_pct,
                        drawdown_pct,
                        liquidity_ratio,
                    ),
                    "dex_url": scan["dex_url"],
                }
            )
        items.sort(key=lambda item: item["watch_seconds"], reverse=True)
        return items

    def should_watch(
        self, scan: dict[str, Any], *, now_ts: Optional[int] = None
    ) -> tuple[bool, str]:
        quality_score, signals = self.scorer.launch_quality(scan)
        scan["launch_quality_score"] = quality_score
        scan["launch_signals"] = signals
        if not self.scorer.fast_track_launch(scan, quality_score, signals):
            if (
                quality_score < self.settings.min_launch_quality_score
                or len(signals) < self.settings.min_launch_signal_count
            ):
                return (
                    False,
                    f"low launch quality {quality_score:.0f}: {','.join(signals) or 'no strong signal'}",
                )
        if float(scan.get("score") or 0.0) < self.settings.min_watch_score:
            return False, "watch score too low"
        if (
            float(scan.get("dev_buy") or 0.0) < 0.05
            and float(scan.get("narrative_score") or 0.0) <= 0
        ):
            return False, "no launch buy pressure"
        liquidity = float(scan.get("liquidity") or 0.0)
        if liquidity < self.settings.min_watch_liquidity_usd:
            return False, "initial liquidity too low"
        if liquidity > self.settings.max_liquidity_usd:
            return False, "initial liquidity too high"
        created_ts = int(scan.get("created_ts") or 0)
        if created_ts > 0:
            now = now_ts or int(time.time())
            age_seconds = max(now - created_ts, 0)
            if age_seconds > self.settings.max_token_age_seconds:
                return False, "token too old for watch window"
        return True, "ok"

    def build_watch_candidate(
        self,
        scan: dict[str, Any],
        radar_samples: list[dict[str, Any]] | None = None,
        *,
        now_ts: Optional[int] = None,
    ) -> dict[str, Any]:
        now = now_ts or int(time.time())
        samples = deepcopy(radar_samples or [])
        if not samples:
            samples = [{"ts": now, "price": float(scan["price"]), "volume_5m": 0.0}]
        return {
            "scan": deepcopy(scan),
            "watch_started_ts": now,
            "last_update_ts": now,
            "last_snapshot_ts": 0,
            "last_snapshot_source": "-",
            "snapshot_miss_count": 0,
            "initial_price": float(scan["price"]),
            "highest_price": float(scan["price"]),
            "latest_price": float(scan["price"]),
            "initial_liquidity": float(scan["liquidity"]),
            "latest_liquidity": float(scan["liquidity"]),
            "samples": samples,
        }

    def trim_to_limit(
        self, watchlist: dict[str, dict[str, Any]]
    ) -> list[tuple[str, dict[str, Any], str]]:
        max_size = max(int(self.settings.max_watchlist_size), 0)
        overflow = len(watchlist) - max_size
        if overflow <= 0:
            return []

        ranked = sorted(
            watchlist.items(),
            key=lambda item: (
                self.scorer.launch_quality(item[1]["scan"])[0],
                float(item[1]["scan"].get("score") or 0.0),
                int(item[1].get("last_snapshot_ts") or 0),
                int(item[1].get("watch_started_ts") or 0),
            ),
        )
        removals: list[tuple[str, dict[str, Any], str]] = []
        for addr, candidate in ranked[:overflow]:
            removed = watchlist.pop(addr, None)
            if removed:
                removals.append((addr, removed["scan"], "观察池超过容量限制，清理低质量候选"))
        return removals

    def prune_for_new_candidate(
        self,
        watchlist: dict[str, dict[str, Any]],
        scan: dict[str, Any],
    ) -> tuple[bool, Optional[tuple[str, dict[str, Any], str]]]:
        if not watchlist:
            return True, None
        new_quality, _ = self.scorer.launch_quality(scan)
        weakest_addr = None
        weakest_quality = 101.0
        for addr, candidate in watchlist.items():
            quality, _ = self.scorer.launch_quality(candidate["scan"])
            if quality < weakest_quality:
                weakest_quality = quality
                weakest_addr = addr
        if weakest_addr and new_quality > weakest_quality + 10:
            removed = watchlist.pop(weakest_addr, None)
            if removed:
                return True, (
                    weakest_addr,
                    removed["scan"],
                    f"观察池容量限制，替换为更高质量候选 {scan['symbol']}",
                )
        return False, None

    def mark_snapshot_pending(
        self,
        watchlist: dict[str, dict[str, Any]],
        addr: str,
        *,
        now_ts: Optional[int] = None,
    ) -> None:
        candidate = watchlist.get(addr)
        if not candidate:
            return
        candidate["snapshot_miss_count"] = int(candidate.get("snapshot_miss_count", 0)) + 1
        candidate["last_update_ts"] = now_ts or int(time.time())

    def token_age_text(self, scan: dict[str, Any], *, now_ts: Optional[int] = None) -> str:
        created_ts = int(scan.get("created_ts") or 0)
        if created_ts <= 0:
            return "-"
        now = now_ts or int(time.time())
        age_seconds = max(now - created_ts, 0)
        return self.format_duration(age_seconds)

    def format_duration(self, age_seconds: int) -> str:
        if age_seconds < 60:
            return f"{age_seconds}s"
        if age_seconds < 3600:
            return f"{age_seconds // 60}m"
        if age_seconds < 86400:
            return f"{age_seconds // 3600}h"
        return f"{age_seconds // 86400}d"

    def watch_status_reason(
        self, elapsed: int, price_change_pct: float, drawdown_pct: float, liquidity_ratio: float
    ) -> str:
        if elapsed < self.settings.min_observation_seconds:
            return f"冷静期中，还需 {self.settings.min_observation_seconds - elapsed}s"
        if drawdown_pct > self.settings.max_observation_drawdown_pct:
            return f"回撤过大 {drawdown_pct * 100:.1f}%"
        if liquidity_ratio < self.settings.min_observation_liquidity_ratio:
            return f"流动性偏弱 {liquidity_ratio:.2f}x"
        if price_change_pct < self.settings.min_observation_price_change_pct:
            return f"动量不足 {price_change_pct * 100:.1f}%"
        return "满足确认条件，等待买入窗口"

    def watch_status_reason_with_snapshot(
        self,
        candidate: dict[str, Any],
        elapsed: int,
        price_change_pct: float,
        drawdown_pct: float,
        liquidity_ratio: float,
    ) -> str:
        if int(candidate.get("last_snapshot_ts", 0)) <= 0:
            return f"等待市场快照，第 {int(candidate.get('snapshot_miss_count', 0))} 次重试"
        return self.watch_status_reason(elapsed, price_change_pct, drawdown_pct, liquidity_ratio)
