from __future__ import annotations

from typing import Any, Mapping, Protocol


class OpportunityStore(Protocol):
    def record_opportunity(self, payload: dict[str, Any]) -> None:
        ...


class OpportunityService:
    def __init__(self, database: OpportunityStore) -> None:
        self.database = database

    def record_signal(self, scan: Mapping[str, Any], reason: str) -> None:
        self._record("signal", scan, reason)

    def record_watch(self, scan: Mapping[str, Any], reason: str) -> None:
        self._record("watch", scan, reason)

    def record_rejected(self, scan: Mapping[str, Any], reason: str) -> None:
        self._record("rejected", scan, reason)

    def record_confirmed(self, scan: Mapping[str, Any], reason: str) -> None:
        self._record("confirmed", scan, reason)

    def record_ignored(self, scan: Mapping[str, Any], reason: str) -> None:
        self._record("ignored", scan, reason)

    def build_payload(self, opportunity_type: str, scan: Mapping[str, Any], reason: str) -> dict[str, Any]:
        return {
            "type": opportunity_type,
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

    def _record(self, opportunity_type: str, scan: Mapping[str, Any], reason: str) -> None:
        self.database.record_opportunity(self.build_payload(opportunity_type, scan, reason))
