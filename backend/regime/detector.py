"""Market regime detector.

Classifies the current SPY session into one of five labels so the setup
detector can bias toward setups that work in that environment.

MVP uses three signals from SPY bars alone:
  - trend_score: |slope of last 30 bar closes| divided by ATR(14). >0.5 strong.
  - range_ratio: last bar range / ATR. <0.6 compressed / chop candidate.
  - (optional) breadth: % of scanner universe above VWAP — strong confirmer.

Labels:
  trend_up     : strong positive slope + broad participation
  trend_down   : strong negative slope + broad participation
  volatile     : wide ranges, no direction (VIX > 25 or range_ratio > 2)
  chop         : compressed ranges and weak slope
  mixed        : default / transitional
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from backend.core.cache import TTLCache
from backend.data.provider import DataProvider
from backend.models.schemas import Bar, Regime, RegimeLabel
from backend.setups.indicators import atr


_BENCH = "SPY"


class RegimeDetector:
    def __init__(
        self, provider: DataProvider, cache: TTLCache | None = None
    ) -> None:
        self.provider = provider
        self._cache = cache or TTLCache(default_ttl_seconds=60.0)

    def current(
        self,
        asof: date | None = None,
        pct_above_vwap: float | None = None,
        vix: float | None = None,
    ) -> Regime:
        asof = asof or date.today()
        bars = self._cache.get_or_compute(
            ("bench_bars", _BENCH, asof),
            lambda: self.provider.get_intraday_bars(_BENCH, asof),
        )
        return classify(bars, pct_above_vwap=pct_above_vwap, vix=vix)


def classify(
    spy_bars: list[Bar],
    pct_above_vwap: float | None = None,
    vix: float | None = None,
) -> Regime:
    if len(spy_bars) < 15:
        return Regime(
            label=RegimeLabel.MIXED,
            trend_score=0.0,
            range_ratio=0.0,
            pct_above_vwap=pct_above_vwap,
            vix=vix,
            asof=datetime.now(timezone.utc),
        )

    window = spy_bars[-30:] if len(spy_bars) >= 30 else spy_bars
    closes = [b.close for b in window]
    per_bar_slope = (closes[-1] - closes[0]) / len(closes)

    atr_val = atr(window, period=min(14, len(window) - 1)) or 0.0
    trend_score = abs(per_bar_slope) / atr_val if atr_val > 0 else 0.0

    last_range = window[-1].high - window[-1].low
    range_ratio = last_range / atr_val if atr_val > 0 else 1.0
    direction = 1 if per_bar_slope >= 0 else -1

    label = _label(trend_score, range_ratio, direction, pct_above_vwap, vix)
    return Regime(
        label=label,
        trend_score=round(trend_score, 3),
        range_ratio=round(range_ratio, 3),
        pct_above_vwap=pct_above_vwap,
        vix=vix,
        asof=datetime.now(timezone.utc),
    )


def _label(
    trend_score: float,
    range_ratio: float,
    direction: int,
    pct_above_vwap: float | None,
    vix: float | None,
) -> RegimeLabel:
    if vix is not None and vix > 25:
        return RegimeLabel.VOLATILE
    if range_ratio > 2.0:
        return RegimeLabel.VOLATILE

    strong_trend = trend_score > 0.5
    # Breadth override: strong trend + wrong-way breadth kicks us to mixed.
    if strong_trend and pct_above_vwap is not None:
        if direction > 0 and pct_above_vwap > 0.55:
            return RegimeLabel.TREND_UP
        if direction < 0 and pct_above_vwap < 0.45:
            return RegimeLabel.TREND_DOWN
        return RegimeLabel.MIXED

    if strong_trend:
        return RegimeLabel.TREND_UP if direction > 0 else RegimeLabel.TREND_DOWN
    if range_ratio < 0.6:
        return RegimeLabel.CHOP
    return RegimeLabel.MIXED
