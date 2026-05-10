from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from copy import deepcopy
from typing import Optional, Tuple

from .db import Database
from .execution import TradeExecutor
from .market_data import MarketDataClient, build_scan_record
from .news import NewsClient, event_match_score
from .settings import Settings

logger = logging.getLogger(__name__)


class TradingEngine:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        market_data: MarketDataClient,
        executor: TradeExecutor,
        news_client: Optional[NewsClient] = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.market_data = market_data
        self.executor = executor
        self.news_client = news_client
        self.last_buy_ts = 0.0
        self.last_metadata_refresh_ts = 0.0
        self.holder_enrichment_semaphore = threading.BoundedSemaphore(4)
        self.strategy_state = self.database.get_strategy_state() or self._default_strategy_state("初始化")
        self.radar_pool = {}
        self.watchlist = {}

    def get_watchlist_status(self) -> list[dict]:
        now = int(time.time())
        items = []
        for addr, candidate in list(self.watchlist.items()):
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
                    "watch_age": self._format_duration(elapsed),
                    "token_age": self._token_age_text(scan),
                    "initial_price": initial_price,
                    "latest_price": latest_price,
                    "price_change_pct": price_change_pct,
                    "drawdown_pct": drawdown_pct,
                    "initial_liquidity": initial_liquidity,
                    "latest_liquidity": latest_liquidity,
                    "liquidity_ratio": liquidity_ratio,
                    "progress": scan["progress"],
                    "dev_buy": scan["dev_buy"],
                    "launch_quality_score": float(scan.get("launch_quality_score") or 0),
                    "launch_signals": scan.get("launch_signals") or [],
                    "holder_count_estimate": float(scan.get("holder_count_estimate") or 0),
                    "top10_owner_pct": float(scan.get("top10_owner_pct") or 0),
                    "top20_owner_pct": float(scan.get("top20_owner_pct") or 0),
                    "largest_owner_pct": float(scan.get("largest_owner_pct") or 0),
                    "bundle_risk_score": float(scan.get("bundle_risk_score") or 0),
                    "bundle_risk_label": "待查" if float(scan.get("holder_count_estimate") or 0) <= 0 else self._bundle_risk_label(float(scan.get("bundle_risk_score") or 0)),
                    "last_update_ts": int(candidate.get("last_update_ts", candidate["watch_started_ts"])),
                    "last_update_age": self._format_duration(max(now - int(candidate.get("last_update_ts", candidate["watch_started_ts"])), 0)),
                    "last_snapshot_age": self._format_duration(max(now - int(candidate.get("last_snapshot_ts", 0)), 0)) if int(candidate.get("last_snapshot_ts", 0)) > 0 else "-",
                    "last_snapshot_source": candidate.get("last_snapshot_source", "-"),
                    "snapshot_miss_count": int(candidate.get("snapshot_miss_count", 0)),
                    "status_reason": self._watch_status_reason_with_snapshot(candidate, elapsed, price_change_pct, drawdown_pct, liquidity_ratio),
                    "dex_url": scan["dex_url"],
                }
            )
        items.sort(key=lambda item: item["watch_seconds"], reverse=True)
        return items

    async def run(self) -> None:
        await asyncio.gather(
            self.monitor_news_events(),
            self.monitor_strategy_optimizer(),
            self.monitor_new_tokens(),
            self.monitor_scan_holder_backfill(),
            self.monitor_radar_pool(),
            self.monitor_watchlist(),
            self.monitor_open_positions(),
        )

    async def monitor_news_events(self) -> None:
        while True:
            try:
                if self.settings.news_enabled and self.news_client is not None:
                    events = await asyncio.to_thread(self.news_client.fetch_events)
                    stored = self.database.record_news_events(events)
                    if stored:
                        logger.info("Updated news event pool with %s changes", stored)
            except Exception:
                logger.exception("Failed to refresh news events")
            await asyncio.sleep(self.settings.news_poll_seconds)

    async def monitor_strategy_optimizer(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self._refresh_strategy_state)
            except Exception:
                logger.exception("Failed to optimize strategy state")
            await asyncio.sleep(30)

    async def monitor_new_tokens(self) -> None:
        async for payload in self.market_data.subscribe_new_tokens():
            try:
                scan = build_scan_record(payload, self.settings.sol_price_usd)
                if not scan["addr"]:
                    continue
                self._enrich_scan_metadata(scan, payload)
                scan = self._apply_narrative_overlay(scan, payload)
                scan["score"] = self._market_quality_score(scan)
                self.database.record_scan(scan)
                asyncio.create_task(self._record_holder_enriched_scan(deepcopy(scan)))
                self._track_radar_candidate(scan)
                self._maybe_add_to_watchlist(scan)
                if self._instant_probe_ok(scan):
                    asyncio.create_task(self._maybe_open_position(deepcopy(scan)))
            except Exception:
                logger.exception("Failed to process token payload")

    async def monitor_scan_holder_backfill(self) -> None:
        while True:
            try:
                pending = self.database.pending_holder_scans(
                    limit=10,
                    max_age_seconds=max(self.settings.max_token_age_seconds * 2, 420),
                )
                for scan in pending:
                    holder_metrics = await asyncio.to_thread(self.market_data.fetch_holder_metrics, scan["addr"])
                    if not holder_metrics or float(holder_metrics.get("holder_count_estimate") or 0.0) <= 0:
                        continue
                    scan.update(holder_metrics)
                    bundle_risk_score = self._bundle_risk_score(scan)
                    scan["bundle_risk_score"] = bundle_risk_score
                    score = self._market_quality_score(scan)
                    self.database.update_scan_holder_metrics(scan["addr"], holder_metrics, score, bundle_risk_score)
                    logger.info(
                        "Backfilled holder metrics for %s: holders=%s top10=%.1f%% risk=%s",
                        scan.get("symbol", "-"),
                        int(float(holder_metrics.get("holder_count_estimate") or 0)),
                        float(holder_metrics.get("top10_owner_pct") or 0.0) * 100,
                        self._bundle_risk_label(bundle_risk_score),
                    )
            except Exception:
                logger.exception("Failed to backfill scan holder metrics")
            await asyncio.sleep(6)

    async def monitor_radar_pool(self) -> None:
        while True:
            try:
                self._prune_radar_pool()
                await self._sync_realtime_tokens()
                addresses = [addr for addr in self.radar_pool.keys() if addr not in self.watchlist and not self.database.has_position(addr)]
                await self._refresh_unknown_metadata()
                if addresses:
                    for addr in addresses:
                        snapshot = self.market_data.get_realtime_snapshot(addr, max_age_seconds=max(self.settings.radar_poll_seconds * 3, 6))
                        if snapshot:
                            await self._refresh_candidate_holder_metrics(addr, self.radar_pool.get(addr))
                            self._process_radar_candidate(addr, snapshot)
            except Exception:
                logger.exception("Failed to refresh radar pool")
            await asyncio.sleep(self.settings.radar_poll_seconds)

    async def monitor_watchlist(self) -> None:
        while True:
            try:
                await self._sync_realtime_tokens()
                self._trim_watchlist_to_limit()
                if not self.watchlist:
                    await asyncio.sleep(self.settings.observation_poll_seconds)
                    continue
                snapshots = self.market_data.fetch_snapshots(list(self.watchlist.keys()))
                for addr in list(self.watchlist.keys()):
                    snapshot = snapshots.get(addr)
                    if snapshot is None:
                        self._mark_watch_snapshot_pending(addr)
                        candidate = self.watchlist.get(addr)
                        if candidate and int(candidate.get("snapshot_miss_count", 0)) >= 8:
                            self._reject_watch_candidate(addr, candidate["scan"], "连续无实时成交快照，释放观察位")
                        continue
                    await self._process_watch_candidate(addr, snapshot)
            except Exception:
                logger.exception("Failed to refresh watchlist")
            await asyncio.sleep(self.settings.observation_poll_seconds)

    async def _maybe_open_position(self, scan: dict) -> None:
        wallet = self.database.wallet()
        allowed, reason = self._should_open_position(scan, wallet)
        if not allowed:
            logger.debug("Skipped %s: %s", scan["symbol"], reason)
            return
        position_size_usd = self._position_size_usd(scan, wallet)
        if position_size_usd <= 0:
            return

        result = self.executor.buy(scan["addr"], position_size_usd)
        if not result.executed:
            logger.warning("Buy skipped for %s: %s", scan["symbol"], result.reason)
            return

        effective_buy_price = self._paper_adjusted_buy_price(scan["price"], result.mode)
        self.database.open_position(
            addr=scan["addr"],
            symbol=scan["symbol"],
            price=effective_buy_price,
            amount_usd=position_size_usd,
            mode=result.mode,
            entry_tx=result.tx_hash,
            entry_liquidity=float(scan["liquidity"]),
            metrics={
                "entry_reason": self._entry_reason(scan, position_size_usd),
                "entry_score": scan["score"],
                "entry_dev_buy": scan["dev_buy"],
                "entry_liquidity": scan["liquidity"],
                "entry_token_age": self._token_age_text(scan),
                "raw_market_price": scan["price"],
                "effective_entry_price": effective_buy_price,
                "narrative_score": scan.get("narrative_score", 0),
                "narrative_tags": scan.get("narrative_tags", []),
                "scalp_override": bool(scan.get("scalp_override")),
                "scalp_reason": scan.get("scalp_reason", ""),
            },
        )
        self.database.update_wallet_for_buy(position_size_usd)
        self.database.record_opportunity(
            {
                "type": "signal",
                "symbol": scan["symbol"],
                "score": scan["score"],
                "reason": (
                    f"score={scan['score']:.0f}, age={self._token_age_text(scan)}, "
                    f"dev_buy={scan['dev_buy']:.2f} SOL, liq=${scan['liquidity']:.0f}, "
                    f"narrative={scan.get('narrative_label', '-')}, "
                    f"bundle={self._bundle_risk_label(float(scan.get('bundle_risk_score') or 0))}, size=${position_size_usd:.2f}"
                ),
                "addr": scan["addr"],
                "liq": scan["liquidity"],
                "vol": scan["dev_buy"],
                "dex_url": scan["dex_url"],
                "scan_time": scan["scan_time"] or "",
                "socials": scan["socials"],
                "progress": scan["progress"],
            }
        )
        self.last_buy_ts = time.time()
        logger.info(
            "Opened %s position for %s | score=%.0f size=$%.2f buy_price=%.10f",
            result.mode,
            scan["symbol"],
            scan["score"],
            position_size_usd,
            effective_buy_price,
        )

    def _maybe_add_to_watchlist(self, scan: dict, record_ignored: bool = True) -> None:
        if scan["addr"] in self.watchlist or self.database.has_position(scan["addr"]):
            return
        if len(self.watchlist) >= self.settings.max_watchlist_size:
            if not self._prune_watchlist_for_new_candidate(scan):
                self.database.record_opportunity(
                    {
                        "type": "rejected",
                        "symbol": scan["symbol"],
                        "score": scan["score"],
                        "reason": "观察池已满且候选质量不足",
                        "addr": scan["addr"],
                        "liq": scan["liquidity"],
                        "vol": scan["dev_buy"],
                        "dex_url": scan["dex_url"],
                        "scan_time": scan["scan_time"] or "",
                        "socials": scan["socials"],
                        "progress": scan["progress"],
                    }
                )
                return
        allowed, reason = self._should_watch(scan)
        if not allowed:
            logger.debug("Ignored %s for watchlist: %s", scan["symbol"], reason)
            if record_ignored and reason.startswith("low launch quality"):
                self.database.record_opportunity(
                    {
                        "type": "ignored",
                        "symbol": scan["symbol"],
                        "score": scan["score"],
                        "reason": reason,
                        "addr": scan["addr"],
                        "liq": scan["liquidity"],
                        "vol": scan["dev_buy"],
                        "dex_url": scan["dex_url"],
                        "scan_time": scan["scan_time"] or "",
                        "socials": scan["socials"],
                        "progress": scan["progress"],
                    }
                )
            return
        self.watchlist[scan["addr"]] = {
            "scan": deepcopy(scan),
            "watch_started_ts": int(time.time()),
            "last_update_ts": int(time.time()),
            "last_snapshot_ts": 0,
            "last_snapshot_source": "-",
            "snapshot_miss_count": 0,
            "initial_price": float(scan["price"]),
            "highest_price": float(scan["price"]),
            "latest_price": float(scan["price"]),
            "initial_liquidity": float(scan["liquidity"]),
            "latest_liquidity": float(scan["liquidity"]),
            "samples": deepcopy((self.radar_pool.get(scan["addr"]) or {}).get("samples") or []),
        }
        if not self.watchlist[scan["addr"]]["samples"]:
            self.watchlist[scan["addr"]]["samples"] = [{"ts": int(time.time()), "price": float(scan["price"]), "volume_5m": 0.0}]
        self.database.record_opportunity(
            {
                "type": "watch",
                "symbol": scan["symbol"],
                "score": scan["score"],
                "reason": (
                    f"进入观察池: age={self._token_age_text(scan)}, score={scan['score']:.0f}, "
                    f"quality={float(scan.get('launch_quality_score') or 0):.0f}, "
                    f"narrative={scan.get('narrative_label', '-')}, "
                    f"dev={scan['dev_buy']:.2f} SOL, liq=${scan['liquidity']:.0f}, top10={float(scan.get('top10_owner_pct') or 0)*100:.1f}%"
                ),
                "addr": scan["addr"],
                "liq": scan["liquidity"],
                "vol": scan["dev_buy"],
                "dex_url": scan["dex_url"],
                "scan_time": scan["scan_time"] or "",
                "socials": scan["socials"],
                "progress": scan["progress"],
            }
        )

    def _track_radar_candidate(self, scan: dict) -> None:
        addr = scan["addr"]
        now = int(time.time())
        self.radar_pool[addr] = {
            "scan": deepcopy(scan),
            "first_seen_ts": now,
            "last_update_ts": now,
            "initial_price": float(scan["price"]),
            "initial_liquidity": float(scan["liquidity"]),
            "highest_price": float(scan["price"]),
            "samples": [{"ts": now, "price": float(scan["price"]), "volume_5m": 0.0}],
        }
        if len(self.radar_pool) > self.settings.max_radar_tracked_tokens:
            oldest = sorted(self.radar_pool.items(), key=lambda item: int(item[1].get("first_seen_ts", 0)))
            for old_addr, _ in oldest[: max(len(self.radar_pool) - self.settings.max_radar_tracked_tokens, 0)]:
                self.radar_pool.pop(old_addr, None)

    def _prune_radar_pool(self) -> None:
        now = int(time.time())
        for addr, candidate in list(self.radar_pool.items()):
            if now - int(candidate.get("first_seen_ts", now)) > self.settings.radar_track_seconds:
                self.radar_pool.pop(addr, None)
            elif addr in self.watchlist or self.database.has_position(addr):
                self.radar_pool.pop(addr, None)

    def _process_radar_candidate(self, addr: str, snapshot: dict) -> None:
        candidate = self.radar_pool.get(addr)
        if not candidate:
            return
        scan = deepcopy(candidate["scan"])
        now = int(time.time())
        elapsed = now - int(candidate["first_seen_ts"])
        current_price = float(snapshot.get("price") or 0.0)
        if current_price <= 0:
            return
        current_liquidity = float(snapshot.get("liquidity_usd") or scan.get("liquidity") or 0.0)
        self._enrich_scan_metadata(scan, snapshot)
        candidate["highest_price"] = max(float(candidate.get("highest_price") or current_price), current_price)
        candidate["last_update_ts"] = now
        self._append_price_sample(candidate, snapshot)
        abnormal, abnormal_reason = self._abnormal_kline_reason(candidate)
        if abnormal:
            self.radar_pool.pop(addr, None)
            self.database.record_opportunity(
                {
                    "type": "rejected",
                    "symbol": scan["symbol"],
                    "score": scan["score"],
                    "reason": abnormal_reason,
                    "addr": scan["addr"],
                    "liq": scan["liquidity"],
                    "vol": scan["dev_buy"],
                    "dex_url": scan["dex_url"],
                    "scan_time": scan["scan_time"] or "",
                    "socials": scan["socials"],
                    "progress": scan["progress"],
                }
            )
            return

        initial_price = max(float(candidate.get("initial_price") or current_price), 0.000000001)
        initial_liquidity = max(float(candidate.get("initial_liquidity") or current_liquidity or 1), 0.000000001)
        price_change_pct = (current_price - initial_price) / initial_price
        liquidity_ratio = current_liquidity / initial_liquidity

        scan["price"] = current_price
        scan["liquidity"] = current_liquidity
        scan["progress"] = self._progress_from_liquidity(current_liquidity)
        scan["realtime_volume_5m"] = float(snapshot.get("volume_5m") or 0.0)
        scan["bundle_risk_score"] = self._bundle_risk_score(scan)
        scan["score"] = self._market_quality_score(scan, price_change_pct, liquidity_ratio, elapsed)
        scan["scan_time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        quality_score, signals = self._launch_quality(scan)
        scan["launch_quality_score"] = quality_score
        scan["launch_signals"] = signals
        candidate["scan"] = deepcopy(scan)
        self.database.record_scan(scan)
        self._maybe_add_to_watchlist(scan, record_ignored=False)
        if self._instant_probe_ok(scan):
            asyncio.create_task(self._maybe_open_position(deepcopy(scan)))

    async def _process_watch_candidate(self, addr: str, snapshot: dict) -> None:
        candidate = self.watchlist.get(addr)
        if not candidate:
            return

        scan = candidate["scan"]
        self._enrich_scan_metadata(scan, snapshot)
        now = int(time.time())
        elapsed = now - int(candidate["watch_started_ts"])
        current_price = float(snapshot["price"])
        current_liquidity = float(snapshot["liquidity_usd"] or candidate["latest_liquidity"])
        candidate["latest_price"] = current_price
        candidate["latest_liquidity"] = current_liquidity
        candidate["highest_price"] = max(float(candidate["highest_price"]), current_price)
        candidate["last_update_ts"] = now
        candidate["last_snapshot_ts"] = now
        candidate["last_snapshot_source"] = str(snapshot.get("source") or "unknown")
        candidate["snapshot_miss_count"] = 0
        self._append_price_sample(candidate, snapshot)
        abnormal, abnormal_reason = self._abnormal_kline_reason(candidate)
        if abnormal:
            self._reject_watch_candidate(addr, scan, abnormal_reason)
            return
        holder_metrics = await asyncio.to_thread(self.market_data.fetch_holder_metrics, addr)

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
        confirmed_scan["progress"] = self._progress_from_liquidity(current_liquidity)
        confirmed_scan.update(holder_metrics)
        confirmed_scan["bundle_risk_score"] = self._bundle_risk_score(confirmed_scan)
        confirmed_scan["score"] = self._market_quality_score(confirmed_scan, price_change_pct, liquidity_ratio, elapsed)
        candidate["scan"] = deepcopy(confirmed_scan)
        self.database.record_scan(confirmed_scan)

        if drawdown_pct > self.settings.max_observation_drawdown_pct:
            self._reject_watch_candidate(addr, confirmed_scan, f"观察期回撤过大 {drawdown_pct*100:.1f}%")
            return
        if elapsed > self.settings.max_observation_seconds:
            self._reject_watch_candidate(addr, confirmed_scan, "观察期超时仍未满足入场确认")
            return
        if elapsed < self.settings.min_observation_seconds:
            self._update_watch_reason(confirmed_scan, elapsed, price_change_pct, liquidity_ratio)
            return
        scalp_override, scalp_reason = self._scalp_override_ok(
            confirmed_scan,
            price_change_pct,
            drawdown_pct,
            liquidity_ratio,
            elapsed,
        )
        confirmed_scan["scalp_override"] = scalp_override
        holder_allowed, holder_reason = self._holder_ok(confirmed_scan)
        if not holder_allowed and not scalp_override:
            self._reject_watch_candidate(addr, confirmed_scan, holder_reason)
            return
        if float(confirmed_scan.get("bundle_risk_score") or 0) >= 0.85 and not scalp_override:
            self._reject_watch_candidate(addr, confirmed_scan, "疑似 bundle 风险过高")
            return
        if liquidity_ratio < self.settings.min_observation_liquidity_ratio:
            self._reject_watch_candidate(addr, confirmed_scan, f"观察期流动性衰减至 {liquidity_ratio:.2f}x")
            return
        if price_change_pct < self.settings.min_observation_price_change_pct:
            self._update_watch_reason(confirmed_scan, elapsed, price_change_pct, liquidity_ratio)
            return
        if scalp_override:
            confirmed_scan["score"] = max(float(confirmed_scan["score"]), self.settings.min_score_to_buy)
            confirmed_scan["scalp_reason"] = scalp_reason

        await self._maybe_open_position(confirmed_scan)
        if self.database.has_position(addr):
            self.watchlist.pop(addr, None)
            self.database.record_opportunity(
                {
                    "type": "confirmed",
                    "symbol": confirmed_scan["symbol"],
                    "score": confirmed_scan["score"],
                    "reason": (
                        f"观察确认买入: watch={elapsed}s, move={price_change_pct*100:.1f}%, "
                        f"drawdown={drawdown_pct*100:.1f}%, liq={liquidity_ratio:.2f}x"
                    ),
                    "addr": confirmed_scan["addr"],
                    "liq": confirmed_scan["liquidity"],
                    "vol": confirmed_scan["dev_buy"],
                    "dex_url": confirmed_scan["dex_url"],
                    "scan_time": confirmed_scan["scan_time"] or "",
                    "socials": confirmed_scan["socials"],
                    "progress": confirmed_scan["progress"],
                }
            )

    def _update_watch_reason(self, scan: dict, elapsed: int, price_change_pct: float, liquidity_ratio: float) -> None:
        self.database.record_opportunity(
            {
                "type": "watch",
                "symbol": scan["symbol"],
                "score": scan["score"],
                "reason": (
                    f"观察中 {elapsed}s: move={price_change_pct*100:.1f}%, "
                    f"liq={liquidity_ratio:.2f}x, quality={float(scan.get('launch_quality_score') or 0):.0f}, "
                    f"bundle={self._bundle_risk_label(float(scan.get('bundle_risk_score') or 0))}, 等待确认信号"
                ),
                "addr": scan["addr"],
                "liq": scan["liquidity"],
                "vol": scan["dev_buy"],
                "dex_url": scan["dex_url"],
                "scan_time": scan["scan_time"] or "",
                "socials": scan["socials"],
                "progress": scan["progress"],
            }
        )

    def _reject_watch_candidate(self, addr: str, scan: dict, reason: str) -> None:
        self.watchlist.pop(addr, None)
        self.database.record_opportunity(
            {
                "type": "rejected",
                "symbol": scan["symbol"],
                "score": scan["score"],
                "reason": reason,
                "addr": scan["addr"],
                "liq": scan["liquidity"],
                "vol": scan["dev_buy"],
                "dex_url": scan["dex_url"],
                "scan_time": scan["scan_time"] or "",
                "socials": scan["socials"],
                "progress": scan["progress"],
            }
        )

    def _should_watch(self, scan: dict) -> tuple[bool, str]:
        quality_score, signals = self._launch_quality(scan)
        scan["launch_quality_score"] = quality_score
        scan["launch_signals"] = signals
        if not self._fast_track_launch(scan, quality_score, signals):
            if quality_score < self.settings.min_launch_quality_score or len(signals) < self.settings.min_launch_signal_count:
                return False, f"low launch quality {quality_score:.0f}: {','.join(signals) or 'no strong signal'}"
        if scan["score"] < self.settings.min_watch_score:
            return False, "watch score too low"
        if float(scan.get("dev_buy") or 0.0) < 0.05 and float(scan.get("narrative_score") or 0.0) <= 0:
            return False, "no launch buy pressure"
        if scan["liquidity"] < self.settings.min_watch_liquidity_usd:
            return False, "initial liquidity too low"
        if scan["liquidity"] > self.settings.max_liquidity_usd:
            return False, "initial liquidity too high"
        created_ts = int(scan.get("created_ts") or 0)
        if created_ts > 0:
            age_seconds = max(int(time.time()) - created_ts, 0)
            if age_seconds > self.settings.max_token_age_seconds:
                return False, "token too old for watch window"
        return True, "ok"

    def _launch_quality(self, scan: dict) -> tuple[float, list[str]]:
        score = 0.0
        signals = []
        symbol = str(scan.get("symbol") or "")
        dev_buy = float(scan.get("dev_buy") or 0.0)
        liquidity = float(scan.get("liquidity") or 0.0)
        narrative_score = float(scan.get("narrative_score") or 0.0)

        if self._symbol_quality_ok(symbol):
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
        if self._has_socials(scan):
            score += 12.0
            signals.append("socials")
        if float(scan.get("score") or 0.0) >= self.settings.min_fast_track_score:
            score += 10.0
            signals.append("high_base_score")
        return min(score, 100.0), signals

    def _fast_track_launch(self, scan: dict, quality_score: float, signals: list[str]) -> bool:
        return (
            float(scan.get("narrative_score") or 0.0) >= 12.0
            or quality_score >= self.settings.min_fast_track_score
            or ("dev_buy_ok" in signals and "early_liquidity_ok" in signals and "clean_symbol" in signals)
        )

    def _symbol_quality_ok(self, symbol: str) -> bool:
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

    def _has_socials(self, scan: dict) -> bool:
        raw = scan.get("socials") or "{}"
        return any(key in raw and f'"{key}": null' not in raw for key in ("twitter", "telegram", "website"))

    def _trim_watchlist_to_limit(self) -> None:
        max_size = max(int(self.settings.max_watchlist_size), 0)
        overflow = len(self.watchlist) - max_size
        if overflow <= 0:
            return

        ranked = sorted(
            self.watchlist.items(),
            key=lambda item: (
                self._launch_quality(item[1]["scan"])[0],
                float(item[1]["scan"].get("score") or 0.0),
                int(item[1].get("last_snapshot_ts") or 0),
                int(item[1].get("watch_started_ts") or 0),
            ),
        )
        for addr, candidate in ranked[:overflow]:
            self._reject_watch_candidate(addr, candidate["scan"], "观察池超过容量限制，清理低质量候选")

    def _prune_watchlist_for_new_candidate(self, scan: dict) -> bool:
        if not self.watchlist:
            return True
        new_quality, _ = self._launch_quality(scan)
        weakest_addr = None
        weakest_quality = 101.0
        for addr, candidate in self.watchlist.items():
            quality, _ = self._launch_quality(candidate["scan"])
            if quality < weakest_quality:
                weakest_quality = quality
                weakest_addr = addr
        if weakest_addr and new_quality > weakest_quality + 10:
            old_scan = self.watchlist.pop(weakest_addr)["scan"]
            self._reject_watch_candidate(weakest_addr, old_scan, f"观察池容量限制，替换为更高质量候选 {scan['symbol']}")
            return True
        return False

    def _holder_ok(self, scan: dict) -> tuple[bool, str]:
        top10_owner_pct = float(scan.get("top10_owner_pct") or 0)
        top20_owner_pct = float(scan.get("top20_owner_pct") or 0)
        holder_count = int(float(scan.get("holder_count_estimate") or 0))
        if holder_count > 0 and holder_count < self.settings.min_holder_count:
            return False, f"holder 数过少 {holder_count}"
        if top10_owner_pct > self.settings.max_top10_owner_pct:
            return False, f"top10 持仓过高 {top10_owner_pct*100:.1f}%"
        if top20_owner_pct > self.settings.max_top20_owner_pct:
            return False, f"top20 持仓过高 {top20_owner_pct*100:.1f}%"
        return True, "ok"

    def _default_strategy_state(self, reason: str) -> dict:
        return {
            "mode": "balanced",
            "score_offset": 0.0,
            "position_multiplier": 1.0,
            "cooldown_multiplier": 1.0,
            "max_open_positions": self.settings.max_open_positions,
            "instant_probe_enabled": True,
            "reason": reason,
            "metrics": {},
            "updated_ts": int(time.time()),
            "cooldown_until_ts": 0,
        }

    def _refresh_strategy_state(self) -> None:
        sells = self.database.recent_sell_trades(limit=30)
        if len(sells) < 4:
            state = self._default_strategy_state("样本不足，保持均衡模式")
            self.strategy_state = state
            self.database.save_strategy_state(state)
            return

        recent = sells[:12]
        pnl_values = [float(row.get("pnl_pct") or 0.0) for row in recent]
        wins = [pnl for pnl in pnl_values if pnl > 0]
        losses = [pnl for pnl in pnl_values if pnl <= 0]
        avg = sum(pnl_values) / len(pnl_values)
        win_rate = len(wins) / len(pnl_values)
        worst = min(pnl_values)
        best = max(pnl_values)
        loss_streak = 0
        for pnl in pnl_values:
            if pnl <= 0:
                loss_streak += 1
            else:
                break
        metrics = {
            "sample_size": len(pnl_values),
            "win_rate": win_rate,
            "avg_trade_pct": avg,
            "worst_trade_pct": worst,
            "best_trade_pct": best,
            "loss_streak": loss_streak,
            "profit_factor": (sum(wins) / abs(sum(losses))) if losses and abs(sum(losses)) > 0 else (999.0 if wins else 0.0),
        }

        now = int(time.time())
        mode = "balanced"
        score_offset = 0.0
        position_multiplier = 1.0
        cooldown_multiplier = 1.0
        max_open_positions = self.settings.max_open_positions
        instant_probe_enabled = True
        cooldown_until_ts = int((self.strategy_state or {}).get("cooldown_until_ts") or 0)
        reason = "近期表现正常，保持均衡"

        if loss_streak >= 3 or avg <= -0.16 or worst <= -0.55:
            mode = "cooldown"
            score_offset = 12.0
            position_multiplier = 0.35
            cooldown_multiplier = 3.0
            max_open_positions = 1
            instant_probe_enabled = False
            cooldown_until_ts = max(cooldown_until_ts, now + 180)
            reason = "近期连续亏损或单笔大亏，进入冷却防守"
        elif win_rate < 0.28 or avg <= -0.07:
            mode = "defensive"
            score_offset = 8.0
            position_multiplier = 0.55
            cooldown_multiplier = 2.0
            max_open_positions = max(1, min(self.settings.max_open_positions, 2))
            instant_probe_enabled = False
            reason = "胜率/均值偏弱，收紧入场并降低仓位"
        elif win_rate >= 0.45 and avg > 0.04 and worst > -0.25:
            mode = "aggressive"
            score_offset = -3.0
            position_multiplier = 1.15
            cooldown_multiplier = 0.6
            max_open_positions = self.settings.max_open_positions
            instant_probe_enabled = True
            cooldown_until_ts = 0
            reason = "近期胜率和均值改善，放宽频率"
        elif now < cooldown_until_ts:
            mode = "cooldown"
            score_offset = 12.0
            position_multiplier = 0.35
            cooldown_multiplier = 3.0
            max_open_positions = 1
            instant_probe_enabled = False
            reason = "冷却期尚未结束"

        state = {
            "mode": mode,
            "score_offset": score_offset,
            "position_multiplier": position_multiplier,
            "cooldown_multiplier": cooldown_multiplier,
            "max_open_positions": max_open_positions,
            "instant_probe_enabled": instant_probe_enabled,
            "reason": reason,
            "metrics": metrics,
            "updated_ts": now,
            "cooldown_until_ts": cooldown_until_ts if mode == "cooldown" else 0,
        }
        if state.get("mode") != (self.strategy_state or {}).get("mode"):
            logger.info("Strategy mode changed to %s: %s", mode, reason)
        self.strategy_state = state
        self.database.save_strategy_state(state)

    def _adaptive_min_score_to_buy(self) -> float:
        return max(0.0, self.settings.min_score_to_buy + float((self.strategy_state or {}).get("score_offset") or 0.0))

    def _adaptive_position_multiplier(self) -> float:
        return max(float((self.strategy_state or {}).get("position_multiplier") or 1.0), 0.1)

    def _adaptive_cooldown_seconds(self) -> float:
        return max(self.settings.min_buy_interval_seconds * float((self.strategy_state or {}).get("cooldown_multiplier") or 1.0), 1.0)

    def _adaptive_max_open_positions(self) -> int:
        configured = int((self.strategy_state or {}).get("max_open_positions") or self.settings.max_open_positions)
        return max(1, min(configured, self.settings.max_open_positions))

    def _instant_probe_allowed_by_strategy(self) -> bool:
        return bool((self.strategy_state or {}).get("instant_probe_enabled", True))

    def _instant_probe_ok(self, scan: dict) -> bool:
        if not self._instant_probe_allowed_by_strategy():
            return False
        if self.database.has_position(scan["addr"]):
            return False
        quality_score = float(scan.get("launch_quality_score") or 0.0)
        if (self.strategy_state or {}).get("mode") == "defensive" and quality_score < 70:
            return False
        if quality_score < 60:
            return False
        if float(scan.get("score") or 0.0) < self._adaptive_min_score_to_buy():
            return False
        dev_buy = float(scan.get("dev_buy") or 0.0)
        if not (self.settings.min_launch_dev_buy_sol <= dev_buy <= self.settings.max_dev_buy_sol):
            return False
        liquidity = float(scan.get("liquidity") or 0.0)
        if liquidity < self.settings.min_liquidity_usd or liquidity > self.settings.max_liquidity_usd:
            return False
        if float(scan.get("bundle_risk_score") or 0.0) >= 0.85:
            return False
        return True

    def _scalp_override_ok(
        self,
        scan: dict,
        price_change_pct: float,
        drawdown_pct: float,
        liquidity_ratio: float,
        elapsed: int,
    ) -> tuple[bool, str]:
        if not self.settings.scalp_override_enabled:
            return False, "scalp disabled"
        if (self.strategy_state or {}).get("mode") in {"cooldown", "defensive"}:
            return False, f"scalp disabled by adaptive mode {(self.strategy_state or {}).get('mode')}"
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

    async def monitor_open_positions(self) -> None:
        while True:
            try:
                await self._sync_realtime_tokens()
                positions = self.database.open_positions()
                price_map = self.market_data.fetch_snapshots([position["addr"] for position in positions])
                for position in positions:
                    await self._process_position(position, price_map)
            except Exception:
                logger.exception("Failed to refresh positions")
            await asyncio.sleep(self.settings.pricing_poll_seconds)

    async def _process_position(self, position: dict, price_map: dict[str, dict]) -> None:
        addr = position["addr"]
        snapshot = price_map.get(addr)
        if not snapshot:
            return
        current_price = float(snapshot.get("price") or 0)
        if current_price <= 0:
            return

        sell_price = self._paper_adjusted_sell_price(current_price, position["mode"])
        buy_price = float(position["buy_price"])
        max_price = max(float(position["max_price"]), sell_price)
        pnl_pct = (sell_price - buy_price) / buy_price
        self.database.update_position_price(
            addr,
            sell_price,
            max_price,
            source=str(snapshot.get("source") or "unknown"),
            updated_ts=int(float(snapshot.get("timestamp") or time.time())),
        )
        hold_seconds = self._hold_seconds(position["buy_time"])
        holder_exit_reason = await self._holder_risk_exit_reason(position, pnl_pct, hold_seconds)
        if holder_exit_reason:
            exit_reason = "holder_risk_cut"
            exit_context = {"holder_risk_reason": holder_exit_reason, "position_age_seconds": hold_seconds}
            sell_fraction = 1.0
        else:
            exit_reason, exit_context, sell_fraction = self._dynamic_exit_signal(position, snapshot, sell_price, max_price, pnl_pct, hold_seconds)
        if not exit_reason:
            return

        amount_usd = float(position["amount_usd"])
        sell_amount_usd = round(amount_usd * sell_fraction, 2)
        if amount_usd - sell_amount_usd <= self.settings.moonbag_min_usd:
            sell_amount_usd = amount_usd
            sell_fraction = 1.0
        if sell_amount_usd <= 0:
            return
        result = self.executor.sell(addr, sell_amount_usd)
        if not result.executed:
            logger.warning("Sell skipped for %s: %s", position["symbol"], result.reason)
            return

        proceeds_usd = self._sale_proceeds(sell_amount_usd, pnl_pct, position["mode"])
        self.database.update_wallet_for_sell(sell_amount_usd, proceeds_usd)
        metrics = {
            "gross_market_price": current_price,
            "effective_exit_price": sell_price,
            "hold_seconds": hold_seconds,
            "exit_reason": exit_reason,
            "sell_fraction": sell_fraction,
            **exit_context,
        }
        remaining_amount_usd = max(amount_usd - sell_amount_usd, 0.0)
        if remaining_amount_usd <= self.settings.moonbag_min_usd or sell_fraction >= 0.999:
            self.database.close_position(
                addr=addr,
                symbol=position["symbol"],
                exit_price=sell_price,
                amount_usd=sell_amount_usd,
                pnl_pct=pnl_pct,
                tx_hash=result.tx_hash,
                metrics=metrics,
            )
        else:
            self.database.reduce_position(
                addr=addr,
                symbol=position["symbol"],
                exit_price=sell_price,
                sold_amount_usd=sell_amount_usd,
                remaining_amount_usd=remaining_amount_usd,
                pnl_pct=pnl_pct,
                tx_hash=result.tx_hash,
                metrics=metrics,
            )
        logger.info(
            "Sold %.0f%% of %s position for %s at %.2f%% reason=%s",
            sell_fraction * 100,
            result.mode,
            position["symbol"],
            pnl_pct * 100,
            exit_reason,
        )

    def _should_open_position(self, scan: dict, wallet: dict) -> tuple[bool, str]:
        if int((self.strategy_state or {}).get("cooldown_until_ts") or 0) > int(time.time()):
            return False, "adaptive cooldown active"
        min_score = self._adaptive_min_score_to_buy()
        if scan["score"] < min_score:
            return False, f"score below adaptive threshold {min_score:.0f}"
        if not (self.settings.min_dev_buy_sol <= scan["dev_buy"] <= self.settings.max_dev_buy_sol):
            return False, "developer buy outside target band"
        if self.database.has_position(scan["addr"]):
            return False, "position already open"
        if self.database.open_position_count() >= self._adaptive_max_open_positions():
            return False, "portfolio already full"
        if scan["liquidity"] < self.settings.min_liquidity_usd:
            return False, "liquidity too low"
        if scan["liquidity"] > self.settings.max_liquidity_usd:
            return False, "liquidity too mature"
        created_ts = int(scan.get("created_ts") or 0)
        if created_ts > 0:
            age_seconds = max(int(time.time()) - created_ts, 0)
            if age_seconds > self.settings.max_token_age_seconds:
                return False, "token too old for entry window"
        if time.time() - self.last_buy_ts < self._adaptive_cooldown_seconds():
            return False, "global buy cooldown active"
        if float(wallet.get("current_balance", 0)) < self.settings.position_size_usd:
            return False, "insufficient balance"
        return True, "ok"

    async def _holder_risk_exit_reason(self, position: dict, pnl_pct: float, hold_seconds: int) -> Optional[str]:
        if hold_seconds < 3 or pnl_pct >= 0.08:
            return None
        holder_metrics = await asyncio.to_thread(self.market_data.fetch_holder_metrics, position["addr"])
        holders = int(float(holder_metrics.get("holder_count_estimate") or 0))
        if holders <= 0:
            return None
        scan = {
            "holder_count_estimate": holders,
            "top10_owner_pct": float(holder_metrics.get("top10_owner_pct") or 0.0),
            "top20_owner_pct": float(holder_metrics.get("top20_owner_pct") or 0.0),
            "largest_owner_pct": float(holder_metrics.get("largest_owner_pct") or 0.0),
        }
        bundle_risk = self._bundle_risk_score(scan)
        if bundle_risk >= 0.85:
            return (
                f"holders={holders}, top10={scan['top10_owner_pct']*100:.1f}%, "
                f"largest={scan['largest_owner_pct']*100:.1f}%, risk={self._bundle_risk_label(bundle_risk)}"
            )
        return None

    def _position_size_usd(self, scan: dict, wallet: dict) -> float:
        balance = float(wallet.get("current_balance", 0))
        base_size = self.settings.position_size_usd
        score = float(scan["score"])
        if score >= 95:
            multiplier = 1.25
        elif score >= 88:
            multiplier = 1.0
        else:
            multiplier = 0.75
        if scan.get("scalp_override") or float(scan.get("bundle_risk_score") or 0.0) >= 0.75:
            multiplier *= self.settings.scalp_risk_position_multiplier
        score_size = base_size * multiplier
        score_size *= self._adaptive_position_multiplier()
        if float(scan.get("narrative_score") or 0) > 0:
            score_size *= self.settings.narrative_position_multiplier
        wallet_cap = balance * self.settings.max_wallet_exposure_pct
        return round(max(min(score_size, self.settings.max_position_size_usd, wallet_cap, balance), 0), 2)

    def _paper_adjusted_buy_price(self, market_price: float, mode: str) -> float:
        if mode != "paper":
            return market_price
        total_bps = self.settings.paper_buy_slippage_bps + self.settings.paper_fee_bps
        return market_price * (1 + total_bps / 10_000)

    def _paper_adjusted_sell_price(self, market_price: float, mode: str) -> float:
        if mode != "paper":
            return market_price
        total_bps = self.settings.paper_sell_slippage_bps + self.settings.paper_fee_bps
        return market_price * max(1 - total_bps / 10_000, 0.01)

    def _sale_proceeds(self, amount_usd: float, pnl_pct: float, mode: str) -> float:
        proceeds = amount_usd * (1 + pnl_pct)
        return max(proceeds, 0.0)

    def _token_age_text(self, scan: dict) -> str:
        created_ts = int(scan.get("created_ts") or 0)
        if created_ts <= 0:
            return "-"
        age_seconds = max(int(time.time()) - created_ts, 0)
        return self._format_duration(age_seconds)

    def _format_duration(self, age_seconds: int) -> str:
        if age_seconds < 60:
            return f"{age_seconds}s"
        if age_seconds < 3600:
            return f"{age_seconds // 60}m"
        if age_seconds < 86400:
            return f"{age_seconds // 3600}h"
        return f"{age_seconds // 86400}d"

    def _entry_reason(self, scan: dict, position_size_usd: float) -> str:
        return (
            f"score={scan['score']:.0f}, age={self._token_age_text(scan)}, "
            f"dev={scan['dev_buy']:.2f} SOL, liq=${scan['liquidity']:.0f}, "
            f"narrative={scan.get('narrative_label', '-')}, "
            f"top10={float(scan.get('top10_owner_pct') or 0)*100:.1f}%, holders={int(float(scan.get('holder_count_estimate') or 0))}, "
            f"bundle={self._bundle_risk_label(float(scan.get('bundle_risk_score') or 0))}, "
            f"mode={'scalp' if scan.get('scalp_override') else 'normal'}, "
            f"size=${position_size_usd:.2f}"
        )

    def _observation_bonus(self, price_change_pct: float, liquidity_ratio: float, elapsed: int) -> float:
        bonus = 0.0
        bonus += min(max(price_change_pct, 0.0) * 120.0, 12.0)
        bonus += min(max(liquidity_ratio - 1.0, 0.0) * 20.0, 8.0)
        if elapsed >= self.settings.min_observation_seconds:
            bonus += 3.0
        return bonus

    def _radar_momentum_bonus(self, price_change_pct: float, liquidity_ratio: float, elapsed: int) -> float:
        bonus = 0.0
        bonus += min(max(price_change_pct, 0.0) * 80.0, 10.0)
        bonus += min(max(liquidity_ratio - 1.0, 0.0) * 15.0, 6.0)
        if elapsed >= 10 and price_change_pct > 0:
            bonus += 2.0
        return bonus

    def _market_quality_score(self, scan: dict, price_change_pct: float = 0.0, liquidity_ratio: float = 1.0, elapsed: int = 0) -> float:
        score = 0.0
        symbol = str(scan.get("symbol") or "")
        liquidity = float(scan.get("liquidity") or 0.0)
        dev_buy = float(scan.get("dev_buy") or 0.0)
        volume_5m = float(scan.get("realtime_volume_5m") or 0.0)
        holders = float(scan.get("holder_count_estimate") or 0.0)
        top10 = float(scan.get("top10_owner_pct") or 0.0)
        largest = float(scan.get("largest_owner_pct") or 0.0)
        created_ts = int(scan.get("created_ts") or 0)
        age_seconds = max(int(time.time()) - created_ts, 0) if created_ts > 0 else 0

        if self._symbol_quality_ok(symbol):
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
            bundle_risk = self._bundle_risk_score(scan)
            if top10 <= 0.28 and largest <= 0.1:
                score += 8.0
            elif top10 <= 0.42 and largest <= 0.16:
                score += 4.0
            score -= self._bundle_penalty(bundle_risk)
            if bundle_risk >= 0.75:
                score = min(score, 45.0)
            elif bundle_risk >= 0.45:
                score = min(score, 62.0)
        else:
            score -= 4.0
            score = min(score, 52.0)

        return max(min(score, 100.0), 0.0)

    async def _refresh_candidate_holder_metrics(self, addr: str, candidate: Optional[dict]) -> None:
        if not candidate:
            return
        now = int(time.time())
        if now - int(candidate.get("holder_refreshed_ts", 0)) < self.settings.holder_refresh_seconds:
            return
        holder_metrics = await asyncio.to_thread(self.market_data.fetch_holder_metrics, addr)
        if not holder_metrics:
            return
        scan = candidate.get("scan") or {}
        scan.update(holder_metrics)
        scan["bundle_risk_score"] = self._bundle_risk_score(scan)
        candidate["scan"] = scan
        candidate["holder_refreshed_ts"] = now

    async def _record_holder_enriched_scan(self, scan: dict) -> None:
        acquired = await asyncio.to_thread(self.holder_enrichment_semaphore.acquire)
        try:
            holder_metrics = await asyncio.to_thread(self.market_data.fetch_holder_metrics, scan["addr"])
        finally:
            if acquired:
                self.holder_enrichment_semaphore.release()
        if not holder_metrics or float(holder_metrics.get("holder_count_estimate") or 0.0) <= 0:
            return
        scan.update(holder_metrics)
        scan["bundle_risk_score"] = self._bundle_risk_score(scan)
        scan["score"] = self._market_quality_score(scan)
        scan["scan_time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        self.database.record_scan(scan)

    def _enrich_scan_metadata(self, scan: dict, source: dict) -> None:
        metadata = self.market_data.get_token_metadata(scan.get("addr", ""))
        merged = {**metadata, **{key: value for key, value in source.items() if value}}
        symbol = str(scan.get("symbol") or "").strip()
        next_symbol = str(merged.get("symbol") or merged.get("ticker") or merged.get("name") or "").strip()
        if next_symbol and (not symbol or symbol.upper() == "UNKNOWN"):
            scan["symbol"] = next_symbol

        socials = self._scan_socials(scan)
        changed = False
        for key in ("twitter", "telegram", "website"):
            if merged.get(key) and not socials.get(key):
                socials[key] = merged[key]
                changed = True
        if changed:
            scan["socials"] = json.dumps(socials)

    async def _refresh_unknown_metadata(self) -> None:
        now = time.time()
        if now - self.last_metadata_refresh_ts < 15:
            return
        unknown_addresses = []
        for pool in (self.radar_pool, self.watchlist):
            for addr, candidate in pool.items():
                symbol = str((candidate.get("scan") or {}).get("symbol") or "").strip().upper()
                if symbol in {"", "UNKNOWN", "UNK"}:
                    unknown_addresses.append(addr)
        if not unknown_addresses:
            self.last_metadata_refresh_ts = now
            return
        try:
            await asyncio.to_thread(self.market_data.fetch_token_metadata, unknown_addresses[:30])
        except Exception:
            logger.debug("Token metadata refresh failed", exc_info=True)
        finally:
            self.last_metadata_refresh_ts = now

    def _scan_socials(self, scan: dict) -> dict:
        raw = scan.get("socials")
        if isinstance(raw, dict):
            return dict(raw)
        try:
            parsed = json.loads(str(raw or "{}"))
        except json.JSONDecodeError:
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        return {
            "twitter": parsed.get("twitter"),
            "telegram": parsed.get("telegram"),
            "website": parsed.get("website"),
        }

    def _append_price_sample(self, candidate: dict, snapshot: dict) -> None:
        samples = candidate.setdefault("samples", [])
        ts = int(float(snapshot.get("timestamp") or time.time()))
        price = float(snapshot.get("price") or 0.0)
        volume_5m = float(snapshot.get("volume_5m") or 0.0)
        if samples:
            last = samples[-1]
            last_ts = int(float(last.get("ts") or 0))
            last_price = float(last.get("price") or 0.0)
            if last_ts == ts and abs(last_price - price) <= 1e-18:
                return
        samples.append(
            {
                "ts": ts,
                "price": price,
                "volume_5m": volume_5m,
            }
        )
        candidate["samples"] = samples[-80:]

    def _abnormal_kline_reason(self, candidate: dict) -> tuple[bool, str]:
        if not self.settings.abnormal_kline_enabled:
            return False, ""
        samples = [
            sample
            for sample in candidate.get("samples", [])
            if float(sample.get("price") or 0.0) > 0
        ]
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
        moves = [
            float(samples[i]["price"]) - float(samples[i - 1]["price"])
            for i in range(1, len(samples))
        ]
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
                    "异常K线: 低成交机械拉升 "
                    f"gain={gain_pct*100:.1f}%, drawdown={max_drawdown_pct*100:.1f}%, "
                    f"down_moves={down_move_ratio*100:.0f}%, volume5m=${volume_5m:.0f}"
                ),
            )
        return False, ""

    def _watch_status_reason(
        self,
        elapsed: int,
        price_change_pct: float,
        drawdown_pct: float,
        liquidity_ratio: float,
    ) -> str:
        if elapsed < self.settings.min_observation_seconds:
            return f"冷静期中，还需 {self.settings.min_observation_seconds - elapsed}s"
        if drawdown_pct > self.settings.max_observation_drawdown_pct:
            return f"回撤过大 {drawdown_pct*100:.1f}%"
        if liquidity_ratio < self.settings.min_observation_liquidity_ratio:
            return f"流动性偏弱 {liquidity_ratio:.2f}x"
        if price_change_pct < self.settings.min_observation_price_change_pct:
            return f"动量不足 {price_change_pct*100:.1f}%"
        return "满足确认条件，等待买入窗口"

    def _watch_status_reason_with_snapshot(
        self,
        candidate: dict,
        elapsed: int,
        price_change_pct: float,
        drawdown_pct: float,
        liquidity_ratio: float,
    ) -> str:
        if int(candidate.get("last_snapshot_ts", 0)) <= 0:
            return f"等待市场快照，第 {int(candidate.get('snapshot_miss_count', 0))} 次重试"
        return self._watch_status_reason(elapsed, price_change_pct, drawdown_pct, liquidity_ratio)

    def _mark_watch_snapshot_pending(self, addr: str) -> None:
        candidate = self.watchlist.get(addr)
        if not candidate:
            return
        candidate["snapshot_miss_count"] = int(candidate.get("snapshot_miss_count", 0)) + 1
        candidate["last_update_ts"] = int(time.time())

    def _bundle_risk_score(self, scan: dict) -> float:
        score = 0.0
        top10 = float(scan.get("top10_owner_pct") or 0)
        largest = float(scan.get("largest_owner_pct") or 0)
        holders = int(float(scan.get("holder_count_estimate") or 0))
        if top10 >= self.settings.bundle_risk_top10_owner_pct:
            score += min((top10 - self.settings.bundle_risk_top10_owner_pct) * 2.2, 0.45)
        if largest >= self.settings.bundle_risk_largest_owner_pct:
            score += min((largest - self.settings.bundle_risk_largest_owner_pct) * 4.0, 0.35)
        if holders > 0 and holders <= self.settings.bundle_risk_min_holders:
            score += min((self.settings.bundle_risk_min_holders - holders) / max(self.settings.bundle_risk_min_holders, 1), 0.25)
        return max(min(score, 1.0), 0.0)

    def _bundle_penalty(self, bundle_risk_score: float) -> float:
        if bundle_risk_score >= 0.85:
            return 25.0
        if bundle_risk_score >= 0.55:
            return 12.0
        if bundle_risk_score >= 0.3:
            return 5.0
        return 0.0

    def _bundle_risk_label(self, score: float) -> str:
        if score >= 0.75:
            return "高"
        if score >= 0.45:
            return "中"
        return "低"

    async def _sync_realtime_tokens(self) -> None:
        priority = []
        seen = set()
        for addr in [position["addr"] for position in self.database.open_positions()] + list(self.watchlist.keys()) + list(self.radar_pool.keys()):
            if addr and addr not in seen:
                priority.append(addr)
                seen.add(addr)
        limit = self.settings.max_radar_tracked_tokens + self.settings.max_watchlist_size + self.settings.max_open_positions
        await self.market_data.set_tracked_tokens(priority[:limit])

    def _progress_from_liquidity(self, liquidity_usd: float) -> str:
        progress_value = int(min(max(liquidity_usd / 690, 0), 100))
        return f"{progress_value}%"

    def _apply_narrative_overlay(self, scan: dict, payload: dict) -> dict:
        if not self.settings.narrative_enabled:
            return scan
        text = " ".join(
            str(payload.get(key) or "")
            for key in ("symbol", "name", "description", "twitter", "telegram", "website")
        ).lower()
        narrative_hits = [word for word in self.settings.narrative_keywords if word and word in text]
        kol_hits = [word for word in self.settings.trusted_kol_keywords if word and word in text]
        news_matches = self._match_news_events(text)
        news_bonus = min(sum(match["bonus"] for match in news_matches), self.settings.news_max_bonus)
        keyword_bonus = min(
            len(narrative_hits) * self.settings.narrative_score_bonus
            + len(kol_hits) * self.settings.trusted_kol_score_bonus,
            self.settings.narrative_max_bonus,
        )
        bonus = min(keyword_bonus + news_bonus, self.settings.narrative_max_bonus + self.settings.news_max_bonus)
        if bonus <= 0:
            scan["narrative_score"] = 0.0
            scan["narrative_tags"] = []
            scan["narrative_label"] = "-"
            scan["news_matches"] = []
            return scan
        news_tags = [hit for match in news_matches for hit in match["hits"]]
        tags = sorted(set(narrative_hits + kol_hits + news_tags))
        scan["score"] = min(float(scan["score"]) + bonus, 100.0)
        scan["narrative_score"] = bonus
        scan["narrative_tags"] = tags
        scan["news_matches"] = news_matches[:3]
        scan["narrative_label"] = ",".join(tags[:4])
        return scan

    def _match_news_events(self, text: str) -> list[dict]:
        matches = []
        if not self.settings.news_enabled:
            return matches
        for event in self.database.active_news_events():
            if float(event.get("confidence") or 0.0) < self.settings.news_min_confidence:
                continue
            score, hits = event_match_score(text, event)
            if score <= 0:
                continue
            matches.append(
                {
                    "title": event.get("title"),
                    "source": event.get("source"),
                    "sentiment": event.get("sentiment"),
                    "confidence": event.get("confidence"),
                    "hits": hits,
                    "bonus": score * self.settings.news_match_bonus,
                }
            )
        matches.sort(key=lambda item: item["bonus"], reverse=True)
        return matches

    def _dynamic_exit_signal(
        self,
        position: dict,
        snapshot: dict,
        sell_price: float,
        max_price: float,
        pnl_pct: float,
        hold_seconds: int,
    ) -> Tuple[Optional[str], dict, float]:
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

        zombie_reason = self._zombie_exit_reason(position, snapshot, pnl_pct, hold_seconds, volume_5m)
        if zombie_reason:
            context["zombie_reason"] = zombie_reason
            return "zombie_position_exit", context, 1.0

        if pnl_pct <= self.settings.emergency_stop_loss_pct:
            return "emergency_stop", context, 1.0

        if hold_seconds >= 8 and not liquidity_missing and liquidity_ratio <= self.settings.emergency_liquidity_ratio:
            return "liquidity_break", context, 1.0

        if peak_gain_pct >= self.settings.fast_exit_peak_pct and drawdown_from_peak_pct >= self.settings.fast_exit_drawdown_pct:
            if int(position.get("moonbag_active") or 0):
                return "moonbag_parabolic_exit", context, 1.0
            return "profit_lock_moonbag", context, self.settings.profit_lock_sell_pct

        if pnl_pct >= self.settings.profit_lock_min_pct:
            if drawdown_from_peak_pct >= self.settings.profit_lock_drawdown_pct and liquidity_ratio <= self.settings.profit_lock_liquidity_ratio:
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

    def _zombie_exit_reason(
        self,
        position: dict,
        snapshot: dict,
        pnl_pct: float,
        hold_seconds: int,
        volume_5m: float,
    ) -> Optional[str]:
        if int(position.get("moonbag_active") or 0):
            return None
        entry_metrics = self.database.latest_buy_metrics(position["addr"])
        has_narrative = float(entry_metrics.get("narrative_score") or 0.0) > 0 or bool(entry_metrics.get("narrative_tags"))
        if has_narrative:
            return None
        if pnl_pct >= self.settings.zombie_min_profit_keep_pct:
            return None
        source = str(snapshot.get("source") or position.get("price_source") or "").lower()
        updated_ts = int(float(snapshot.get("timestamp") or position.get("price_updated_ts") or 0))
        non_live_age = max(int(time.time()) - updated_ts, 0) if source != "pumpportal" and updated_ts > 0 else 0
        if hold_seconds >= self.settings.zombie_position_seconds and volume_5m <= self.settings.zombie_min_volume_5m_usd:
            return f"持仓 {hold_seconds}s 且 5m 成交仅 ${volume_5m:.0f}"
        if hold_seconds >= self.settings.zombie_position_seconds and source != "pumpportal":
            return f"持仓 {hold_seconds}s 且无实时成交流 source={source or '-'}"
        if non_live_age >= self.settings.zombie_non_live_seconds and volume_5m <= self.settings.zombie_min_volume_5m_usd:
            return f"非实时行情 {non_live_age}s 且 5m 成交 ${volume_5m:.0f}"
        return None

    def _hold_seconds(self, buy_time: str) -> int:
        try:
            buy_ts = time.mktime(time.strptime(buy_time, "%Y-%m-%d %H:%M:%S"))
        except (TypeError, ValueError):
            return 0
        return max(int(time.time() - buy_ts), 0)
