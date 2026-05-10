from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from .scoring import SignalScorer
from .settings import Settings


@dataclass(frozen=True)
class RadarObservationDecision:
    scan: dict[str, Any]
    rejected_reason: Optional[str]
    record_scan: bool


@dataclass(frozen=True)
class WatchObservationDecision:
    scan: dict[str, Any]
    rejected_reason: Optional[str]
    watch_reason: Optional[str]
    open_reason: Optional[str]
    record_scan: bool


class ObservationService:
    def __init__(self, settings: Settings, scorer: SignalScorer) -> None:
        self.settings = settings
        self.scorer = scorer

    def track_radar_candidate(
        self,
        radar_pool: dict[str, dict[str, Any]],
        scan: dict[str, Any],
        *,
        now_ts: Optional[int] = None,
    ) -> None:
        addr = str(scan["addr"])
        now = now_ts or int(time.time())
        radar_pool[addr] = {
            "scan": deepcopy(scan),
            "first_seen_ts": now,
            "last_update_ts": now,
            "initial_price": float(scan["price"]),
            "initial_liquidity": float(scan["liquidity"]),
            "highest_price": float(scan["price"]),
            "samples": [{"ts": now, "price": float(scan["price"]), "volume_5m": 0.0}],
        }
        if len(radar_pool) > self.settings.max_radar_tracked_tokens:
            oldest = sorted(radar_pool.items(), key=lambda item: int(item[1].get("first_seen_ts", 0)))
            overflow = max(len(radar_pool) - self.settings.max_radar_tracked_tokens, 0)
            for old_addr, _ in oldest[:overflow]:
                radar_pool.pop(old_addr, None)

    def prune_radar_pool(
        self,
        radar_pool: dict[str, dict[str, Any]],
        watchlist_addresses: Iterable[str],
        position_addresses: Iterable[str],
        *,
        now_ts: Optional[int] = None,
    ) -> None:
        now = now_ts or int(time.time())
        watched = set(watchlist_addresses)
        held = set(position_addresses)
        for addr, candidate in list(radar_pool.items()):
            if now - int(candidate.get("first_seen_ts", now)) > self.settings.radar_track_seconds:
                radar_pool.pop(addr, None)
            elif addr in watched or addr in held:
                radar_pool.pop(addr, None)

    def process_radar_candidate(
        self,
        candidate: dict[str, Any],
        snapshot: dict[str, Any],
        *,
        now_ts: Optional[int] = None,
    ) -> Optional[RadarObservationDecision]:
        scan = deepcopy(candidate["scan"])
        now = now_ts or int(time.time())
        elapsed = now - int(candidate["first_seen_ts"])
        current_price = float(snapshot.get("price") or 0.0)
        if current_price <= 0:
            return None

        current_liquidity = float(snapshot.get("liquidity_usd") or scan.get("liquidity") or 0.0)
        candidate["highest_price"] = max(float(candidate.get("highest_price") or current_price), current_price)
        candidate["last_update_ts"] = now
        self.append_price_sample(candidate, snapshot, now_ts=now)
        abnormal, abnormal_reason = self.abnormal_kline_reason(candidate)
        if abnormal:
            return RadarObservationDecision(scan=scan, rejected_reason=abnormal_reason, record_scan=False)

        initial_price = max(float(candidate.get("initial_price") or current_price), 0.000000001)
        initial_liquidity = max(float(candidate.get("initial_liquidity") or current_liquidity or 1), 0.000000001)
        price_change_pct = (current_price - initial_price) / initial_price
        liquidity_ratio = current_liquidity / initial_liquidity

        scan["price"] = current_price
        scan["liquidity"] = current_liquidity
        scan["progress"] = self.progress_from_liquidity(current_liquidity)
        scan["realtime_volume_5m"] = float(snapshot.get("volume_5m") or 0.0)
        scan["bundle_risk_score"] = self.scorer.bundle_risk_score(scan)
        scan["score"] = self.scorer.market_quality_score(
            scan,
            price_change_pct,
            liquidity_ratio,
            elapsed,
            now_ts=now,
        )
        scan["scan_time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        quality_score, signals = self.scorer.launch_quality(scan)
        scan["launch_quality_score"] = quality_score
        scan["launch_signals"] = signals
        candidate["scan"] = deepcopy(scan)
        return RadarObservationDecision(scan=scan, rejected_reason=None, record_scan=True)

    def process_watch_candidate(
        self,
        candidate: dict[str, Any],
        snapshot: dict[str, Any],
        holder_metrics: dict[str, Any],
        strategy_mode: str,
        *,
        now_ts: Optional[int] = None,
    ) -> Optional[WatchObservationDecision]:
        scan = candidate["scan"]
        now = now_ts or int(time.time())
        elapsed = now - int(candidate["watch_started_ts"])
        current_price = float(snapshot.get("price") or 0.0)
        if current_price <= 0:
            return None

        current_liquidity = float(snapshot.get("liquidity_usd") or candidate.get("latest_liquidity") or 0.0)
        candidate["latest_price"] = current_price
        candidate["latest_liquidity"] = current_liquidity
        candidate["highest_price"] = max(float(candidate["highest_price"]), current_price)
        candidate["last_update_ts"] = now
        candidate["last_snapshot_ts"] = now
        candidate["last_snapshot_source"] = str(snapshot.get("source") or "unknown")
        candidate["snapshot_miss_count"] = 0
        self.append_price_sample(candidate, snapshot, now_ts=now)
        abnormal, abnormal_reason = self.abnormal_kline_reason(candidate)
        if abnormal:
            return WatchObservationDecision(
                scan=scan,
                rejected_reason=abnormal_reason,
                watch_reason=None,
                open_reason=None,
                record_scan=False,
            )

        initial_price = max(float(candidate["initial_price"]), 0.000000001)
        initial_liquidity = max(float(candidate["initial_liquidity"]), 0.000000001)
        price_change_pct = (current_price - initial_price) / initial_price
        drawdown_pct = (float(candidate["highest_price"]) - current_price) / max(float(candidate["highest_price"]), 0.000000001)
        liquidity_ratio = current_liquidity / initial_liquidity

        confirmed_scan = deepcopy(scan)
        confirmed_scan["price"] = current_price
        confirmed_scan["liquidity"] = current_liquidity
        confirmed_scan["realtime_volume_5m"] = float(snapshot.get("volume_5m") or 0.0)
        confirmed_scan["scan_time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        confirmed_scan["progress"] = self.progress_from_liquidity(current_liquidity)
        confirmed_scan.update(holder_metrics)
        confirmed_scan["bundle_risk_score"] = self.scorer.bundle_risk_score(confirmed_scan)
        confirmed_scan["score"] = self.scorer.market_quality_score(
            confirmed_scan,
            price_change_pct,
            liquidity_ratio,
            elapsed,
            now_ts=now,
        )
        candidate["scan"] = deepcopy(confirmed_scan)

        if drawdown_pct > self.settings.max_observation_drawdown_pct:
            return WatchObservationDecision(
                scan=confirmed_scan,
                rejected_reason=f"瑙傚療鏈熷洖鎾よ繃澶?{drawdown_pct*100:.1f}%",
                watch_reason=None,
                open_reason=None,
                record_scan=True,
            )
        if elapsed > self.settings.max_observation_seconds:
            return WatchObservationDecision(
                scan=confirmed_scan,
                rejected_reason="瑙傚療鏈熻秴鏃朵粛鏈弧瓒冲叆鍦虹‘璁?",
                watch_reason=None,
                open_reason=None,
                record_scan=True,
            )
        if elapsed < self.settings.min_observation_seconds:
            return WatchObservationDecision(
                scan=confirmed_scan,
                rejected_reason=None,
                watch_reason=self.watch_update_reason(confirmed_scan, elapsed, price_change_pct, liquidity_ratio),
                open_reason=None,
                record_scan=True,
            )

        scalp_override, scalp_reason = self.scalp_override_ok(
            confirmed_scan,
            price_change_pct,
            drawdown_pct,
            liquidity_ratio,
            elapsed,
            strategy_mode=strategy_mode,
        )
        confirmed_scan["scalp_override"] = scalp_override
        holder_allowed, holder_reason = self.scorer.holder_ok(confirmed_scan)
        if not holder_allowed and not scalp_override:
            return WatchObservationDecision(
                scan=confirmed_scan,
                rejected_reason=holder_reason,
                watch_reason=None,
                open_reason=None,
                record_scan=True,
            )
        if float(confirmed_scan.get("bundle_risk_score") or 0.0) >= 0.85 and not scalp_override:
            return WatchObservationDecision(
                scan=confirmed_scan,
                rejected_reason="鐤戜技 bundle 椋庨櫓杩囬珮",
                watch_reason=None,
                open_reason=None,
                record_scan=True,
            )
        if liquidity_ratio < self.settings.min_observation_liquidity_ratio:
            return WatchObservationDecision(
                scan=confirmed_scan,
                rejected_reason=f"瑙傚療鏈熸祦鍔ㄦ€ц“鍑忚嚦 {liquidity_ratio:.2f}x",
                watch_reason=None,
                open_reason=None,
                record_scan=True,
            )
        if price_change_pct < self.settings.min_observation_price_change_pct:
            return WatchObservationDecision(
                scan=confirmed_scan,
                rejected_reason=None,
                watch_reason=self.watch_update_reason(confirmed_scan, elapsed, price_change_pct, liquidity_ratio),
                open_reason=None,
                record_scan=True,
            )

        if scalp_override:
            confirmed_scan["score"] = max(float(confirmed_scan["score"]), self.settings.min_score_to_buy)
            confirmed_scan["scalp_reason"] = scalp_reason
            candidate["scan"] = deepcopy(confirmed_scan)

        return WatchObservationDecision(
            scan=confirmed_scan,
            rejected_reason=None,
            watch_reason=None,
            open_reason=self.watch_confirmed_reason(elapsed, price_change_pct, drawdown_pct, liquidity_ratio),
            record_scan=True,
        )

    def scalp_override_ok(
        self,
        scan: dict[str, Any],
        price_change_pct: float,
        drawdown_pct: float,
        liquidity_ratio: float,
        elapsed: int,
        *,
        strategy_mode: str,
    ) -> tuple[bool, str]:
        if not self.settings.scalp_override_enabled:
            return False, "scalp disabled"
        if strategy_mode in {"cooldown", "defensive"}:
            return False, f"scalp disabled by adaptive mode {strategy_mode}"
        holders = int(float(scan.get("holder_count_estimate") or 0))
        largest = float(scan.get("largest_owner_pct") or 0.0)
        volume_5m = float(scan.get("realtime_volume_5m") or 0.0)
        if holders < self.settings.scalp_min_holders:
            return False, f"scalp holders too low {holders}"
        if largest >= 0.92:
            return False, f"scalp largest wallet too high {largest*100:.1f}%"
        if elapsed < max(4, min(self.settings.min_observation_seconds, 8)):
            return False, "scalp needs a few live samples"
        if price_change_pct < self.settings.scalp_min_move_pct:
            return False, f"scalp move too weak {price_change_pct*100:.1f}%"
        if drawdown_pct > self.settings.scalp_max_drawdown_pct:
            return False, f"scalp drawdown too high {drawdown_pct*100:.1f}%"
        if liquidity_ratio < self.settings.scalp_min_liquidity_ratio:
            return False, f"scalp liquidity faded {liquidity_ratio:.2f}x"
        if volume_5m < self.settings.scalp_min_volume_5m_usd:
            return False, f"scalp volume too low ${volume_5m:.1f}"
        return True, (
            f"scalp override: move={price_change_pct*100:.1f}%, "
            f"drawdown={drawdown_pct*100:.1f}%, liq={liquidity_ratio:.2f}x, "
            f"vol5m=${volume_5m:.1f}, holders={holders}"
        )

    def append_price_sample(
        self,
        candidate: dict[str, Any],
        snapshot: dict[str, Any],
        *,
        now_ts: Optional[int] = None,
    ) -> None:
        samples = candidate.setdefault("samples", [])
        ts = int(float(snapshot.get("timestamp") or now_ts or time.time()))
        price = float(snapshot.get("price") or 0.0)
        volume_5m = float(snapshot.get("volume_5m") or 0.0)
        if samples:
            last = samples[-1]
            last_ts = int(float(last.get("ts") or 0))
            last_price = float(last.get("price") or 0.0)
            if last_ts == ts and abs(last_price - price) <= 1e-18:
                return
        samples.append({"ts": ts, "price": price, "volume_5m": volume_5m})
        candidate["samples"] = samples[-80:]

    def abnormal_kline_reason(self, candidate: dict[str, Any]) -> tuple[bool, str]:
        if not self.settings.abnormal_kline_enabled:
            return False, ""

        samples = [sample for sample in candidate.get("samples", []) if float(sample.get("price") or 0.0) > 0]
        if len(samples) < self.settings.abnormal_kline_min_samples:
            return False, ""

        elapsed = int(samples[-1]["ts"]) - int(samples[0]["ts"])
        if elapsed < self.settings.abnormal_kline_min_seconds:
            return False, ""

        first_price = max(float(samples[0]["price"]), 0.000000001)
        last_price = float(samples[-1]["price"])
        highest_price = max(float(sample["price"]) for sample in samples)
        gain_pct = (last_price - first_price) / first_price
        max_drawdown_pct = (highest_price - last_price) / max(highest_price, 0.000000001)
        moves = [float(samples[i]["price"]) - float(samples[i - 1]["price"]) for i in range(1, len(samples))]
        down_move_ratio = len([move for move in moves if move < 0]) / max(len(moves), 1)
        flat_or_up_ratio = len([move for move in moves if move >= 0]) / max(len(moves), 1)
        volume_5m = max(float(sample.get("volume_5m") or 0.0) for sample in samples)
        gain_to_volume = gain_pct / max(volume_5m, 1.0)

        looks_like_drawn_line = (
            gain_pct >= self.settings.abnormal_kline_min_gain_pct
            and max_drawdown_pct <= self.settings.abnormal_kline_max_drawdown_pct
            and down_move_ratio <= self.settings.abnormal_kline_max_down_move_ratio
            and flat_or_up_ratio >= 0.85
        )
        low_volume_rise = (
            volume_5m <= self.settings.abnormal_kline_max_volume_5m_usd
            or gain_to_volume >= self.settings.abnormal_kline_min_gain_to_volume
        )
        if looks_like_drawn_line and low_volume_rise:
            return (
                True,
                (
                    "寮傚父K绾? 浣庢垚浜ゆ満姊版媺鍗?"
                    f"gain={gain_pct*100:.1f}%, drawdown={max_drawdown_pct*100:.1f}%, "
                    f"down_moves={down_move_ratio*100:.0f}%, volume5m=${volume_5m:.0f}"
                ),
            )
        return False, ""

    def progress_from_liquidity(self, liquidity_usd: float) -> str:
        progress_value = int(min(max(liquidity_usd / 690, 0), 100))
        return f"{progress_value}%"

    def watch_update_reason(
        self,
        scan: dict[str, Any],
        elapsed: int,
        price_change_pct: float,
        liquidity_ratio: float,
    ) -> str:
        return (
            f"瑙傚療涓?{elapsed}s: move={price_change_pct*100:.1f}%, "
            f"liq={liquidity_ratio:.2f}x, quality={float(scan.get('launch_quality_score') or 0):.0f}, "
            f"bundle={self.scorer.bundle_risk_label(float(scan.get('bundle_risk_score') or 0))}, 绛夊緟纭淇″彿"
        )

    def watch_confirmed_reason(
        self,
        elapsed: int,
        price_change_pct: float,
        drawdown_pct: float,
        liquidity_ratio: float,
    ) -> str:
        return (
            f"瑙傚療纭涔板叆: watch={elapsed}s, move={price_change_pct*100:.1f}%, "
            f"drawdown={drawdown_pct*100:.1f}%, liq={liquidity_ratio:.2f}x"
        )
