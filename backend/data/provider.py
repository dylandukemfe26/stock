"""Data provider interface.

All business logic (scanner, setups, risk) depends on this interface only.
Concrete implementations live alongside (mock_provider.py, polygon_provider.py, ...).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Protocol

from backend.config import get_settings
from backend.models.schemas import Bar, DailyStats, PremarketSnapshot, Quote


class DataProvider(ABC):
    """Minimum surface area needed for MVP."""

    @abstractmethod
    def list_universe(self) -> list[str]:
        """Return tradable symbols for the scanner to consider."""

    @abstractmethod
    def get_daily_stats(self, symbol: str, asof: date) -> DailyStats:
        """Prev close, 20d avg volume, ATR(14), etc."""

    @abstractmethod
    def get_premarket_snapshot(self, symbol: str, asof: date) -> PremarketSnapshot | None:
        """Aggregated premarket bar data. None if no premarket activity."""

    @abstractmethod
    def get_quote(self, symbol: str) -> Quote:
        """Latest quote."""

    @abstractmethod
    def get_intraday_bars(
        self, symbol: str, asof: date, timeframe: str = "1m"
    ) -> list[Bar]:
        """Intraday bars for the trading day, sorted ascending by time."""


def get_provider() -> DataProvider:
    """Factory picks provider from settings."""
    settings = get_settings()
    if settings.data_provider == "mock":
        from backend.data.mock_provider import MockProvider
        return MockProvider()
    if settings.data_provider == "polygon":
        from backend.data.polygon_provider import PolygonProvider
        return PolygonProvider(api_key=settings.polygon_api_key)
    raise ValueError(f"Unknown data provider: {settings.data_provider}")
