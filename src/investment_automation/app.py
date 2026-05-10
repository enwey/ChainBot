from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Optional

from .db import Database
from .engine import TradingEngine
from .execution import TradeExecutor
from .maintenance import MaintenanceService
from .market_data import MarketDataClient
from .news import NewsClient
from .runtime_safety import RuntimeSafetyManager
from .settings import Settings, get_settings
from .web import create_server

logger = logging.getLogger(__name__)


def configure_logging(level_name: str = "INFO") -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        force=True,
    )


class Application:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.started_at = int(time.time())
        self.database = Database(settings)
        # Ensure schema-dependent collaborators can read persisted state on first boot.
        self.database.initialize()
        self.maintenance = MaintenanceService(settings, self.database)
        self.runtime_safety = RuntimeSafetyManager(settings)
        self.market_data = MarketDataClient(settings, failure_sink=self.runtime_safety)
        self.news_client = NewsClient(settings, failure_sink=self.runtime_safety)
        self.executor = TradeExecutor(
            settings, self.market_data, safety_manager=self.runtime_safety
        )
        self.engine = TradingEngine(
            settings,
            self.database,
            self.market_data,
            self.executor,
            self.news_client,
            runtime_safety=self.runtime_safety,
        )
        self.server = create_server(
            settings=settings,
            database=self.database,
            executor=self.executor,
            engine=self.engine,
            market_data=self.market_data,
            started_at=self.started_at,
        )
        self.server_thread: Optional[threading.Thread] = None
        self._stopped = False

    def bootstrap(self) -> None:
        try:
            self.maintenance.run_startup_tasks()
        except Exception:
            logger.exception("Startup maintenance failed")

    def start_http_server(self) -> None:
        if self.server_thread and self.server_thread.is_alive():
            return
        self.server_thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
            name="chainbot-http-server",
        )
        self.server_thread.start()
        logger.info("HTTP server listening on http://%s:%s", self.settings.host, self.settings.port)
        logger.info("Execution mode: %s", "live" if self.settings.is_live_mode else "paper")

    async def run(self) -> None:
        self.bootstrap()
        self.start_http_server()
        try:
            await self.engine.run()
        finally:
            await self.stop()

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True

        self.server.shutdown()
        self.server.server_close()
        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(timeout=5)

        self.engine.close()
        await self.market_data.aclose()
        self.executor.close()
        self.news_client.close()


def main() -> None:
    configure_logging()
    settings = get_settings()
    configure_logging(settings.log_level)
    app = Application(settings)

    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        logger.info("Shutting down service")
