"""Polygon.io provider stub.

Left intentionally thin — wire real HTTP calls when an API key is available.
The point is to show where each data call plugs in so you can swap vendors
(Polygon, Alpaca, IEX, Tradier) without touching business logic.
"""
from __future__ import annotations

from datetime import date

from backend.data.provider import DataProvider
from backend.models.schemas import Bar, DailyStats, PremarketSnapshot, Quote


class PolygonProvider(DataProvider):
    def __init__(self, api_key: str | None) -> None:
        if not api_key:
            raise RuntimeError("POLYGON_API_KEY is required for PolygonProvider")
        self.api_key = api_key
        # TODO: self.client = httpx.Client(base_url="https://api.polygon.io", ...)

    def list_universe(self) -> list[str]:
        # TODO: /v3/reference/tickers?market=stocks&active=true
        raise NotImplementedError

    def get_daily_stats(self, symbol: str, asof: date) -> DailyStats:
        # TODO: /v2/aggs/ticker/{symbol}/range/1/day/... for last 20 days + ATR calc
        raise NotImplementedError

    def get_premarket_snapshot(self, symbol: str, asof: date) -> PremarketSnapshot | None:
        # TODO: /v2/snapshot/locale/us/markets/stocks/tickers/{symbol}
        raise NotImplementedError

    def get_quote(self, symbol: str) -> Quote:
        # TODO: /v2/last/nbbo/{symbol}
        raise NotImplementedError

    def get_intraday_bars(self, symbol: str, asof: date, timeframe: str = "1m") -> list[Bar]:
        # TODO: /v2/aggs/ticker/{symbol}/range/1/minute/{asof}/{asof}
        raise NotImplementedError
