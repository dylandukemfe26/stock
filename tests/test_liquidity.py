from datetime import datetime, timezone

from backend.data.mock_provider import MockProvider
from backend.liquidity.service import LiquidityService
from backend.models.schemas import DailyStats, LiquidityTier, Quote


class _StubProvider(MockProvider):
    def __init__(self, spread=0.02, adv=50_000_000, price=100.0):
        self._spread = spread
        self._adv = adv
        self._price = price

    def get_quote(self, symbol):
        half = self._spread / 2
        return Quote(
            symbol=symbol,
            last=self._price,
            bid=self._price - half,
            ask=self._price + half,
            ts=datetime.now(timezone.utc),
        )

    def get_daily_stats(self, symbol, asof):
        return DailyStats(
            symbol=symbol,
            prev_close=self._price,
            avg_volume_20d=int(self._adv / self._price),
            avg_dollar_volume_20d=self._adv,
            atr_14d=1.0,
            float_shares=100_000_000,
        )


def test_tight_spread_liquid_is_tier_a():
    liq = LiquidityService(_StubProvider(spread=0.02, adv=500_000_000, price=100))
    s = liq.score("AAA")
    assert s.tier == LiquidityTier.A
    assert s.tradeability >= 80
    assert s.spread_pct < 0.05


def test_wide_spread_is_tier_c():
    liq = LiquidityService(_StubProvider(spread=0.60, adv=500_000_000, price=100))
    s = liq.score("WIDE")
    # 0.60 spread on $100 = 0.60% — easily tier C.
    assert s.tier == LiquidityTier.C


def test_thin_volume_is_tier_c():
    liq = LiquidityService(_StubProvider(spread=0.02, adv=1_000_000, price=100))
    s = liq.score("THIN")
    assert s.tier == LiquidityTier.C


def test_moderate_is_tier_b():
    # ~0.18% spread pushes score below 80; plenty of volume so no liquidity cut.
    liq = LiquidityService(_StubProvider(spread=0.18, adv=200_000_000, price=100))
    s = liq.score("MOD")
    assert s.tier == LiquidityTier.B, f"got {s}"
