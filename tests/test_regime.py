from datetime import datetime, timedelta, timezone

from backend.models.schemas import Bar, RegimeLabel
from backend.regime.detector import classify


def _trend_bars(n, start=400.0, step=0.3, range_=0.2):
    out = []
    t0 = datetime(2024, 5, 1, 13, 30, tzinfo=timezone.utc)
    price = start
    for i in range(n):
        nxt = price + step
        out.append(
            Bar(
                ts=t0 + timedelta(minutes=i),
                open=price,
                high=max(price, nxt) + range_ / 2,
                low=min(price, nxt) - range_ / 2,
                close=nxt,
                volume=10000,
            )
        )
        price = nxt
    return out


def _flat_bars(n, start=400.0, range_=0.05):
    out = []
    t0 = datetime(2024, 5, 1, 13, 30, tzinfo=timezone.utc)
    for i in range(n):
        out.append(
            Bar(
                ts=t0 + timedelta(minutes=i),
                open=start,
                high=start + range_,
                low=start - range_,
                close=start,
                volume=10000,
            )
        )
    return out


def test_trend_up_classified():
    bars = _trend_bars(40, step=0.5)
    r = classify(bars)
    assert r.label == RegimeLabel.TREND_UP


def test_trend_down_classified():
    bars = _trend_bars(40, step=-0.5)
    r = classify(bars)
    assert r.label == RegimeLabel.TREND_DOWN


def test_chop_classified_on_compressed_range():
    bars = _flat_bars(40)
    r = classify(bars)
    # Flat bars have zero slope → trend_score 0 → range_ratio check or chop.
    assert r.label in {RegimeLabel.CHOP, RegimeLabel.MIXED}


def test_volatile_when_vix_high():
    bars = _trend_bars(40, step=0.1)
    r = classify(bars, vix=30)
    assert r.label == RegimeLabel.VOLATILE


def test_breadth_override_keeps_trend_honest():
    # Up-sloping SPY but breadth says only 40% above VWAP → mixed, not trend_up.
    bars = _trend_bars(40, step=0.5)
    r = classify(bars, pct_above_vwap=0.4)
    assert r.label == RegimeLabel.MIXED
