"""Central configuration. Env-driven with safe defaults for dev."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_", extra="ignore")

    # Data provider: "mock" | "polygon" | "alpaca"
    data_provider: str = "mock"
    polygon_api_key: str | None = None
    alpaca_api_key: str | None = None
    alpaca_api_secret: str | None = None

    database_url: str = "sqlite:///./trading.db"

    # Scanner defaults
    min_gap_pct: float = 3.0
    min_relative_volume: float = 2.0
    min_premarket_volume: int = 50_000
    min_price: float = 2.0
    max_price: float = 2_000.0
    min_avg_dollar_volume: float = 10_000_000.0  # liquidity floor

    # Risk defaults
    account_equity: float = 25_000.0
    max_risk_per_trade_pct: float = 1.0  # of account equity
    max_daily_loss_pct: float = 3.0
    max_open_positions: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
