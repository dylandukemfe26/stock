"""Liquidity and spread analyzer.

Keeps thin/wide-spread names out of the day-trading workflow. Combines the
live bid/ask spread with the 20-day average dollar volume into a single
0..100 tradeability score and an A/B/C tier.

Tiers in practice (on US equities):
  A: tight spreads (<5 bps), high volume. Normal day-trading names.
  B: wider spreads or moderate volume. Usable with reduced size.
  C: thin/illiquid. Avoid.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from backend.core.cache import TTLCache
from backend.data.provider import DataProvider
from backend.models.schemas import LiquidityScore, LiquidityTier


class LiquidityService:
    def __init__(
        self, provider: DataProvider, cache: TTLCache | None = None
    ) -> None:
        self.provider = provider
        self._cache = cache or TTLCache(default_ttl_seconds=5.0)

    def score(self, symbol: str) -> LiquidityScore:
        return self._cache.get_or_compute(
            ("liq", symbol),
            lambda: self._compute(symbol),
        )

    def _compute(self, symbol: str) -> LiquidityScore:
        quote = self.provider.get_quote(symbol)
        stats = self.provider.get_daily_stats(symbol, date.today())
        mid = (quote.bid + quote.ask) / 2 if quote.bid and quote.ask else quote.last
        spread_pct = (
            (quote.ask - quote.bid) / mid * 100
            if (quote.bid and quote.ask and mid > 0)
            else 0.0
        )
        # Round-trip slippage estimate: one full spread paid across entry+exit.
        est_slippage = spread_pct

        score = 100.0
        # Spread penalty (bps). 5 bps = 0.05%.
        if spread_pct > 0.05:
            score -= 20
        if spread_pct > 0.15:
            score -= 30
        if spread_pct > 0.30:
            score -= 40
        # Volume penalty.
        adv = stats.avg_dollar_volume_20d
        if adv < 5_000_000:
            score -= 30
        if adv < 2_000_000:
            score -= 60
        score = max(0.0, score)

        tier = (
            LiquidityTier.A
            if score >= 80
            else LiquidityTier.B if score >= 50 else LiquidityTier.C
        )
        return LiquidityScore(
            symbol=symbol,
            spread_pct=round(spread_pct, 4),
            est_round_trip_slippage_pct=round(est_slippage, 4),
            tradeability=round(score, 1),
            tier=tier,
            asof=datetime.now(timezone.utc),
        )
