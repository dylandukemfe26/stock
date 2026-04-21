"""Deterministic mock data provider.

Lets the whole system run end-to-end without API keys. Generates plausible
premarket gaps, volumes, and intraday bars from a seeded RNG per symbol.
Safe to use in tests and UI demos.
"""
from __future__ import annotations

import hashlib
import math
import random
from datetime import date, datetime, time, timedelta, timezone

from backend.data.provider import DataProvider
from backend.models.schemas import Bar, DailyStats, PremarketSnapshot, Quote


_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMD", "META", "AMZN", "GOOGL",
    "NFLX", "COIN", "PLTR", "SHOP", "UBER", "SNOW", "CRWD", "SOFI",
    "RIVN", "LCID", "MARA", "RIOT", "BBAI", "SMCI", "ARM", "GME",
    "AMC", "BABA", "PDD", "NIO", "XPEV", "TSM",
]


def _seed_for(symbol: str, asof: date) -> int:
    key = f"{symbol}:{asof.isoformat()}".encode()
    return int(hashlib.sha256(key).hexdigest()[:8], 16)


class MockProvider(DataProvider):
    def list_universe(self) -> list[str]:
        return list(_UNIVERSE)

    def get_daily_stats(self, symbol: str, asof: date) -> DailyStats:
        rng = random.Random(_seed_for(symbol, asof) ^ 0xA1)
        prev_close = round(rng.uniform(5, 500), 2)
        avg_volume = int(rng.uniform(1_000_000, 40_000_000))
        atr = round(prev_close * rng.uniform(0.01, 0.05), 2)
        avg_dollar_volume = prev_close * avg_volume
        float_shares = int(rng.uniform(50_000_000, 2_000_000_000))
        return DailyStats(
            symbol=symbol,
            prev_close=prev_close,
            avg_volume_20d=avg_volume,
            avg_dollar_volume_20d=avg_dollar_volume,
            atr_14d=atr,
            float_shares=float_shares,
        )

    def get_premarket_snapshot(
        self, symbol: str, asof: date
    ) -> PremarketSnapshot | None:
        stats = self.get_daily_stats(symbol, asof)
        rng = random.Random(_seed_for(symbol, asof) ^ 0xB2)

        # ~70% of symbols have meaningful premarket action.
        if rng.random() < 0.30:
            return None

        gap_pct = rng.gauss(0, 3.5)  # centered at 0, std 3.5%
        # A few symbols get big gaps to make the scanner interesting.
        if rng.random() < 0.15:
            gap_pct += rng.choice([-1, 1]) * rng.uniform(5, 15)

        last = round(stats.prev_close * (1 + gap_pct / 100), 2)
        high = round(last * (1 + abs(rng.gauss(0, 0.01))), 2)
        low = round(last * (1 - abs(rng.gauss(0, 0.01))), 2)
        pre_volume = int(max(0, rng.gauss(stats.avg_volume_20d * 0.05, stats.avg_volume_20d * 0.05)))
        asof_dt = datetime.combine(asof, time(9, 25), tzinfo=timezone.utc)
        return PremarketSnapshot(
            symbol=symbol,
            last=last,
            premarket_high=max(high, last),
            premarket_low=min(low, last),
            premarket_volume=pre_volume,
            asof=asof_dt,
        )

    def get_quote(self, symbol: str) -> Quote:
        today = date.today()
        snap = self.get_premarket_snapshot(symbol, today)
        last = snap.last if snap else self.get_daily_stats(symbol, today).prev_close
        return Quote(
            symbol=symbol,
            last=last,
            bid=round(last - 0.01, 2),
            ask=round(last + 0.01, 2),
            ts=datetime.now(timezone.utc),
        )

    def get_intraday_bars(
        self, symbol: str, asof: date, timeframe: str = "1m"
    ) -> list[Bar]:
        """Generate 1-minute bars for a regular trading session (09:30-16:00 ET)."""
        if timeframe != "1m":
            raise NotImplementedError("mock provider only supports 1m bars")

        stats = self.get_daily_stats(symbol, asof)
        snap = self.get_premarket_snapshot(symbol, asof)
        open_price = snap.last if snap else stats.prev_close

        rng = random.Random(_seed_for(symbol, asof) ^ 0xC3)
        drift = rng.gauss(0, 0.0005)  # small per-minute drift
        vol = stats.atr_14d / stats.prev_close / math.sqrt(390)  # per-minute sigma

        bars: list[Bar] = []
        price = open_price
        # 09:30 to 16:00 ET = 6.5h = 390 bars. Timestamps are naive UTC-ish for mock.
        start = datetime.combine(asof, time(13, 30), tzinfo=timezone.utc)  # 09:30 ET in UTC
        per_minute_volume = max(1000, stats.avg_volume_20d // 390)

        for i in range(390):
            ret = rng.gauss(drift, vol)
            new_price = max(0.01, price * (1 + ret))
            high = max(price, new_price) * (1 + abs(rng.gauss(0, vol / 2)))
            low = min(price, new_price) * (1 - abs(rng.gauss(0, vol / 2)))
            # Volume profile: heavy open, lighter mid-day, heavier close.
            vol_mult = 2.5 if i < 15 else (1.8 if i > 370 else 1.0)
            v = int(max(100, rng.gauss(per_minute_volume * vol_mult, per_minute_volume * 0.4)))
            bars.append(
                Bar(
                    ts=start + timedelta(minutes=i),
                    open=round(price, 2),
                    high=round(high, 2),
                    low=round(low, 2),
                    close=round(new_price, 2),
                    volume=v,
                )
            )
            price = new_price
        return bars
