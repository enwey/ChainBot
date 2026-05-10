from __future__ import annotations

import asyncio
import logging
import time
from copy import deepcopy
from typing import Optional, Tuple

from .decision_audit import DecisionAuditService
from .db import Database
from .enrichment import EnrichmentService
from .execution import TradeExecutor
from .market_data import MarketDataClient, build_scan_record
from .models import StrategyState
from .narrative import NarrativeService
from .news import NewsClient
from .observation import ObservationService
from .opportunity import OpportunityService
from .position import PositionService
from .risk import RiskService
from .scoring import SignalScorer
from .settings import Settings
from .watchlist import WatchlistService

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
        self.risk_service = RiskService(settings)
        self.signal_scorer = SignalScorer(settings)
        self.decision_audit_service = DecisionAuditService(database)
        self.enrichment_service = EnrichmentService(settings, market_data, self.signal_scorer)
        self.narrative_service = NarrativeService(settings, database)
        self.observation_service = ObservationService(settings, self.signal_scorer)
        self.opportunity_service = OpportunityService(database)
        self.watchlist_service = WatchlistService(settings, self.signal_scorer)
        self.position_service = PositionService(settings, self.signal_scorer, self.watchlist_service, database, market_data)
        self.started_at = int(time.time())
        self.last_buy_ts = 0.0
        self.strategy_state = StrategyState.from_dict(
            self.database.get_strategy_state(),
            default_max_open_positions=self.settings.max_open_positions,
            reason="initialized",
        )
        self.radar_pool = {}
        self.watchlist = {}
        self.worker_status = {
            name: {
                "state": "idle",
                "iterations": 0,
                "error_count": 0,
                "last_seen_ts": 0,
                "last_success_ts": 0,
                "last_error_ts": 0,
                "last_error": "",
            }
            for name in (
                "news",
                "strategy",
                "new_tokens",
                "holder_backfill",
                "radar",
                "watchlist",
                "positions",
            )
        }

    def get_watchlist_status(self) -> list[dict]:
        return self.watchlist_service.get_watchlist_status(self.watchlist)

    def runtime_status(self) -> dict:
        now = int(time.time())
        workers = {}
        for name, status in self.worker_status.items():
            workers[name] = {
                **status,
                "last_seen_age": self._format_duration(max(now - int(status.get("last_seen_ts") or 0), 0))
                if int(status.get("last_seen_ts") or 0) > 0
                else "-",
                "last_success_age": self._format_duration(max(now - int(status.get("last_success_ts") or 0), 0))
                if int(status.get("last_success_ts") or 0) > 0
                else "-",
            }
        return {
            "uptime_seconds": max(now - self.started_at, 0),
            "strategy_mode": self.strategy_state.mode,
            "strategy_reason": self.strategy_state.reason,
            "watchlist_size": len(self.watchlist),
            "radar_pool_size": len(self.radar_pool),
            "open_positions": self.database.open_position_count(),
            "workers": workers,
        }

    def _mark_worker_running(self, name: str) -> None:
        status = self.worker_status.setdefault(name, {})
        status["state"] = "running"
        status["last_seen_ts"] = int(time.time())

    def _mark_worker_success(self, name: str) -> None:
        status = self.worker_status.setdefault(name, {})
        now = int(time.time())
        status["state"] = "running"
        status["iterations"] = int(status.get("iterations", 0)) + 1
        status["last_seen_ts"] = now
        status["last_success_ts"] = now

    def _mark_worker_error(self, name: str, exc: Exception) -> None:
        status = self.worker_status.setdefault(name, {})
        now = int(time.time())
        status["state"] = "error"
        status["error_count"] = int(status.get("error_count", 0)) + 1
        status["last_seen_ts"] = now
        status["last_error_ts"] = now
        status["last_error"] = str(exc)

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
            self._mark_worker_running("news")
            try:
                if self.settings.news_enabled and self.news_client is not None:
                    events = await asyncio.to_thread(self.news_client.fetch_events)
                    stored = self.database.record_news_events(events)
                    if stored:
                        logger.info("Updated news event pool with %s changes", stored)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._mark_worker_error("news", exc)
                logger.exception("Failed to refresh news events")
            else:
                self._mark_worker_success("news")
            await asyncio.sleep(self.settings.news_poll_seconds)

    async def monitor_strategy_optimizer(self) -> None:
        while True:
            self._mark_worker_running("strategy")
            try:
                await asyncio.to_thread(self._refresh_strategy_state)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._mark_worker_error("strategy", exc)
                logger.exception("Failed to optimize strategy state")
            else:
                self._mark_worker_success("strategy")
            await asyncio.sleep(30)

    async def monitor_new_tokens(self) -> None:
        while True:
            self._mark_worker_running("new_tokens")
            try:
                async for payload in self.market_data.subscribe_new_tokens():
                    self._mark_worker_running("new_tokens")
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
                    except Exception as exc:
                        self._mark_worker_error("new_tokens", exc)
                        logger.exception("Failed to process token payload")
                    else:
                        self._mark_worker_success("new_tokens")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._mark_worker_error("new_tokens", exc)
                logger.exception("Token monitor stream failed")
                await asyncio.sleep(self.settings.monitor_poll_seconds)

    async def monitor_scan_holder_backfill(self) -> None:
        while True:
            self._mark_worker_running("holder_backfill")
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
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._mark_worker_error("holder_backfill", exc)
                logger.exception("Failed to backfill scan holder metrics")
            else:
                self._mark_worker_success("holder_backfill")
            await asyncio.sleep(6)

    async def monitor_radar_pool(self) -> None:
        while True:
            self._mark_worker_running("radar")
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
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._mark_worker_error("radar", exc)
                logger.exception("Failed to refresh radar pool")
            else:
                self._mark_worker_success("radar")
            await asyncio.sleep(self.settings.radar_poll_seconds)

    async def monitor_watchlist(self) -> None:
        while True:
            self._mark_worker_running("watchlist")
            try:
                await self._sync_realtime_tokens()
                self._trim_watchlist_to_limit()
                if not self.watchlist:
                    self._mark_worker_success("watchlist")
                    await asyncio.sleep(self.settings.observation_poll_seconds)
                    continue
                snapshots = await asyncio.to_thread(self.market_data.fetch_snapshots, list(self.watchlist.keys()))
                for addr in list(self.watchlist.keys()):
                    snapshot = snapshots.get(addr)
                    if snapshot is None:
                        self._mark_watch_snapshot_pending(addr)
                        candidate = self.watchlist.get(addr)
                        if candidate and int(candidate.get("snapshot_miss_count", 0)) >= 8:
                            self._reject_watch_candidate(addr, candidate["scan"], "连续无实时成交快照，释放观察位")
                        continue
                    await self._process_watch_candidate(addr, snapshot)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._mark_worker_error("watchlist", exc)
                logger.exception("Failed to refresh watchlist")
            else:
                self._mark_worker_success("watchlist")
            await asyncio.sleep(self.settings.observation_poll_seconds)

    async def _maybe_open_position(self, scan: dict) -> None:
        wallet = self.database.wallet()
        allowed, reason = self._should_open_position(scan, wallet)
        if not allowed:
            logger.debug("Skipped %s: %s", scan["symbol"], reason)
            self.decision_audit_service.record_entry_blocked(
                scan,
                reason,
                strategy_mode=self.strategy_state.mode,
                wallet=wallet,
            )
            return
        position_size_usd = self._position_size_usd(scan, wallet)
        if position_size_usd <= 0:
            self.decision_audit_service.record_entry_blocked(
                scan,
                "position sizing produced zero",
                strategy_mode=self.strategy_state.mode,
                wallet=wallet,
                position_size_usd=position_size_usd,
            )
            return

        result = self.executor.buy(scan["addr"], position_size_usd)
        if not result.executed:
            logger.warning("Buy skipped for %s: %s", scan["symbol"], result.reason)
            self.decision_audit_service.record_entry_execution_skipped(
                scan,
                result.reason or "execution skipped",
                strategy_mode=self.strategy_state.mode,
                position_size_usd=position_size_usd,
                execution_mode=result.mode,
                wallet=wallet,
            )
            return

        effective_buy_price = self._paper_adjusted_buy_price(scan["price"], result.mode)
        entry_reason = self._entry_reason(scan, position_size_usd)
        self.database.open_position(
            addr=scan["addr"],
            symbol=scan["symbol"],
            price=effective_buy_price,
            amount_usd=position_size_usd,
            mode=result.mode,
            entry_tx=result.tx_hash,
            entry_liquidity=float(scan["liquidity"]),
            metrics={
                "entry_reason": entry_reason,
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
        self.decision_audit_service.record_entry_opened(
            scan,
            strategy_mode=self.strategy_state.mode,
            position_size_usd=position_size_usd,
            execution_mode=result.mode,
            tx_hash=result.tx_hash,
            effective_buy_price=effective_buy_price,
            entry_reason=entry_reason,
            wallet=wallet,
        )
        self.opportunity_service.record_signal(
            scan,
            (
                f"score={scan['score']:.0f}, age={self._token_age_text(scan)}, "
                f"dev_buy={scan['dev_buy']:.2f} SOL, liq=${scan['liquidity']:.0f}, "
                f"narrative={scan.get('narrative_label', '-')}, "
                f"bundle={self._bundle_risk_label(float(scan.get('bundle_risk_score') or 0))}, size=${position_size_usd:.2f}"
            ),
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
            replaced, removed = self._prune_watchlist_for_new_candidate(scan)
            if removed is not None:
                removed_addr, removed_scan, removed_reason = removed
                self._reject_watch_candidate(removed_addr, removed_scan, removed_reason)
            if not replaced:
                self.opportunity_service.record_rejected(scan, "观察池已满且候选质量不足")
                return
        allowed, reason = self._should_watch(scan)
        if not allowed:
            logger.debug("Ignored %s for watchlist: %s", scan["symbol"], reason)
            if record_ignored and reason.startswith("low launch quality"):
                self.opportunity_service.record_ignored(scan, reason)
            return
        self.watchlist[scan["addr"]] = self.watchlist_service.build_watch_candidate(
            scan,
            deepcopy((self.radar_pool.get(scan["addr"]) or {}).get("samples") or []),
        )
        self.opportunity_service.record_watch(
            scan,
            (
                f"进入观察池: age={self._token_age_text(scan)}, score={scan['score']:.0f}, "
                f"quality={float(scan.get('launch_quality_score') or 0):.0f}, "
                f"narrative={scan.get('narrative_label', '-')}, "
                f"dev={scan['dev_buy']:.2f} SOL, liq=${scan['liquidity']:.0f}, top10={float(scan.get('top10_owner_pct') or 0)*100:.1f}%"
            ),
        )

    def _track_radar_candidate(self, scan: dict) -> None:
        self.observation_service.track_radar_candidate(self.radar_pool, scan)

    def _prune_radar_pool(self) -> None:
        self.observation_service.prune_radar_pool(
            self.radar_pool,
            self.watchlist.keys(),
            (position["addr"] for position in self.database.open_positions()),
        )

    def _process_radar_candidate(self, addr: str, snapshot: dict) -> None:
        candidate = self.radar_pool.get(addr)
        if not candidate:
            return
        self._enrich_scan_metadata(candidate["scan"], snapshot)
        decision = self.observation_service.process_radar_candidate(candidate, snapshot)
        if decision is None:
            return
        if decision.rejected_reason:
            self.radar_pool.pop(addr, None)
            self.opportunity_service.record_rejected(decision.scan, decision.rejected_reason)
            return
        if decision.record_scan:
            self.database.record_scan(decision.scan)
        self._maybe_add_to_watchlist(decision.scan, record_ignored=False)
        if self._instant_probe_ok(decision.scan):
            asyncio.create_task(self._maybe_open_position(deepcopy(decision.scan)))

    async def _process_watch_candidate(self, addr: str, snapshot: dict) -> None:
        candidate = self.watchlist.get(addr)
        if not candidate:
            return

        self._enrich_scan_metadata(candidate["scan"], snapshot)
        holder_metrics = await asyncio.to_thread(self.market_data.fetch_holder_metrics, addr)
        decision = self.observation_service.process_watch_candidate(
            candidate,
            snapshot,
            holder_metrics,
            self.strategy_state.mode,
        )
        if decision is None:
            return
        if decision.record_scan:
            self.database.record_scan(decision.scan)
        if decision.rejected_reason:
            self._reject_watch_candidate(addr, decision.scan, decision.rejected_reason)
            return
        if decision.watch_reason:
            self.opportunity_service.record_watch(decision.scan, decision.watch_reason)
            return

        await self._maybe_open_position(decision.scan)
        if self.database.has_position(addr):
            self.watchlist.pop(addr, None)
            self.opportunity_service.record_confirmed(decision.scan, decision.open_reason or "watch confirmed")

    def _update_watch_reason(self, scan: dict, elapsed: int, price_change_pct: float, liquidity_ratio: float) -> None:
        self.opportunity_service.record_watch(
            scan,
            self.observation_service.watch_update_reason(scan, elapsed, price_change_pct, liquidity_ratio),
        )

    def _reject_watch_candidate(self, addr: str, scan: dict, reason: str) -> None:
        self.watchlist.pop(addr, None)
        self.opportunity_service.record_rejected(scan, reason)

    def _should_watch(self, scan: dict) -> tuple[bool, str]:
        return self.watchlist_service.should_watch(scan)

    def _launch_quality(self, scan: dict) -> tuple[float, list[str]]:
        return self.signal_scorer.launch_quality(scan)

    def _fast_track_launch(self, scan: dict, quality_score: float, signals: list[str]) -> bool:
        return self.signal_scorer.fast_track_launch(scan, quality_score, signals)

    def _symbol_quality_ok(self, symbol: str) -> bool:
        return self.signal_scorer.symbol_quality_ok(symbol)

    def _has_socials(self, scan: dict) -> bool:
        return self.signal_scorer.has_socials(scan)

    def _trim_watchlist_to_limit(self) -> None:
        for addr, removed_scan, reason in self.watchlist_service.trim_to_limit(self.watchlist):
            self._reject_watch_candidate(addr, removed_scan, reason)

    def _prune_watchlist_for_new_candidate(self, scan: dict) -> tuple[bool, Optional[tuple[str, dict, str]]]:
        return self.watchlist_service.prune_for_new_candidate(self.watchlist, scan)

    def _holder_ok(self, scan: dict) -> tuple[bool, str]:
        return self.signal_scorer.holder_ok(scan)

    def _default_strategy_state(self, reason: str) -> StrategyState:
        return self.risk_service.default_strategy_state(reason)

    def _refresh_strategy_state(self) -> None:
        previous_mode = self.strategy_state.mode
        state = self.risk_service.evaluate_strategy_state(
            self.database.recent_sell_trades(limit=30),
            self.strategy_state,
        )
        if state.mode != previous_mode:
            logger.info("Strategy mode changed to %s: %s", state.mode, state.reason)
        self.strategy_state = state
        self.database.save_strategy_state(state.to_dict())

    def _adaptive_min_score_to_buy(self) -> float:
        return self.risk_service.adaptive_min_score_to_buy(self.strategy_state)

    def _adaptive_position_multiplier(self) -> float:
        return self.risk_service.adaptive_position_multiplier(self.strategy_state)

    def _adaptive_cooldown_seconds(self) -> float:
        return self.risk_service.adaptive_cooldown_seconds(self.strategy_state)

    def _adaptive_max_open_positions(self) -> int:
        return self.risk_service.adaptive_max_open_positions(self.strategy_state)

    def _instant_probe_allowed_by_strategy(self) -> bool:
        return self.risk_service.instant_probe_allowed(self.strategy_state)

    def _instant_probe_ok(self, scan: dict) -> bool:
        return self.risk_service.instant_probe_ok(
            scan,
            self.strategy_state,
            has_position=self.database.has_position(scan["addr"]),
        )

    def _scalp_override_ok(
        self,
        scan: dict,
        price_change_pct: float,
        drawdown_pct: float,
        liquidity_ratio: float,
        elapsed: int,
    ) -> tuple[bool, str]:
        return self.observation_service.scalp_override_ok(
            scan,
            price_change_pct,
            drawdown_pct,
            liquidity_ratio,
            elapsed,
            strategy_mode=self.strategy_state.mode,
        )

    async def monitor_open_positions(self) -> None:
        while True:
            self._mark_worker_running("positions")
            try:
                await self._sync_realtime_tokens()
                positions = self.database.open_positions()
                price_map = await asyncio.to_thread(
                    self.market_data.fetch_snapshots,
                    [position["addr"] for position in positions],
                )
                for position in positions:
                    await self._process_position(position, price_map)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._mark_worker_error("positions", exc)
                logger.exception("Failed to refresh positions")
            else:
                self._mark_worker_success("positions")
            await asyncio.sleep(self.settings.pricing_poll_seconds)

    async def _process_position(self, position: dict, price_map: dict[str, dict]) -> None:
        addr = position["addr"]
        snapshot = price_map.get(addr)
        if not snapshot:
            return
        decision = await self.position_service.evaluate_position(position, snapshot)
        if decision is None:
            return

        self.database.update_position_price(
            addr,
            decision.sell_price,
            decision.max_price,
            source=str(snapshot.get("source") or "unknown"),
            updated_ts=int(float(snapshot.get("timestamp") or time.time())),
        )
        if not decision.exit_reason:
            return

        if decision.sell_amount_usd <= 0:
            self.decision_audit_service.record_exit_execution_skipped(
                position,
                decision,
                "sell amount resolved to zero",
                execution_mode=str(position.get("mode") or "unknown"),
                market_snapshot=snapshot,
            )
            return
        result = self.executor.sell(addr, decision.sell_amount_usd)
        if not result.executed:
            logger.warning("Sell skipped for %s: %s", position["symbol"], result.reason)
            self.decision_audit_service.record_exit_execution_skipped(
                position,
                decision,
                result.reason or "execution skipped",
                execution_mode=result.mode,
                market_snapshot=snapshot,
            )
            return

        proceeds_usd = self._sale_proceeds(decision.sell_amount_usd, decision.pnl_pct, position["mode"])
        self.database.update_wallet_for_sell(decision.sell_amount_usd, proceeds_usd)
        metrics = {
            "gross_market_price": decision.current_price,
            "effective_exit_price": decision.sell_price,
            "hold_seconds": decision.hold_seconds,
            "exit_reason": decision.exit_reason,
            "sell_fraction": decision.sell_fraction,
            **decision.exit_context,
        }
        if decision.remaining_amount_usd <= self.settings.moonbag_min_usd or decision.sell_fraction >= 0.999:
            self.database.close_position(
                addr=addr,
                symbol=position["symbol"],
                exit_price=decision.sell_price,
                amount_usd=decision.sell_amount_usd,
                pnl_pct=decision.pnl_pct,
                tx_hash=result.tx_hash,
                metrics=metrics,
            )
        else:
            self.database.reduce_position(
                addr=addr,
                symbol=position["symbol"],
                exit_price=decision.sell_price,
                sold_amount_usd=decision.sell_amount_usd,
                remaining_amount_usd=decision.remaining_amount_usd,
                pnl_pct=decision.pnl_pct,
                tx_hash=result.tx_hash,
                metrics=metrics,
            )
        self.decision_audit_service.record_exit_executed(
            position,
            decision,
            execution_mode=result.mode,
            tx_hash=result.tx_hash,
            market_snapshot=snapshot,
        )
        logger.info(
            "Sold %.0f%% of %s position for %s at %.2f%% reason=%s",
            decision.sell_fraction * 100,
            result.mode,
            position["symbol"],
            decision.pnl_pct * 100,
            decision.exit_reason,
        )

    def _should_open_position(self, scan: dict, wallet: dict) -> tuple[bool, str]:
        decision = self.risk_service.should_open_position(
            scan,
            wallet,
            self.strategy_state,
            has_position=self.database.has_position(scan["addr"]),
            open_position_count=self.database.open_position_count(),
            last_buy_ts=self.last_buy_ts,
        )
        return decision.allowed, decision.reason

    async def _holder_risk_exit_reason(self, position: dict, pnl_pct: float, hold_seconds: int) -> Optional[str]:
        return await self.position_service.holder_risk_exit_reason(position, pnl_pct, hold_seconds)

    def _position_size_usd(self, scan: dict, wallet: dict) -> float:
        return self.risk_service.position_size_usd(scan, wallet, self.strategy_state)

    def _paper_adjusted_buy_price(self, market_price: float, mode: str) -> float:
        return self.position_service.paper_adjusted_buy_price(market_price, mode)

    def _paper_adjusted_sell_price(self, market_price: float, mode: str) -> float:
        return self.position_service.paper_adjusted_sell_price(market_price, mode)

    def _sale_proceeds(self, amount_usd: float, pnl_pct: float, mode: str) -> float:
        return self.position_service.sale_proceeds(amount_usd, pnl_pct, mode)

    def _token_age_text(self, scan: dict) -> str:
        return self.watchlist_service.token_age_text(scan)

    def _format_duration(self, age_seconds: int) -> str:
        return self.watchlist_service.format_duration(age_seconds)

    def _entry_reason(self, scan: dict, position_size_usd: float) -> str:
        return self.position_service.entry_reason(scan, position_size_usd)

    def _observation_bonus(self, price_change_pct: float, liquidity_ratio: float, elapsed: int) -> float:
        return self.signal_scorer.observation_bonus(price_change_pct, liquidity_ratio, elapsed)

    def _radar_momentum_bonus(self, price_change_pct: float, liquidity_ratio: float, elapsed: int) -> float:
        return self.signal_scorer.radar_momentum_bonus(price_change_pct, liquidity_ratio, elapsed)

    def _market_quality_score(self, scan: dict, price_change_pct: float = 0.0, liquidity_ratio: float = 1.0, elapsed: int = 0) -> float:
        return self.signal_scorer.market_quality_score(scan, price_change_pct, liquidity_ratio, elapsed)

    async def _refresh_candidate_holder_metrics(self, addr: str, candidate: Optional[dict]) -> None:
        await self.enrichment_service.refresh_candidate_holder_metrics(addr, candidate)

    async def _record_holder_enriched_scan(self, scan: dict) -> None:
        enriched_scan = await self.enrichment_service.holder_enriched_scan(scan)
        if enriched_scan:
            self.database.record_scan(enriched_scan)

    def _enrich_scan_metadata(self, scan: dict, source: dict) -> None:
        self.enrichment_service.enrich_scan_metadata(scan, source)

    async def _refresh_unknown_metadata(self) -> None:
        try:
            await self.enrichment_service.refresh_unknown_metadata((self.radar_pool, self.watchlist))
        except Exception:
            logger.debug("Token metadata refresh failed", exc_info=True)

    def _scan_socials(self, scan: dict) -> dict:
        return self.enrichment_service.scan_socials(scan)

    def _append_price_sample(self, candidate: dict, snapshot: dict) -> None:
        self.observation_service.append_price_sample(candidate, snapshot)

    def _abnormal_kline_reason(self, candidate: dict) -> tuple[bool, str]:
        return self.observation_service.abnormal_kline_reason(candidate)

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
        return self.watchlist_service.watch_status_reason(elapsed, price_change_pct, drawdown_pct, liquidity_ratio)

    def _watch_status_reason_with_snapshot(
        self,
        candidate: dict,
        elapsed: int,
        price_change_pct: float,
        drawdown_pct: float,
        liquidity_ratio: float,
    ) -> str:
        return self.watchlist_service.watch_status_reason_with_snapshot(
            candidate,
            elapsed,
            price_change_pct,
            drawdown_pct,
            liquidity_ratio,
        )

    def _mark_watch_snapshot_pending(self, addr: str) -> None:
        self.watchlist_service.mark_snapshot_pending(self.watchlist, addr)

    def _bundle_risk_score(self, scan: dict) -> float:
        return self.signal_scorer.bundle_risk_score(scan)

    def _bundle_penalty(self, bundle_risk_score: float) -> float:
        return self.signal_scorer.bundle_penalty(bundle_risk_score)

    def _bundle_risk_label(self, score: float) -> str:
        return self.signal_scorer.bundle_risk_label(score)

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
        return self.observation_service.progress_from_liquidity(liquidity_usd)

    def _apply_narrative_overlay(self, scan: dict, payload: dict) -> dict:
        return self.narrative_service.apply_overlay(scan, payload)

    def _match_news_events(self, text: str) -> list[dict]:
        return self.narrative_service.match_news_events(text)

    def _dynamic_exit_signal(
        self,
        position: dict,
        snapshot: dict,
        sell_price: float,
        max_price: float,
        pnl_pct: float,
        hold_seconds: int,
    ) -> Tuple[Optional[str], dict, float]:
        return self.position_service.dynamic_exit_signal(position, snapshot, sell_price, max_price, pnl_pct, hold_seconds)

    def _zombie_exit_reason(
        self,
        position: dict,
        snapshot: dict,
        pnl_pct: float,
        hold_seconds: int,
        volume_5m: float,
    ) -> Optional[str]:
        return self.position_service.zombie_exit_reason(position, snapshot, pnl_pct, hold_seconds, volume_5m)

    def _hold_seconds(self, buy_time: str) -> int:
        return self.position_service.hold_seconds(buy_time)
