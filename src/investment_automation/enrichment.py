from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any, Iterable, Optional

from .market_data import MarketDataClient
from .scoring import SignalScorer
from .settings import Settings


class EnrichmentService:
    def __init__(
        self,
        settings: Settings,
        market_data: MarketDataClient,
        scorer: SignalScorer,
        *,
        holder_concurrency: int = 4,
    ) -> None:
        self.settings = settings
        self.market_data = market_data
        self.scorer = scorer
        self.last_metadata_refresh_ts = 0.0
        self.holder_enrichment_semaphore = threading.BoundedSemaphore(max(holder_concurrency, 1))

    async def refresh_candidate_holder_metrics(
        self,
        addr: str,
        candidate: Optional[dict[str, Any]],
        *,
        now_ts: Optional[int] = None,
    ) -> None:
        if not candidate:
            return
        now = now_ts or int(time.time())
        if (
            now - int(candidate.get("holder_refreshed_ts", 0))
            < self.settings.holder_refresh_seconds
        ):
            return
        holder_metrics = await asyncio.to_thread(self.market_data.fetch_holder_metrics, addr)
        if not holder_metrics:
            return
        scan = candidate.get("scan") or {}
        scan.update(holder_metrics)
        scan["bundle_risk_score"] = self.scorer.bundle_risk_score(scan)
        candidate["scan"] = scan
        candidate["holder_refreshed_ts"] = now

    async def holder_enriched_scan(
        self,
        scan: dict[str, Any],
        *,
        now_ts: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        acquired = await asyncio.to_thread(self.holder_enrichment_semaphore.acquire)
        try:
            holder_metrics = await asyncio.to_thread(
                self.market_data.fetch_holder_metrics, scan["addr"]
            )
        finally:
            if acquired:
                self.holder_enrichment_semaphore.release()
        if not holder_metrics or float(holder_metrics.get("holder_count_estimate") or 0.0) <= 0:
            return None

        enriched = dict(scan)
        enriched.update(holder_metrics)
        enriched["bundle_risk_score"] = self.scorer.bundle_risk_score(enriched)
        enriched["score"] = self.scorer.market_quality_score(enriched, now_ts=now_ts)
        reference_ts = now_ts or int(time.time())
        enriched["scan_time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(reference_ts))
        return enriched

    def enrich_scan_metadata(self, scan: dict[str, Any], source: dict[str, Any]) -> None:
        metadata = self.market_data.get_token_metadata(str(scan.get("addr") or ""))
        merged = {**metadata, **{key: value for key, value in source.items() if value}}
        symbol = str(scan.get("symbol") or "").strip()
        next_symbol = str(
            merged.get("symbol") or merged.get("ticker") or merged.get("name") or ""
        ).strip()
        if next_symbol and (not symbol or symbol.upper() == "UNKNOWN"):
            scan["symbol"] = next_symbol

        socials = self.scan_socials(scan)
        changed = False
        for key in ("twitter", "telegram", "website"):
            if merged.get(key) and not socials.get(key):
                socials[key] = merged[key]
                changed = True
        if changed:
            scan["socials"] = json.dumps(socials)

    async def refresh_unknown_metadata(
        self,
        pools: Iterable[dict[str, dict[str, Any]]],
        *,
        now_ts: Optional[float] = None,
    ) -> None:
        now = now_ts if now_ts is not None else time.time()
        if now - self.last_metadata_refresh_ts < 15:
            return

        unknown_addresses: list[str] = []
        for pool in pools:
            for addr, candidate in pool.items():
                symbol = str((candidate.get("scan") or {}).get("symbol") or "").strip().upper()
                if symbol in {"", "UNKNOWN", "UNK"}:
                    unknown_addresses.append(addr)

        if not unknown_addresses:
            self.last_metadata_refresh_ts = now
            return

        try:
            await asyncio.to_thread(self.market_data.fetch_token_metadata, unknown_addresses[:30])
        finally:
            self.last_metadata_refresh_ts = now

    def scan_socials(self, scan: dict[str, Any]) -> dict[str, Any]:
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
