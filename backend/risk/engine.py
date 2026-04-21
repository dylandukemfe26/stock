"""Risk engine.

Three responsibilities:
  1. Size a position for a proposed trade (never risk more than $X).
  2. Track open + realized risk through the day.
  3. Enforce account-level circuit breakers (max daily loss, max positions,
     cool-off after consecutive losses).

Not a replacement for broker-side risk controls. This is a pre-trade check.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from backend.models.schemas import (
    PositionSizeRequest,
    PositionSizeResult,
    RiskStatus,
    Side,
)


@dataclass
class RiskConfig:
    account_equity: float = 25_000.0
    max_risk_per_trade_pct: float = 1.0     # of account equity
    max_daily_loss_pct: float = 3.0         # of account equity
    max_open_positions: int = 5
    revenge_cooloff_losses: int = 2         # consecutive losses
    revenge_cooloff_minutes: int = 15
    max_position_pct_of_equity: float = 25  # hard cap on exposure


@dataclass
class OpenPosition:
    symbol: str
    side: Side
    shares: int
    entry: float
    stop: float
    opened_at: datetime

    @property
    def dollar_risk(self) -> float:
        return self.shares * abs(self.entry - self.stop)


@dataclass
class ClosedTrade:
    symbol: str
    realized_pnl: float
    closed_at: datetime


@dataclass
class RiskState:
    trading_day: date
    open_positions: list[OpenPosition] = field(default_factory=list)
    closed_trades: list[ClosedTrade] = field(default_factory=list)
    last_loss_at: datetime | None = None
    consecutive_losses: int = 0
    manual_halt: bool = False


class RiskEngine:
    """In-memory MVP. Swap the state store for SQLite/Postgres in phase 2."""

    def __init__(self, config: RiskConfig | None = None) -> None:
        self.cfg = config or RiskConfig()
        self.state = RiskState(trading_day=date.today())

    # --------- sizing ---------
    def size_position(self, req: PositionSizeRequest) -> PositionSizeResult:
        notes: list[str] = []
        risk_per_share = abs(req.entry - req.stop)
        if risk_per_share <= 0:
            return PositionSizeResult(
                shares=0,
                dollar_risk=0,
                dollar_exposure=0,
                risk_per_share=0,
                stop_distance_pct=0,
                notes=["entry and stop are equal; cannot size"],
            )

        dollar_risk_budget = req.account_equity * (req.risk_per_trade_pct / 100.0)

        # If ATR provided and stop is tight relative to ATR, warn — likely to get
        # stopped on noise. Not a hard block; inform the trader.
        if req.atr and risk_per_share < 0.5 * req.atr:
            notes.append(f"stop is tight vs ATR ({risk_per_share:.2f} < 0.5*ATR {req.atr:.2f})")
        if req.atr and risk_per_share > 2.0 * req.atr:
            notes.append(f"stop is wide vs ATR ({risk_per_share:.2f} > 2*ATR {req.atr:.2f})")

        shares = int(dollar_risk_budget // risk_per_share)
        if shares <= 0:
            notes.append("dollar risk budget smaller than 1 share of risk")
            return PositionSizeResult(
                shares=0,
                dollar_risk=0,
                dollar_exposure=0,
                risk_per_share=round(risk_per_share, 4),
                stop_distance_pct=round(risk_per_share / req.entry * 100.0, 3),
                notes=notes,
            )

        # Hard cap: position notional <= max_position_pct_of_equity
        max_exposure = req.account_equity * (self.cfg.max_position_pct_of_equity / 100.0)
        max_by_exposure = int(max_exposure // req.entry) if req.entry > 0 else shares
        if max_by_exposure < shares:
            notes.append("capped by max position size (exposure)")
            shares = max_by_exposure

        if req.max_shares is not None and req.max_shares < shares:
            notes.append("capped by user-provided max_shares")
            shares = req.max_shares

        dollar_risk = shares * risk_per_share
        exposure = shares * req.entry
        stop_pct = risk_per_share / req.entry * 100.0
        return PositionSizeResult(
            shares=shares,
            dollar_risk=round(dollar_risk, 2),
            dollar_exposure=round(exposure, 2),
            risk_per_share=round(risk_per_share, 4),
            stop_distance_pct=round(stop_pct, 3),
            notes=notes,
        )

    # --------- state transitions ---------
    def record_open(self, pos: OpenPosition) -> None:
        self._maybe_reset_day()
        self.state.open_positions.append(pos)

    def record_close(self, symbol: str, exit_price: float, closed_at: datetime | None = None) -> float:
        self._maybe_reset_day()
        closed_at = closed_at or datetime.now(timezone.utc)
        pos = next((p for p in self.state.open_positions if p.symbol == symbol), None)
        if pos is None:
            raise ValueError(f"no open position for {symbol}")
        if pos.side == Side.LONG:
            pnl = (exit_price - pos.entry) * pos.shares
        else:
            pnl = (pos.entry - exit_price) * pos.shares
        self.state.open_positions.remove(pos)
        self.state.closed_trades.append(
            ClosedTrade(symbol=symbol, realized_pnl=pnl, closed_at=closed_at)
        )
        if pnl < 0:
            self.state.consecutive_losses += 1
            self.state.last_loss_at = closed_at
        else:
            self.state.consecutive_losses = 0
        return pnl

    def halt_trading(self, reason: str = "manual") -> None:
        self.state.manual_halt = True

    # --------- pre-trade check ---------
    def can_open_trade(
        self,
        *,
        proposed_dollar_risk: float,
        now: datetime | None = None,
    ) -> tuple[bool, list[str]]:
        self._maybe_reset_day()
        now = now or datetime.now(timezone.utc)
        reasons: list[str] = []

        status = self.status()
        if status.halted:
            reasons.extend(status.halt_reasons)
            return False, reasons

        if len(self.state.open_positions) >= self.cfg.max_open_positions:
            reasons.append("max open positions reached")

        remaining = status.daily_loss_remaining
        # Treat proposed risk like a potential loss — don't let it push us past limit.
        if proposed_dollar_risk > remaining:
            reasons.append(
                f"proposed risk ${proposed_dollar_risk:.0f} exceeds remaining daily budget ${remaining:.0f}"
            )

        if (
            self.state.consecutive_losses >= self.cfg.revenge_cooloff_losses
            and self.state.last_loss_at
        ):
            delta_min = (now - self.state.last_loss_at).total_seconds() / 60.0
            if delta_min < self.cfg.revenge_cooloff_minutes:
                reasons.append(
                    f"revenge cool-off: wait {self.cfg.revenge_cooloff_minutes - delta_min:.0f}m"
                )

        return (len(reasons) == 0), reasons

    # --------- reporting ---------
    def status(self, unrealized_pnl: float = 0.0) -> RiskStatus:
        self._maybe_reset_day()
        realized = sum(t.realized_pnl for t in self.state.closed_trades)
        total_risk = sum(p.dollar_risk for p in self.state.open_positions)
        daily_limit = self.cfg.account_equity * (self.cfg.max_daily_loss_pct / 100.0)
        # "Remaining" tracks how much more we can lose today including open risk.
        total_drawdown = min(0.0, realized + unrealized_pnl)
        remaining = max(0.0, daily_limit + total_drawdown - total_risk)

        halt_reasons: list[str] = []
        if self.state.manual_halt:
            halt_reasons.append("manual halt")
        if -realized >= daily_limit:
            halt_reasons.append("daily loss limit hit (realized)")
        if -(realized + unrealized_pnl) >= daily_limit:
            halt_reasons.append("daily loss limit hit (including unrealized)")

        return RiskStatus(
            trading_day=self.state.trading_day,
            account_equity=self.cfg.account_equity,
            realized_pnl=round(realized, 2),
            unrealized_pnl=round(unrealized_pnl, 2),
            total_risk_used=round(total_risk, 2),
            open_positions=len(self.state.open_positions),
            daily_loss_limit=round(daily_limit, 2),
            daily_loss_remaining=round(remaining, 2),
            halted=bool(halt_reasons),
            halt_reasons=halt_reasons,
        )

    def _maybe_reset_day(self) -> None:
        today = date.today()
        if self.state.trading_day != today:
            self.state = RiskState(trading_day=today)
