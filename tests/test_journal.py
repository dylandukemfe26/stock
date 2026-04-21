from datetime import datetime, timedelta, timezone

import pytest

from backend.journal import JournalService
from backend.journal.mistakes import TradeSnapshot, detect_mistakes
from backend.models.schemas import (
    MistakeType,
    SetupType,
    Side,
    TradeOpenRequest,
)


def _open(svc, **kw):
    defaults = dict(
        symbol="TEST", side=Side.LONG, shares=100,
        entry=10.0, stop=9.8, target1=10.6,
    )
    defaults.update(kw)
    return svc.open(TradeOpenRequest(**defaults))


def test_open_and_close_basic_long():
    svc = JournalService()
    row = _open(svc, entry=10.0, stop=9.8, shares=100)
    assert row.pnl is None
    closed = svc.close(row.id, exit_price=10.4)
    assert closed.pnl == pytest.approx(40.0)
    assert closed.realized_r == pytest.approx(2.0)  # $40 / ($0.20 * 100) = 2R
    assert closed.closed_at is not None


def test_open_and_close_short_pnl_sign():
    svc = JournalService()
    row = _open(svc, side=Side.SHORT, entry=50, stop=51, shares=100)
    closed = svc.close(row.id, exit_price=48)
    assert closed.pnl == pytest.approx(200.0)   # (50-48)*100
    assert closed.realized_r == pytest.approx(2.0)


def test_close_nonexistent_raises():
    svc = JournalService()
    with pytest.raises(KeyError):
        svc.close(999, exit_price=10)


def test_double_close_raises():
    svc = JournalService()
    row = _open(svc)
    svc.close(row.id, exit_price=10.4)
    with pytest.raises(ValueError):
        svc.close(row.id, exit_price=10.5)


def test_equal_entry_and_stop_rejected():
    svc = JournalService()
    with pytest.raises(ValueError):
        _open(svc, entry=10.0, stop=10.0)


def test_early_exit_mistake_flagged():
    svc = JournalService()
    row = _open(svc, entry=10.0, stop=9.8, target1=10.6, shares=100)
    # planned move 0.6; closing at 10.1 = 0.1 move = 17% of planned < 40%.
    closed = svc.close(row.id, exit_price=10.1)
    assert MistakeType.EARLY_EXIT.value in closed.mistakes


def test_no_early_exit_when_hit_target():
    svc = JournalService()
    row = _open(svc, entry=10.0, stop=9.8, target1=10.6, shares=100)
    closed = svc.close(row.id, exit_price=10.6)
    assert MistakeType.EARLY_EXIT.value not in closed.mistakes


def test_moved_stop_away_flagged():
    svc = JournalService()
    row = _open(svc, side=Side.LONG, entry=10.0, stop=9.8, shares=100)
    svc.move_stop(row.id, 9.5)   # widened the stop against us
    closed = svc.close(row.id, exit_price=9.5)
    assert MistakeType.MOVED_STOP_AWAY.value in closed.mistakes
    # risk_per_share uses ORIGINAL stop so realized_r is vs plan, not vs widened stop.
    assert closed.realized_r == pytest.approx(-2.5)


def test_sized_up_after_loss_flagged():
    svc = JournalService()
    # Trade 1: loss, 100 shares.
    t1 = _open(svc, entry=10, stop=9.8, shares=100)
    svc.close(t1.id, exit_price=9.8)
    # Trade 2: 200 shares (>=1.5x) — revenge.
    t2 = _open(svc, entry=20, stop=19.5, shares=200)
    closed = svc.close(t2.id, exit_price=19.5)
    assert MistakeType.SIZED_UP_AFTER_LOSS.value in closed.mistakes


def test_summary_aggregates_correctly():
    svc = JournalService()
    # 3 longs: +$50, +$50, -$30 using (entry, stop, exit).
    for i, (entry, stop, exit_) in enumerate(
        [(10, 9.5, 10.5), (20, 19, 20.5), (30, 29, 29.7)]
    ):
        row = _open(svc, symbol=f"S{i}", entry=entry, stop=stop, shares=100)
        svc.close(row.id, exit_price=exit_)
    s = svc.summary()
    assert s.n_closed == 3
    assert s.win_rate == pytest.approx(2 / 3, abs=0.01)
    assert s.total_pnl == pytest.approx(50 + 50 - 30)
    assert s.best_trade_pnl == 50
    assert s.worst_trade_pnl == -30


def test_by_setup_ranks_by_expectancy():
    svc = JournalService()
    # ORB longs: 2 wins 1 loss, net positive.
    for e, s, x in [(10, 9.9, 10.2), (11, 10.9, 11.2), (12, 11.9, 11.8)]:
        r = _open(svc, symbol="A", entry=e, stop=s, shares=100, setup=SetupType.ORB)
        svc.close(r.id, exit_price=x)
    # VWAP rejection shorts: stop above entry; losing shorts exit above entry.
    for e, stp, x in [(15, 15.2, 15.4), (16, 16.2, 16.4)]:
        r = _open(svc, symbol="B", side=Side.SHORT, entry=e, stop=stp, shares=100,
                  setup=SetupType.VWAP_REJECTION)
        svc.close(r.id, exit_price=x)
    stats = svc.by_setup()
    labels = [s.setup for s in stats]
    # ORB should rank above VWAP rejection (higher expectancy).
    assert labels.index("opening_range_breakout") < labels.index("vwap_rejection")


def test_by_regime_buckets():
    svc = JournalService()
    for regime in ("trend_up", "trend_up", "chop"):
        r = _open(svc, regime_at_open=regime)
        svc.close(r.id, exit_price=10.4)
    stats = svc.by_regime()
    labels = {s.regime: s.n for s in stats}
    assert labels["trend_up"] == 2
    assert labels["chop"] == 1


# --------- direct mistake rule unit tests ---------


def _snap(**kw):
    defaults = dict(
        symbol="X", side=Side.LONG, shares=100,
        entry=10.0, stop=9.8, target1=10.6, original_stop=9.8,
        exit_price=10.4, pnl=40.0,
        opened_at=datetime.now(timezone.utc),
        closed_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    defaults.update(kw)
    return TradeSnapshot(**defaults)


def test_mistake_rule_no_recent_history_no_revenge_flag():
    assert MistakeType.SIZED_UP_AFTER_LOSS not in detect_mistakes(_snap(shares=500), [])


def test_mistake_rule_clean_trade_no_mistakes():
    assert detect_mistakes(_snap(exit_price=10.6, pnl=60), []) == []
