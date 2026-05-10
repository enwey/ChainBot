from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, Optional, Set, Tuple

import requests
import websockets

from .settings import Settings


class MarketDataClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "investment-automation/0.1"})
        self._holder_cache: dict[str, tuple[float, dict[str, float]]] = {}
        self._stream_task: Optional[asyncio.Task] = None
        self._ws = None
        self._stream_lock: Optional[asyncio.Lock] = None
        self._new_token_queue: Optional["asyncio.Queue[dict[str, Any]]"] = None
        self._desired_token_subscriptions: Set[str] = set()
        self._active_token_subscriptions: Set[str] = set()
        self._trade_cache: Dict[str, Dict[str, Any]] = {}
        self._metadata_cache: Dict[str, Dict[str, Any]] = {}

    async def subscribe_new_tokens(self):
        await self.ensure_realtime_stream()
        if self._new_token_queue is None:
            raise RuntimeError("realtime queue not initialized")
        while True:
            payload = await self._new_token_queue.get()
            yield payload

    async def ensure_realtime_stream(self) -> None:
        if self._stream_lock is None:
            self._stream_lock = asyncio.Lock()
        if self._new_token_queue is None:
            self._new_token_queue = asyncio.Queue()
        async with self._stream_lock:
            if self._stream_task and not self._stream_task.done():
                return
            self._stream_task = asyncio.create_task(self._run_stream(), name="pumpportal-stream")

    async def set_tracked_tokens(self, addresses: list[str]) -> None:
        await self.ensure_realtime_stream()
        desired = {address for address in addresses if address}
        self._desired_token_subscriptions = desired
        await self._sync_token_subscriptions()

    def get_realtime_snapshot(self, address: str, max_age_seconds: int = 8) -> Optional[dict[str, Any]]:
        cached = self._trade_cache.get(address)
        if not cached:
            return None
        if time.time() - float(cached.get("timestamp", 0)) > max_age_seconds:
            return None
        return dict(cached)

    def get_token_metadata(self, address: str) -> dict[str, Any]:
        return dict(self._metadata_cache.get(address) or {})

    def fetch_token_metadata(self, addresses: list[str]) -> dict[str, dict[str, Any]]:
        missing = [address for address in addresses if address and not self._metadata_cache.get(address, {}).get("symbol")]
        if not missing:
            return {address: self.get_token_metadata(address) for address in addresses if self.get_token_metadata(address)}

        response = self.session.get(
            f"{self.settings.dexscreener_token_url}/" + ",".join(missing),
            timeout=self.settings.dexscreener_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        for pair in payload.get("pairs") or []:
            if pair.get("chainId") != "solana":
                continue
            address = (pair.get("baseToken") or {}).get("address")
            if not address:
                continue
            metadata = self._extract_token_metadata(
                {
                    "mint": address,
                    "symbol": (pair.get("baseToken") or {}).get("symbol"),
                    "name": (pair.get("baseToken") or {}).get("name"),
                    "url": pair.get("url"),
                }
            )
            self._merge_token_metadata(address, metadata)
        return {address: self.get_token_metadata(address) for address in addresses if self.get_token_metadata(address)}

    def fetch_prices(self, addresses: list[str]) -> dict[str, float]:
        snapshots = self.fetch_snapshots(addresses)
        return {address: item["price"] for address, item in snapshots.items() if item["price"] > 0}

    def fetch_snapshots(self, addresses: list[str]) -> dict[str, dict[str, float]]:
        if not addresses:
            return {}
        snapshots: dict[str, dict[str, float]] = {}
        missing = []
        for address in addresses:
            realtime = self.get_realtime_snapshot(address)
            if realtime:
                snapshots[address] = realtime
            else:
                missing.append(address)

        if not missing:
            return snapshots

        for batch in self._chunks(missing, 30):
            response = self.session.get(
                f"{self.settings.dexscreener_token_url}/" + ",".join(batch),
                timeout=self.settings.dexscreener_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            for pair in payload.get("pairs") or []:
                if pair.get("chainId") != "solana":
                    continue
                address = pair.get("baseToken", {}).get("address")
                try:
                    price = float(pair.get("priceUsd", 0) or 0)
                except (TypeError, ValueError):
                    price = 0.0
                try:
                    liquidity_usd = float((pair.get("liquidity") or {}).get("usd", 0) or 0)
                except (TypeError, ValueError):
                    liquidity_usd = 0.0
                try:
                    volume_5m = float((pair.get("volume") or {}).get("m5", 0) or 0)
                except (TypeError, ValueError):
                    volume_5m = 0.0
                if address and price > 0:
                    metadata = self._extract_token_metadata(
                        {
                            "mint": address,
                            "symbol": (pair.get("baseToken") or {}).get("symbol"),
                            "name": (pair.get("baseToken") or {}).get("name"),
                            "url": pair.get("url"),
                        }
                    )
                    self._merge_token_metadata(address, metadata)
                    snapshots[address] = {
                        "price": price,
                        "liquidity_usd": liquidity_usd,
                        "volume_5m": volume_5m,
                        "source": "dexscreener",
                        "timestamp": time.time(),
                        **metadata,
                    }
        return snapshots

    def _chunks(self, values: list[str], size: int):
        for index in range(0, len(values), size):
            yield values[index : index + size]

    def rugcheck_is_safe(self, address: str) -> bool:
        try:
            response = self.session.get(
                self.settings.rugcheck_url.format(address=address),
                timeout=5,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return False

        if payload.get("score", 0) > 800:
            return False

        fatal_words = {"mint", "freeze"}
        for risk in payload.get("risks", []):
            name = str(risk.get("name", "")).lower()
            if any(word in name for word in fatal_words):
                return False
        return True

    def fetch_holder_metrics(self, mint: str) -> dict[str, float]:
        cached = self._holder_cache.get(mint)
        now = time.time()
        if cached and now - cached[0] < self.settings.holder_refresh_seconds:
            return dict(cached[1])

        rugcheck_metrics = self._rugcheck_holder_metrics(mint)
        if float(rugcheck_metrics.get("holder_count_estimate") or 0.0) > 0:
            return rugcheck_metrics

        try:
            supply_resp = self._rpc_call("getTokenSupply", [mint, {"commitment": "confirmed"}])
            largest_resp = self._rpc_call("getTokenLargestAccounts", [mint, {"commitment": "confirmed"}])
        except Exception:
            return rugcheck_metrics

        supply_value = (((supply_resp or {}).get("value") or {}).get("uiAmount")) or 0
        try:
            total_supply = float(supply_value or 0)
        except (TypeError, ValueError):
            total_supply = 0.0

        largest_accounts = (largest_resp or {}).get("value") or []
        if not largest_accounts:
            return rugcheck_metrics or self._empty_holder_metrics(total_supply=total_supply)

        account_addresses = [item.get("address") for item in largest_accounts if item.get("address")]
        owners = self._fetch_token_account_owners(account_addresses)

        owner_balances = {}
        top10_token_balance = 0.0
        top20_token_balance = 0.0
        for idx, item in enumerate(largest_accounts):
            try:
                balance = float(item.get("uiAmount") or item.get("uiAmountString") or 0)
            except (TypeError, ValueError):
                balance = 0.0
            address = item.get("address")
            owner = owners.get(address, address or f"unknown-{idx}")
            owner_balances[owner] = owner_balances.get(owner, 0.0) + balance
            if idx < 10:
                top10_token_balance += balance
            if idx < 20:
                top20_token_balance += balance

        sorted_owner_balances = sorted(owner_balances.values(), reverse=True)
        top10_owner_balance = sum(sorted_owner_balances[:10])
        top20_owner_balance = sum(sorted_owner_balances[:20])

        denominator = total_supply if total_supply > 0 else max(top20_owner_balance, 1.0)
        metrics = {
            "holder_count_estimate": float(len(owner_balances)),
            "top10_token_pct": top10_token_balance / denominator,
            "top20_token_pct": top20_token_balance / denominator,
            "top10_owner_pct": top10_owner_balance / denominator,
            "top20_owner_pct": top20_owner_balance / denominator,
            "largest_owner_pct": (sorted_owner_balances[0] / denominator) if sorted_owner_balances else 0.0,
            "total_supply": total_supply,
        }
        self._holder_cache[mint] = (now, metrics)
        return metrics

    def _rugcheck_holder_metrics(self, mint: str, total_supply: float = 0.0) -> dict[str, float]:
        try:
            response = self.session.get(
                self.settings.rugcheck_url.format(address=mint),
                timeout=5,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return self._empty_holder_metrics(total_supply=total_supply)

        top_holders = payload.get("topHolders") or []
        if not top_holders:
            return self._empty_holder_metrics(total_supply=total_supply)

        holder_pcts = []
        owners = set()
        for item in top_holders:
            try:
                pct = float(item.get("pct") or 0.0) / 100.0
            except (TypeError, ValueError):
                pct = 0.0
            if pct > 0:
                holder_pcts.append(pct)
            owner = item.get("owner") or item.get("address")
            if owner:
                owners.add(owner)

        try:
            total_holders = float(payload.get("totalHolders") or 0.0)
        except (TypeError, ValueError):
            total_holders = 0.0
        holder_count = max(total_holders, float(len(owners)))
        token = payload.get("token") or {}
        try:
            supply = float(token.get("supply") or total_supply or 0.0)
        except (TypeError, ValueError):
            supply = total_supply

        metrics = {
            "holder_count_estimate": holder_count,
            "top10_token_pct": min(sum(holder_pcts[:10]), 1.0),
            "top20_token_pct": min(sum(holder_pcts[:20]), 1.0),
            "top10_owner_pct": min(sum(holder_pcts[:10]), 1.0),
            "top20_owner_pct": min(sum(holder_pcts[:20]), 1.0),
            "largest_owner_pct": max(holder_pcts) if holder_pcts else 0.0,
            "total_supply": supply,
        }
        self._holder_cache[mint] = (time.time(), metrics)
        return metrics

    def _fetch_token_account_owners(self, addresses: list[str]) -> dict[str, str]:
        if not addresses:
            return {}
        owners = {}
        for i in range(0, len(addresses), 100):
            chunk = addresses[i:i + 100]
            try:
                result = self._rpc_call(
                    "getMultipleAccounts",
                    [chunk, {"encoding": "jsonParsed", "commitment": "confirmed"}],
                )
            except Exception:
                continue
            values = (result or {}).get("value") or []
            for address, account in zip(chunk, values):
                parsed = (((account or {}).get("data") or {}).get("parsed") or {})
                owner = (((parsed.get("info") or {}).get("owner")))
                if owner:
                    owners[address] = owner
        return owners

    def _rpc_call(self, method: str, params: list) -> dict:
        response = self.session.post(
            self.settings.rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            timeout=self.settings.rpc_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            raise RuntimeError(payload["error"])
        return payload.get("result") or {}

    def _empty_holder_metrics(self, total_supply: float = 0.0) -> dict[str, float]:
        return {
            "holder_count_estimate": 0.0,
            "top10_token_pct": 0.0,
            "top20_token_pct": 0.0,
            "top10_owner_pct": 0.0,
            "top20_owner_pct": 0.0,
            "largest_owner_pct": 0.0,
            "bundle_risk_score": 0.0,
            "total_supply": total_supply,
        }

    async def _run_stream(self) -> None:
        while True:
            try:
                async with websockets.connect(self.settings.pumpportal_ws_url, ping_interval=20, ping_timeout=20) as ws:
                    self._ws = ws
                    self._active_token_subscriptions = set()
                    await self._send_json({"method": "subscribeNewToken"})
                    await self._sync_token_subscriptions()
                    async for raw in ws:
                        try:
                            payload = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        self._handle_stream_payload(payload)
            except Exception:
                self._ws = None
                self._active_token_subscriptions = set()
                await asyncio.sleep(self.settings.monitor_poll_seconds)

    async def _sync_token_subscriptions(self) -> None:
        ws = self._ws
        if ws is None:
            return
        to_add = sorted(self._desired_token_subscriptions - self._active_token_subscriptions)
        to_remove = sorted(self._active_token_subscriptions - self._desired_token_subscriptions)
        if to_remove:
            await self._send_json({"method": "unsubscribeTokenTrade", "keys": to_remove})
            self._active_token_subscriptions -= set(to_remove)
        if to_add:
            await self._send_json({"method": "subscribeTokenTrade", "keys": to_add})
            self._active_token_subscriptions |= set(to_add)

    async def _send_json(self, payload: dict[str, Any]) -> None:
        if self._ws is None:
            return
        await self._ws.send(json.dumps(payload))

    def _handle_stream_payload(self, payload: dict[str, Any]) -> None:
        address = self._payload_address(payload)
        if address:
            self._merge_token_metadata(address, self._extract_token_metadata(payload))

        if payload.get("mint") and payload.get("pool") == "pump":
            if self._new_token_queue is not None:
                self._new_token_queue.put_nowait(payload)

        trade_snapshot = self._extract_trade_snapshot(payload)
        if trade_snapshot is None:
            return
        address, snapshot = trade_snapshot
        snapshot.update(self.get_token_metadata(address))
        self._trade_cache[address] = snapshot

    def _extract_trade_snapshot(self, payload: dict[str, Any]) -> Optional[Tuple[str, dict[str, float]]]:
        address = self._payload_address(payload)
        if not address:
            return None

        price = self._coerce_float(
            payload.get("priceUsd"),
            payload.get("price_usd"),
            payload.get("usdPrice"),
            payload.get("price"),
        )
        market_cap_sol = self._coerce_float(payload.get("marketCapSol"))
        market_cap_usd = self._coerce_float(payload.get("marketCapUsd"))
        liquidity_usd = self._coerce_float(
            payload.get("liquidityUsd"),
            payload.get("liquidity"),
        )
        if liquidity_usd <= 0 and market_cap_usd > 0:
            liquidity_usd = market_cap_usd / 2
        elif liquidity_usd <= 0 and market_cap_sol > 0:
            liquidity_usd = (market_cap_sol * self.settings.sol_price_usd) / 2

        if price <= 0 and market_cap_usd > 0:
            price = market_cap_usd / 1_000_000_000
        if price <= 0:
            return None

        volume_5m = self._coerce_float(
            payload.get("volume5m"),
            payload.get("volume_5m"),
            payload.get("solAmount"),
            payload.get("amountSol"),
        )
        return (
            str(address),
            {
                "price": price,
                "liquidity_usd": liquidity_usd,
                "volume_5m": volume_5m,
                "source": "pumpportal",
                "timestamp": time.time(),
            },
        )

    def _payload_address(self, payload: dict[str, Any]) -> str:
        return str(
            payload.get("mint")
            or payload.get("tokenAddress")
            or payload.get("baseMint")
            or payload.get("baseToken")
            or payload.get("ca")
            or ""
        )

    def _extract_token_metadata(self, payload: dict[str, Any]) -> dict[str, Any]:
        symbol = str(payload.get("symbol") or payload.get("ticker") or "").strip()
        name = str(payload.get("name") or payload.get("tokenName") or "").strip()
        metadata = {}
        if symbol:
            metadata["symbol"] = symbol
        if name:
            metadata["name"] = name
        for key in ("twitter", "telegram", "website", "url", "image"):
            value = payload.get(key)
            if value:
                metadata[key] = value
        return metadata

    def _merge_token_metadata(self, address: str, metadata: dict[str, Any]) -> None:
        if not address or not metadata:
            return
        current = self._metadata_cache.setdefault(address, {})
        for key, value in metadata.items():
            if value and not current.get(key):
                current[key] = value

    def _coerce_float(self, *values: Any) -> float:
        for value in values:
            try:
                numeric = float(value or 0)
            except (TypeError, ValueError):
                continue
            if numeric > 0:
                return numeric
        return 0.0


def build_scan_record(payload: dict[str, Any], sol_price_usd: float) -> dict[str, Any]:
    dev_buy = float(payload.get("solAmount", 0) or 0)
    market_cap_sol = float(payload.get("marketCapSol", 0) or 0)
    market_cap_usd = market_cap_sol * sol_price_usd
    price = market_cap_usd / 1_000_000_000 if market_cap_usd > 0 else 0.000000001
    has_socials = bool(payload.get("twitter") or payload.get("telegram") or payload.get("website"))
    progress = f"{int(min(max(market_cap_usd / 690, 0), 100))}%"
    first_seen_ts = int(time.time())
    created_ts = _normalize_timestamp(payload.get("created_timestamp") or payload.get("createdAt") or payload.get("created_at"))
    if created_ts <= 0:
        created_ts = first_seen_ts
    token_age_seconds = max(first_seen_ts - created_ts, 0) if created_ts > 0 else None

    score = 20.0
    if 0.5 <= dev_buy <= 6.0:
        score += 35.0 - min(abs(dev_buy - 2.5) * 8.0, 25.0)
    elif dev_buy > 6.0:
        score += 5.0
    if has_socials:
        score += 15.0
    if token_age_seconds is not None:
        if token_age_seconds <= 60:
            score += 20.0
        elif token_age_seconds <= 300:
            score += 10.0
        elif token_age_seconds <= 900:
            score += 3.0
    if 3_000 <= market_cap_usd <= 30_000:
        score += 20.0
    elif market_cap_usd < 1_500:
        score -= 15.0
    elif market_cap_usd > 100_000:
        score -= 10.0
    progress_value = int(progress.rstrip("%"))
    if 3 <= progress_value <= 35:
        score += 10.0
    elif progress_value >= 80:
        score -= 5.0
    score = max(min(score, 100.0), 0.0)

    symbol = str(payload.get("symbol") or payload.get("ticker") or payload.get("name") or "UNKNOWN").strip() or "UNKNOWN"
    return {
        "scan_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(first_seen_ts)),
        "platform": "PUMP",
        "symbol": symbol,
        "age": "",
        "addr": payload.get("mint"),
        "price": price,
        "liquidity": market_cap_usd / 2 if market_cap_usd > 0 else 0,
        "dev_buy": dev_buy,
        "progress": progress,
        "ratio": "n/a",
        "score": score,
        "first_seen_ts": first_seen_ts,
        "created_ts": created_ts,
        "holder_count_estimate": 0.0,
        "top10_owner_pct": 0.0,
        "top20_owner_pct": 0.0,
        "largest_owner_pct": 0.0,
        "bundle_risk_score": 0.0,
        "launch_quality_score": 0.0,
        "launch_signals": [],
        "socials": json.dumps(
            {
                "twitter": payload.get("twitter"),
                "telegram": payload.get("telegram"),
                "website": payload.get("website"),
            }
        ),
        "dex_url": f"https://pump.fun/{payload.get('mint')}",
    }


def _normalize_timestamp(raw_value: Any) -> int:
    if raw_value in (None, ""):
        return 0
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return 0
    if value > 1_000_000_000_000:
        value = value / 1000
    return int(value) if value > 0 else 0
