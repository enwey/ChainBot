from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _csv(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name, default)
    return tuple(item.strip().lower() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    base_dir: Path = Path(os.getenv("APP_BASE_DIR", Path.cwd()))
    data_dir: Path = Path(os.getenv("DATA_DIR", "data"))
    web_dir: Path = Path(os.getenv("WEB_DIR", "web"))
    host: str = os.getenv("APP_HOST", "127.0.0.1")
    port: int = int(os.getenv("APP_PORT", "8000"))
    rpc_url: str = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
    execution_mode: str = os.getenv("EXECUTION_MODE", "paper").strip().lower()
    enable_live_trading: bool = _bool("ENABLE_LIVE_TRADING", False)
    initial_balance_usd: float = float(os.getenv("INITIAL_BALANCE_USD", "200"))
    position_size_usd: float = float(os.getenv("POSITION_SIZE_USD", "15"))
    max_open_positions: int = int(os.getenv("MAX_OPEN_POSITIONS", "2"))
    min_score_to_buy: float = float(os.getenv("MIN_SCORE_TO_BUY", "85"))
    min_watch_score: float = float(os.getenv("MIN_WATCH_SCORE", "72"))
    min_dev_buy_sol: float = float(os.getenv("MIN_DEV_BUY_SOL", "1.0"))
    max_dev_buy_sol: float = float(os.getenv("MAX_DEV_BUY_SOL", "4.0"))
    min_liquidity_usd: float = float(os.getenv("MIN_LIQUIDITY_USD", "2500"))
    max_liquidity_usd: float = float(os.getenv("MAX_LIQUIDITY_USD", "50000"))
    min_watch_liquidity_usd: float = float(os.getenv("MIN_WATCH_LIQUIDITY_USD", "2200"))
    max_watchlist_size: int = int(os.getenv("MAX_WATCHLIST_SIZE", "20"))
    min_launch_quality_score: float = float(os.getenv("MIN_LAUNCH_QUALITY_SCORE", "55"))
    min_launch_dev_buy_sol: float = float(os.getenv("MIN_LAUNCH_DEV_BUY_SOL", "0.75"))
    min_launch_signal_count: int = int(os.getenv("MIN_LAUNCH_SIGNAL_COUNT", "2"))
    min_fast_track_score: float = float(os.getenv("MIN_FAST_TRACK_SCORE", "90"))
    radar_track_seconds: int = int(os.getenv("RADAR_TRACK_SECONDS", "240"))
    radar_poll_seconds: int = int(os.getenv("RADAR_POLL_SECONDS", "2"))
    max_radar_tracked_tokens: int = int(os.getenv("MAX_RADAR_TRACKED_TOKENS", "60"))
    abnormal_kline_enabled: bool = _bool("ABNORMAL_KLINE_ENABLED", True)
    abnormal_kline_min_seconds: int = int(os.getenv("ABNORMAL_KLINE_MIN_SECONDS", "18"))
    abnormal_kline_min_samples: int = int(os.getenv("ABNORMAL_KLINE_MIN_SAMPLES", "6"))
    abnormal_kline_min_gain_pct: float = float(os.getenv("ABNORMAL_KLINE_MIN_GAIN_PCT", "0.35"))
    abnormal_kline_max_drawdown_pct: float = float(os.getenv("ABNORMAL_KLINE_MAX_DRAWDOWN_PCT", "0.035"))
    abnormal_kline_max_down_move_ratio: float = float(os.getenv("ABNORMAL_KLINE_MAX_DOWN_MOVE_RATIO", "0.15"))
    abnormal_kline_max_volume_5m_usd: float = float(os.getenv("ABNORMAL_KLINE_MAX_VOLUME_5M_USD", "350"))
    abnormal_kline_min_gain_to_volume: float = float(os.getenv("ABNORMAL_KLINE_MIN_GAIN_TO_VOLUME", "0.0015"))
    max_token_age_seconds: int = int(os.getenv("MAX_TOKEN_AGE_SECONDS", "300"))
    max_top10_owner_pct: float = float(os.getenv("MAX_TOP10_OWNER_PCT", "0.45"))
    max_top20_owner_pct: float = float(os.getenv("MAX_TOP20_OWNER_PCT", "0.65"))
    min_holder_count: int = int(os.getenv("MIN_HOLDER_COUNT", "25"))
    bundle_risk_top10_owner_pct: float = float(os.getenv("BUNDLE_RISK_TOP10_OWNER_PCT", "0.35"))
    bundle_risk_largest_owner_pct: float = float(os.getenv("BUNDLE_RISK_LARGEST_OWNER_PCT", "0.12"))
    bundle_risk_min_holders: int = int(os.getenv("BUNDLE_RISK_MIN_HOLDERS", "18"))
    min_observation_seconds: int = int(os.getenv("MIN_OBSERVATION_SECONDS", "30"))
    max_observation_seconds: int = int(os.getenv("MAX_OBSERVATION_SECONDS", "180"))
    observation_poll_seconds: int = int(os.getenv("OBSERVATION_POLL_SECONDS", "2"))
    min_observation_price_change_pct: float = float(os.getenv("MIN_OBSERVATION_PRICE_CHANGE_PCT", "0.03"))
    max_observation_drawdown_pct: float = float(os.getenv("MAX_OBSERVATION_DRAWDOWN_PCT", "0.15"))
    min_observation_liquidity_ratio: float = float(os.getenv("MIN_OBSERVATION_LIQUIDITY_RATIO", "0.85"))
    min_buy_interval_seconds: int = int(os.getenv("MIN_BUY_INTERVAL_SECONDS", "20"))
    max_wallet_exposure_pct: float = float(os.getenv("MAX_WALLET_EXPOSURE_PCT", "0.12"))
    max_position_size_usd: float = float(os.getenv("MAX_POSITION_SIZE_USD", "25"))
    scalp_override_enabled: bool = _bool("SCALP_OVERRIDE_ENABLED", True)
    scalp_min_move_pct: float = float(os.getenv("SCALP_MIN_MOVE_PCT", "0.08"))
    scalp_max_drawdown_pct: float = float(os.getenv("SCALP_MAX_DRAWDOWN_PCT", "0.08"))
    scalp_min_liquidity_ratio: float = float(os.getenv("SCALP_MIN_LIQUIDITY_RATIO", "0.92"))
    scalp_min_volume_5m_usd: float = float(os.getenv("SCALP_MIN_VOLUME_5M_USD", "0.5"))
    scalp_min_holders: int = int(os.getenv("SCALP_MIN_HOLDERS", "3"))
    scalp_risk_position_multiplier: float = float(os.getenv("SCALP_RISK_POSITION_MULTIPLIER", "0.45"))
    narrative_enabled: bool = _bool("NARRATIVE_ENABLED", True)
    narrative_keywords: tuple[str, ...] = _csv(
        "NARRATIVE_KEYWORDS",
        "ai,news,cpi,fed,sec,etf,cex,listing,binance,coinbase,court,war,election,trump,elon,tesla,cz,openai,nvidia",
    )
    trusted_kol_keywords: tuple[str, ...] = _csv("TRUSTED_KOL_KEYWORDS", "cz,binance,elon,trump,vitalik,solana")
    narrative_score_bonus: float = float(os.getenv("NARRATIVE_SCORE_BONUS", "10"))
    trusted_kol_score_bonus: float = float(os.getenv("TRUSTED_KOL_SCORE_BONUS", "12"))
    narrative_max_bonus: float = float(os.getenv("NARRATIVE_MAX_BONUS", "22"))
    narrative_position_multiplier: float = float(os.getenv("NARRATIVE_POSITION_MULTIPLIER", "1.2"))
    news_enabled: bool = _bool("NEWS_ENABLED", True)
    news_poll_seconds: int = int(os.getenv("NEWS_POLL_SECONDS", "120"))
    news_event_ttl_seconds: int = int(os.getenv("NEWS_EVENT_TTL_SECONDS", "21600"))
    news_match_bonus: float = float(os.getenv("NEWS_MATCH_BONUS", "12"))
    news_max_bonus: float = float(os.getenv("NEWS_MAX_BONUS", "24"))
    news_min_confidence: float = float(os.getenv("NEWS_MIN_CONFIDENCE", "0.35"))
    news_feeds: tuple[str, ...] = _csv(
        "NEWS_FEEDS",
        "https://www.coindesk.com/arc/outboundfeeds/rss/,https://cointelegraph.com/rss,https://decrypt.co/feed",
    )
    kol_monitor_enabled: bool = _bool("KOL_MONITOR_ENABLED", True)
    kol_feeds: tuple[str, ...] = _csv("KOL_FEEDS", "")
    kol_profiles: tuple[str, ...] = _csv(
        "KOL_PROFILES",
        "elon=elon|musk|tesla|spacex|x|doge|dogecoin|grok,cz=cz|binance|bnb|build|bnbchain,heyi=heyi|he yi|何一|合一|binance|bnb,vitalik=vitalik|ethereum|eth|base|zksync|l2",
    )
    kol_source_weight: float = float(os.getenv("KOL_SOURCE_WEIGHT", "0.9"))
    kol_event_ttl_seconds: int = int(os.getenv("KOL_EVENT_TTL_SECONDS", "7200"))
    emergency_stop_loss_pct: float = float(os.getenv("EMERGENCY_STOP_LOSS_PCT", "-0.18"))
    emergency_liquidity_ratio: float = float(os.getenv("EMERGENCY_LIQUIDITY_RATIO", "0.55"))
    fast_exit_peak_pct: float = float(os.getenv("FAST_EXIT_PEAK_PCT", "0.55"))
    fast_exit_drawdown_pct: float = float(os.getenv("FAST_EXIT_DRAWDOWN_PCT", "0.12"))
    profit_lock_min_pct: float = float(os.getenv("PROFIT_LOCK_MIN_PCT", "0.18"))
    profit_lock_sell_pct: float = float(os.getenv("PROFIT_LOCK_SELL_PCT", "0.75"))
    moonbag_min_usd: float = float(os.getenv("MOONBAG_MIN_USD", "3"))
    profit_lock_drawdown_pct: float = float(os.getenv("PROFIT_LOCK_DRAWDOWN_PCT", "0.09"))
    profit_lock_liquidity_ratio: float = float(os.getenv("PROFIT_LOCK_LIQUIDITY_RATIO", "0.82"))
    profit_lock_min_hold_seconds: int = int(os.getenv("PROFIT_LOCK_MIN_HOLD_SECONDS", "20"))
    stale_position_seconds: int = int(os.getenv("STALE_POSITION_SECONDS", "90"))
    min_volume_to_position_ratio: float = float(os.getenv("MIN_VOLUME_TO_POSITION_RATIO", "2.5"))
    zombie_position_seconds: int = int(os.getenv("ZOMBIE_POSITION_SECONDS", "900"))
    zombie_min_volume_5m_usd: float = float(os.getenv("ZOMBIE_MIN_VOLUME_5M_USD", "300"))
    zombie_non_live_seconds: int = int(os.getenv("ZOMBIE_NON_LIVE_SECONDS", "240"))
    zombie_min_profit_keep_pct: float = float(os.getenv("ZOMBIE_MIN_PROFIT_KEEP_PCT", "0.5"))
    sol_price_usd: float = float(os.getenv("SOL_PRICE_USD", "150"))
    buy_slippage_bps: int = int(os.getenv("BUY_SLIPPAGE_BPS", "300"))
    sell_slippage_bps: int = int(os.getenv("SELL_SLIPPAGE_BPS", "400"))
    paper_buy_slippage_bps: int = int(os.getenv("PAPER_BUY_SLIPPAGE_BPS", "150"))
    paper_sell_slippage_bps: int = int(os.getenv("PAPER_SELL_SLIPPAGE_BPS", "200"))
    paper_fee_bps: int = int(os.getenv("PAPER_FEE_BPS", "50"))
    monitor_poll_seconds: int = int(os.getenv("MONITOR_POLL_SECONDS", "2"))
    pricing_poll_seconds: int = int(os.getenv("PRICING_POLL_SECONDS", "2"))
    holder_refresh_seconds: int = int(os.getenv("HOLDER_REFRESH_SECONDS", "20"))
    dexscreener_timeout_seconds: int = int(os.getenv("DEXSCREENER_TIMEOUT_SECONDS", "4"))
    rpc_timeout_seconds: int = int(os.getenv("RPC_TIMEOUT_SECONDS", "8"))
    pumpportal_ws_url: str = os.getenv("PUMPPORTAL_WS_URL", "wss://pumpportal.fun/api/data")
    dexscreener_token_url: str = os.getenv(
        "DEXSCREENER_TOKEN_URL",
        "https://api.dexscreener.com/latest/dex/tokens",
    )
    rugcheck_url: str = os.getenv(
        "RUGCHECK_URL",
        "https://api.rugcheck.xyz/v1/tokens/{address}/report",
    )
    jupiter_quote_url: str = os.getenv("JUPITER_QUOTE_URL", "https://quote-api.jup.ag/v6/quote")
    jupiter_swap_url: str = os.getenv("JUPITER_SWAP_URL", "https://quote-api.jup.ag/v6/swap")
    solana_private_key: Optional[str] = os.getenv("SOLANA_PRIVATE_KEY")

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


def get_settings() -> Settings:
    return Settings()
