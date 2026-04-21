from datetime import datetime, timedelta, timezone

from backend.models.schemas import Bar, SetupType, Side
from backend.setups.detector import SetupDetector
from backend.setups.indicators import opening_range, vwap_series


def _bar(i, o, h, l, c, v):
    return Bar(
        ts=datetime(2024, 5, 1, 13, 30, tzinfo=timezone.utc) + timedelta(minutes=i),
        open=o, high=h, low=l, close=c, volume=v,
    )


def _flat_bars(n, price=100.0, vol=10000):
    return [_bar(i, price, price + 0.05, price - 0.05, price, vol) for i in range(n)]


def test_vwap_monotonic_with_rising_closes():
    bars = [_bar(i, 100 + i * 0.1, 100 + i * 0.1 + 0.2, 100 + i * 0.1 - 0.2, 100 + i * 0.1, 5000) for i in range(10)]
    v = vwap_series(bars)
    assert len(v) == 10
    assert v[-1] > v[0]


def test_opening_range_picks_first_n_bars():
    bars = [_bar(0, 100, 101, 99, 100, 5000),
            _bar(1, 100, 102, 99, 101, 5000),
            _bar(2, 101, 103, 100, 102, 5000),
            _bar(3, 102, 104, 101, 103, 5000),
            _bar(4, 103, 105, 102, 104, 5000),
            _bar(5, 104, 106, 103, 105, 5000)]
    hi, lo = opening_range(bars, minutes=5)
    assert hi == 105
    assert lo == 99


def test_orb_long_triggers_on_breakout_with_volume():
    detector = SetupDetector()
    # Tight 5-bar opening range (104.0-104.5) matches intraday ATR so the
    # structural stop passes the risk-reasonableness check.
    bars = [
        _bar(0, 104.0, 104.5, 104.0, 104.2, 10000),
        _bar(1, 104.2, 104.5, 104.0, 104.3, 10000),
        _bar(2, 104.3, 104.5, 104.0, 104.2, 10000),
        _bar(3, 104.2, 104.5, 104.0, 104.3, 10000),
        _bar(4, 104.3, 104.5, 104.0, 104.2, 10000),
        _bar(5, 104.2, 104.4, 104.1, 104.3, 10000),
        _bar(6, 104.3, 104.4, 104.1, 104.3, 10000),
        _bar(7, 104.3, 104.5, 104.2, 104.4, 10000),
        _bar(8, 104.4, 104.5, 104.2, 104.4, 10000),
        _bar(9, 104.4, 104.5, 104.3, 104.4, 10000),
        _bar(10, 104.4, 104.5, 104.3, 104.4, 10000),
        _bar(11, 104.4, 105.2, 104.4, 105.1, 40000),   # breakout + volume
    ]
    signals = detector.detect("TEST", bars, prev_day_high=200, prev_day_low=50)
    orb = [s for s in signals if s.setup == SetupType.ORB]
    assert orb, f"expected ORB signal, got {[s.setup for s in signals]}"
    assert orb[0].side == Side.LONG
    assert orb[0].plan.target1 > orb[0].plan.entry
    assert orb[0].plan.stop < orb[0].plan.entry


def test_detector_needs_minimum_bars():
    detector = SetupDetector()
    assert detector.detect("TEST", _flat_bars(3)) == []
