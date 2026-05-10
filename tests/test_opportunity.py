from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from investment_automation.opportunity import OpportunityService


class RecordingStore:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def record_opportunity(self, payload: dict) -> None:
        self.payloads.append(payload)


class OpportunityServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = RecordingStore()
        self.service = OpportunityService(self.store)

    def test_record_signal_for_open_buy_flow(self) -> None:
        scan = self._scan()

        self.service.record_signal(scan, "score=92, size=$15.00")

        self.assert_payload(
            self.store.payloads[-1],
            scan,
            expected_type="signal",
            expected_reason="score=92, size=$15.00",
        )

    def test_record_watch_for_add_and_update_flow(self) -> None:
        scan = self._scan(scan_time="")

        self.service.record_watch(scan, "进入观察池")
        self.service.record_watch(scan, "观察中 12s")

        self.assertEqual(len(self.store.payloads), 2)
        self.assert_payload(
            self.store.payloads[0],
            scan,
            expected_type="watch",
            expected_reason="进入观察池",
        )
        self.assert_payload(
            self.store.payloads[1],
            scan,
            expected_type="watch",
            expected_reason="观察中 12s",
        )
        self.assertEqual(self.store.payloads[1]["scan_time"], "")

    def test_record_rejected_for_overflow_and_radar_flows(self) -> None:
        scan = self._scan()

        self.service.record_rejected(scan, "观察池已满且候选质量不足")
        self.service.record_rejected(scan, "abnormal radar momentum")

        self.assertEqual(len(self.store.payloads), 2)
        self.assert_payload(
            self.store.payloads[0],
            scan,
            expected_type="rejected",
            expected_reason="观察池已满且候选质量不足",
        )
        self.assert_payload(
            self.store.payloads[1],
            scan,
            expected_type="rejected",
            expected_reason="abnormal radar momentum",
        )

    def test_record_confirmed_for_watch_to_buy_flow(self) -> None:
        scan = self._scan()

        self.service.record_confirmed(scan, "观察确认买入")

        self.assert_payload(
            self.store.payloads[-1],
            scan,
            expected_type="confirmed",
            expected_reason="观察确认买入",
        )

    def test_record_ignored_for_low_quality_flow(self) -> None:
        scan = self._scan(scan_time=None)

        self.service.record_ignored(scan, "low launch quality 61: no strong signal")

        self.assert_payload(
            self.store.payloads[-1],
            scan,
            expected_type="ignored",
            expected_reason="low launch quality 61: no strong signal",
        )
        self.assertEqual(self.store.payloads[-1]["scan_time"], "")

    def assert_payload(
        self,
        payload: dict,
        scan: dict,
        *,
        expected_type: str,
        expected_reason: str,
    ) -> None:
        self.assertEqual(
            payload,
            {
                "type": expected_type,
                "symbol": scan["symbol"],
                "score": scan["score"],
                "reason": expected_reason,
                "addr": scan["addr"],
                "liq": scan["liquidity"],
                "vol": scan["dev_buy"],
                "dex_url": scan["dex_url"],
                "scan_time": scan["scan_time"] or "",
                "socials": scan["socials"],
                "progress": scan["progress"],
            },
        )

    def _scan(self, *, scan_time: str | None = "2026-05-10 20:00:00") -> dict:
        return {
            "addr": "token-1",
            "symbol": "AIBOT",
            "score": 92.0,
            "liquidity": 4200.0,
            "dev_buy": 1.7,
            "dex_url": "https://pump.fun/token-1",
            "scan_time": scan_time,
            "socials": '{"twitter":"https://x.com/aibot"}',
            "progress": "12%",
        }


if __name__ == "__main__":
    unittest.main()
