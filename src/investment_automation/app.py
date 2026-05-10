from __future__ import annotations

import asyncio
import logging
import threading

from .db import Database
from .engine import TradingEngine
from .execution import TradeExecutor
from .market_data import MarketDataClient
from .news import NewsClient
from .settings import get_settings
from .web import create_server


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def main() -> None:
    configure_logging()
    settings = get_settings()
    database = Database(settings)
    database.initialize()

    market_data = MarketDataClient(settings)
    news_client = NewsClient(settings)
    executor = TradeExecutor(settings, market_data)
    engine = TradingEngine(settings, database, market_data, executor, news_client)
    server = create_server(settings, database, executor, engine)

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    logging.info("HTTP server listening on http://%s:%s", settings.host, settings.port)
    logging.info("Execution mode: %s", "live" if settings.is_live_mode else "paper")

    try:
        asyncio.run(engine.run())
    except KeyboardInterrupt:
        logging.info("Shutting down service")
        server.shutdown()
