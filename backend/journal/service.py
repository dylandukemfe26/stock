"""Trade Journal.

Owns the lifecycle of recorded trades and computes the analytics the trader
uses to understand their own edge and mistakes.

In-memory for MVP. Swap `_trades` for a SQLAlchemy session without touching
the public API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from threading import Lock

from backend.core.events import EventBus, EventType
from backend.journal.mistakes import TradeSnapshot, detect_mistakes
from backend.models.schemas import (
    JournalSummary,
    MistakeType,
    RegimeStats,
    SetupStats,
    Side,
    SetupType,
    TradeOpenRequest,
    TradeRow,
)


@dataclass
class _StoredTrade:
    id: int
    symbol: str
    side: Side
    shares: int
    entry: float
    stop: float
    original_stop: float
    target1: float | None
    setup: str | None
    signal_id: int | None
    opened_at: datetime
    exit_price: float | None = None
    pnl: float | None = None
    closed_at: datetime | None = None
    regime_at_open: str | None = None
    rs_pct_at_open: float | None = None
    liquidity_tier_at_open: str | None = None
    catalyst_at_open: str | None = None
    notes: str = ""
    emotion: str | None = None
    mistakes: list[MistakeType] = field(default_factory=list)

    @property
    def risk_per_share(self) -> float:
        return abs(self.entry - self.original_stop)

    @property
    def is_open(self) -> bool:
        return self.exit_price is None

    @property
    def realized_r(self) -> float | None:
        if self.pnl is None:
            return None
        denom = self.shares * self.risk_per_share
        return self.pnl / denom if denom > 0 else None

    def to_row(self) -> TradeRow:
        hold_min = None
        if self.closed_at:
            hold_min = int((self.closed_at - self.opened_at).total_seconds() // 60)
        return TradeRow(
            id=self.id,
            symbol=self.symbol,
            side=self.side,
            shares=self.shares,
            entry=self.entry,
            stop=self.stop,
            target1=self.target1,
            exit_price=self.exit_price,
            pnl=self.pnl,
            realized_r=self.realized_r,
            setup=self.setup,
            opened_at=self.opened_at,
            closed_at=self.closed_at,
            hold_minutes=hold_min,
            regime_at_open=self.regime_at_open,
            rs_pct_at_open=self.rs_pct_at_open,
            liquidity_tier_at_open=self.liquidity_tier_at_open,
            mistakes=[m.value for m in self.mistakes],
            notes=self.notes,
        )


class JournalService:
    def __init__(self, bus: EventBus | None = None) -> None:
        self._trades: dict[int, _StoredTrade] = {}
        self._next_id = 1
        self._lock = Lock()
        self._bus = bus

    # --------------- open/close/update ---------------

    def open(self, req: TradeOpenRequest) -> TradeRow:
        if req.entry == req.stop:
            raise ValueError("entry and stop cannot be equal")
        with self._lock:
            tid = self._next_id
            self._next_id += 1
            setup_label = req.setup.value if isinstance(req.setup, SetupType) else req.setup
            t = _StoredTrade(
                id=tid,
                symbol=req.symbol.upper(),
                side=req.side,
                shares=req.shares,
                entry=req.entry,
                stop=req.stop,
                original_stop=req.stop,
                target1=req.target1,
                setup=setup_label,
                signal_id=req.signal_id,
                opened_at=datetime.now(timezone.utc),
                regime_at_open=req.regime_at_open,
                rs_pct_at_open=req.rs_pct_at_open,
                liquidity_tier_at_open=req.liquidity_tier_at_open,
                catalyst_at_open=req.catalyst_at_open,
                notes=req.notes,
                emotion=req.emotion,
            )
            self._trades[tid] = t
        if self._bus is not None:
            self._bus.publish(
                EventType.TRADE_OPEN,
                {"trade_id": tid, "symbol": t.symbol, "shares": t.shares},
            )
        return t.to_row()

    def move_stop(self, trade_id: int, new_stop: float) -> TradeRow:
        with self._lock:
            t = self._trades.get(trade_id)
            if t is None:
                raise KeyError(trade_id)
            if not t.is_open:
                raise ValueError("cannot move stop on a closed trade")
            t.stop = new_stop
        return t.to_row()

    def close(
        self, trade_id: int, exit_price: float, notes: str | None = None
    ) -> TradeRow:
        with self._lock:
            t = self._trades.get(trade_id)
            if t is None:
                raise KeyError(trade_id)
            if not t.is_open:
                raise ValueError("trade already closed")
            t.exit_price = exit_price
            if t.side == Side.LONG:
                t.pnl = (exit_price - t.entry) * t.shares
            else:
                t.pnl = (t.entry - exit_price) * t.shares
            t.closed_at = datetime.now(timezone.utc)
            if notes:
                t.notes = (t.notes + " | " + notes).strip(" |") if t.notes else notes

            prior_closed = [
                x for x in self._trades.values()
                if not x.is_open and x.id != trade_id
            ]
            prior_closed.sort(key=lambda x: x.closed_at or x.opened_at)
            snap = _snapshot(t)
            recent_snaps = [_snapshot(p) for p in prior_closed[-5:]]
            t.mistakes = detect_mistakes(snap, recent_snaps)
        if self._bus is not None:
            self._bus.publish(
                EventType.TRADE_CLOSE,
                {"trade_id": trade_id, "pnl": t.pnl, "mistakes": [m.value for m in t.mistakes]},
            )
        return t.to_row()

    # --------------- queries ---------------

    def get(self, trade_id: int) -> TradeRow:
        t = self._trades[trade_id]
        return t.to_row()

    def today(self) -> list[TradeRow]:
        today = date.today()
        return [
            t.to_row() for t in self._trades.values()
            if t.opened_at.date() == today
        ]

    def all_closed(self) -> list[_StoredTrade]:
        return [t for t in self._trades.values() if not t.is_open]

    def summary(self, trading_day: date | None = None) -> JournalSummary:
        day = trading_day or date.today()
        trades = [t for t in self._trades.values() if t.opened_at.date() == day]
        closed = [t for t in trades if not t.is_open]
        wins = [t for t in closed if (t.pnl or 0) > 0]
        win_rate = len(wins) / len(closed) if closed else 0.0
        total_pnl = sum((t.pnl or 0) for t in closed)
        rs = [t.realized_r for t in closed if t.realized_r is not None]
        avg_r = sum(rs) / len(rs) if rs else 0.0
        best = max((t.pnl or 0) for t in closed) if closed else 0.0
        worst = min((t.pnl or 0) for t in closed) if closed else 0.0

        mistakes: dict[str, int] = {}
        for t in closed:
            for m in t.mistakes:
                mistakes[m.value] = mistakes.get(m.value, 0) + 1

        return JournalSummary(
            trading_day=day,
            n_total=len(trades),
            n_open=len(trades) - len(closed),
            n_closed=len(closed),
            win_rate=round(win_rate, 3),
            total_pnl=round(total_pnl, 2),
            avg_r=round(avg_r, 3),
            best_trade_pnl=round(best, 2),
            worst_trade_pnl=round(worst, 2),
            mistakes_count=mistakes,
        )

    def by_setup(self) -> list[SetupStats]:
        return _bucket_stats(
            self.all_closed(),
            key=lambda t: t.setup or "unspecified",
            stats_cls=SetupStats,
            label_field="setup",
        )

    def by_regime(self) -> list[RegimeStats]:
        return _bucket_stats(
            [t for t in self.all_closed() if t.regime_at_open],
            key=lambda t: t.regime_at_open,
            stats_cls=RegimeStats,
            label_field="regime",
        )


def _snapshot(t: _StoredTrade) -> TradeSnapshot:
    return TradeSnapshot(
        symbol=t.symbol,
        side=t.side,
        shares=t.shares,
        entry=t.entry,
        stop=t.stop,
        target1=t.target1,
        original_stop=t.original_stop,
        exit_price=t.exit_price or 0.0,
        pnl=t.pnl or 0.0,
        opened_at=t.opened_at,
        closed_at=t.closed_at or t.opened_at,
    )


def _bucket_stats(trades, key, stats_cls, label_field: str):
    buckets: dict[str, list[_StoredTrade]] = {}
    for t in trades:
        buckets.setdefault(key(t), []).append(t)
    out = []
    for label, group in buckets.items():
        n = len(group)
        wins = [t for t in group if (t.pnl or 0) > 0]
        losses = [t for t in group if (t.pnl or 0) < 0]
        win_rate = len(wins) / n if n else 0
        avg_win_r = _avg_r(wins)
        avg_loss_r = _avg_r(losses)
        expectancy = win_rate * avg_win_r + (1 - win_rate) * avg_loss_r
        total_pnl = sum((t.pnl or 0) for t in group)
        avg_r = _avg_r(group)
        kwargs = {
            label_field: label,
            "n": n,
            "win_rate": round(win_rate, 3),
            "expectancy_r": round(expectancy, 3),
            "total_pnl": round(total_pnl, 2),
        }
        if stats_cls is SetupStats:
            kwargs["avg_r"] = round(avg_r, 3)
        out.append(stats_cls(**kwargs))
    out.sort(key=lambda s: s.expectancy_r, reverse=True)
    return out


def _avg_r(trades) -> float:
    rs = [t.realized_r for t in trades if t.realized_r is not None]
    return sum(rs) / len(rs) if rs else 0.0
