from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class SettingsValidationError(ValueError):
    """Raised when application settings are invalid."""


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _csv(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name, default)
    return tuple(item.strip().lower() for item in raw.split(",") if item.strip())


def _optional(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise SettingsValidationError(f"{name} must be an integer, got {raw!r}") from exc


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise SettingsValidationError(f"{name} must be a number, got {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    service_name: str
    environment: str
    log_level: str
    base_dir: Path
    data_dir: Path
    web_dir: Path
    maintenance_enabled: bool
    scan_log_retention_days: int
    opportunity_retention_days: int
    news_event_retention_days: int
    decision_audit_retention_days: int
    host: str
    port: int
    rpc_url: str
    execution_mode: str
    enable_live_trading: bool
    admin_api_token: Optional[str]
    initial_balance_usd: float
    position_size_usd: float
    max_open_positions: int
    min_score_to_buy: float
    min_watch_score: float
    min_dev_buy_sol: float
    max_dev_buy_sol: float
    min_liquidity_usd: float
    max_liquidity_usd: float
    min_watch_liquidity_usd: float
    max_watchlist_size: int
    min_launch_quality_score: float
    min_launch_dev_buy_sol: float
    min_launch_signal_count: int
    min_fast_track_score: float
    radar_track_seconds: int
    radar_poll_seconds: int
    max_radar_tracked_tokens: int
    abnormal_kline_enabled: bool
    abnormal_kline_min_seconds: int
    abnormal_kline_min_samples: int
    abnormal_kline_min_gain_pct: float
    abnormal_kline_max_drawdown_pct: float
    abnormal_kline_max_down_move_ratio: float
    abnormal_kline_max_volume_5m_usd: float
    abnormal_kline_min_gain_to_volume: float
    max_token_age_seconds: int
    max_top10_owner_pct: float
    max_top20_owner_pct: float
    min_holder_count: int
    bundle_risk_top10_owner_pct: float
    bundle_risk_largest_owner_pct: float
    bundle_risk_min_holders: int
    min_observation_seconds: int
    max_observation_seconds: int
    observation_poll_seconds: int
    min_observation_price_change_pct: float
    max_observation_drawdown_pct: float
    min_observation_liquidity_ratio: float
    min_buy_interval_seconds: int
    max_wallet_exposure_pct: float
    max_position_size_usd: float
    max_daily_loss_usd: float
    max_consecutive_dependency_failures: int
    dependency_failure_window_seconds: int
    abnormal_exit_cooldown_seconds: int
    abnormal_exit_threshold: int
    scalp_override_enabled: bool
    scalp_min_move_pct: float
    scalp_max_drawdown_pct: float
    scalp_min_liquidity_ratio: float
    scalp_min_volume_5m_usd: float
    scalp_min_holders: int
    scalp_risk_position_multiplier: float
    narrative_enabled: bool
    narrative_keywords: tuple[str, ...]
    trusted_kol_keywords: tuple[str, ...]
    narrative_score_bonus: float
    trusted_kol_score_bonus: float
    narrative_max_bonus: float
    narrative_position_multiplier: float
    news_enabled: bool
    news_poll_seconds: int
    news_event_ttl_seconds: int
    news_match_bonus: float
    news_max_bonus: float
    news_min_confidence: float
    news_feeds: tuple[str, ...]
    kol_monitor_enabled: bool
    kol_feeds: tuple[str, ...]
    kol_profiles: tuple[str, ...]
    kol_source_weight: float
    kol_event_ttl_seconds: int
    emergency_stop_loss_pct: float
    emergency_liquidity_ratio: float
    fast_exit_peak_pct: float
    fast_exit_drawdown_pct: float
    profit_lock_min_pct: float
    profit_lock_sell_pct: float
    moonbag_min_usd: float
    profit_lock_drawdown_pct: float
    profit_lock_liquidity_ratio: float
    profit_lock_min_hold_seconds: int
    stale_position_seconds: int
    min_volume_to_position_ratio: float
    zombie_position_seconds: int
    zombie_min_volume_5m_usd: float
    zombie_non_live_seconds: int
    zombie_min_profit_keep_pct: float
    sol_price_usd: float
    buy_slippage_bps: int
    sell_slippage_bps: int
    paper_buy_slippage_bps: int
    paper_sell_slippage_bps: int
    paper_fee_bps: int
    order_idempotency_ttl_seconds: int
    monitor_poll_seconds: int
    pricing_poll_seconds: int
    holder_refresh_seconds: int
    dexscreener_timeout_seconds: int
    rpc_timeout_seconds: int
    pumpportal_ws_url: str
    dexscreener_token_url: str
    rugcheck_url: str
    jupiter_quote_url: str
    jupiter_swap_url: str
    solana_private_key: Optional[str]

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            service_name=os.getenv("APP_NAME", "ChainBot").strip() or "ChainBot",
            environment=os.getenv("APP_ENV", "development").strip().lower() or "development",
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            base_dir=Path(os.getenv("APP_BASE_DIR", Path.cwd())),
            data_dir=Path(os.getenv("DATA_DIR", "data")),
            web_dir=Path(os.getenv("WEB_DIR", "web")),
            maintenance_enabled=_bool("MAINTENANCE_ENABLED", True),
            scan_log_retention_days=_int("SCAN_LOG_RETENTION_DAYS", 7),
            opportunity_retention_days=_int("OPPORTUNITY_RETENTION_DAYS", 14),
            news_event_retention_days=_int("NEWS_EVENT_RETENTION_DAYS", 3),
            decision_audit_retention_days=_int("DECISION_AUDIT_RETENTION_DAYS", 30),
            host=os.getenv("APP_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=_int("APP_PORT", 8000),
            rpc_url=os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com").strip(),
            execution_mode=os.getenv("EXECUTION_MODE", "paper").strip().lower(),
            enable_live_trading=_bool("ENABLE_LIVE_TRADING", False),
            admin_api_token=_optional("ADMIN_API_TOKEN"),
            initial_balance_usd=_float("INITIAL_BALANCE_USD", 200.0),
            position_size_usd=_float("POSITION_SIZE_USD", 15.0),
            max_open_positions=_int("MAX_OPEN_POSITIONS", 2),
            min_score_to_buy=_float("MIN_SCORE_TO_BUY", 85.0),
            min_watch_score=_float("MIN_WATCH_SCORE", 72.0),
            min_dev_buy_sol=_float("MIN_DEV_BUY_SOL", 1.0),
            max_dev_buy_sol=_float("MAX_DEV_BUY_SOL", 4.0),
            min_liquidity_usd=_float("MIN_LIQUIDITY_USD", 2500.0),
            max_liquidity_usd=_float("MAX_LIQUIDITY_USD", 50000.0),
            min_watch_liquidity_usd=_float("MIN_WATCH_LIQUIDITY_USD", 2200.0),
            max_watchlist_size=_int("MAX_WATCHLIST_SIZE", 20),
            min_launch_quality_score=_float("MIN_LAUNCH_QUALITY_SCORE", 55.0),
            min_launch_dev_buy_sol=_float("MIN_LAUNCH_DEV_BUY_SOL", 0.75),
            min_launch_signal_count=_int("MIN_LAUNCH_SIGNAL_COUNT", 2),
            min_fast_track_score=_float("MIN_FAST_TRACK_SCORE", 90.0),
            radar_track_seconds=_int("RADAR_TRACK_SECONDS", 240),
            radar_poll_seconds=_int("RADAR_POLL_SECONDS", 2),
            max_radar_tracked_tokens=_int("MAX_RADAR_TRACKED_TOKENS", 60),
            abnormal_kline_enabled=_bool("ABNORMAL_KLINE_ENABLED", True),
            abnormal_kline_min_seconds=_int("ABNORMAL_KLINE_MIN_SECONDS", 18),
            abnormal_kline_min_samples=_int("ABNORMAL_KLINE_MIN_SAMPLES", 6),
            abnormal_kline_min_gain_pct=_float("ABNORMAL_KLINE_MIN_GAIN_PCT", 0.35),
            abnormal_kline_max_drawdown_pct=_float("ABNORMAL_KLINE_MAX_DRAWDOWN_PCT", 0.035),
            abnormal_kline_max_down_move_ratio=_float("ABNORMAL_KLINE_MAX_DOWN_MOVE_RATIO", 0.15),
            abnormal_kline_max_volume_5m_usd=_float("ABNORMAL_KLINE_MAX_VOLUME_5M_USD", 350.0),
            abnormal_kline_min_gain_to_volume=_float("ABNORMAL_KLINE_MIN_GAIN_TO_VOLUME", 0.0015),
            max_token_age_seconds=_int("MAX_TOKEN_AGE_SECONDS", 300),
            max_top10_owner_pct=_float("MAX_TOP10_OWNER_PCT", 0.45),
            max_top20_owner_pct=_float("MAX_TOP20_OWNER_PCT", 0.65),
            min_holder_count=_int("MIN_HOLDER_COUNT", 25),
            bundle_risk_top10_owner_pct=_float("BUNDLE_RISK_TOP10_OWNER_PCT", 0.35),
            bundle_risk_largest_owner_pct=_float("BUNDLE_RISK_LARGEST_OWNER_PCT", 0.12),
            bundle_risk_min_holders=_int("BUNDLE_RISK_MIN_HOLDERS", 18),
            min_observation_seconds=_int("MIN_OBSERVATION_SECONDS", 30),
            max_observation_seconds=_int("MAX_OBSERVATION_SECONDS", 180),
            observation_poll_seconds=_int("OBSERVATION_POLL_SECONDS", 2),
            min_observation_price_change_pct=_float("MIN_OBSERVATION_PRICE_CHANGE_PCT", 0.03),
            max_observation_drawdown_pct=_float("MAX_OBSERVATION_DRAWDOWN_PCT", 0.15),
            min_observation_liquidity_ratio=_float("MIN_OBSERVATION_LIQUIDITY_RATIO", 0.85),
            min_buy_interval_seconds=_int("MIN_BUY_INTERVAL_SECONDS", 20),
            max_wallet_exposure_pct=_float("MAX_WALLET_EXPOSURE_PCT", 0.12),
            max_position_size_usd=_float("MAX_POSITION_SIZE_USD", 25.0),
            max_daily_loss_usd=_float("MAX_DAILY_LOSS_USD", 40.0),
            max_consecutive_dependency_failures=_int("MAX_CONSECUTIVE_DEPENDENCY_FAILURES", 4),
            dependency_failure_window_seconds=_int("DEPENDENCY_FAILURE_WINDOW_SECONDS", 300),
            abnormal_exit_cooldown_seconds=_int("ABNORMAL_EXIT_COOLDOWN_SECONDS", 300),
            abnormal_exit_threshold=_int("ABNORMAL_EXIT_THRESHOLD", 2),
            scalp_override_enabled=_bool("SCALP_OVERRIDE_ENABLED", True),
            scalp_min_move_pct=_float("SCALP_MIN_MOVE_PCT", 0.08),
            scalp_max_drawdown_pct=_float("SCALP_MAX_DRAWDOWN_PCT", 0.08),
            scalp_min_liquidity_ratio=_float("SCALP_MIN_LIQUIDITY_RATIO", 0.92),
            scalp_min_volume_5m_usd=_float("SCALP_MIN_VOLUME_5M_USD", 0.5),
            scalp_min_holders=_int("SCALP_MIN_HOLDERS", 3),
            scalp_risk_position_multiplier=_float("SCALP_RISK_POSITION_MULTIPLIER", 0.45),
            narrative_enabled=_bool("NARRATIVE_ENABLED", True),
            narrative_keywords=_csv(
                "NARRATIVE_KEYWORDS",
                "ai,news,cpi,fed,sec,etf,cex,listing,binance,coinbase,court,war,election,trump,elon,tesla,cz,openai,nvidia",
            ),
            trusted_kol_keywords=_csv(
                "TRUSTED_KOL_KEYWORDS", "cz,binance,elon,trump,vitalik,solana"
            ),
            narrative_score_bonus=_float("NARRATIVE_SCORE_BONUS", 10.0),
            trusted_kol_score_bonus=_float("TRUSTED_KOL_SCORE_BONUS", 12.0),
            narrative_max_bonus=_float("NARRATIVE_MAX_BONUS", 22.0),
            narrative_position_multiplier=_float("NARRATIVE_POSITION_MULTIPLIER", 1.2),
            news_enabled=_bool("NEWS_ENABLED", True),
            news_poll_seconds=_int("NEWS_POLL_SECONDS", 120),
            news_event_ttl_seconds=_int("NEWS_EVENT_TTL_SECONDS", 21600),
            news_match_bonus=_float("NEWS_MATCH_BONUS", 12.0),
            news_max_bonus=_float("NEWS_MAX_BONUS", 24.0),
            news_min_confidence=_float("NEWS_MIN_CONFIDENCE", 0.35),
            news_feeds=_csv(
                "NEWS_FEEDS",
                "https://www.coindesk.com/arc/outboundfeeds/rss/,https://cointelegraph.com/rss,https://decrypt.co/feed",
            ),
            kol_monitor_enabled=_bool("KOL_MONITOR_ENABLED", True),
            kol_feeds=_csv("KOL_FEEDS", ""),
            kol_profiles=_csv(
                "KOL_PROFILES",
                "elon=elon|musk|tesla|spacex|x|doge|dogecoin|grok,cz=cz|binance|bnb|build|bnbchain,heyi=heyi|he yi|何一|合一|binance|bnb,vitalik=vitalik|ethereum|eth|base|zksync|l2",
            ),
            kol_source_weight=_float("KOL_SOURCE_WEIGHT", 0.9),
            kol_event_ttl_seconds=_int("KOL_EVENT_TTL_SECONDS", 7200),
            emergency_stop_loss_pct=_float("EMERGENCY_STOP_LOSS_PCT", -0.18),
            emergency_liquidity_ratio=_float("EMERGENCY_LIQUIDITY_RATIO", 0.55),
            fast_exit_peak_pct=_float("FAST_EXIT_PEAK_PCT", 0.55),
            fast_exit_drawdown_pct=_float("FAST_EXIT_DRAWDOWN_PCT", 0.12),
            profit_lock_min_pct=_float("PROFIT_LOCK_MIN_PCT", 0.18),
            profit_lock_sell_pct=_float("PROFIT_LOCK_SELL_PCT", 0.75),
            moonbag_min_usd=_float("MOONBAG_MIN_USD", 3.0),
            profit_lock_drawdown_pct=_float("PROFIT_LOCK_DRAWDOWN_PCT", 0.09),
            profit_lock_liquidity_ratio=_float("PROFIT_LOCK_LIQUIDITY_RATIO", 0.82),
            profit_lock_min_hold_seconds=_int("PROFIT_LOCK_MIN_HOLD_SECONDS", 20),
            stale_position_seconds=_int("STALE_POSITION_SECONDS", 90),
            min_volume_to_position_ratio=_float("MIN_VOLUME_TO_POSITION_RATIO", 2.5),
            zombie_position_seconds=_int("ZOMBIE_POSITION_SECONDS", 900),
            zombie_min_volume_5m_usd=_float("ZOMBIE_MIN_VOLUME_5M_USD", 300.0),
            zombie_non_live_seconds=_int("ZOMBIE_NON_LIVE_SECONDS", 240),
            zombie_min_profit_keep_pct=_float("ZOMBIE_MIN_PROFIT_KEEP_PCT", 0.5),
            sol_price_usd=_float("SOL_PRICE_USD", 150.0),
            buy_slippage_bps=_int("BUY_SLIPPAGE_BPS", 300),
            sell_slippage_bps=_int("SELL_SLIPPAGE_BPS", 400),
            paper_buy_slippage_bps=_int("PAPER_BUY_SLIPPAGE_BPS", 150),
            paper_sell_slippage_bps=_int("PAPER_SELL_SLIPPAGE_BPS", 200),
            paper_fee_bps=_int("PAPER_FEE_BPS", 50),
            order_idempotency_ttl_seconds=_int("ORDER_IDEMPOTENCY_TTL_SECONDS", 180),
            monitor_poll_seconds=_int("MONITOR_POLL_SECONDS", 2),
            pricing_poll_seconds=_int("PRICING_POLL_SECONDS", 2),
            holder_refresh_seconds=_int("HOLDER_REFRESH_SECONDS", 20),
            dexscreener_timeout_seconds=_int("DEXSCREENER_TIMEOUT_SECONDS", 4),
            rpc_timeout_seconds=_int("RPC_TIMEOUT_SECONDS", 8),
            pumpportal_ws_url=os.getenv(
                "PUMPPORTAL_WS_URL", "wss://pumpportal.fun/api/data"
            ).strip(),
            dexscreener_token_url=os.getenv(
                "DEXSCREENER_TOKEN_URL",
                "https://api.dexscreener.com/latest/dex/tokens",
            ).strip(),
            rugcheck_url=os.getenv(
                "RUGCHECK_URL",
                "https://api.rugcheck.xyz/v1/tokens/{address}/report",
            ).strip(),
            jupiter_quote_url=os.getenv(
                "JUPITER_QUOTE_URL", "https://quote-api.jup.ag/v6/quote"
            ).strip(),
            jupiter_swap_url=os.getenv(
                "JUPITER_SWAP_URL", "https://quote-api.jup.ag/v6/swap"
            ).strip(),
            solana_private_key=_optional("SOLANA_PRIVATE_KEY"),
        )

    @property
    def resolved_data_dir(self) -> Path:
        return (self.base_dir / self.data_dir).resolve()

    @property
    def resolved_web_dir(self) -> Path:
        return (self.base_dir / self.web_dir).resolve()

    @property
    def database_path(self) -> Path:
        return self.resolved_data_dir / "trading.db"

    @property
    def is_live_mode(self) -> bool:
        return self.execution_mode == "live" and self.enable_live_trading

    @property
    def binds_public_interface(self) -> bool:
        return self.host not in {"127.0.0.1", "localhost", "::1"}

    def validate(self) -> list[str]:
        errors: list[str] = []
        warnings: list[str] = []

        if self.execution_mode not in {"paper", "live"}:
            errors.append("EXECUTION_MODE must be either 'paper' or 'live'.")
        if not 1 <= self.port <= 65535:
            errors.append("APP_PORT must be between 1 and 65535.")
        if self.initial_balance_usd <= 0:
            errors.append("INITIAL_BALANCE_USD must be greater than 0.")
        if self.position_size_usd <= 0:
            errors.append("POSITION_SIZE_USD must be greater than 0.")
        if self.max_position_size_usd <= 0:
            errors.append("MAX_POSITION_SIZE_USD must be greater than 0.")
        if self.max_daily_loss_usd <= 0:
            errors.append("MAX_DAILY_LOSS_USD must be greater than 0.")
        if self.order_idempotency_ttl_seconds < 1:
            errors.append("ORDER_IDEMPOTENCY_TTL_SECONDS must be at least 1.")
        if self.max_open_positions < 1:
            errors.append("MAX_OPEN_POSITIONS must be at least 1.")
        if self.max_consecutive_dependency_failures < 1:
            errors.append("MAX_CONSECUTIVE_DEPENDENCY_FAILURES must be at least 1.")
        if self.dependency_failure_window_seconds < 1:
            errors.append("DEPENDENCY_FAILURE_WINDOW_SECONDS must be at least 1.")
        if self.abnormal_exit_cooldown_seconds < 1:
            errors.append("ABNORMAL_EXIT_COOLDOWN_SECONDS must be at least 1.")
        if self.abnormal_exit_threshold < 1:
            errors.append("ABNORMAL_EXIT_THRESHOLD must be at least 1.")
        if self.max_watchlist_size < 1:
            errors.append("MAX_WATCHLIST_SIZE must be at least 1.")
        if self.scan_log_retention_days < 0:
            errors.append("SCAN_LOG_RETENTION_DAYS must be greater than or equal to 0.")
        if self.opportunity_retention_days < 0:
            errors.append("OPPORTUNITY_RETENTION_DAYS must be greater than or equal to 0.")
        if self.news_event_retention_days < 0:
            errors.append("NEWS_EVENT_RETENTION_DAYS must be greater than or equal to 0.")
        if self.decision_audit_retention_days < 0:
            errors.append("DECISION_AUDIT_RETENTION_DAYS must be greater than or equal to 0.")
        if not 0 < self.max_wallet_exposure_pct <= 1:
            errors.append("MAX_WALLET_EXPOSURE_PCT must be between 0 and 1.")
        if not 0 <= self.min_score_to_buy <= 100:
            errors.append("MIN_SCORE_TO_BUY must be between 0 and 100.")
        if not 0 <= self.min_watch_score <= 100:
            errors.append("MIN_WATCH_SCORE must be between 0 and 100.")
        if self.min_watch_score > self.min_score_to_buy:
            warnings.append(
                "MIN_WATCH_SCORE is higher than MIN_SCORE_TO_BUY, which narrows the watch funnel."
            )
        if self.min_dev_buy_sol > self.max_dev_buy_sol:
            errors.append("MIN_DEV_BUY_SOL cannot be greater than MAX_DEV_BUY_SOL.")
        if self.min_liquidity_usd > self.max_liquidity_usd:
            errors.append("MIN_LIQUIDITY_USD cannot be greater than MAX_LIQUIDITY_USD.")
        if self.min_observation_seconds > self.max_observation_seconds:
            errors.append("MIN_OBSERVATION_SECONDS cannot be greater than MAX_OBSERVATION_SECONDS.")
        if self.is_live_mode and not self.solana_private_key:
            errors.append("SOLANA_PRIVATE_KEY is required when live trading is enabled.")
        if self.binds_public_interface and not self.admin_api_token:
            errors.append(
                "ADMIN_API_TOKEN is required when APP_HOST is not bound to a loopback interface."
            )
        if not self.resolved_web_dir.exists():
            errors.append(f"WEB_DIR does not exist: {self.resolved_web_dir}")
        if not self.rpc_url:
            errors.append("SOLANA_RPC_URL must not be empty.")
        if not self.pumpportal_ws_url:
            errors.append("PUMPPORTAL_WS_URL must not be empty.")

        if errors:
            raise SettingsValidationError("Invalid application settings:\n- " + "\n- ".join(errors))
        return warnings


def get_settings() -> Settings:
    settings = Settings.from_env()
    for warning in settings.validate():
        logger.warning("Settings warning: %s", warning)
    return settings
