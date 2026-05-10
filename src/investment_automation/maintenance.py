from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

from .db import Database
from .settings import Settings

logger = logging.getLogger(__name__)

SECONDS_PER_DAY = 24 * 60 * 60


@dataclass(frozen=True)
class MaintenanceResult:
    scan_logs_deleted: int = 0
    opportunities_deleted: int = 0
    news_events_deleted: int = 0
    decision_audits_deleted: int = 0

    @property
    def total_deleted(self) -> int:
        return (
            self.scan_logs_deleted
            + self.opportunities_deleted
            + self.news_events_deleted
            + self.decision_audits_deleted
        )


class MaintenanceService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.database = database
        self.clock = clock

    def run_startup_tasks(self) -> MaintenanceResult:
        if not self.settings.maintenance_enabled:
            logger.info("Startup maintenance is disabled")
            return MaintenanceResult()

        now_ts = int(self.clock())
        result = MaintenanceResult(
            scan_logs_deleted=self._delete_scan_logs(now_ts),
            opportunities_deleted=self._delete_opportunities(now_ts),
            news_events_deleted=self._delete_news_events(now_ts),
            decision_audits_deleted=self._delete_decision_audits(now_ts),
        )

        logger.info(
            "Startup maintenance finished: %s rows removed (%s scan_logs, %s opportunities, %s news_events, %s decision_audits)",
            result.total_deleted,
            result.scan_logs_deleted,
            result.opportunities_deleted,
            result.news_events_deleted,
            result.decision_audits_deleted,
        )
        return result

    def _delete_scan_logs(self, now_ts: int) -> int:
        if self.settings.scan_log_retention_days <= 0:
            return 0
        cutoff_ts = self._cutoff_timestamp(now_ts, self.settings.scan_log_retention_days)
        return self.database.delete_scan_logs_older_than(cutoff_ts, self._timestamp_text(cutoff_ts))

    def _delete_opportunities(self, now_ts: int) -> int:
        if self.settings.opportunity_retention_days <= 0:
            return 0
        cutoff_ts = self._cutoff_timestamp(now_ts, self.settings.opportunity_retention_days)
        return self.database.delete_opportunities_older_than(self._timestamp_text(cutoff_ts))

    def _delete_news_events(self, now_ts: int) -> int:
        if self.settings.news_event_retention_days <= 0:
            return 0
        cutoff_ts = self._cutoff_timestamp(now_ts, self.settings.news_event_retention_days)
        return self.database.delete_news_events_older_than(cutoff_ts)

    def _delete_decision_audits(self, now_ts: int) -> int:
        if self.settings.decision_audit_retention_days <= 0:
            return 0
        cutoff_ts = self._cutoff_timestamp(now_ts, self.settings.decision_audit_retention_days)
        return self.database.delete_decision_audit_older_than(
            cutoff_ts, self._timestamp_text(cutoff_ts)
        )

    def _cutoff_timestamp(self, now_ts: int, retention_days: int) -> int:
        return max(0, now_ts - (retention_days * SECONDS_PER_DAY))

    def _timestamp_text(self, timestamp: int) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
