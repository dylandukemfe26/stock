from datetime import date, datetime, timedelta, timezone

import pytest

from backend.data.mock_provider import MockProvider
from backend.models.schemas import Bar, StrengthFlag
from backend.rs.service import RSService, _persistence, _session_rs


def _bars(path):
    """Build bars from a list of (open, close) pairs."""
    start = datetime(2024, 5, 1, 13, 30, tzinfo=timezone.utc)
    out = []
    for i, (o, c) in enumerate(path):
        out.append(
            Bar(
                ts=start + timedelta(minutes=i),
                open=o, high=max(o, c) + 0.05, low=min(o, c) - 0.05,
                close=c, volume=1000,
            )
        )
    return out


def test_session_rs_positive_when_sym_outperforms():
    sym = _bars([(100, 100), (100, 101), (101, 102)])      # +2% session
    bench = _bars([(400, 400), (400, 401), (401, 401.5)])  # +0.375% session
    assert _session_rs(sym, bench) > 0


def test_session_rs_zero_when_aligned():
    same = _bars([(100, 100), (100, 101)])
    assert _session_rs(same, same) == pytest.approx(0)


def test_persistence_fully_outperforming():
    sym = _bars([(100, 100), (100, 101), (101, 102), (102, 103)])
    bench = _bars([(400, 400), (400, 400.1), (400.1, 400.2), (400.2, 400.3)])
    assert _persistence(sym, bench, lookback=3) == 1.0


def test_rsservice_returns_leader_when_session_rs_and_persistence_strong():
    # Use a custom provider where the target symbol climbs steadily and SPY drifts.
    from datetime import date as date_cls

    class _StubProvider(MockProvider):
        def get_intraday_bars(self, symbol, asof, timeframe="1m"):
            if symbol == "SPY":
                return _bars([(400, 400 + i * 0.01) for i in range(20)])
            return _bars([(100, 100 + i * 0.5) for i in range(20)])  # big uptrend

    svc = RSService(_StubProvider())
    snap = svc.snapshot("AAPL", asof=date_cls(2024, 5, 1))
    assert snap is not None
    assert snap.strength == StrengthFlag.LEADER
    assert snap.session_rs_pct > 1.0
    assert snap.persistence > 0.65


def test_rsservice_returns_laggard_when_session_rs_negative():
    class _StubProvider(MockProvider):
        def get_intraday_bars(self, symbol, asof, timeframe="1m"):
            if symbol == "SPY":
                return _bars([(400, 400 + i * 0.01) for i in range(20)])
            return _bars([(100, 100 - i * 0.5) for i in range(20)])  # big downtrend

    svc = RSService(_StubProvider())
    snap = svc.snapshot("LAGGY", asof=date(2024, 5, 1))
    assert snap.strength == StrengthFlag.LAGGARD
    assert snap.session_rs_pct < -1.0
