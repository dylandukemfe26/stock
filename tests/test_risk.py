from datetime import datetime, timedelta, timezone

from backend.models.schemas import PositionSizeRequest, Side
from backend.risk.engine import ClosedTrade, OpenPosition, RiskConfig, RiskEngine


def test_sizing_basic():
    # Raise exposure cap so we exercise pure risk-based sizing.
    engine = RiskEngine(RiskConfig(
        account_equity=25_000, max_risk_per_trade_pct=1.0,
        max_position_pct_of_equity=200,
    ))
    req = PositionSizeRequest(
        account_equity=25_000, risk_per_trade_pct=1.0,
        entry=50.0, stop=49.5, side=Side.LONG,
    )
    # risk budget = $250, risk/sh = 0.50 -> 500 shares exactly.
    res = engine.size_position(req)
    assert res.shares == 500
    assert res.dollar_risk == 250.0
    assert res.dollar_exposure == 25_000.0


def test_sizing_cap_by_exposure():
    engine = RiskEngine(RiskConfig(
        account_equity=25_000, max_risk_per_trade_pct=1.0, max_position_pct_of_equity=25,
    ))
    # Tight stop makes budget allow >> 25% equity. Exposure cap should kick in.
    req = PositionSizeRequest(
        account_equity=25_000, risk_per_trade_pct=1.0,
        entry=50.0, stop=49.95, side=Side.LONG,
    )
    res = engine.size_position(req)
    # 25% of 25k = 6250 notional. 6250 / 50 = 125 shares max.
    assert res.shares == 125
    assert "capped by max position size" in " ".join(res.notes)


def test_sizing_zero_risk_when_entry_equals_stop():
    engine = RiskEngine()
    req = PositionSizeRequest(
        account_equity=25_000, risk_per_trade_pct=1.0,
        entry=50, stop=50, side=Side.LONG,
    )
    assert engine.size_position(req).shares == 0


def test_daily_loss_halt():
    engine = RiskEngine(RiskConfig(account_equity=10_000, max_daily_loss_pct=3.0))
    # One big realized loss of $400 (>3% * 10k = $300).
    engine.state.closed_trades.append(
        ClosedTrade(symbol="X", realized_pnl=-400, closed_at=datetime.now(timezone.utc))
    )
    status = engine.status()
    assert status.halted
    ok, reasons = engine.can_open_trade(proposed_dollar_risk=10)
    assert not ok
    assert any("limit" in r.lower() for r in reasons)


def test_revenge_cooloff():
    cfg = RiskConfig(
        account_equity=10_000, revenge_cooloff_losses=2, revenge_cooloff_minutes=10,
    )
    engine = RiskEngine(cfg)
    engine.state.consecutive_losses = 2
    engine.state.last_loss_at = datetime.now(timezone.utc) - timedelta(minutes=3)
    ok, reasons = engine.can_open_trade(proposed_dollar_risk=10)
    assert not ok
    assert any("cool-off" in r for r in reasons)


def test_open_and_close_position_tracks_pnl():
    engine = RiskEngine(RiskConfig(account_equity=50_000))
    pos = OpenPosition(
        symbol="AAA", side=Side.LONG, shares=100,
        entry=10.0, stop=9.5, opened_at=datetime.now(timezone.utc),
    )
    engine.record_open(pos)
    pnl = engine.record_close("AAA", exit_price=10.5)
    assert pnl == 50.0
    status = engine.status()
    assert status.realized_pnl == 50.0
    assert status.open_positions == 0
